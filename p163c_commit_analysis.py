#!/usr/bin/env python3
"""p163c_commit_analysis.py — per-slot commit/alignment forensics.

Joins, per TX-lattice slot:
  - detection:  [P158-DIAG] episode_end start=.. max_cor=..   (strong = real)
  - frame commit: [SYNC_LONG] HT-mode-plateau SELECTED ... computed_fs=.. tag_off=..
  - decode:     [DECODE_SEQ] seq=..

For each slot: detected? committed? computed_fs (pre-force d_frame_start)?
decoded?  Then: for chain-lost frames (detected, not decoded), was there a
commit, and how far was its computed_fs from the forced 174?

Usage: p163c_commit_analysis.py <run.err>
"""
import re
import sys
import numpy as np


def main():
    path = sys.argv[1]
    seqs = set()
    ep = {}        # start(real-time) -> max_cor
    out2start = {} # trigger out_pos(compressed) -> start(real-time)
    commits = []   # (tag_off(compressed), computed_fs, best_lower_peak, score)
    for line in open(path, errors='ignore'):
        m = re.search(r'\[DECODE_SEQ\] seq=(\d+)', line)
        if m:
            seqs.add(int(m.group(1)))
            continue
        m = re.search(r'\[P158-DIAG\] trigger start=(\d+) trigger_cor=[\d.]+ out_pos=(\d+)', line)
        if m:
            out2start[int(m.group(2))] = int(m.group(1))
            continue
        m = re.search(r'episode_end start=(\d+) len=\d+ max_cor=([\d.]+)', line)
        if m:
            ep[int(m.group(1))] = float(m.group(2))
            continue
        m = re.search(r'HT-mode-plateau SELECTED:.*computed_fs=(-?\d+) tag_off=(\d+)', line)
        if m:
            cs = int(m.group(1))
            to = int(m.group(2))
            lp = re.search(r'best_lower_peak=(\d+)', line)
            sc = re.search(r'score=([\d.]+)', line)
            commits.append((to, cs, int(lp.group(1)) if lp else -1,
                            float(sc.group(1)) if sc else -1.0))

    strong = np.sort(np.array([k for k, v in ep.items() if v > 100]))
    lo, hi = min(seqs), max(seqs)
    missing = sorted(set(range(lo, hi + 1)) - seqs)

    # lattice from strong episode starts (real-time)
    best = None
    for period in np.arange(2.000e6, 2.060e6, 250):
        ph = np.mod(strong, period)
        z = np.exp(2j * np.pi * ph / period).mean()
        if best is None or abs(z) > best[1]:
            best = (period, abs(z))
    period = best[0]
    p0 = np.angle(np.exp(2j * np.pi * np.mod(strong, period) / period).mean()) % (2 * np.pi)
    p0 *= period / (2 * np.pi)
    print(f'frames [{lo}..{hi}]={hi-lo+1}  decoded={len(seqs)}  missing={len(missing)}  '
          f'commits={len(commits)}  strong_eps={len(strong)}  trigger_map={len(out2start)}')
    print(f'lattice period={period/20000:.4f}ms conc={best[1]:.3f}')

    # map each commit's tag_off(compressed) -> real-time start -> slot index
    def slot_of_rt(rt):
        return lo + int(round((rt - p0) / period))

    ok_fs = []
    loss_fs = []
    loss_no_commit = 0
    unmapped_commits = 0
    loss_detail = []
    # build slot -> list of computed_fs
    slot_fs = {}
    for to, cs, lp, sc in commits:
        rt = out2start.get(to)
        if rt is None:
            unmapped_commits += 1
            continue
        slot_fs.setdefault(slot_of_rt(rt), []).append(cs)

    for k in range(lo, hi + 1):
        cfs = slot_fs.get(k)
        if k in missing:
            if cfs:
                loss_fs.extend(cfs)
                loss_detail.append((k, cfs))
            else:
                loss_no_commit += 1
                loss_detail.append((k, None))
        else:
            if cfs:
                ok_fs.extend(cfs)

    ok_fs = np.array(ok_fs)
    if len(ok_fs):
        print(f'\nOK 帧 commit computed_fs: n={len(ok_fs)}  '
              f'p5={np.percentile(ok_fs,5):.0f} p25={np.percentile(ok_fs,25):.0f} '
              f'p50={np.percentile(ok_fs,50):.0f} p75={np.percentile(ok_fs,75):.0f} p95={np.percentile(ok_fs,95):.0f}')
    print(f'unmapped commits (tag_off not in trigger map): {unmapped_commits}')
    print(f'\n丢失帧明细（seq: computed_fs 列表 / None=无 commit）:')
    for k, cfs in loss_detail:
        print(f'  seq={k}: computed_fs={cfs}')
    print(f'\n链损分类: 有 commit={len([l for l in loss_detail if l[1]])}  '
          f'无 commit(搜索失败)={loss_no_commit}')


if __name__ == '__main__':
    main()
