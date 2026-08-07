#!/usr/bin/env python3
"""p162b_analyze.py — per-arm extraction for the cor-floor ABAB batch.

For each pair arm's saved harness stderr (pairNN_[AB].rt.err), reports:
  - unique wifi_start detections (offset-deduped; noise+real)
  - garbage L-SIG attempts (LSIG_DECODE total minus correct enc=0 len=72)
  - DECODE_SUCCESS / terminal fails (ground truth)

Usage: p162b_analyze.py <batch_dir>   (the <ts> dir under batch_results/p162b_cor_floor/)
"""
import re
import sys
import glob
import os


def analyze(path):
    det_offsets = set()
    lsig_total = 0
    lsig_correct = 0
    ds = 0
    fail = 0
    for line in open(path, errors='ignore'):
        m = re.search(r'first_key=wifi_start offset=(\d+)', line)
        if m:
            det_offsets.add(int(m.group(1)))
        if 'LSIG_DECODE' in line:
            lsig_total += 1
            if 'enc=0 len=72' in line:
                lsig_correct += 1
        if 'DECODE_SUCCESS' in line:
            ds += 1
        if 'LDPC FCS error calc' in line:
            fail += 1
    return dict(det=len(det_offsets), lsig_total=lsig_total,
                lsig_correct=lsig_correct, garbage=lsig_total - lsig_correct,
                ds=ds, fail=fail)


def main():
    d = sys.argv[1]
    rows = []
    for f in sorted(glob.glob(os.path.join(d, 'pair*_?.rt.err'))):
        base = os.path.basename(f)
        m = re.match(r'pair(\d+)_([AB])\.rt\.err', base)
        if not m:
            continue
        pair, arm = int(m.group(1)), m.group(2)
        r = analyze(f)
        rows.append((pair, arm, r))
    pairs = {}
    for pair, arm, r in rows:
        pairs.setdefault(pair, {})[arm] = r
    print(f'{"pair":>4} {"A_det":>7} {"B_det":>7} {"A_garb":>7} {"B_garb":>7} '
          f'{"A_DS":>5} {"B_DS":>5} {"A_fail":>6} {"B_fail":>6}')
    for p in sorted(pairs):
        a = pairs[p].get('A')
        b = pairs[p].get('B')
        if not a or not b:
            continue
        print(f'{p:>4} {a["det"]:>7} {b["det"]:>7} {a["garbage"]:>7} {b["garbage"]:>7} '
              f'{a["ds"]:>5} {b["ds"]:>5} {a["fail"]:>6} {b["fail"]:>6}')
    # means
    va = [pairs[p]['A'] for p in pairs if 'A' in pairs[p] and 'B' in pairs[p]]
    vb = [pairs[p]['B'] for p in pairs if 'A' in pairs[p] and 'B' in pairs[p]]
    if va:
        for k in ['det', 'garbage', 'ds', 'fail']:
            ma = sum(r[k] for r in va) / len(va)
            mb = sum(r[k] for r in vb) / len(vb)
            print(f'mean {k:>8}: A={ma:8.1f}  B={mb:8.1f}  (B-A={mb-ma:+.1f})')


if __name__ == '__main__':
    main()
