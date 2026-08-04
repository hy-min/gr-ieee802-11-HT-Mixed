#!/usr/bin/env python3
"""Phase 159: offline arrival-loss budget on real USRP IQ (ground truth).

Simulates the CURRENT sync_short_fused(boxcar) + sync_short state machine
sample-faithfully on a P150 antenna capture (same testbed/RF config), finds
ground-truth frame positions (strong L-STF events), and classifies each:

  detected   - SEARCH at frame start, triggered within the L-STF window
  trapped    - COPY (a trap or previous episode) at frame start
  missed     - SEARCH at frame start but NO trigger within the L-STF window

Cross-checks vs on-air DIAG run (2026-08-04): COPY occupancy ~0.5%,
~1026 episodes/45s, trap max_cor ~0.3 vs real ~626 (scaled domain).

Also pre-tests the AND-gate variant (boxcar>thr AND S&C>0.5) to see if it
cuts false tags without losing real frames — BEFORE any C++ work.
"""
import numpy as np

F = '/home/hy/captures/p150_ant_fix.fc32'
N = 80_000_000            # 4 s at 20 MHz -> ~39 frames at 101.5 ms
FS = 20e6

# ---- load + calibrate scale so noise boxcar p90 == 0.13 (C++ scaled domain) ----
x = np.fromfile(F, dtype=np.complex64, count=N, offset=40_000_000)
mult = np.abs(x[16:] * np.conj(x[:-16]))
box = np.convolve(mult, np.ones(16), mode='valid')          # boxcar metric
k = 0.13 / np.percentile(box, 90)
x = x * np.sqrt(k)                                          # power scales k, |mult| scales k
box = box * k
print(f'[cal] scale k={k:.1f}  noise boxcar p90={np.percentile(box,90):.4f} '
      f'(target 0.13)  noise power p50={np.percentile(np.abs(x)**2,50):.6f}')

# Schmidl-Cox metric (for AND-gate variant), aligned with box
P = np.convolve(x[16:] * np.conj(x[:-16]), np.ones(32), mode='valid')
R = np.convolve((np.abs(x)**2)[:-16], np.ones(32), mode='valid')
sc = (np.abs(P)**2) / (R**2 + 1e-12)
sc = np.minimum(sc, 4.0)   # clip blow-ups (R->0) for threshold sanity

# ---- ground-truth frames: strong boxcar events, clustered ----
STRONG = 5.0               # >> noise p90 (0.13), << real-frame boxcar (~600)
idx = np.where(box > STRONG)[0]
frames = []
if len(idx):
    grp = np.split(idx, np.where(np.diff(idx) > 2000)[0] + 1)
    frames = [int(g[0]) for g in grp if len(g) >= 25]
print(f'[gt] ground-truth frames found: {len(frames)} '
      f'(expect ~{N/FS/0.1015:.0f})')

# ---- detector simulation (chunked, C++-faithful) ----
MIN_PLATEAU = 24
GAP_THRESHOLD = 500
MAX_SAMPLES = 5400 * 80
GAP_POWER = 0.01
CHUNK = 8192
SEARCH, COPY = 0, 1

def simulate(use_and_gate=False):
    state = SEARCH
    plateau = 0
    below = 0
    copied = 0
    thresh = 3.0
    window = np.zeros(4096)
    widx = 0
    wfilled = 0
    tags = []            # trigger positions
    copy_at = np.zeros(len(frames), dtype=bool)   # state at each GT frame start
    copy_samples = 0
    i = 0
    n = len(box)
    fset = frames
    fptr = 0
    while i < n:
        j = min(i + CHUNK, n)
        seg = box[i:j]
        # fill adaptive window (whole chunk, C++ look-ahead behavior)
        for v in seg:
            window[widx] = v
            widx = (widx + 1) & 4095
            if wfilled < 4096:
                wfilled += 1
        if wfilled >= 4096:
            p90 = np.sort(window)[wfilled * 9 // 10]
            thresh = max(p90 * 1.5, 0.01, 0.2)
        else:
            thresh = 3.0
        for t, v in enumerate(seg):
            pos = i + t
            # mark state at GT frame starts
            while fptr < len(fset) and fset[fptr] == pos:
                if state == COPY:
                    copy_at[fptr] = True
                fptr += 1
            if state == SEARCH:
                gate = (sc[pos] > 0.5) if use_and_gate else True
                if v > thresh and gate:
                    if plateau < MIN_PLATEAU:
                        plateau += 1
                    else:
                        tags.append(pos)
                        state = COPY
                        copied = 0
                        below = 0
                        plateau = 0
                else:
                    plateau = 0
            else:  # COPY
                copy_samples += 1
                copied += 1
                pw = np.abs(x[pos])**2 if pos < len(x) else 0.0
                if pw >= GAP_POWER:
                    below = 0
                else:
                    below += 1
                    if below >= GAP_THRESHOLD:
                        state = SEARCH
                        below = 0
                        copied = 0
                if copied >= MAX_SAMPLES:
                    state = SEARCH
                    copied = 0
        i = j
    return tags, copy_at, copy_samples

def report(tags, copy_at, copy_samples, label):
    # classify GT frames
    trapped = int(copy_at.sum())
    det = 0
    for f0 in frames:
        if copy_at[frames.index(f0)]:
            continue
        if any(f0 - 2000 <= tg <= f0 + 2000 for tg in tags):
            det += 1
    # false tags: not within +-2000 of any GT frame
    fr = np.array(frames)
    false_tags = sum(1 for tg in tags
                     if len(fr) == 0 or np.min(np.abs(fr - tg)) > 2000)
    missed = len(frames) - trapped - det
    occ = copy_samples / len(box) * 100
    print(f'[{label}] tags={len(tags)} (false={false_tags})  '
          f'GT: detected={det} trapped={trapped} missed={missed} / {len(frames)}'
          f'  COPY occupancy={occ:.2f}%')
    return det, trapped, missed, false_tags

tags_b, copy_b, cs_b = simulate(use_and_gate=False)
report(tags_b, copy_b, cs_b, 'BOXCAR (current)')

tags_a, copy_a, cs_a = simulate(use_and_gate=True)
report(tags_a, copy_a, cs_a, 'AND-gate (boxcar AND S&C>0.5)')
