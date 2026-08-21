#!/usr/bin/env python3
"""Pair per-symbol TX-reference mismatch profiles (.out, dout) with frame
outcomes (.err, USRP_LOG), split by OK/FAIL. Reveals WHERE bits die.

Usage: python3 mcs1_symerr_profile.py <run.err> <run.out>
"""
import re
import sys
from collections import defaultdict

SYM_RE = re.compile(r'\[decode_mac\]\[SYM\s+(\d+)\] deintl-vs-TX-punctured '
                    r'mismatches=\s*(\d+)')
SUM_RE = re.compile(r'\[decode_mac\]\[DEINTL-vs-TX-PUNCTURED\] '
                    r'total_mismatches=(\d+) first_bad_sym=(-?\d+)')
PARAMS_RE = re.compile(r'\[EQ_CONV_PARAMS\] mcs=(\d+) len=(\d+)')
FAIL_RE = re.compile(r'\[DECODE_FAIL\] Conv FCS error.*ourmac=(\d)')


def parse_out(path):
    """List of frames: [(n_sym_expected, {sym: mism}, total)] — sym=0 restart
    delimits frames."""
    frames = []
    cur = {}
    for line in open(path, errors='ignore'):
        m = SYM_RE.search(line)
        if m:
            sym, mism = int(m.group(1)), int(m.group(2))
            if sym == 0 and cur:
                frames.append(cur)
                cur = {}
            cur[sym] = mism
            continue
        if SUM_RE.search(line):
            if cur:
                frames.append(cur)
                cur = {}
    if cur:
        frames.append(cur)
    return frames


def parse_err(path):
    """List of outcomes in frame order: True=OK False=FAIL(ourmac=1) None=other."""
    out = []
    for line in open(path, errors='ignore'):
        if PARAMS_RE.search(line):
            out.append(None)  # placeholder, frame started
        elif 'DECODE_SUCCESS' in line:
            if out:
                out[-1] = True
        else:
            m = FAIL_RE.search(line)
            if m and m.group(1) == '1':
                for i in range(len(out) - 1, -1, -1):
                    if out[i] is None:
                        out[i] = False
                        break
    return out


def main(err_path, out_path):
    frames = parse_out(out_path)
    outcomes = parse_err(err_path)
    n = min(len(frames), len(outcomes))
    print(f'{err_path}\n  paired={n} (out_frames={len(frames)} '
          f'err_frames={len(outcomes)})')

    ok_map = defaultdict(list)
    fail_map = defaultdict(list)
    n_ok = n_fail = 0
    for fr, oc in zip(frames[:n], outcomes[:n]):
        if oc is True:
            n_ok += 1
            for s, v in fr.items():
                ok_map[s].append(v)
        elif oc is False:
            n_fail += 1
            for s, v in fr.items():
                fail_map[s].append(v)
    print(f'  OK={n_ok} FAIL={n_fail}')
    maxsym = max(list(ok_map.keys()) + list(fail_map.keys()) + [0])
    for name, mp in (('OK  ', ok_map), ('FAIL', fail_map)):
        row = f'  {name} mean mism/104 by sym: '
        for s in range(maxsym + 1):
            v = mp.get(s)
            row += f'{s}:{(sum(v) / len(v)):5.2f} ' if v else f'{s}:  n/a '
        print(row)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
