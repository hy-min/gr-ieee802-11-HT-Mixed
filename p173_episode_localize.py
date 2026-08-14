#!/usr/bin/env python3
"""Phase 173: episode TX/RF localization.

For each torn/weak frame in the RX capture, pull the SAME frame index from the
TX-chain digital capture (frames back-to-back, 2481 samples each) and compare:
  TX digital torn/weak  -> host/GR side (software-fixable)
  TX digital clean+full -> device analog side (hardware)

Alignment: RX slot index k = round((pos - pos0)/P); TX frame k at byte offset
k*2481*8. Both streams start at flowgraph start.
"""
import sys
import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from p172_fullframe_hole_scan import detect_bursts, scan_frame, FRAME_LEN, W

def tx_frame_stats(path, k):
    seg = np.fromfile(path, dtype=np.complex64, count=FRAME_LEN, offset=k*FRAME_LEN*8)
    if len(seg) < FRAME_LEN:
        return None
    mag = np.abs(seg)
    peak = float(mag.max())
    rm = np.convolve(mag, np.ones(W)/W, mode='valid')
    min_roll = float(rm.min()) if len(rm) else 0.0
    return peak, float(mag.mean()), min_roll

def main(rx_path, tx_path):
    import os
    tx_frames = os.path.getsize(tx_path) // 8 // FRAME_LEN
    print(f"TX file: {tx_frames} frames (size/(8*{FRAME_LEN}) exact = "
          f"{os.path.getsize(tx_path) % (8*FRAME_LEN) == 0})")
    pos = detect_bursts(rx_path)
    d = np.diff(pos)
    P = np.median(d[(d > 1.5e6) & (d < 2.5e6)])
    ref = pos[0]
    print(f"RX bursts={len(pos)}  P={P:.0f}")

    print(f"{'slot':>5} {'RXpeak':>6} {'RXmin':>7} {'RXholes':>7} | {'TXpeak':>6} {'TXmin':>7}  verdict")
    n_dev, n_host = 0, 0
    for bi, p in enumerate(pos):
        r = scan_frame(rx_path, int(p))
        if r is None:
            continue
        peak, mean_mag, min_roll, holes = r
        real_holes = [(s, dd, mv) for (s, dd, mv) in holes if 40 <= s < 2400 and mv < 0.05]
        weak = peak < 2.0
        if not weak and not real_holes:
            continue  # intact frame
        k = int(round((p - ref) / P))
        tx = tx_frame_stats(tx_path, k) if k < tx_frames else None
        if tx is None:
            print(f"{k:>5} {peak:>6.2f} {min_roll:>7.4f} {len(real_holes):>7} |  TX frame missing")
            continue
        tx_peak, tx_mean, tx_min = tx
        tx_torn = tx_peak < 2.0 or tx_min < 0.05 * tx_peak
        verdict = "HOST-SIDE" if tx_torn else "DEVICE-SIDE"
        if tx_torn: n_host += 1
        else: n_dev += 1
        print(f"{k:>5} {peak:>6.2f} {min_roll:>7.4f} {len(real_holes):>7} | {tx_peak:>6.2f} {tx_min:>7.4f}  {verdict}")
    print(f"\n=== VERDICT: host-side={n_host}  device-side={n_dev} ===")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
