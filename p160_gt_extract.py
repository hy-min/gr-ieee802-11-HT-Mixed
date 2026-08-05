#!/usr/bin/env python3
"""Phase 160: ground-truth frame extraction from a fresh IQ capture.

Finds every real L-STF via the boxcar metric (chunked), clusters, and
records per-frame peak strength. Output: npy arrays of frame positions
and peak boxcar values — the ground truth for detection-miss accounting.
"""
import numpy as np
import sys

F = '/home/hy/captures/p160_detect_60s.fc32'
CHUNK = 100_000_000          # 5 s at 20 MHz
FS = 20e6

# strong-event threshold on boxcar; calibrated from first chunk
peaks_pos = []
peaks_val = []
offset = 0
tail = np.zeros(0, dtype=np.complex64)
thr = None
while True:
    raw = np.fromfile(F, dtype=np.complex64, count=CHUNK, offset=offset * 8)
    if len(raw) == 0:
        break
    x = np.concatenate([tail, raw])
    mult = np.abs(x[16:] * np.conj(x[:-16]))
    box = np.convolve(mult, np.ones(16), mode='valid')
    if thr is None:
        p90 = np.percentile(box, 90)
        thr = max(p90 * 200, 1e-3)   # real L-STF >> noise by orders of magnitude
        print(f'[gt] boxcar noise p90={p90:.5f} -> strong threshold={thr:.3f}')
    idx = np.where(box > thr)[0]
    if len(idx):
        grp = np.split(idx, np.where(np.diff(idx) > 2000)[0] + 1)
        for g in grp:
            if len(g) < 10:
                continue
            pos = offset - 16 + int(g[0])
            peaks_pos.append(pos)
            peaks_val.append(float(box[g].max()))
    tail = x[-32:]
    offset += len(raw)
    print(f'[gt] processed {offset/1e6:.0f}M samples, frames so far={len(peaks_pos)}', flush=True)

pos = np.array(peaks_pos)
val = np.array(peaks_val)
# dedupe (chunk-boundary repeats)
keep = np.concatenate([[True], np.diff(pos) > 2000])
pos, val = pos[keep], val[keep]
np.save('/tmp/p160_gt_pos.npy', pos)
np.save('/tmp/p160_gt_val.npy', val)
d = np.diff(pos) / FS * 1000
print(f'[gt] TOTAL frames={len(pos)}  span={pos[-1]/FS:.1f}s')
print(f'[gt] inter-frame ms: p5={np.percentile(d,5):.1f} p50={np.percentile(d,50):.1f} p95={np.percentile(d,95):.1f}')
print(f'[gt] peak boxcar: p5={np.percentile(val,5):.1f} p50={np.percentile(val,50):.1f} p95={np.percentile(val,95):.1f}')
