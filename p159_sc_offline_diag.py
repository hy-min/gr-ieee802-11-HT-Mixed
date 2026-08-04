#!/usr/bin/env python3
"""Offline: why does Schmidl-Cox |P|^2/R^2 break down on real USRP IQ?

Sanity-run evidence (2026-08-04): S&C metric -> 71661 wifi_start tags/45s
(35x boxcar), DECODE_SUCCESS=0. Hypothesis: DC/LO coherent component makes
|P|/R -> 1 during noise-only periods (P88-style structured-noise failure).

Reads a slice of a P150 antenna capture (same testbed/RF config as the
realtime path) and compares S&C vs boxcar metric distributions.
"""
import numpy as np

F = '/home/hy/captures/p150_ant_fix.fc32'
N = 40_000_000          # 2 s at 20 MHz
DTYPE = np.complex64

x = np.fromfile(F, dtype=DTYPE, count=N, offset=80_000_000)  # skip startup
print(f'samples: {len(x)/1e6:.0f}M  mean(DC)={x.mean():.6f}  '
      f'rms={np.sqrt(np.mean(np.abs(x)**2)):.4f}')

# DC-removed variant for comparison
x_dc = x - x.mean()

def metrics(v, tag):
    mult = v[16:] * np.conj(v[:-16])
    p2 = np.abs(v)**2
    W = 32
    P = np.convolve(mult, np.ones(W), mode='valid')          # complex sum
    R = np.convolve(p2[:-16], np.ones(W), mode='valid')      # energy, aligned with mult
    sc = (np.abs(P)**2) / (R**2 + 1e-12)                     # S&C [0,1]
    box = np.convolve(np.abs(mult), np.ones(16), mode='valid')  # boxcar
    for name, m in (('S&C', sc), ('boxcar', box)):
        q = np.percentile(m, [50, 90, 99, 99.9, 100])
        print(f'{tag} {name}: p50={q[0]:.4f} p90={q[1]:.4f} p99={q[2]:.4f} '
              f'p99.9={q[3]:.4f} max={q[4]:.4f}')
    # occupancy above candidate S&C thresholds
    for thr in (0.2, 0.35, 0.5, 0.7, 0.9):
        occ = np.mean(sc > thr)
        print(f'{tag} S&C>{thr}: occ={occ*100:.2f}%')
    return sc, box

sc_raw, box_raw = metrics(x, 'RAW ')
sc_dc, _ = metrics(x_dc, 'DCrm')

# Longest above-threshold run (plateau length) on raw S&C
for thr in (0.2, 0.5):
    above = sc_raw > thr
    # find run lengths
    d = np.diff(above.astype(np.int8))
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    if len(starts) and len(ends):
        if ends[0] < starts[0]: ends = ends[1:]
        if len(starts) > len(ends): starts = starts[:len(ends)]
        rl = ends - starts
        print(f'RAW S&C>{thr}: n_plateaus={len(rl)} max_len={rl.max()} '
              f'p99_len={np.percentile(rl, 99):.0f} n>=25={(rl >= 25).sum()}')
