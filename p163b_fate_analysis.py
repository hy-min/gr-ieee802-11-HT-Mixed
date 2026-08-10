#!/usr/bin/env python3
"""p163b_fate_analysis.py — per-frame fate forensics for the arrival axis.

Joins three evidence streams from a DIAG + DECODE_SEQ realtime run (.err):
  - [DECODE_SEQ] seq=N            (decoded frames; MAC seq, 12-bit, +1/frame)
  - [P158-DIAG] trigger start=..  (every sync_short detection, real-time pos)
  - [P158-DIAG] episode_end start=.. max_cor=..  (episode outcome + strength)

Output: for each missing seq (lost frame), the detection/noise context at its
lattice slot, classifying the loss stage:
  - no strong detection near its slot        -> DETECTION loss
  - strong detection but no decode           -> CHAIN/DECODE loss
Plus the noise-detection density timeline (to see storm structure).

Usage: p163b_fate_analysis.py <run.err> [--expected N]
"""
import re
import sys
import numpy as np


def main():
    path = sys.argv[1]
    seqs = set()
    trig = []   # (pos, trigger_cor)
    ep = {}     # start -> max_cor
    for line in open(path, errors='ignore'):
        m = re.search(r'\[DECODE_SEQ\] seq=(\d+)', line)
        if m:
            seqs.add(int(m.group(1)))
            continue
        m = re.search(r'\[P158-DIAG\] trigger start=(\d+) trigger_cor=([\d.]+)', line)
        if m:
            trig.append((int(m.group(1)), float(m.group(2))))
            continue
        m = re.search(r'episode_end start=(\d+) len=\d+ max_cor=([\d.]+)', line)
        if m:
            ep[int(m.group(1))] = float(m.group(2))

    if not seqs:
        print('no DECODE_SEQ lines — was IEEE80211_DECODE_SEQ=1 set?')
        return

    lo, hi = min(seqs), max(seqs)
    n_frames = hi - lo + 1
    missing = sorted(set(range(lo, hi + 1)) - seqs)
    print(f'seqs decoded: {len(seqs)}  range [{lo}..{hi}] = {n_frames} frames  '
          f'missing={len(missing)} ({len(missing)/n_frames*100:.2f}% loss)')

    # detection timeline (strong = real-strength trigger on the episode level)
    trig_pos = np.array([t[0] for t in trig]) if trig else np.array([])
    ep_strong = {k: v for k, v in ep.items() if v > 100}   # real frames
    ep_weak = {k: v for k, v in ep.items() if v <= 100}    # noise
    print(f'detections: {len(trig)} trigger lines, episodes={len(ep)} '
          f'(strong/real={len(ep_strong)} weak/noise={len(ep_weak)})')

    # lattice fit on strong episode starts (real frames): period ~100.088ms
    s = np.sort(np.array(list(ep_strong.keys())))
    if len(s) > 10:
        best = None
        for period in np.arange(2.000e6, 2.060e6, 250):
            ph = np.mod(s, period)
            z = np.exp(2j * np.pi * ph / period).mean()
            if best is None or abs(z) > best[1]:
                best = (period, abs(z))
        period = best[0]
        p0 = np.angle(np.exp(2j * np.pi * np.mod(s, period) / period).mean()) % (2 * np.pi)
        p0 *= period / (2 * np.pi)
        print(f'lattice: period={period/20000:.4f}ms concentration={best[1]:.3f}')

        # per missing seq: lattice slot time, and whether a strong ep is near it
        # map seq -> slot index (seq lo is slot 0-ish)
        strong_pos = s
        det_loss = 0
        chain_loss = 0
        noise_ctx = []
        for mseq in missing[:200]:
            # expected position of this frame's L-STF on the lattice
            k = mseq - lo
            exp_pos = p0 + k * period
            # nearest strong episode within +-0.5 period
            if len(strong_pos):
                d = np.min(np.abs(strong_pos - exp_pos))
            else:
                d = 9e9
            if d < period * 0.5:
                chain_loss += 1
            else:
                det_loss += 1
            # noise density within +-1 period
            nw = sum(1 for wp in ep_weak if abs(wp - exp_pos) < period)
            noise_ctx.append(nw)
        print(f'missing-frame classification (first {min(200,len(missing))}): '
              f'DETECTION loss={det_loss}  CHAIN/DECODE loss={chain_loss}')
        if noise_ctx:
            print(f'noise episodes within +-1 slot of a lost frame: '
                  f'mean={np.mean(noise_ctx):.2f} max={max(noise_ctx)} '
                  f'(vs global weak density {len(ep_weak)/(n_frames):.3f}/slot)')

        # noise time structure: weak episodes per 10s bin
        if ep_weak:
            wk = np.sort(np.array(list(ep_weak.keys())))
            span = s.max() - s.min()
            bins = 10
            counts = []
            for b in range(bins):
                lo_b = s.min() + span * b / bins
                hi_b = s.min() + span * (b + 1) / bins
                counts.append(int(((wk >= lo_b) & (wk < hi_b)).sum()))
            print(f'weak/noise episodes per ~{span/bins/20e6:.0f}s bin: {counts}')


if __name__ == '__main__':
    main()
