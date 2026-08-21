#!/usr/bin/env python3
"""Parse sweep .err logs: pair frames with outcomes, per-symbol QPSK phase
deviation trajectories for FAIL vs OK frames (MCS 1/2 only — QPSK).

Usage: python3 mcs1_frame_fate.py <log.err> [mcs]
Ideal QPSK points at 45+90k deg; per-SC deviation = min angular distance.
Only the 4 logged SCs (eq[0], eq[25], eq[26], eq[51]) are available.
"""
import math
import re
import sys
from collections import defaultdict

EQ_RE = re.compile(r'\[EQ_HTDATA\] sym=(\d+) cpe_deg=.*?eq\[0\]=')
EQV_RE = re.compile(r'\[EQ_HTDATA\] sym=(\d+) eq\[0\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[25\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[26\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[51\]=([-\d.]+)([+-][\d.]+)i')
PARAMS_RE = re.compile(r'\[EQ_CONV_PARAMS\] mcs=(\d+) len=(\d+) n_dbps=(\d+) n_sym=(\d+)')
WIN_RE = re.compile(r'\[LSIG_CANDIDATE_WIN\]')

QPSK_IDEALS = [45 + 90 * k for k in range(4)]


def qpsk_dev(re_, im_):
    """Angular deviation (deg) from nearest ideal QPSK point."""
    ang = math.degrees(math.atan2(im_, re_)) % 360
    return min(abs(ang - t) if abs(ang - t) <= 180 else 360 - abs(ang - t)
               for t in QPSK_IDEALS)


def main(path):
    frames = []          # list of dict(params, symbols{sym: [dev x4]}, outcome)
    cur = None
    pending_sym = None
    for line in open(path, errors='ignore'):
        m = PARAMS_RE.search(line)
        if m:
            if cur is not None:
                cur['outcome'] = 'orphan'
                frames.append(cur)
            cur = {'mcs': int(m.group(1)), 'n_sym': int(m.group(4)),
                   'symbols': defaultdict(list), 'outcome': None}
            continue
        if cur is None:
            continue
        m = EQ_RE.search(line)
        if m and 'eq[0]=...' in line:
            pending_sym = int(m.group(1))
            continue
        m = EQV_RE.search(line)
        if m:
            sym = int(m.group(1))
            vals = [(float(m.group(2 + 2 * i)), float(m.group(3 + 2 * i)))
                    for i in range(4)]
            cur['symbols'][sym] = [qpsk_dev(r, i) for r, i in vals]
            continue
        if 'DECODE_SUCCESS' in line:
            cur['outcome'] = 'OK'
            frames.append(cur)
            cur = None
            continue
        m2 = re.search(r'\[DECODE_FAIL\] Conv FCS error.*ourmac=(\d)', line)
        if m2:
            cur['outcome'] = 'FAIL' if m2.group(1) == '1' else 'foreign'
            frames.append(cur)
            cur = None
            continue
    if cur is not None:
        cur['outcome'] = 'orphan'
        frames.append(cur)

    ok = [f for f in frames if f['outcome'] == 'OK']
    fail = [f for f in frames if f['outcome'] == 'FAIL']
    print(f'{path}: frames OK={len(ok)} FAIL={len(fail)} '
          f'orphan={sum(1 for f in frames if f["outcome"] == "orphan")} '
          f'foreign={sum(1 for f in frames if f["outcome"] == "foreign")}')

    for name, group in (('OK', ok), ('FAIL', fail)):
        bysym = defaultdict(list)
        for f in group:
            for sym, devs in f['symbols'].items():
                bysym[sym].append(sum(devs) / len(devs))
        if not bysym:
            continue
        maxsym = max(bysym)
        line_out = f'  {name}: mean per-SC QPSK deviation (deg) by symbol: '
        for s in range(maxsym + 1):
            v = bysym.get(s)
            line_out += f'{s}:{sum(v) / len(v):5.1f}' if v else f'{s}:  n/a'
            line_out += ' '
        print(line_out)


if __name__ == '__main__':
    main(sys.argv[1])
