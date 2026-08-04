#!/usr/bin/env python3
"""Phase 159: lattice analysis of positioned COPY episodes (fixed)."""
import re
import sys
import numpy as np

rt = sys.argv[1] if len(sys.argv) > 1 else '/tmp/p159_diag2.rt.err'
arrival = int(sys.argv[2]) if len(sys.argv) > 2 else 0

starts, lens, cors, emas = [], [], [], []
for line in open(rt, errors='ignore'):
    m = re.search(r'episode_end start=(\d+) len=(\d+) max_cor=([\d.]+).*ema=([\d.]+)', line)
    if m:
        starts.append(int(m.group(1))); lens.append(int(m.group(2)))
        cors.append(float(m.group(3))); emas.append(float(m.group(4)))
starts = np.array(starts); lens = np.array(lens)
cors = np.array(cors); emas = np.array(emas)
strong = cors > 10
s = np.sort(starts[strong])
print(f'episodes={len(starts)} strong={strong.sum()} trap={(~strong).sum()}')
span0, span1 = starts.min(), starts.max()
print(f'span: {(span1-span0)/20e6:.1f}s')

# --- robust period: circular variance minimization over fine grid ---
TOL = 20000  # +-1ms
best = None
for period in np.arange(2.000e6, 2.060e6, 250):
    ph = np.mod(s, period)
    # concentration: resultant vector length of doubled angles (handles 0/2pi wrap)
    z = np.exp(2j * np.pi * ph / period).mean()
    conc = np.abs(z)
    if best is None or conc > best[1]:
        best = (period, conc)
period, conc = best
# phase from circular mean
ph = np.mod(s, period)
p0 = np.angle(np.exp(2j * np.pi * ph / period).mean()) % (2 * np.pi)
p0 = p0 * period / (2 * np.pi)
d = np.abs((s - p0) % period)
d = np.minimum(d, period - d)
on = d < TOL
print(f'lattice: period={period/20000:.3f}ms concentration={conc:.3f} '
      f'strong-on-lattice={on.sum()}/{len(s)} ({on.mean()*100:.0f}%)')

# phase histogram for visual inspection
h, edges = np.histogram(np.mod(s, period) / period, bins=40)
print('phase hist:', ' '.join(f'{v:3d}' for v in h))

# --- slots over span (correct anchoring at p0) ---
k0 = int(np.ceil((span0 - p0) / period))
k1 = int(np.floor((span1 - p0) / period))
slots = p0 + np.arange(k0, k1 + 1) * period
d2 = np.abs(s[:, None] - slots[None, :])
near = d2 < TOL
slot_hits = near.any(axis=0)
det = int(slot_hits.sum()); tot = len(slots)
print(f'lattice slots: {tot}  detected: {det} ({det/tot*100:.1f}%)  '
      f'missed: {tot-det} ({(tot-det)/tot*100:.1f}%)')
if det:
    mult = near.sum(axis=0)[slot_hits]
    print(f'strong eps per detected slot: mean={mult.mean():.2f} max={mult.max()}')
off = ~near.any(axis=1)
print(f'strong episodes OFF lattice (interference/other): {off.sum()}')

tr = ~strong
trap_on = 0
if tr.any() and tot:
    d_tr = np.abs(starts[tr][:, None] - slots[None, :])
    trap_on = int((d_tr < TOL).any(axis=1).sum())
print(f'trap episodes: {tr.sum()}  occupancy={lens[tr].sum()/(span1-span0)*100:.2f}%  '
      f'traps on lattice: {trap_on}')

if arrival:
    print('--- budget (per slot) ---')
    print(f'detected={det/tot*100:.1f}%  arrival={arrival}  '
          f'chain_success={arrival/max(det,1)*100:.1f}%  '
          f'end_to_end={arrival/tot*100:.1f}%')
