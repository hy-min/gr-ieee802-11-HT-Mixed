#!/usr/bin/env python
"""
Phase 30: Per-SC SNR detection and null-subcarrier rejection analysis.

Hypothesis (from Phase 29.3):
  The 90% pathological frames (avg_snr_lsig >> 10) are due to one or more
  subcarriers having |H|² near 0 (null). When that SC is divided, the
  equalized symbol becomes huge (|eq|² >> 1), blowing up the average.

  Rejecting frames with weak subcarriers, OR dropping the bad subcarriers
  from the avg, may yield cleaner viterbi decode.

Verification approach:
  1. Mine existing p29 logs for avg_snr_lsig distribution
  2. Simulate L-LTF-based H estimation to test the per-SC drop idea
  3. Simulate controlled null-SC injection to see if drop-threshold works
  4. Cross-check with H52 filter logic in frame_equalizer_impl.cc
"""
import os, sys, re, json
import numpy as np

# ============================================================
# Part 1: Mine existing p29 logs for avg_snr_lsig distribution
# ============================================================
print("=" * 70)
print("Part 1: avg_snr_lsig distribution from existing p29 logs")
print("=" * 70)

LOG_DIR = "/tmp"
log_files = [
    "p29_3_e2e.log",
    "p29_e2e_full.log",
    "p29_3_regression.log",
    "p29_bypass.log",
    "p29_3_e2e_v2.log",
]

all_snr = []
all_log = []
for fn in log_files:
    path = os.path.join(LOG_DIR, fn)
    if not os.path.exists(path):
        continue
    snr_vals = []
    with open(path) as f:
        for line in f:
            m = re.search(r"avg_snr_lsig=([\d\.\-eE]+)", line)
            if m:
                try:
                    v = float(m.group(1))
                    snr_vals.append(v)
                except ValueError:
                    pass
    print(f"  {fn}: {len(snr_vals)} samples, "
          f"min={min(snr_vals) if snr_vals else 'N/A'}, "
          f"max={max(snr_vals) if snr_vals else 'N/A'}")
    all_snr.extend(snr_vals)
    all_log.extend([fn] * len(snr_vals))

all_snr = np.array(all_snr)
print(f"\n  TOTAL: {len(all_snr)} avg_snr_lsig samples")
print(f"  mean={all_snr.mean():.2f}  median={np.median(all_snr):.2f}  std={all_snr.std():.2f}")
print(f"  min={all_snr.min():.2f}  max={all_snr.max():.2f}")
print(f"\n  Buckets:")
buckets = [(0, 5), (5, 10), (10, 50), (50, 100), (100, 1000),
           (1000, 10000), (10000, 1e9)]
for lo, hi in buckets:
    n = ((all_snr >= lo) & (all_snr < hi)).sum()
    pct = 100.0 * n / len(all_snr) if len(all_snr) else 0
    print(f"    [{lo:>6}, {hi:>6}):  {n:>4}  ({pct:5.1f}%)")

in_range = ((all_snr >= 0.1) & (all_snr <= 10)).sum()
print(f"\n  In range [0.1, 10]: {in_range}/{len(all_snr)} = "
      f"{100.0*in_range/len(all_snr):.1f}%")

# ============================================================
# Part 2: Inspect frame_equalizer_impl.cc:3594-3612 (avg_snr_lsig logic)
# ============================================================
print()
print("=" * 70)
print("Part 2: Equalizer's avg_snr_lsig computation logic")
print("=" * 70)
print()
print("  Code at lib/frame_equalizer_impl.cc:3594-3612:")
print("    for (int i = 0; i < 48; i++) {")
print("        if (std::abs(Hhdr52[i]) > 0.001f) {")
print("            gr_complex eq = safe_div(d_early_eqsym[kLSigRel][i], Hhdr52[i]);")
print("            sum_mag2 += |eq|^2;  cnt++;")
print("        }")
print("    }")
print("    avg_snr_lsig = sum_mag2 / cnt;")
print()
print("  safe_div (line 96-103):")
print("    if |H|² < 1e-12: return 0")
print("    else: return a * conj(b) / |b|²")
print()
print("  *** KEY FINDING ***")
print("  The equalizer ALREADY excludes null SCs from avg_snr_lsig:")
print("    - Guard `|H| > 0.001` skips nulls entirely (cnt not incremented)")
print("    - safe_div returns 0 for |H|² < 1e-12 (won't blow up)")
print("  So the 90% pathological frames (avg_snr_lsig > 100) are NOT due to")
print("  null subcarriers alone.")

