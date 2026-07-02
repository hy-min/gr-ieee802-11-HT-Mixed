#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 78b Task 4: Compare USRP per-frame metrics to synthetic reference.

The 78b-3 std_im comparison was inconclusive (2.298 vs 2.217). This script
performs REFINED analysis to identify the structural difference:

  - Per-SC distribution of |re| and std(im)
  - Null SC detection and isolation
  - "No-null" std_im (excluding null SCs)
  - H52 magnitude distribution
  - Per-frame null count

If synthetic no-null std_im ~ 0.7 and USRP no-null std_im is much higher,
the wall is in H estimation. If similar, the wall is in the channel model
(synthetic is too harsh, or USRP has unmodeled impairment).
"""
import json
import numpy as np

USRP_PATH = '/tmp/p78b_per_frame.json'
SYNTH_PATH = '/tmp/p78b_synthetic_metrics.json'
OUT_PATH = '/tmp/p78b_comparison.json'

NULL_THRESHOLD = 0.15  # SCs with |H| < 0.15 are nulls

# 52-SC HT-SIG array layout (matches `insert_ht_pilots`):
#   data48 at array indices 0..47
#   pilots at array indices 48..51 (SC -21, -7, +7, +21)
DATA48_IDX = list(range(0, 48))


def extract_usrp_per_sc(usrp_data):
    """Extract per-SC statistics from USRP eq dump.

    For each frame, returns 48-element arrays of re and im values per data SC.
    """
    all_re = []
    all_im = []
    frame_ids = []
    for f in usrp_data:
        if 'eq_htsig0' in f:
            data = f['eq_htsig0'][:52]
            data48 = [data[i] for i in DATA48_IDX]
            re_vals = np.array([c['re'] for c in data48])
            im_vals = np.array([c['im'] for c in data48])
            mask = ~(np.isnan(re_vals) | np.isnan(im_vals))
            if mask.sum() >= 40:
                all_re.append(re_vals[mask])
                all_im.append(im_vals[mask])
                frame_ids.append(f['frame_id'])
    return all_re, all_im, frame_ids


def main():
    usrp = json.load(open(USRP_PATH))
    synth = json.load(open(SYNTH_PATH))

    print("=== USRP per-frame data ===")
    usrp_re, usrp_im, usrp_frame_ids = extract_usrp_per_sc(usrp)
    print(f"  Frames with eq dump: {len(usrp_re)}")

    if not usrp_re:
        print("ERROR: No USRP eq data found. Check /tmp/p78b_per_frame.json")
        return

    usrp_re_arr = np.array(usrp_re)
    usrp_im_arr = np.array(usrp_im)

    print(f"  Shape: {usrp_re_arr.shape}")

    usrp_per_sc_re_mean = usrp_re_arr.mean(axis=0)
    usrp_per_sc_re_std = usrp_re_arr.std(axis=0)
    usrp_per_sc_im_mean = usrp_im_arr.mean(axis=0)
    usrp_per_sc_im_std = usrp_re_arr.std(axis=0) * 0 + usrp_im_arr.std(axis=0)
    # Recompute per-SC std_im properly:
    usrp_per_sc_im_std = usrp_im_arr.std(axis=0)
    usrp_per_sc_abs_re = np.abs(usrp_re_arr).mean(axis=0)

    print(f"  Per-SC mean(|re|) range: "
          f"[{usrp_per_sc_abs_re.min():.3f}, {usrp_per_sc_abs_re.max():.3f}]")
    print(f"  Per-SC std(im) range: "
          f"[{usrp_per_sc_im_std.min():.3f}, {usrp_per_sc_im_std.max():.3f}]")
    print(f"  Per-SC std(im) median: {np.median(usrp_per_sc_im_std):.3f}")

    # Identify null SCs (those with very high std_im)
    null_threshold_im = 3.0
    null_mask = usrp_per_sc_im_std > null_threshold_im
    n_nulls_usrp = int(null_mask.sum())
    print(f"  USRP null SCs (std_im > {null_threshold_im}): {n_nulls_usrp}/48")

    if n_nulls_usrp < 48:
        no_null_std_im_usrp = float(usrp_im_arr[:, ~null_mask].std())
        print(f"  USRP no-null std(im): {no_null_std_im_usrp:.3f}")
    else:
        no_null_std_im_usrp = None
        print(f"  USRP all SCs flagged as null")

    # === Synthetic re-computation with per-SC recording ===
    print()
    print("=== Re-computing synthetic per-SC ===")
    import sys
    sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
    from test_htsig_viterbi_synthetic import (
        make_known_htsig_bits, _bcc_encode_48,
        htsig_interleave, bpsk_qbpsk_modulate, insert_ht_pilots,
        apply_usrp_like_channel,
    )

    case = {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}
    bits48_tx = make_known_htsig_bits(**case)
    coded96 = _bcc_encode_48(bits48_tx)
    coded0 = coded96[0:48]
    coded1 = coded96[48:96]
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)

    n_frames = 100
    synth_re_list = []
    synth_im_list = []
    synth_H_mag_list = []
    synth_n_nulls_per_frame = []
    for seed in range(n_frames):
        sc52_0_rx, sc52_1_rx, H, delta, n_nulls = apply_usrp_like_channel(
            sc52_0, sc52_1, frame_seed=seed)
        eq52_0 = sc52_0_rx / H
        # data48 at array indices 0..47 (matches insert_ht_pilots layout)
        eq48_0 = eq52_0[0:48]
        re_vals = np.array([c.real for c in eq48_0])
        im_vals = np.array([c.imag for c in eq48_0])
        synth_re_list.append(re_vals)
        synth_im_list.append(im_vals)
        # H is over 52 SCs; data48 maps to H[0:48]
        synth_H_mag_list.append(np.abs(H[0:48]))
        synth_n_nulls_per_frame.append(int(n_nulls))

    synth_re_arr = np.array(synth_re_list)
    synth_im_arr = np.array(synth_im_list)
    synth_H_mag_arr = np.array(synth_H_mag_list)
    synth_n_nulls_arr = np.array(synth_n_nulls_per_frame)

    print(f"  Shape: {synth_re_arr.shape}")

    synth_per_sc_re_mean = synth_re_arr.mean(axis=0)
    synth_per_sc_im_mean = synth_im_arr.mean(axis=0)
    synth_per_sc_im_std = synth_im_arr.std(axis=0)
    synth_per_sc_abs_re = np.abs(synth_re_arr).mean(axis=0)

    print(f"  Per-SC mean(|re|) range: "
          f"[{synth_per_sc_abs_re.min():.3f}, {synth_per_sc_abs_re.max():.3f}]")
    print(f"  Per-SC std(im) range: "
          f"[{synth_per_sc_im_std.min():.3f}, {synth_per_sc_im_std.max():.3f}]")
    print(f"  Per-SC std(im) median: {np.median(synth_per_sc_im_std):.3f}")

    print()
    print("=== H52 magnitude distribution ===")
    print(f"  Synthetic H52 mean(|H|): {synth_H_mag_arr.mean():.3f}")
    print(f"  Synthetic H52 min(|H|): {synth_H_mag_arr.min():.3f}")
    print(f"  Synthetic H52 max(|H|): {synth_H_mag_arr.max():.3f}")
    print(f"  Synthetic nulls per frame: "
          f"mean={synth_n_nulls_arr.mean():.1f}, "
          f"std={synth_n_nulls_arr.std():.1f}, "
          f"range=[{synth_n_nulls_arr.min()}, {synth_n_nulls_arr.max()}]")

    synth_null_mask = synth_H_mag_arr.mean(axis=0) < NULL_THRESHOLD
    n_nulls_synth = int(synth_null_mask.sum())
    print(f"  Synthetic null SCs (mean |H| < {NULL_THRESHOLD}): {n_nulls_synth}/48")

    if n_nulls_synth < 48:
        no_null_std_im_synth = float(synth_im_arr[:, ~synth_null_mask].std())
        print(f"  Synthetic no-null std(im): {no_null_std_im_synth:.3f}")
    else:
        no_null_std_im_synth = None
        print(f"  Synthetic all SCs flagged as null")

    # === Comparison ===
    print()
    print("=" * 60)
    print("COMPARISON: USRP vs Synthetic")
    print("=" * 60)
    print(f"  {'Metric':<40} {'USRP':<15} {'Synthetic':<15}")
    print(f"  {'-'*40} {'-'*15} {'-'*15}")
    print(f"  {'std(im) all SCs':<40} {usrp_im_arr.std():<15.3f} {synth_im_arr.std():<15.3f}")
    if no_null_std_im_usrp is not None and no_null_std_im_synth is not None:
        print(f"  {'std(im) no-null SCs':<40} "
              f"{no_null_std_im_usrp:<15.3f} {no_null_std_im_synth:<15.3f}")
    print(f"  {'mean(|re|) all SCs':<40} "
          f"{np.abs(usrp_re_arr).mean():<15.3f} {np.abs(synth_re_arr).mean():<15.3f}")
    print(f"  {'per-SC std(im) median':<40} "
          f"{np.median(usrp_per_sc_im_std):<15.3f} {np.median(synth_per_sc_im_std):<15.3f}")
    print(f"  {'per-SC std(im) max':<40} "
          f"{usrp_per_sc_im_std.max():<15.3f} {synth_per_sc_im_std.max():<15.3f}")
    print(f"  {'null SCs per frame':<40} "
          f"{'N/A (no H)':<15} {synth_n_nulls_arr.mean():<15.1f}")
    print()

    if no_null_std_im_usrp is not None and no_null_std_im_synth is not None:
        if no_null_std_im_usrp > 1.5 * no_null_std_im_synth:
            print("INTERPRETATION: USRP no-null std(im) is much higher than synthetic.")
            print("  -> The wall is in H estimation (USRP H is wrong even at non-null SCs).")
        elif no_null_std_im_usrp < 1.2 * no_null_std_im_synth:
            print("INTERPRETATION: USRP no-null std(im) is similar to synthetic.")
            print("  -> The wall is NOT in H estimation alone.")
            print("  -> Likely unmodeled USRP impairment "
                  "(DC, IQ imbalance, 64-PSK residual pattern).")
        else:
            print("INTERPRETATION: USRP no-null std(im) is moderately higher than synthetic.")
            print("  -> Some H estimation error + some unmodeled impairment.")

    # Per-SC correlation: USRP vs Synthetic std_im pattern
    corr = None
    if len(usrp_per_sc_im_std) == len(synth_per_sc_im_std):
        corr = float(np.corrcoef(usrp_per_sc_im_std, synth_per_sc_im_std)[0, 1])
        print(f"  Per-SC std(im) correlation (USRP vs Synthetic): {corr:.3f}")

    out = {
        'usrp_summary': {
            'n_frames': len(usrp_re),
            'std_im_all': float(usrp_im_arr.std()),
            'std_im_no_null': float(no_null_std_im_usrp) if no_null_std_im_usrp is not None else None,
            'mean_abs_re': float(np.abs(usrp_re_arr).mean()),
            'per_sc_std_im_median': float(np.median(usrp_per_sc_im_std)),
            'per_sc_std_im_max': float(usrp_per_sc_im_std.max()),
            'per_sc_std_im_min': float(usrp_per_sc_im_std.min()),
            'n_null_sc_im': n_nulls_usrp,
        },
        'synth_summary': {
            'n_frames': n_frames,
            'std_im_all': float(synth_im_arr.std()),
            'std_im_no_null': float(no_null_std_im_synth) if no_null_std_im_synth is not None else None,
            'mean_abs_re': float(np.abs(synth_re_arr).mean()),
            'per_sc_std_im_median': float(np.median(synth_per_sc_im_std)),
            'per_sc_std_im_max': float(synth_per_sc_im_std.max()),
            'per_sc_std_im_min': float(synth_per_sc_im_std.min()),
            'h52_mag_mean': float(synth_H_mag_arr.mean()),
            'h52_mag_min': float(synth_H_mag_arr.min()),
            'h52_mag_max': float(synth_H_mag_arr.max()),
            'nulls_per_frame_mean': float(synth_n_nulls_arr.mean()),
            'nulls_per_frame_std': float(synth_n_nulls_arr.std()),
            'nulls_per_frame_range': [int(synth_n_nulls_arr.min()),
                                      int(synth_n_nulls_arr.max())],
            'n_null_sc_h52': n_nulls_synth,
        },
        'per_sc_std_im_corr': corr,
    }
    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()