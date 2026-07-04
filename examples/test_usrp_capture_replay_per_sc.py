#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 80b Stage 2: Offline USRP capture replay with per-SC LUT.

Reuses Phase 79 capture-replay infrastructure (test_usrp_capture_replay_htsig.py):
  - Read /tmp/p80b_5250_capture.json from Task 6
  - Run frame_equalizer pipeline on each frame
  - Compare HT_SIG_CAND/parse/PARSE results with vs without LUT

Pass criteria:
  - Baseline (env=OFF): n_candidates ~16 per frame (mirrors 5890, no improvement)
  - With LUT (env=ON, lut.json): EITHER n_candidates_increase > 0 OR
                                  at least 1 frame achieves PARSE_OK
  - No regression: with-LUT avg_snr_htsig >= without-LUT avg_snr_htsig
"""

import argparse
import json
import os
import subprocess
import sys
import re


def run_replay(capture_path, lut_path=None, duration=30):
    """Re-run capture with frame_equalizer pipeline + dump HT-SIG events."""
    env = os.environ.copy()
    env["IEEE80211_LSIG_RATE_FORCE"] = "0xD"
    env["IEEE80211_LSIG_RATE_ACCEPT"] = "0xD,0x9"
    env["IEEE80211_TIMING_OFFSET_APPLY"] = "1"
    env["IEEE80211_HTSIG_PER_SYMBOL_DELTA"] = "1"
    env["IEEE80211_LSIG_VALIDITY_AUDIT"] = "1"
    if lut_path:
        env["IEEE80211_HTSIG_PER_SC_LUT"] = lut_path

    cmd = [
        "bash", "-c",
        f"unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so "
        f"PYTHONPATH=build/python/bindings:python:examples "
        f"/home/hy/conda/envs/gnuradio/bin/python "
        f"examples/test_usrp_capture_replay_htsig.py "
        f"--capture {capture_path} --duration {duration}"
    ]
    log_file = f"/tmp/p80b_replay_{'with_lut' if lut_path else 'baseline'}.log"
    with open(log_file, 'w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                       env=env, cwd="/home/hy/gr-ieee802-11")
    print(f"[LOG] {log_file}")
    return log_file


def parse_replay_metrics(log_file):
    """Parse avg_snr_htsig, n_candidates, n_parse_ok from log."""
    with open(log_file, 'r') as f:
        content = f.read()

    metrics = {}
    # avg_snr_htsig=float
    m = re.search(r'avg_snr_htsig[=:]?\s*(-?\d+\.\d+)', content)
    if m:
        metrics['avg_snr_htsig'] = float(m.group(1))

    # HT_SIG_CAND count
    n_cand = len(re.findall(r'\[HT_SIG_CAND\]', content))
    metrics['n_candidates'] = n_cand

    # PARSE_OK count
    n_ok = len(re.findall(r'\[HT_SIG_PARSE_OK\]', content))
    metrics['n_parse_ok'] = n_ok

    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default="/tmp/p80b_5250_capture.json")
    parser.add_argument("--lut", default="/tmp/p80b_lut_5250.json")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    print("\n=== Phase 80b Stage 2: Baseline (no LUT) ===")
    base_log = run_replay(args.capture, lut_path=None, duration=args.duration)
    base = parse_replay_metrics(base_log)

    print("\n=== Phase 80b Stage 2: With LUT ===")
    lut_log = run_replay(args.capture, lut_path=args.lut, duration=args.duration)
    with_lut = parse_replay_metrics(lut_log)

    print(f"\nBaseline:  {base}")
    print(f"With LUT:  {with_lut}")

    # Pass criteria
    print("\n=== Verdict ===")
    if 'avg_snr_htsig' in base and 'avg_snr_htsig' in with_lut:
        no_regress = with_lut['avg_snr_htsig'] >= base['avg_snr_htsig'] - 0.5
        improvement = with_lut.get('n_candidates', 0) > base.get('n_candidates', 0)
        any_parse = with_lut.get('n_parse_ok', 0) > 0
        if no_regress and (improvement or any_parse):
            print(f"[PASS] Stage 2: avg_snr_htsig {base['avg_snr_htsig']:.2f} → "
                  f"{with_lut['avg_snr_htsig']:.2f} dB, "
                  f"cand={with_lut['n_candidates']}, parse_ok={with_lut['n_parse_ok']}")
            sys.exit(0)
        else:
            print(f"[FAIL] no improvement detected")
            sys.exit(1)
    else:
        print("[FAIL] could not parse avg_snr_htsig from logs")
        sys.exit(1)


if __name__ == "__main__":
    main()
