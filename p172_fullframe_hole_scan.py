#!/usr/bin/env python3
"""Phase 172 M1: FULL-FRAME silence-hole scan (offline, zero USRP time).

p170b_localize.py only scans the preamble region (+250..+520 rel L-STF start).
This scan covers the whole frame (+16..+2470) to answer:
  (a) hole POSITION distribution within frames (burst-start-locked => scheduler
      chunk artifact; uniform => host jitter / NIC),
  (b) hole DURATION distribution,
  (c) whether FULL-STRENGTH frames carry data-section holes (P171's two
      unexplained full-strength failures had intact preambles).

Frame extent ~2481 samples (P170: TX capture 800 frames x 2481 exactly).
Hole = rolling mean |x| (w=24) < 5% of frame peak, run length >= 24.
"""
import numpy as np
import sys

FS = 20e6
FRAME_LEN = 2481
W = 24

def detect_bursts(path):
    CHUNK = 100_000_000
    pos_list = []
    offset = 0
    tail = np.zeros(0, dtype=np.complex64)
    thr = None
    while True:
        raw = np.fromfile(path, dtype=np.complex64, count=CHUNK, offset=offset*8)
        if len(raw) == 0:
            break
        x = np.concatenate([tail, raw])
        mult = np.abs(x[16:] * np.conj(x[:-16]))
        box = np.convolve(mult, np.ones(16), mode='valid')
        if thr is None:
            p90 = np.percentile(box, 90)
            thr = max(p90 * 200, 1e-3)
        idx = np.where(box > thr)[0]
        if len(idx):
            grp = np.split(idx, np.where(np.diff(idx) > 2000)[0]+1)
            for g in grp:
                if len(g) >= 10:
                    pos_list.append(offset - 16 + int(g[0]))
        tail = x[-32:]
        offset += len(raw)
        del x, mult, box
    pos_arr = np.array(pos_list)
    if len(pos_arr) > 1:
        keep = np.concatenate([[True], np.diff(pos_arr) > 2000])
        pos_arr = pos_arr[keep]
    return pos_arr

def scan_frame(path, p):
    """Return (peak, mean_mag, min_roll, holes[(start_rel, dur, min_val)])."""
    seg = np.fromfile(path, dtype=np.complex64, count=FRAME_LEN+200,
                      offset=max(0, p-50)*8)
    base = min(50, p)
    frame = seg[base:base+FRAME_LEN]
    if len(frame) < FRAME_LEN//2:
        return None
    mag = np.abs(frame)
    peak = mag.max()
    thresh = 0.05 * peak
    rm = np.convolve(mag, np.ones(W)/W, mode='valid')
    if len(rm) == 0:
        return None
    below = rm < thresh
    holes = []
    i = 0
    while i < len(below):
        if below[i]:
            j = i
            while j < len(below) and below[j]:
                j += 1
            dur = j - i + W - 1  # window span
            if dur >= W:
                holes.append((i, dur, float(rm[i:j].min())))
            i = j
        else:
            i += 1
    return float(peak), float(mag.mean()), float(rm.min()), holes

def zone(start):
    if start < 160:   return 'L-STF '
    if start < 320:   return 'L-LTF '
    if start < 400:   return 'L-SIG '
    if start < 560:   return 'HT-SIG'
    if start < 720:   return 'HT-LTF'
    return 'DATA  '

def main(path):
    print(f"=== {path} ===")
    pos = detect_bursts(path)
    print(f"bursts: {len(pos)}")
    n_hole = 0
    all_holes = []
    for bi, p in enumerate(pos):
        r = scan_frame(path, int(p))
        if r is None:
            continue
        peak, mean_mag, min_roll, holes = r
        if holes:
            n_hole += 1
            for (s, d, mv) in holes:
                all_holes.append((bi, int(p), s, d, mv, peak, mean_mag))
    print(f"frames with >=1 hole: {n_hole} / {len(pos)}")
    print(f"{'burst':>5} {'zone':>6} {'start':>5} {'dur':>5} {'min_roll':>8} {'peak':>6} {'mean':>6}")
    for bi, p, s, d, mv, pk, mn in all_holes:
        print(f"{bi:>5} {zone(s):>6} {s:>5} {d:>5} {mv:>8.4f} {pk:>6.2f} {mn:>6.3f}")
    if all_holes:
        starts = np.array([h[2] for h in all_holes])
        durs = np.array([h[3] for h in all_holes])
        print(f"\nhole start: median={np.median(starts):.0f} "
              f"min={starts.min()} max={starts.max()}")
        print(f"hole dur:   median={np.median(durs):.0f} "
              f"min={durs.min()} max={durs.max()} samples "
              f"(1 sample = 50 ns)")
        hist, edges = np.histogram(starts, bins=[0,160,320,400,560,720,2481])
        zones = ['L-STF','L-LTF','L-SIG','HT-SIG','HT-LTF','DATA']
        for z, h in zip(zones, hist):
            print(f"  {z}: {h}")

if __name__ == '__main__':
    for path in sys.argv[1:]:
        main(path)
