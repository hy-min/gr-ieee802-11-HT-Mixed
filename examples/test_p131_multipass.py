#!/usr/bin/env python3
"""Phase 131 T1: Multi-pass H52+delta refinement simulation.

Concept:
  Pass 1: standard HT-SIG viterbi (with soft LLR Phase 129 v2)
  Pass 2: re-estimate delta from best candidate's expected constellation vs rx
  Pass 3: re-decode HT-SIG with refined delta
  Iterate 2-3 times.

Goal: bridge remaining gap from Phase 129 v2 to CRC pass.

Note: This is a Python simulation. C++ implementation would be ~100 lines
following the existing decode_htsig_from_rotated pattern.
"""
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_htsig_viterbi_synthetic import (
    make_known_htsig_bits, _bcc_encode_48, htsig_interleave,
    bpsk_qbpsk_modulate, insert_ht_pilots, _ht_crc8_compute,
    apply_usrp_like_channel, htsig_deinterleave,
    viterbi_decode_133_171,
)
from test_p129_soft_llr_viterbi import (
    viterbi_decode_soft_133_171, compute_llr_bpsk_imag,
    estimate_sigma2_from_nulls, decode_htsig_attempt_soft,
    apply_usrp_like_channel_v2,
)


def estimate_delta_from_decoded_bits(eq52_a, eq52_b, dec48_a, dec48_b, H52):
    """Re-estimate delta (timing offset) using decoded bits as reference.

    For HT-SIG0 and HT-SIG1, after bit decoding we know the expected constellation.
    Phase difference between eq and expected reveals residual delta.
    """
    # Reconstruct expected symbols from decoded bits (QBPSK on imag axis)
    # But decoded bits are de-interleaved; we'd need to re-interleave first
    # Simpler: use the (rx52_a / H52) vs expected phase per SC
    # Actually for this simulation, delta doesn't really help much.
    # Use a simpler proxy: phase slope across SCs after equalization
    if np.allclose(H52, 0):
        return 0.0
    eq = eq52_a / H52
    # Compute arg(eq) per SC
    phase_per_sc = np.angle(eq)
    sc_indices = np.array([-26, -25, -24, -23, -22, -20, -19, -18, -17, -16,
                            -15, -14, -13, -12, -11, -10, -9, -8, -6, -5, -4, -3,
                            -2, -1, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15,
                            16, 17, 18, 19, 20, 22, 23, 24, 25, 26])
    # Linear regression: phase = a + b*sc, delta = -b*64/(2*pi)
    if len(phase_per_sc) < 2:
        return 0.0
    A = np.vstack([sc_indices[:48], np.ones(48)]).T
    slope, _ = np.linalg.lstsq(A, phase_per_sc[:48], rcond=None)[0]
    delta = -slope * 64.0 / (2.0 * np.pi)
    return float(delta)


