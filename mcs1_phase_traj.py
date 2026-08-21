#!/usr/bin/env python3
"""Rebuild per-symbol phase trajectories from [EQ_HTDATA] logs and test
determinism: is the frame-tail phase damage a predictable slow drift
(linear slope, high R2, correlated dphi) or an irreducible random walk?

For each frame, per-symbol residual phase phi[sym] = mean over the 4 logged
SCs of the signed angle to the nearest QPSK ideal point. Then:
  - linear fit phi ~ sym  -> slope (deg/sym) + R2
  - dphi[sym]=phi[sym]-phi[sym-1] -> mean/std + lag-1 autocorrelation
  - split by outcome OK / FAIL
"""
import math
import re
import sys
from collections import defaultdict

EQV_RE = re.compile(r'\[EQ_HTDATA\] sym=(\d+) eq\[0\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[25\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[26\]=([-\d.]+)([+-][\d.]+)i '
                    r'eq\[51\]=([-\d.]+)([+-][\d.]+)i')
PARAMS_RE = re.compile(r'\[EQ_CONV_PARAMS\] mcs=(\d+) len=(\d+) n_dbps=\d+ n_sym=(\d+)')
FAIL_RE = re.compile(r'\[DECODE_FAIL\] Conv FCS error.*ourmac=(\d)')

IDEALS = [45 + 90 * k for k in range(4)]


def signed_dev(deg):
    """Signed angular deviation (deg, -45..+45) from nearest QPSK ideal."""
    d = deg % 360
    best = min(IDEALS, key=lambda t: min(abs(d - t), 360 - abs(d - t)))
    raw = d - best
    if raw > 180:
        raw -= 360
    if raw < -180:
        raw += 360
    return raw


def fit(pairs):
    """(slope_deg_per_sym, R2) least-squares linear fit of y~x."""
    n = len(pairs)
    if n < 2:
        return 0.0, 1.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    slope = sxy / sxx if sxx > 0 else 0.0
    if sxx <= 0:
        return 0.0, 1.0
    pred = [my + slope * (x - mx) for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, pred))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, r2


def main(path):
    frames = []  # (mcs, n_sym, {sym: phi}, outcome)
    cur = None
    for line in open(path, errors='ignore'):
        m = PARAMS_RE.search(line)
        if m:
            if cur is not None:
                cur['outcome'] = 'orphan'
                frames.append(cur)
            cur = {'mcs': int(m.group(1)), 'n_sym': int(m.group(3)),
                   'phi': {}, 'outcome': None}
            continue
        if cur is None:
            continue
        m = EQV_RE.search(line)
        if m:
            sym = int(m.group(1))
            devs = []
            for i in range(4):
                re_, im_ = float(m.group(2 + 2 * i)), float(m.group(3 + 2 * i))
                deg = math.degrees(math.atan2(im_, re_)) % 360
                devs.append(signed_dev(deg))
            cur['phi'][sym] = sum(devs) / len(devs)
            continue
        if 'DECODE_SUCCESS' in line:
            cur['outcome'] = 'OK'
            frames.append(cur)
            cur = None
            continue
        m = FAIL_RE.search(line)
        if m:
            cur['outcome'] = 'FAIL' if m.group(1) == '1' else 'foreign'
            frames.append(cur)
            cur = None
    if cur is not None:
        cur['outcome'] = 'orphan'
        frames.append(cur)

    ok = [f for f in frames if f['outcome'] == 'OK']
    fail = [f for f in frames if f['outcome'] == 'FAIL']
    print(f'{path}')
    print(f'  frames OK={len(ok)} FAIL={len(fail)} orphan={sum(1 for f in frames if f["outcome"]=="orphan")}')

    for name, group in (('OK', ok), ('FAIL', fail)):
        if not group:
            continue
        slopes, r2s = [], []
        dphis = []
        tail_phi = []      # phi at last symbol
        drift_accum = []   # total drift from sym0 to last
        for f in group:
            phis = sorted(f['phi'].items())
            if len(phis) < 3:
                continue
            slope, r2 = fit(phis)
            slopes.append(slope)
            r2s.append(r2)
            ds = [phis[i][1] - phis[i - 1][1] for i in range(1, len(phis))]
            dphis += ds
            tail_phi.append(phis[-1][1])
            drift_accum.append(phis[-1][1] - phis[0][1])
        # lag-1 autocorrelation of dphi
        rho = None
        if len(dphis) > 2:
            m_ = sum(dphis) / len(dphis)
            num = sum((dphis[i] - m_) * (dphis[i - 1] - m_) for i in range(1, len(dphis)))
            den = sum((d - m_) ** 2 for d in dphis)
            rho = num / den if den > 0 else 0.0
        mean_s = sum(slopes) / len(slopes)
        mean_r2 = sum(r2s) / len(r2s)
        mean_tail = sum(tail_phi) / len(tail_phi)
        mean_acc = sum(drift_accum) / len(drift_accum)
        print(f'  {name}: linear slope={mean_s:+.3f} deg/sym  R2={mean_r2:.2f}  '
              f'dphi std={ (sum((d - sum(dphis)/len(dphis))**2 for d in dphis)/len(dphis))**0.5:.2f} deg  '
              f'dphi lag1-rho={rho if rho is not None else float("nan"):+.2f}  '
              f'tail phi={mean_tail:+.1f} deg  total drift={mean_acc:+.1f} deg')


if __name__ == '__main__':
    main(sys.argv[1])
