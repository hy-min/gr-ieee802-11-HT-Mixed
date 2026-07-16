#!/usr/bin/env python3
"""Phase 150 systematic-debugging Step 1.1: GROUND TRUTH — count REAL frames in a
capture via amplitude-burst detection (robust for strong signals).

Frames are high-amplitude bursts rising far above the noise floor. We find samples
with |x| above an absolute threshold, group nearby samples into bursts (a new burst
starts after a gap > min_gap samples), and count bursts = real frames. This is the
denominator for the TRUE decode_mac arrival rate (decoded / real_frames).
"""
import argparse
import numpy as np


def count_frames(iq, thresh, min_gap, chunk=20_000_000):
    raw = np.memmap(iq, dtype=np.complex64, mode='r')
    total = len(raw)
    bursts = []          # global sample index of each burst center
    prev_peak = -10**12  # global index of last above-thresh sample
    open_burst = False
    for off in range(0, total, chunk):
        seg = np.abs(np.array(raw[off:off + chunk]))
        idx = np.where(seg > thresh)[0]
        for q in idx:
            g = off + int(q)
            if g - prev_peak > min_gap:
                bursts.append(g)      # new burst start
            prev_peak = g
    return total, bursts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iq', required=True)
    ap.add_argument('--thresh', type=float, default=3.0,
                    help='absolute |x| threshold (noise floor ~0.03, frames >>1)')
    ap.add_argument('--min-gap', type=int, default=100_000,
                    help='min samples between bursts (100k = 5ms @20MHz)')
    args = ap.parse_args()

    total, bursts = count_frames(args.iq, args.thresh, args.min_gap)
    dur = total / 20e6
    n = len(bursts)
    print(f"[P150] file={args.iq} samples={total} ({dur:.2f}s)")
    print(f"[P150] REAL frames (amplitude bursts): {n}  -> {n/dur:.2f}/s")
    if n > 1:
        gaps = np.diff(bursts)
        print(f"[P150] inter-frame gap: median={int(np.median(gaps))} "
              f"min={int(gaps.min())} max={int(gaps.max())} samples "
              f"(expect ~2,000,000 for 100ms interval)")
        print(f"[P150] first 10 frame starts: {bursts[:10]}")
    print(f"[P150] GROUND_TRUTH real_frames={n}")


if __name__ == '__main__':
    main()