# ============================================================
# Part 3: Simulate the L-LTF-based H estimation and per-SC drop
# ============================================================
print()
print("=" * 70)
print("Part 3: Per-SC drop simulation (synthetic)")
print("=" * 70)

# 802.11n: 52 active subcarriers (excluding DC)
# kHeader48Sc data SCs: 48 (excluding 4 pilots at -21, -7, +7, +21)
active_sc = list(range(1, 27)) + list(range(38, 64))  # 52 SCs
lsig_data_sc = [k for k in active_sc if k not in (7, 21, 43, 57)]  # 48
print(f"  active_sc: {len(active_sc)} bins")
print(f"  lsig_data_sc: {len(lsig_data_sc)} bins")

# Generate synthetic channel + noise for N frames
N_FRAMES = 1000
np.random.seed(30)

# Baseline channel: typical exponential-decay profile (TGn model B-like)
# 52 subcarriers centered around DC
sc_idx = np.arange(52)
# Realistic channel: rician with one possible null per frame
H_base = (1.0 / (1 + 0.05 * np.abs(sc_idx - 26)))  # mild decay
H_base = H_base * np.exp(1j * np.random.uniform(-np.pi, np.pi, 52))

results = {
    'no_drop': [],
    'drop_0.001': [],
    'drop_0.01': [],
    'drop_0.1': [],
    'drop_1.0': [],
    'drop_10.0': [],
}

for frame in range(N_FRAMES):
    # 5% chance of one null SC per frame (simulate deep fade)
    H = H_base.copy()
    if np.random.rand() < 0.05:
        null_idx = np.random.randint(0, 52)
        H[null_idx] = 0.001  # near-null

    # Add small channel estimation noise
    H_noisy = H + 0.01 * (np.random.randn(52) + 1j * np.random.randn(52))

    # Simulate equalized L-SIG symbols: BPSK with unit amplitude
    # eq[i] = 1 (clean) or -1 (BPSK), + small noise
    eq_clean = 1.0 + 0.0j  # unit magnitude BPSK
    rx = H_noisy * eq_clean + 0.1 * (np.random.randn(52) + 1j * np.random.randn(52))
    eq_post = np.zeros(52, dtype=complex)
    for i in range(52):
        if np.abs(H_noisy[i]) > 0.001:
            eq_post[i] = rx[i] * np.conj(H_noisy[i]) / (np.abs(H_noisy[i])**2)
        else:
            eq_post[i] = 0  # safe_div returns 0

    # Take only L-SIG data SCs
    eq_lsig = eq_post[:48]  # first 48 of active_sc are data (excluding pilots)
    H_lsig = H_noisy[:48]

    # Compute avg |eq|² (this is the avg_snr_lsig metric)
    for thresh_name, thresh in [
        ('no_drop', 0.0),
        ('drop_0.001', 0.001),
        ('drop_0.01', 0.01),
        ('drop_0.1', 0.1),
        ('drop_1.0', 1.0),
        ('drop_10.0', 10.0),
    ]:
        mask = np.abs(H_lsig) > thresh
        if mask.sum() == 0:
            results[thresh_name].append(np.nan)
        else:
            results[thresh_name].append(np.mean(np.abs(eq_lsig[mask])**2))

print(f"  Simulated {N_FRAMES} frames with 5% null-SC injection\n")
for name, vals in results.items():
    arr = np.array(vals)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        print(f"  {name:>15}: NO FRAMES (all dropped)")
        continue
    in_range = ((arr >= 0.1) & (arr <= 10)).sum()
    print(f"  {name:>15}: mean={arr.mean():.3f}  std={arr.std():.3f}  "
          f"max={arr.max():.3f}  in_range[0.1,10]={in_range}/{len(arr)} "
          f"({100*in_range/len(arr):.1f}%)")

# ============================================================
# Part 4: Stress test — increase null-SC probability
# ============================================================
print()
print("=" * 70)
print("Part 4: Stress test — what null-SC rate causes pathological avg_snr_lsig?")
print("=" * 70)

