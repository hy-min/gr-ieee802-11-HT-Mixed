#!/home/hy/conda/envs/gnuradio/bin/python
"""Generate synthetic USRP-like reference metrics for Phase 78b comparison.

Uses the same Layer 4 channel model as test_htsig_viterbi_synthetic.py
but computes per-frame metrics matching what we extracted from USRP:
  - eq_htsig0 mean(|re|) and std(im)
  - H52 magnitude distribution
  - Per-symbol phase drift HTSIG0 vs HTSIG1

Saves to /tmp/p78b_synthetic_metrics.json
"""
import sys
import json
import numpy as np

# Import from the existing test
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from test_htsig_viterbi_synthetic import (
    make_known_htsig_bits, _bcc_encode_48,
    htsig_interleave, bpsk_qbpsk_modulate, insert_ht_pilots,
    apply_usrp_like_channel,
    K_SC_INDEX_52,
)


def compute_frame_metrics(eq52_0, eq52_1, H, delta, n_nulls):
    """Compute per-frame metrics from equalized HT-SIG0/1 (no rotation, ideal H)."""
    # Drop pilots, keep 48 data SCs
    eq48_0 = eq52_0[0:48]
    eq48_1 = eq52_1[0:48]
    pilots_0 = eq52_0[48:52]
    pilots_1 = eq52_1[48:52]

    # QBPSK target: data on IMAG axis, pilots on IMAG axis with known polarity
    re_0 = np.array([c.real for c in eq48_0])
    im_0 = np.array([c.imag for c in eq48_0])
    re_1 = np.array([c.real for c in eq48_1])
    im_1 = np.array([c.imag for c in eq48_1])

    return {
        'eq_htsig0_mean_abs_re': float(np.mean(np.abs(re_0))),
        'eq_htsig0_mean_im': float(np.mean(im_0)),
        'eq_htsig0_std_im': float(np.std(im_0)),
        'eq_htsig0_mean_abs_im': float(np.mean(np.abs(im_0))),
        'eq_htsig1_mean_abs_re': float(np.mean(np.abs(re_1))),
        'eq_htsig1_mean_im': float(np.mean(im_1)),
        'eq_htsig1_std_im': float(np.std(im_1)),
        'eq_htsig1_mean_abs_im': float(np.mean(np.abs(im_1))),
        # Pilot phases (mean arg of 4 pilots, after equalization)
        'pilots_htsig0_mean_arg': float(np.angle(np.mean(pilots_0))),
        'pilots_htsig1_mean_arg': float(np.angle(np.mean(pilots_1))),
        # Per-symbol phase drift: difference between HTSIG1 and HTSIG0 pilot mean
        'htsig1_minus_htsig0_phase': float(
            np.angle(np.mean(pilots_1)) - np.angle(np.mean(pilots_0))
        ),
        # H52 magnitude distribution
        'h52_mag_mean': float(np.mean(np.abs(H))),
        'h52_mag_min': float(np.min(np.abs(H))),
        'h52_mag_max': float(np.max(np.abs(H))),
        'h52_n_nulls': int(n_nulls),
        'delta': float(delta),
    }


def main():
    case = {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}
    n_frames = 100

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

    per_frame = []
    for seed in range(n_frames):
        sc52_0_rx, sc52_1_rx, H, delta, n_nulls = apply_usrp_like_channel(
            sc52_0, sc52_1, frame_seed=seed)
        # IDEAL equalization
        eq52_0 = sc52_0_rx / H
        eq52_1 = sc52_1_rx / H
        m = compute_frame_metrics(eq52_0, eq52_1, H, delta, n_nulls)
        per_frame.append(m)

    # Aggregate
    summary = {
        'case': case,
        'n_frames': n_frames,
        'mean_abs_re_0_mean': float(np.mean([f['eq_htsig0_mean_abs_re'] for f in per_frame])),
        'mean_abs_re_0_std': float(np.std([f['eq_htsig0_mean_abs_re'] for f in per_frame])),
        'std_im_0_mean': float(np.mean([f['eq_htsig0_std_im'] for f in per_frame])),
        'std_im_0_std': float(np.std([f['eq_htsig0_std_im'] for f in per_frame])),
        'htsig1_minus_htsig0_phase_mean': float(np.mean(
            [f['htsig1_minus_htsig0_phase'] for f in per_frame]
        )),
        'htsig1_minus_htsig0_phase_std': float(np.std(
            [f['htsig1_minus_htsig0_phase'] for f in per_frame]
        )),
        'h52_mag_mean': float(np.mean([f['h52_mag_mean'] for f in per_frame])),
        'h52_mag_min_overall': float(np.min([f['h52_mag_min'] for f in per_frame])),
        'h52_mag_max_overall': float(np.max([f['h52_mag_max'] for f in per_frame])),
        'delta_mean': float(np.mean([f['delta'] for f in per_frame])),
        'delta_std': float(np.std([f['delta'] for f in per_frame])),
    }

    print("=== Synthetic reference summary (100 frames, IDEAL H) ===")
    print(f"  eq_htsig0 mean(|re|) = {summary['mean_abs_re_0_mean']:.3f} (target <0.3)")
    print(f"  eq_htsig0 std(im)   = {summary['std_im_0_mean']:.3f} (target <0.3)")
    print(f"  HTSIG0-HTSIG1 phase std = {summary['htsig1_minus_htsig0_phase_std']:.3f} rad")
    print(f"  H52 mag range = [{summary['h52_mag_min_overall']:.3f}, {summary['h52_mag_max_overall']:.3f}]")
    print(f"  delta mean = {summary['delta_mean']:.3f}, std = {summary['delta_std']:.3f}")
    print()
    print("=== USRP observation (8 frames, ESTIMATED H from L-LTF) ===")
    print("  eq_htsig0 mean(|re|) = 1.471 (target <0.3)")
    print("  eq_htsig0 std(im)   = 2.217 (target <0.3)")
    print("  HTSIG0-HTSIG1 phase std = 1.701 rad")
    print()
    if summary['std_im_0_mean'] < 0.5:
        print("INTERPRETATION: Synthetic with IDEAL H is clean (std_im < 0.5).")
        print("  USRP std_im=2.217 must come from H estimation error or unmodeled impairment.")
    else:
        print("INTERPRETATION: Synthetic itself has high std_im even with IDEAL H.")
        print("  Channel model may be too harsh; consider reducing null count or AWGN.")

    out_path = '/tmp/p78b_synthetic_metrics.json'
    with open(out_path, 'w') as f:
        json.dump({'summary': summary, 'per_frame': per_frame}, f, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()