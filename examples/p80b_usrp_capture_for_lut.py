#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 80b Stage 1.5: Capture N>=30 USRP frames at 5250 MHz.

Required env vars for HT-SIG events to fire (Phase 81 discovery):
    export IEEE80211_LSIG_RATE_FORCE=0xD
    export IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9    # Phase 81 patch, commit 452059a
    export IEEE80211_TIMING_OFFSET_APPLY=1
    export IEEE80211_HTSIG_PER_SYMBOL_DELTA=1    # Phase 79
    export IEEE80211_HTSIG_INPUT_DUMP=1          # Phase 19 diag, dumps HT-SIG0/1 rx52 + H52

Output: /tmp/p80b_5250_capture.json — list of N frames with htsig0/htsig1 rx52/H52
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time


def run_capture(duration_s, freq_mhz, tx_gain, output_path):
    """Run test_usrp_minimal_loopback with diag env vars, parse HT_STRUCT_AUDIT lines."""
    env = os.environ.copy()
    env.update({
        "IEEE80211_LSIG_RATE_FORCE": "0xD",
        "IEEE80211_LSIG_RATE_ACCEPT": "0xD,0x9",
        "IEEE80211_TIMING_OFFSET_APPLY": "1",
        "IEEE80211_HTSIG_PER_SYMBOL_DELTA": "1",
        "IEEE80211_HTSIG_INPUT_DUMP": "1",
        "IEEE80211_HT_STRUCT_AUDIT": "1",
    })

    # test_usrp_minimal_loopback.py lives at repo root (NOT in examples/),
    # not under examples/. Hardcoded addr=192.168.10.2 inside the script.
    cmd = [
        "bash", "-c",
        f"unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so "
        f"PYTHONPATH=build/python/bindings:python:examples "
        f"/home/hy/conda/envs/gnuradio/bin/python "
        f"./test_usrp_minimal_loopback.py "
        f"--duration {duration_s} --freq {freq_mhz} --tx-gain {tx_gain} --rate 20"
    ]

    print(f"[RUN] {' '.join(cmd[:6])}... → {output_path}")
    log_file = "/tmp/p80b_capture.log"
    with open(log_file, "w") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                              env=env, cwd="/home/hy/gr-ieee802-11")
    print(f"[LOG] {log_file} ({proc.returncode})")
    return log_file


def parse_capture_log(log_file, min_frames=30):
    """Parse HT_STRUCT_AUDIT + HTSIG_INPUT_DUMP blocks from log.

    Expected per-frame lines:
      [HTSIG_INPUT_DUMP] frame_idx=N rx52=[a+bj,...] H52=[c+dj,...]
      [HT_SIG_CAND] ...

    Returns list of frames with {frame_idx, freq_mhz, htsig0:{rx52,H52}, htsig1:{rx52,H52}}.
    """
    with open(log_file, 'r') as f:
        content = f.read()

    # Look for HTSIG_INPUT_DUMP blocks (counter==4 only per Phase 35 contract)
    # Format: [HTSIG_INPUT_DUMP] frame=123 sym_idx=0 rx52=[...] H52=[...]
    frames = []
    pattern = re.compile(
        r'\[HTSIG_INPUT_DUMP\] frame=(\d+) sym_idx=(\d+) '
        r'rx52=\[([^\]]+)\] H52=\[([^\]]+)\]'
    )
    by_frame = {}
    for m in pattern.finditer(content):
        frame_idx = int(m.group(1))
        sym_idx = int(m.group(2))
        rx52_str = m.group(3)
        h52_str = m.group(4)
        # Parse complex array
        def parse_complex(s):
            nums = re.findall(r'([-+]?\d+\.\d+e[-+]?\d+|[-+]?\d+\.\d+)', s)
            # Expect real, imag pairs
            pairs = []
            for i in range(0, len(nums) - 1, 2):
                pairs.append((float(nums[i]), float(nums[i+1])))
            return pairs[:52]  # truncate to 52 SCs

        rx52 = parse_complex(rx52_str)
        h52 = parse_complex(h52_str)
        if len(rx52) != 52 or len(h52) != 52:
            continue  # malformed line

        if frame_idx not in by_frame:
            by_frame[frame_idx] = {"frame_idx": frame_idx, "freq_mhz": 5250}
        key = "htsig0" if sym_idx == 0 else "htsig1"
        by_frame[frame_idx][key] = {"rx52": rx52, "H52": h52}

    for k in sorted(by_frame.keys()):
        f = by_frame[k]
        if "htsig0" in f and "htsig1" in f:
            frames.append(f)

    print(f"[PARSE] {len(frames)} frames from {log_file}")
    return frames[:max(min_frames, 30)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=120,
                        help="Capture duration in seconds (longer = more frames)")
    parser.add_argument("--freq", type=int, default=5250)
    parser.add_argument("--tx-gain", type=int, default=0)
    parser.add_argument("--min-frames", type=int, default=30)
    parser.add_argument("--output", default="/tmp/p80b_5250_capture.json")
    args = parser.parse_args()

    log = run_capture(args.duration, args.freq, args.tx_gain, args.output)
    frames = parse_capture_log(log, args.min_frames)

    if len(frames) < args.min_frames:
        print(f"[FAIL] only {len(frames)} frames (need >= {args.min_frames})")
        sys.exit(1)

    with open(args.output, 'w') as f:
        json.dump(frames, f)

    print(f"[OK] saved {len(frames)} frames → {args.output}")


if __name__ == "__main__":
    main()