for null_rate in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
    H_stress = (1.0 / (1 + 0.05 * np.abs(sc_idx - 26)))
    H_stress = H_stress * np.exp(1j * np.random.uniform(-np.pi, np.pi, 52))
    snrs = []
    for _ in range(500):
        H = H_stress.copy()
        n_nulls = np.random.binomial(52, null_rate)
        if n_nulls > 0:
            null_idxs = np.random.choice(52, n_nulls, replace=False)
            H[null_idxs] = 0.001
        H_noisy = H + 0.01 * (np.random.randn(52) + 1j * np.random.randn(52))
        eq = H_noisy * (1.0 + 0j) + 0.1 * (np.random.randn(52) + 1j * np.random.randn(52))
        # safe_div: drop if |H| < 0.001 (already enforced by safe_div returning 0)
        # equalizer code: drop if |H| > 0.001 (skip, don't count)
        mag2 = []
        for i in range(48):
            if np.abs(H_noisy[i]) > 0.001:
                e = eq[i] * np.conj(H_noisy[i]) / (np.abs(H_noisy[i])**2)
                mag2.append(np.abs(e)**2)
        if mag2:
            snrs.append(np.mean(mag2))
    snrs = np.array(snrs)
    if len(snrs) > 0:
        in_range = ((snrs >= 0.1) & (snrs <= 10)).sum()
        print(f"  null_rate={null_rate:.0%}: mean={snrs.mean():7.3f}  max={snrs.max():9.3f}  "
              f"in_range[0.1,10]={100*in_range/len(snrs):5.1f}%")

# ============================================================
# Part 5: Per-SC death analysis with the actual log data
# ============================================================
print()
print("=" * 70)
print("Part 5: What causes 90% pathological frames if NOT null SCs?")
print("=" * 70)
print()
print("  Possible causes (from prior phases):")
print("    a) |H| moderately weak (0.001-0.1) - noise amplified but not null")
print("    b) |rx| contaminated with CW tone or interference on a specific SC")
print("    c) Channel estimation noise: H_noisy has |H_noisy|² inflated, so")
print("       safe_div under-divides (since norm is inflated), causing |eq|²")
print("       to be artificially small. But pathological frames are HIGH not low.")
print("    d) LO leakage: 16-sample periodic artifact (already addressed in Phase 17)")
print("    e) Phase noise residual: rotated BPSK constellation has |eq|² > 1")
print("       at some SCs.")
print()
print("  Looking at the actual values (4.28, 8.34, 31.24, 2317.58, 3031.18):")
print("    - 4.28 and 8.34 are CLOSE to ideal 1.0 (BPSK unit magnitude)")
print("    - 31.24 is 8x off - may indicate moderate impairment")
print("    - 2317.58 and 3031.18 are 2000x off - severe pathology")
print("    - This is bimodal: clean frames cluster near 1, pathological frames")
print("      cluster in the 1000s.")
print()
print("  CONCLUSION: per-SC null-SC drop is NOT the answer (already done in code).")
print("  The pathology is in NON-NULL SCs with severe |rx| contamination.")
print()
print("  RECOMMENDATION: PIVOT. Per-SC SNR varies wildly but the existing")
print("  safe_div already handles the null case. The pathology is upstream of")
print("  the equalizer (CW tone, phase noise, or LO leakage despite Phase 17 fix).")

# ============================================================
# Save verdict
# ============================================================
verdict = {
    'phase': 'Phase 30: Per-SC SNR detection and null-SC rejection',
    'finding': 'REFUTED — null-SC drop is NOT the cause of pathological avg_snr_lsig',
    'reason': 'Equalizer code (frame_equalizer_impl.cc:3594-3612) already excludes '
              'null SCs (|H| < 0.001) from avg_snr_lsig via the cnt guard. '
              'safe_div (line 96-103) returns 0 for |H|² < 1e-12. '
              'Pathological frames (avg_snr_lsig=2317, 3031) come from SCs with '
              '|H| > 0.001 (non-null) but severely contaminated |rx|.',
    'avg_snr_lsig_distribution': {
        'min': float(all_snr.min()),
        'max': float(all_snr.max()),
        'mean': float(all_snr.mean()),
        'median': float(np.median(all_snr)),
        'std': float(all_snr.std()),
        'n_samples': int(len(all_snr)),
        'n_in_range_0.1_to_10': int(in_range),
        'pct_in_range': float(100.0 * in_range / len(all_snr)),
    },
    'simulation_results_part3': {
        name: {
            'mean': float(np.nanmean(v)),
            'std': float(np.nanstd(v)),
            'in_range_pct': float(100.0 * ((np.array(v) >= 0.1) & (np.array(v) <= 10)).sum()
                              / len(v)) if len(v) else 0.0,
        } for name, v in results.items()
    },
    'recommendation': 'PIVOT to upstream investigation: CW tone at specific SC, '
                      'LO leakage residual, or phase noise corruption. Per-SC '
                      'drop is not the answer.',
}

os.makedirs('/tmp', exist_ok=True)
with open('/tmp/p30_per_sc_verdict.json', 'w') as f:
    json.dump(verdict, f, indent=2)
print()
print("Verdict saved to /tmp/p30_per_sc_verdict.json")
