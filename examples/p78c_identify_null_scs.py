#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 78c-1: Identify 5 stable null SCs from USRP per-frame data."""
import json
import sys
import numpy as np

USRP_PATH = '/tmp/p78b_per_frame.json'
OUT_PATH = '/tmp/p78c_null_scs.json'

# Load USRP per-frame data
with open(USRP_PATH) as f:
    frames = json.load(f)

# Collect per-SC im values across all USRP frames
# Use eq_htsig0 (HT-SIG0 equalized values) data array indices 0..47 (drop 4 pilots)
all_re = []
all_im = []
for fr in frames:
    if 'eq_htsig0' not in fr:
        continue
    data = fr['eq_htsig0'][:48]
    re_vals = np.array([c['re'] if c['re'] is not None else np.nan for c in data])
    im_vals = np.array([c['im'] if c['im'] is not None else np.nan for c in data])
    mask = ~(np.isnan(re_vals) | np.isnan(im_vals))
    if mask.sum() < 40:
        continue
    all_re.append(re_vals)
    all_im.append(im_vals)

print(f"Loaded {len(all_re)} USRP frames")
all_re_arr = np.array(all_re)
all_im_arr = np.array(all_im)

# Per-SC statistics across frames
per_sc_std_im = all_im_arr.std(axis=0)
per_sc_mean_abs_re = np.abs(all_re_arr).mean(axis=0)
per_sc_mean_im = all_im_arr.mean(axis=0)

print("\n=== Per-SC std_im statistics (USRP) across %d frames ===" % len(all_re))
print(f"  min:    {per_sc_std_im.min():.3f}  (SC {np.argmin(per_sc_std_im)})")
print(f"  median: {np.median(per_sc_std_im):.3f}")
print(f"  max:    {per_sc_std_im.max():.3f}  (SC {np.argmax(per_sc_std_im)})")

# Top 10 SCs by std_im (largest noise variance — most "null-like")
top10 = np.argsort(per_sc_std_im)[-10:][::-1]
print("\n=== Top 10 SCs by std_im (data array indices) ===")
print(f"  {'SC':<5} {'std_im':<10} {'mean(|re|)':<12} {'mean(im)':<10}")
for sc in top10:
    print(f"  {sc:<5} {per_sc_std_im[sc]:<10.3f} {per_sc_mean_abs_re[sc]:<12.3f} {per_sc_mean_im[sc]:<10.3f}")

# Identify top 5 null SCs (data array indices, 0..47)
top5_null_scs = top10[:5].tolist()
print(f"\nTop 5 null SCs (data array indices): {top5_null_scs}")

# Map to actual SC indices
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from test_htsig_viterbi_synthetic import K_SC_INDEX_52
actual_sc_indices = K_SC_INDEX_52[top5_null_scs]
print(f"Top 5 null SCs actual indices: {actual_sc_indices.tolist()}")

# Save
out = {
    'n_frames': len(all_re),
    'top5_null_scs_data_order': top5_null_scs,
    'top5_null_scs_actual_index': actual_sc_indices.tolist(),
    'top10_data_order': top10.tolist(),
    'top10_actual_index': K_SC_INDEX_52[top10].tolist(),
    'per_sc_std_im': per_sc_std_im.tolist(),
    'per_sc_mean_abs_re': per_sc_mean_abs_re.tolist(),
    'per_sc_mean_im': per_sc_mean_im.tolist(),
    'std_im_max': float(per_sc_std_im.max()),
    'std_im_median': float(np.median(per_sc_std_im)),
    'std_im_min': float(per_sc_std_im.min()),
}
with open(OUT_PATH, 'w') as f:
    json.dump(out, f, indent=2)
print(f"\nSaved to {OUT_PATH}")
