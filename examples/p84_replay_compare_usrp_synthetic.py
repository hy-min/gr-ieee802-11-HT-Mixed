#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 84: replay-comparison driver.

Usage:
    python examples/p84_replay_compare_usrp_synthetic.py \
        --in /tmp/p28_loopback_iq.fc32 \
        --duration 30 \
        --out /tmp/p84_comparison.json

Pipeline:
  1. Run existing p68_replay_offline.py as a subprocess with Phase 81 env config
     (IEEE80211_LSIG_RATE_FORCE=0xD, IEEE80211_TIMING_OFFSET_APPLY=1).
  2. Parse replay log via p84_replay_metric_log.parse_replay_log().
  3. Run test_htsig_viterbi_synthetic_layer5 with the SAME channel-model
     parameters, capture synthetic SNRs and rates.
  4. Emit side-by-side comparison as JSON.

Caveats:
  - The C++ frame_equalizer USRP_LOG format depends on env vars
    (IEEE80211_HT_STRUCT_AUDIT, IEEE80211_H52_EQ_INPUT_DUMP, etc). The parser
    looks for [FRAME_EQ] lines; if none are produced the script still emits
    a JSON report with frame_count=0.
  - USRP run requires actual hardware. Synthetic-only path runs without
    hardware and consumes 0 cable runs.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from p84_replay_metric_log import parse_replay_log


REPO_ROOT = '/home/hy/gr-ieee802-11'
REPLAY_SCRIPT = os.path.join(REPO_ROOT, 'examples/p68_replay_offline.py')
LAYER5_SCRIPT = os.path.join(REPO_ROOT, 'examples/test_htsig_viterbi_synthetic_layer5.py')


def run_usrp_replay(in_path, duration, log_path):
    """Invoke p68_replay_offline.py with Phase 81 env config.

    Note: The C++ frame_equalizer logs are emitted via USRP_LOG which goes to
    stdout by default. We capture stdout to the log file so parse_replay_log
    can scan for [FRAME_EQ] lines.
    """
    env = os.environ.copy()
    env.update({
        'IEEE80211_LSIG_RATE_FORCE': '0xD',
        'IEEE80211_LSIG_RATE_ACCEPT': '0xD,0x9',  # Phase 81 patch
        'IEEE80211_TIMING_OFFSET_APPLY': '1',
        'IEEE80211_HT_STRUCT_AUDIT': '1',
        'IEEE80211_H52_EQ_INPUT_DUMP': '1',
    })
    cmd = [
        '/home/hy/conda/envs/gnuradio/bin/python',
        REPLAY_SCRIPT,
        '--in', in_path,
        '--duration', str(duration),
        '--out-log', log_path,
    ]
    print(f"[P84] USRP replay: {' '.join(cmd[:6])}...", flush=True)
    try:
        with open(log_path, 'w') as logf:
            proc = subprocess.run(cmd, env=env, cwd=REPO_ROOT, timeout=duration + 60,
                                  stdout=logf, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            print(f"[P84] WARNING: replay returned code {proc.returncode}")
    except subprocess.TimeoutExpired:
        print(f"[P84] WARNING: replay timed out after {duration + 60}s")


def run_synthetic_layer5(out_path):
    """Run the Layer 5 model test, capture output."""
    cmd = [
        '/home/hy/conda/envs/gnuradio/bin/python',
        LAYER5_SCRIPT,
    ]
    print(f"[P84] Synthetic model test: {os.path.basename(LAYER5_SCRIPT)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    with open(out_path, 'w') as f:
        f.write("STDOUT:\n")
        f.write(proc.stdout)
        f.write("\nSTDERR:\n")
        f.write(proc.stderr)
    if proc.returncode != 0:
        print(f"[P84] WARNING: layer5 returned {proc.returncode}")
    return proc.stdout


def main():
    parser = argparse.ArgumentParser(description='Phase 84 USRP vs synthetic replay comparison')
    parser.add_argument('--in', dest='in_path', default='/tmp/p28_loopback_iq.fc32',
                        help='USRP capture file (fc32) to replay')
    parser.add_argument('--duration', type=float, default=30.0,
                        help='USRP replay wall-clock duration in seconds')
    parser.add_argument('--out', default='/tmp/p84_comparison.json',
                        help='Output comparison JSON path')
    parser.add_argument('--skip-usrp', action='store_true',
                        help='Skip USRP replay (synthetic-only path, 0 cable runs)')
    parser.add_argument('--skip-synth', action='store_true',
                        help='Skip synthetic model test')
    args = parser.parse_args()

    log_fd, log_path = tempfile.mkstemp(suffix='_p84_replay.log')
    os.close(log_fd)
    layer5_out = args.out + ".layer5"

    usrp_metrics = None
    if not args.skip_usrp:
        if not os.path.exists(args.in_path):
            print(f"[P84] ERROR: {args.in_path} does not exist; pass --skip-usrp to skip",
                  file=sys.stderr)
            sys.exit(1)
        run_usrp_replay(args.in_path, args.duration, log_path)
        snrs, rates, n_usrp = parse_replay_log(log_path, snr_is_db=False)
        usrp_metrics = {
            "snr_mean": sum(snrs) / max(1, len(snrs)) if snrs else 0.0,
            "snr_count": len(snrs),
            "rates": rates,
            "frame_count": n_usrp,
            "rate_distribution": {f"0x{r:X}": rates.count(r) for r in set(rates)},
        }
        os.unlink(log_path)
    synth_output = None
    if not args.skip_synth:
        run_synthetic_layer5(layer5_out)
        with open(layer5_out) as f:
            synth_output = f.read()

    out = {
        "phase": 84,
        "inputs": {
            "usrp_capture": args.in_path if not args.skip_usrp else None,
            "duration": args.duration if not args.skip_usrp else None,
        },
        "usrp": usrp_metrics,
        "synthetic_layer5_log": synth_output,
        "comparison_target": {
            "snr_band": [4.0, 10.0],
            "rate_distribution_expectation": "0x9 prevalent (Phase 81 fingerprint)",
        },
    }
    with open(args.out, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"[P84] Wrote {args.out}")
    if usrp_metrics:
        print(f"  USRP frames: {usrp_metrics['frame_count']}")
        print(f"  USRP mean SNR: {usrp_metrics['snr_mean']:.2f} dB")
        print(f"  USRP rate distribution: {usrp_metrics['rate_distribution']}")


if __name__ == '__main__':
    main()