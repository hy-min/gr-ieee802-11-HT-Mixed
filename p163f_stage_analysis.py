#!/usr/bin/env python3
"""p163f_stage_analysis.py — per-lost-frame stage-attribution (fixed tag_off join).

For each lattice slot, classifies the furthest stage the frame reached:
  TRIGGER (detected) -> FAST_SYNC restart -> SEARCH (Top-corr) -> COMMIT
  (HT-mode-plateau SELECTED) -> LSIG_OK -> EQ_TAG (arrived) -> DECODE_SEQ.

Missing seqs (lost frames) are attributed to their furthest stage, revealing
the true failure point (vs the earlier artifact that showed 'never-searched'
due to tag_off=0 on the FAST_SYNC path).

Usage: p163f_stage_analysis.py <run.err>
"""
import re
import sys
import numpy as np


def main():
    path = sys.argv[1]
    seqs = set()
    ep = {}
    out2start = {}
    stage = {}  # slot -> furthest stage string
    def note(slotset, pos, s):
        slotset.setdefault(pos, s)

    searches = {}   # rt -> topmag
    commits = {}    # rt -> computed_fs
    restarts = set()
    triggers = {}   # rt -> trigger_cor
    lsig_ok_pos = []  # nread-ish not available; count only
    eqtag_count = 0

    for line in open(path, errors='ignore'):
        m = re.search(r'\[DECODE_SEQ\] seq=(\d+)', line)
        if m: seqs.add(int(m.group(1))); continue
        m = re.search(r'\[P158-DIAG\] trigger start=(\d+) trigger_cor=([\d.]+) out_pos=(\d+)', line)
        if m:
            rt = int(m.group(1)); out2start[int(m.group(3))] = rt
            triggers[rt] = float(m.group(2)); continue
        m = re.search(r'episode_end start=(\d+) len=\d+ max_cor=([\d.]+)', line)
        if m: ep[int(m.group(1))] = float(m.group(2)); continue
        m = re.search(r'SYNC_LONG_FAST_SYNC\] Direct SYNC.*offset=(\d+)', line)
        if m:
            rt = out2start.get(int(m.group(1)))
            if rt: restarts.add(rt)
            continue
        m = re.search(r'Top correlation magnitude: ([\d.]+) tag_off=(\d+)', line)
        if m:
            rt = out2start.get(int(m.group(2)))
            if rt: searches[rt] = float(m.group(1))
            continue
        m = re.search(r'HT-mode-plateau SELECTED:.*computed_fs=(-?\d+) tag_off=(\d+)', line)
        if m:
            rt = out2start.get(int(m.group(2)))
            if rt: commits[rt] = int(m.group(1))
            continue

    strong = np.sort(np.array([k for k, v in ep.items() if v > 100]))
    lo, hi = min(seqs), max(seqs)
    missing = sorted(set(range(lo, hi + 1)) - seqs)
    best = None
    for period in np.arange(2.000e6, 2.060e6, 250):
        z = np.exp(2j * np.pi * np.mod(strong, period) / period).mean()
        if best is None or abs(z) > best[1]:
            best = (period, abs(z))
    period = best[0]
    p0 = np.angle(np.exp(2j * np.pi * np.mod(strong, period) / period).mean()) % (2 * np.pi)
    p0 *= period / (2 * np.pi)

    def near(rt_coll, center, tol):
        return any(abs(x - center) < tol for x in rt_coll)

    print(f'frames[{lo}..{hi}] missing={len(missing)}  '
          f'triggers={len(triggers)} restarts={len(restarts)} searches={len(searches)} commits={len(commits)}')
    print(f'lattice period={period/20000:.4f}ms conc={best[1]:.3f}')
    TOL = period * 0.4
    counts = {}
    details = []
    for k in missing:
        center = p0 + (k - lo) * period
        st = 'no-trigger'
        if near(triggers, center, TOL):
            st = 'trigger-only'
        if near(restarts, center, TOL):
            st = 'restart'
        if near(searches, center, TOL):
            st = 'searched'
        if near(commits, center, TOL):
            st = 'committed'
        counts[st] = counts.get(st, 0) + 1
        details.append((k, st))
    print('\n丢失帧最远到达阶段分布:')
    for s, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {s:14} {c}')
    print('\n明细 (seq: stage):')
    for k, s in details:
        print(f'  seq={k}: {s}')


if __name__ == '__main__':
    main()
