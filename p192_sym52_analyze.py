#!/usr/bin/env python3
"""
p192_sym52_analyze.py — where inside a long frame do the equalised symbols die?

P192 established that a clean self-link cannot decode frames beyond ~len400:
the MAC header decodes correctly while the tail does not, and the tail's
viterbi output is IDENTICAL from frame to frame even though the transmitter
sends different content every frame (the MAC seq increments).  A constant
output from a varying input means the tail carries no information at all.

`IEEE80211_SYM52_DUMP=all` dumps `d_rx_eq` -- the buffer decode_mac zero-fills
at frame start (`assign(expected, 0)`) and then fills symbol by symbol from the
upstream pipeline.  So if the upstream stops delivering, the tail stays at
exactly 0.0 and this dump shows it directly.

This tool splits the dump into frames (a frame restarts at sym=0), computes the
per-symbol mean |eq|^2, and reports where the energy dies.

Reading
-------
  tail zero from some K        -> upstream stopped delivering; K is the budget
  tail non-zero but frozen     -> upstream delivered stale/duplicated data
  tail non-zero and varying    -> the symbols arrive fine; the fault is
                                  downstream of d_rx_eq (demap/viterbi)

Usage: python3 p192_sym52_analyze.py <arm.err> [--zero-thresh 1e-6]
"""
import argparse
import re
import sys
from collections import Counter

LINE_RE = re.compile(r'^\[SYM52\] sym=(\d+) eq=(.*)$')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('errfile')
    p.add_argument('--zero-thresh', type=float, default=1e-6,
                   help='mean |eq|^2 below this counts as "no data"')
    p.add_argument('--max-frames', type=int, default=0,
                   help='0 = all')
    a = p.parse_args()

    frames = []
    cur = []
    expected = None
    with open(a.errfile, errors='replace') as fh:
        for line in fh:
            m = LINE_RE.match(line)
            if not m:
                continue
            s = int(m.group(1))
            if s == 0 and cur:
                frames.append(cur)
                cur = []
                if a.max_frames and len(frames) >= a.max_frames:
                    break
            vals = [float(x) for x in m.group(2).split(',') if x]
            re2 = im2 = 0.0
            n = 0
            for i in range(0, len(vals) - 1, 2):
                re2 += vals[i] * vals[i]
                im2 += vals[i + 1] * vals[i + 1]
                n += 1
            cur.append(((re2 + im2) / n if n else 0.0, re2, im2))
        else:
            if cur:
                frames.append(cur)

    if not frames:
        print('没有解析到帧——确认 err 文件里是否有 [SYM52] 行')
        return
    expected = max(len(f) for f in frames)
    print(f'帧数={len(frames)}  最长={expected} 符号')

    first_dead = Counter()
    n_full = 0
    for f in frames:
        dead = None
        for i, (e, _r, _m) in enumerate(f):
            if e < a.zero_thresh:
                dead = i
                break
        if dead is None:
            n_full += 1
        else:
            first_dead[dead] += 1

    print(f'  全程有能量（未出现零符号）: {n_full}')
    if first_dead:
        print('  首个零符号的序号分布（序号: 帧数）:')
        for k, c in sorted(first_dead.items())[:15]:
            print(f'    sym={k:<4} {c} 帧')
        if len(first_dead) > 15:
            print(f'    …共 {len(first_dead)} 个不同序号')

    # energy profile: mean per symbol index across frames
    prof = []
    imag_prof = []
    for i in range(expected):
        es = [f[i][0] for f in frames if i < len(f)]
        prof.append(sum(es) / len(es) if es else float('nan'))
        r = sum(f[i][1] for f in frames if i < len(f))
        m = sum(f[i][2] for f in frames if i < len(f))
        imag_prof.append(m / (r + m) if (r + m) > 0 else float('nan'))
    print('\n  逐符号平均 |eq|² （每 10 个符号取样）:')
    for i in range(0, expected, 10):
        print(f'    sym={i:<4} {prof[i]:.4f}')

    # BPSK phase probe: MCS0 is BPSK, so a correctly equalised symbol has all
    # its energy on the real axis.  Any residual rotation theta shows up as
    # imaginary energy ~ sin^2(theta), which is a direct read-out of a
    # progressive phase error across the frame -- and unlike |eq|^2 it does
    # not care whether the data was delivered at all.
    if imag_prof:
        print('\n  逐符号平均 虚部能量占比 Σim²/(Σre²+Σim²) （BPSK 应 ~0）:')
        for i in range(0, len(imag_prof), 5):
            print(f'    sym={i:<4} {imag_prof[i]:.4f}')


if __name__ == '__main__':
    main()
