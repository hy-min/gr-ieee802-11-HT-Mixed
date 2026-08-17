#!/usr/bin/env python3
"""Phase 174: quantify foreign-WiFi contamination of the failure/success budget.

Inputs:
  1. harness stderr with IEEE80211_DECODE_SEQ=1 + IEEE80211_FAIL_PSDU_DUMP=1
  2. RX capture (.fc32) for on-air foreign-frame census via CFO fingerprint

Accounting:
  ours_decoded  : DECODE_SEQ entries continuing the 12-bit seq thread (gap<=30)
  ours_lost     : sum of seq gaps in that thread
  foreign_dec   : DECODE_SEQ entries off-thread (arbitrary seq, thread ignores them)
  ours_fail     : FAIL_PSDU heads with addr1 == 42 42 42 42 42 42
  foreign_fail  : FAIL_PSDU heads without our MAC pattern
  onair_foreign : capture bursts whose L-STF CFO is outside our +75kHz band
"""
import re
import sys

import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from p172_fullframe_hole_scan import detect_bursts

FS = 20e6
OUR_MAC_HEX = '42' * 6          # addr1 bytes 4..9 -> hex chars 8..19
GAP_TOL = 30


def parse_stderr(path):
    txt = open(path, errors='ignore').read()
    seqs = [int(m.group(1)) for m in re.finditer(r'\[DECODE_SEQ\] seq=(\d+)', txt)]
    heads = re.findall(r'\[FAIL_PSDU\] len=\d+ head=([0-9a-f]+)', txt)
    n_succ = txt.count('[DECODE_SUCCESS]')
    n_fail = txt.count('[DECODE_FAIL]')
    return seqs, heads, n_succ, n_fail


def classify_seqs(seqs):
    if not seqs:
        return 0, 0, []
    last = seqs[0]
    ours, lost, foreign = 1, 0, []
    for s in seqs[1:]:
        gap = (s - last) % 4096
        if gap <= GAP_TOL:
            ours += 1
            lost += gap - 1 if gap > 0 else 0
            last = s
        else:
            foreign.append(s)
    return ours, lost, foreign


def cfo_of(path, p):
    seg = np.fromfile(path, dtype=np.complex64, count=2600, offset=int(p) * 8)
    if len(seg) < 600:
        return None, None
    lstf = seg[:144]
    cfo = np.angle((lstf[16:] * np.conj(lstf[:-16])).mean()) / (2 * np.pi * 16 / FS)
    return cfo, float(np.abs(seg).max())


def main(err_path, cap_path):
    seqs, heads, n_succ, n_fail = parse_stderr(err_path)
    ours, lost, foreign = classify_seqs(seqs)
    ours_fail = sum(1 for h in heads if h[8:20] == OUR_MAC_HEX)
    foreign_fail = len(heads) - ours_fail

    print(f"stderr: DECODE_SUCCESS={n_succ} DECODE_FAIL={n_fail} "
          f"SEQ_lines={len(seqs)} FAIL_PSDU={len(heads)}")
    print(f"seq thread: ours_decoded={ours} ours_lost(gaps)={lost} "
          f"foreign_decoded={len(foreign)}")
    if foreign:
        print(f"  foreign seqs: {foreign[:20]}")
    print(f"FAIL_PSDU: ours={ours_fail} foreign={foreign_fail}")
    for h in heads:
        tag = 'OURS' if h[8:20] == OUR_MAC_HEX else 'FOREIGN'
        print(f"  {tag}: {h}")

    pos = detect_bursts(cap_path)
    our_band = foreign_band = junk = 0
    f_samples = []
    for p in pos:
        r = cfo_of(cap_path, p)
        if r[0] is None:
            continue
        cfo, peak = r
        if 40e3 <= cfo <= 110e3 and peak > 2.0:
            our_band += 1
        elif peak > 0.5:
            foreign_band += 1
            f_samples.append((cfo / 1e3, peak))
        else:
            junk += 1
    print(f"\ncapture census: bursts={len(pos)} ours(+75k band)={our_band} "
          f"foreign(other CFO)={foreign_band} junk={junk}")
    print(f"  foreign CFO/peak sample: "
          f"{[(round(c,1), round(p,2)) for c, p in f_samples[:15]]}")

    total_our_tx = ours + lost
    print(f"\n=== P174 QUANTIFICATION ===")
    print(f"our frames: decoded={ours} lost={lost} (air-window est={total_our_tx})")
    print(f"foreign frames: decoded_into_success={len(foreign)} "
          f"counted_as_fail={foreign_fail} on_air={foreign_band}")
    print(f"DECODE_SUCCESS inflation: {len(foreign)}/{n_succ} "
          f"({100.0*len(foreign)/max(n_succ,1):.2f}%)")
    print(f"DECODE_FAIL contamination: {foreign_fail}/{n_fail}")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