def multipass_decode(eq48_a, eq48_b, eq52_a, eq52_b, H52_a, H52_b,
                     n_iterations=3, sigma2=None):
    """Multi-pass decoder.

    Pass 1: standard soft viterbi (Phase 129 v2)
    Pass 2: estimate delta from best candidate, pre-rotate eq, re-decode
    Iterate.

    Args:
        eq48_a, eq48_b: post-equalization 48-SC arrays
        eq52_a, eq52_b: full 52-SC arrays (for delta estimation)
        H52_a, H52_b: channel estimates
        n_iterations: number of passes
        sigma2: noise variance estimate

    Returns:
        (decoded48, info_dict)
    """
    best_dec = None
    best_metric = -1e18
    best_info = None

    cur_eq48_a = eq48_a.copy()
    cur_eq48_b = eq48_b.copy()

    for iteration in range(n_iterations):
        # Pass: try all 16 (rot, inv_a, inv_b) candidates
        for rot_idx in range(4):
            rot_phases = [1j, 1.0+0j, -1j, -1.0+0j]
            rot = rot_phases[rot_idx]
            for inv_a in (False, True):
                for inv_b in (False, True):
                    r_a = cur_eq48_a * rot
                    r_b = cur_eq48_b * rot
                    h_a_sq = np.abs(H52_a[:48]) ** 2
                    h_b_sq = np.abs(H52_b[:48]) ** 2
                    if sigma2 is None or sigma2 <= 0:
                        continue
                    llr_a = compute_llr_bpsk_imag(r_a.imag, h_a_sq, sigma2)
                    llr_b = compute_llr_bpsk_imag(r_b.imag, h_b_sq, sigma2)
                    if inv_a:
                        llr_a = -llr_a
                    if inv_b:
                        llr_b = -llr_b
                    # Deinterleave
                    llr_a_deint = np.zeros(48)
                    llr_b_deint = np.zeros(48)
                    for k in range(48):
                        j_idx = 3 * (k % 16) + k // 16
                        llr_a_deint[k] = llr_a[j_idx]
                        llr_b_deint[k] = llr_b[j_idx]
                    llr96 = np.concatenate([llr_a_deint, llr_b_deint])
                    dec48, metric = viterbi_decode_soft_133_171(llr96)
                    if dec48 is None or len(dec48) != 48:
                        continue
                    # Validation
                    tail_ok = np.all(dec48[42:48] == 0)
                    crc_calc = _ht_crc8_compute(dec48[0:34])
                    crc_match = np.array_equal(crc_calc, dec48[34:42])
                    field_ok = (dec48[7] == 0 and np.all(dec48[24:27] == 0))
                    crc_ok = tail_ok and crc_match and field_ok
                    if metric > best_metric:
                        best_metric = metric
                        best_dec = dec48
                        best_info = {
                            "crc_ok": crc_ok,
                            "metric": metric,
                            "iteration": iteration,
                            "rot_idx": rot_idx,
                        }
        # After pass 1, estimate delta from best candidate and pre-rotate
        if best_dec is None:
            break
        # Delta estimation from current eq
        delta_a = estimate_delta_from_decoded_bits(
            eq52_a, eq52_b, best_dec[:48], best_dec[48:96] if len(best_dec) >= 96 else best_dec,
            H52_a)
        delta_b = estimate_delta_from_decoded_bits(
            eq52_b, eq52_a, best_dec[48:96] if len(best_dec) >= 96 else best_dec, best_dec[:48],
            H52_b)
        # Pre-rotate eq by exp(-j*2*pi*sc*delta/64) for next pass
        sc_indices = np.array([-26, -25, -24, -23, -22, -20, -19, -18, -17, -16,
                                -15, -14, -13, -12, -11, -10, -9, -8, -6, -5, -4, -3,
                                -2, -1, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15,
                                16, 17, 18, 19, 20, 22, 23, 24, 25, 26])
        rot_a = np.exp(-1j * 2 * np.pi * sc_indices * delta_a / 64.0)
        rot_b = np.exp(-1j * 2 * np.pi * sc_indices * delta_b / 64.0)
        cur_eq48_a = eq48_a * rot_a
        cur_eq48_b = eq48_b * rot_b

    return best_dec, best_info


def test_multipass_sweep():
    """Sweep sigma and compare single-pass vs multi-pass."""
    case_kwargs = {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}
    n_frames = 50
    sigmas = [1.0, 1.5, 1.77, 2.0]

    print(f"\n[INFO] Phase 131 multi-pass sweep, n_frames={n_frames} per sigma")
    for sigma in sigmas:
        single_pass = 0
        multi_pass = 0
        for seed in range(n_frames):
            # Build frame
            bits48_tx = make_known_htsig_bits(**case_kwargs)
            coded96 = _bcc_encode_48(bits48_tx)
            intl0 = htsig_interleave(coded96[0:48])
            intl1 = htsig_interleave(coded96[48:96])
            syms0 = bpsk_qbpsk_modulate(intl0)
            syms1 = bpsk_qbpsk_modulate(intl1)
            sc52_0 = insert_ht_pilots(syms0, 0)
            sc52_1 = insert_ht_pilots(syms1, 1)
            # USRP-like channel
            sc52_0_rx, sc52_1_rx, H, delta, n_nulls, null_idx = \
                apply_usrp_like_channel_v2(
                    sc52_0, sc52_1, frame_seed=seed, sigma_per_sc_rad=sigma)
            # Equalize
            eq52_a = sc52_0_rx / H
            eq52_b = sc52_1_rx / H
            eq48_a = eq52_a[:48]
            eq48_b = eq52_b[:48]
            # σ² estimation from nulls
            null_in_data = [i for i in null_idx if i < 48]
            sigma2 = estimate_sigma2_from_nulls(
                eq52_a, eq52_b, H, H, null_in_data, null_in_data)
            sigma2 = max(sigma2, 0.01)

            # Single pass (no iteration)
            dec, info = multipass_decode(
                eq48_a, eq48_b, eq52_a, eq52_b, H, H,
                n_iterations=1, sigma2=sigma2)
            if dec is not None and np.array_equal(dec[:48], bits48_tx):
                single_pass += 1
            # Multi pass (3 iterations)
            dec, info = multipass_decode(
                eq48_a, eq48_b, eq52_a, eq52_b, H, H,
                n_iterations=3, sigma2=sigma2)
            if dec is not None and np.array_equal(dec[:48], bits48_tx):
                multi_pass += 1
        gain = multi_pass - single_pass
        print(f"  sigma={sigma:.2f} rad: single={single_pass}/{n_frames}, "
              f"multi={multi_pass}/{n_frames}, gain=+{gain}")


if __name__ == "__main__":
    test_multipass_sweep()
    print("\nPhase 131 T1 done.")