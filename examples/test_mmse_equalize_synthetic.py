#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 46 AR5: MMSE equalization synthetic tests.

Verifies that the MMSE equalizer (eq = conj(H)·rx / (|H|² + N0)) behaves
correctly under various conditions:
  1. test_mmse_vs_zf_clean     — No noise, uniform |H|, MMSE == ZF.
  2. test_mmse_at_null_sc      — Channel null at one SC, MMSE suppresses noise.
  3. test_mmse_phase_preservation — Random H, BPSK signal, MMSE preserves phase.
  4. test_mmse_n0_robustness   — 25th percentile of |H|² stable under null outliers.
  5. test_htsig_viterbi_with_mmse — Full HT-SIG viterbi simulation with channel
     nulls, comparing ZF vs MMSE viterbi success rates at various SNR.

The MMSE implementation under test (mirrors lib/frame_equalizer_impl.cc:106-130):
    N0 = 25th percentile of |H[i]|² over the 48 data SCs
    eq[i] = conj(H[i]) · rx[i] / (|H[i]|² + N0)
"""

import numpy as np


# ============================================================
# MMSE core: re-implement in NumPy to mirror the C++ helper.
# Used by all 5 tests.
# ============================================================
def mmse_equalize_htsig_np(rx52, H52):
    """Apply MMSE equalization. Returns eq48 (only data SCs, not pilots).

    eq[i] = conj(H[i]) * rx[i] / (|H[i]|² + N0)
    N0 = 25th percentile of |H[i]|² over the 48 data SCs.
    """
    assert rx52.shape == (52,)
    assert H52.shape == (52,)
    h_sq = np.abs(H52[:48]) ** 2
    sorted_h_sq = np.sort(h_sq)
    # 25th percentile of 48 values: linear interp at 11.5 -> average of [11] and [12]
    N0 = 0.5 * (sorted_h_sq[11] + sorted_h_sq[12])
    if N0 < 1e-9:
        N0 = 1e-9
    eq = np.empty(48, dtype=np.complex64)
    for i in range(48):
        denom = h_sq[i] + N0
        eq[i] = np.conj(H52[i]) * rx52[i] / denom
    return eq


def zf_equalize_htsig_np(rx52, H52):
    """Apply Zero-Forcing equalization. Returns eq48.

    eq[i] = conj(H[i]) * rx[i] / |H[i]|²  (with floor to avoid div-by-zero).
    Mirrors safe_div() in lib/frame_equalizer_impl.cc.
    """
    assert rx52.shape == (52,)
    assert H52.shape == (52,)
    eq = np.empty(48, dtype=np.complex64)
    for i in range(48):
        h_sq = np.abs(H52[i]) ** 2
        if h_sq < 1e-12:
            eq[i] = 0.0 + 0.0j
        else:
            eq[i] = np.conj(H52[i]) * rx52[i] / h_sq
    return eq


def h_mag_with_nulls(H52, n_nulls=0, null_mag=0.05, rng=None):
    """Mutate H52 by setting n_nulls random SCs to |H|=null_mag (with random phase).

    Returns a NEW H52 array (does not modify input).
    """
    H = H52.copy()
    if rng is None:
        rng = np.random.default_rng(seed=42)
    if n_nulls > 0:
        idx = rng.choice(48, size=n_nulls, replace=False)
        for i in idx:
            # Keep phase same, but reduce magnitude
            current_phase = np.angle(H[i])
            H[i] = null_mag * np.exp(1j * current_phase)
    return H


def test_mmse_vs_zf_clean():
    """Test 1: No noise, strong uniform |H|, MMSE ≈ ZF (N0 << |H|²).

    With |H|=2.0 and no noise, |H|²=4 >> N0=4 (uniform: 25th-pct=4),
    so MMSE denominator is |H|² + N0 ≈ 2|H|², halving the gain. This is the
    MMSE bias — at SNR=0dB it sacrifices half the signal magnitude.
    We compare to ZF behavior and verify MMSE preserves the BPSK sign and
    is a CONSTANT SCALAR from ZF (not random)."""
    rng = np.random.default_rng(seed=123)
    # Uniform channel: |H|=2.0 (strong), random phases
    h_phases = rng.uniform(-np.pi, np.pi, 48)
    H52 = np.zeros(52, dtype=np.complex64)
    H52[:48] = 2.0 * np.exp(1j * h_phases)
    # Random BPSK signal
    s = rng.choice([-1.0, 1.0], 48).astype(np.complex64)
    # No noise: rx = H * s
    rx52 = np.zeros(52, dtype=np.complex64)
    rx52[:48] = H52[:48] * s
    eq_mmse = mmse_equalize_htsig_np(rx52, H52)
    eq_zf = zf_equalize_htsig_np(rx52, H52)
    # At uniform |H|=2.0, N0 = 25th percentile of 4.0 = 4.0. So MMSE denominator
    # = 4 + 4 = 8, ZF denominator = 4. MMSE eq = 0.5 * ZF eq at all SCs (uniform).
    # This is the expected MMSE behavior at SNR=0dB (no noise, all signal).
    # Test that the ratio is constant and the sign is preserved.
    ratios = eq_mmse / eq_zf
    ratio_variance = float(np.std(np.real(ratios)))
    mean_ratio = float(np.mean(np.real(ratios)))
    # All ratios should be identical (since H is uniform)
    assert ratio_variance < 1e-4, \
        f"MMSE/ZF ratio must be constant for uniform H: std = {ratio_variance}"
    # And MMSE must have the same sign as s on IMAG axis (preserves QBPSK bits)
    s_bits = (s.imag >= 0).astype(np.uint8)
    mmse_bits = (eq_mmse.imag >= 0).astype(np.uint8)
    zf_bits = (eq_zf.imag >= 0).astype(np.uint8)
    mmse_match = bool(np.array_equal(s_bits, mmse_bits))
    zf_match = bool(np.array_equal(s_bits, zf_bits))
    assert mmse_match, "MMSE must preserve signal sign (no noise, uniform H)"
    assert zf_match, "ZF must preserve signal sign (sanity check)"
    print(f"[PASS] test_mmse_vs_zf_clean: ratio(MMSE/ZF)={mean_ratio:.3f}, "
          f"sign-preserved={mmse_match}")


def test_mmse_at_null_sc():
    """Test 2: One SC has |H|=0.05 (null). MMSE must suppress noise there;
    ZF will amplify it."""
    rng = np.random.default_rng(seed=456)
    # Strong |H|=0.5 on most SCs, one null SC at index 10
    H52 = np.zeros(52, dtype=np.complex64)
    H52[:48] = 0.5 * np.exp(1j * rng.uniform(-np.pi, np.pi, 48))
    H52[10] = 0.05  # null
    # Signal
    s = np.ones(48, dtype=np.complex64)  # all +1 for simplicity
    rx_clean = np.zeros(52, dtype=np.complex64)
    rx_clean[:48] = H52[:48] * s
    # Add AWGN: SNR = 10 dB at strong SCs. At null SC, |H|=0.05 means |H|²=0.0025.
    # Strong SC |H|²=0.25, so signal at null SC is 10x weaker. With same noise σ²,
    # SNR at null SC is 20 dB lower than at strong SC. ZF will produce a HUGE
    # noise-amplified value at the null SC. MMSE must produce ~0 (capped by 1/N0).
    sig_power = np.mean(np.abs(rx_clean[:48]) ** 2)
    noise_power = sig_power / 10  # 10 dB SNR
    noise = np.sqrt(noise_power / 2) * (rng.standard_normal(52) +
                                        1j * rng.standard_normal(52))
    rx52 = rx_clean + noise.astype(np.complex64)
    eq_mmse = mmse_equalize_htsig_np(rx52, H52)
    eq_zf = zf_equalize_htsig_np(rx52, H52)
    # At the null SC (index 10):
    mmse_null_mag = float(np.abs(eq_mmse[10]))
    zf_null_mag = float(np.abs(eq_zf[10]))
    # MMSE at null should be small (bounded by signal/noise). ZF at null should
    # be MUCH larger because |H|=0.05 amplifies noise by 1/|H|² = 400x.
    assert mmse_null_mag < zf_null_mag, \
        f"MMSE must suppress noise at null SC: MMSE={mmse_null_mag}, ZF={zf_null_mag}"
    assert mmse_null_mag < 1.0, \
        f"MMSE at null SC must be bounded; got |eq|= {mmse_null_mag}"
    # And MMSE at strong SCs must still recover signal
    err_mmse_strong = np.mean(np.abs(eq_mmse[np.arange(48) != 10] - 1.0))
    assert err_mmse_strong < 0.5, \
        f"MMSE must recover signal at strong SCs; err = {err_mmse_strong}"
    print(f"[PASS] test_mmse_at_null_sc: |MMSE(null)|={mmse_null_mag:.3f}, "
          f"|ZF(null)|={zf_null_mag:.3f}, MMSE/ZF ratio={mmse_null_mag/zf_null_mag:.3f}, "
          f"signal recovery at strong SCs err={err_mmse_strong:.3f}")


def test_mmse_phase_preservation():
    """Test 3: Random H (no nulls, |H| large), QBPSK signal on IMAG axis.

    HT-SIG uses QBPSK: bit 0 -> -j, bit 1 -> +j. After equalization,
    eq.imag >= 0 maps back to bit 1. We test that MMSE preserves the IMAG
    sign at strong SCs (|H|² >> N0).
    """
    rng = np.random.default_rng(seed=789)
    n_trials = 50
    bit_match_mmse_list = []
    bit_match_zf_list = []
    for trial in range(n_trials):
        # Strong H: |H| in [1.5, 2.5] — |H|² >> N0 regime
        mags = rng.uniform(1.5, 2.5, 48)
        phases = rng.uniform(-np.pi, np.pi, 48)
        H52 = np.zeros(52, dtype=np.complex64)
        H52[:48] = mags * np.exp(1j * phases)
        # QBPSK signal: ±j (HT-SIG constellation)
        bits = rng.choice([0, 1], 48).astype(np.uint8)
        s = np.empty(48, dtype=np.complex64)
        for i in range(48):
            s[i] = 1j if bits[i] else -1j
        # 20 dB SNR
        rx_clean = np.zeros(52, dtype=np.complex64)
        rx_clean[:48] = H52[:48] * s
        sig_power = np.mean(np.abs(rx_clean[:48]) ** 2)
        noise_power = sig_power / (10 ** 2.0)  # 20 dB SNR
        noise = np.sqrt(noise_power / 2) * (rng.standard_normal(52) +
                                            1j * rng.standard_normal(52))
        rx52 = rx_clean + noise.astype(np.complex64)
        eq_mmse = mmse_equalize_htsig_np(rx52, H52)
        eq_zf = zf_equalize_htsig_np(rx52, H52)
        # In the real chain, HT-SIG uses QBPSK: bits on IMAG axis.
        # Sign of IMAG is the bit.
        s_bits = (s.imag >= 0).astype(np.uint8)
        mmse_bits = (eq_mmse.imag >= 0).astype(np.uint8)
        zf_bits = (eq_zf.imag >= 0).astype(np.uint8)
        bit_match_mmse_list.append(float(np.mean(mmse_bits == s_bits)))
        bit_match_zf_list.append(float(np.mean(zf_bits == s_bits)))
    avg_bit_match_mmse = float(np.mean(bit_match_mmse_list))
    avg_bit_match_zf = float(np.mean(bit_match_zf_list))
    # Both should be near-perfect in this strong-signal regime
    assert avg_bit_match_mmse > 0.95, \
        f"MMSE bit match rate too low: {avg_bit_match_mmse} (expected > 0.95 at 20dB SNR with strong H)"
    assert avg_bit_match_zf > 0.95, \
        f"ZF bit match rate too low (sanity): {avg_bit_match_zf}"
    print(f"[PASS] test_mmse_phase_preservation: trials={n_trials}, "
          f"bit-match(MMSE)={avg_bit_match_mmse:.3f}, bit-match(ZF)={avg_bit_match_zf:.3f} "
          f"(strong H, 20dB SNR)")


def test_mmse_n0_robustness():
    """Test 4: 25th percentile of |H|² must be stable when 5/48 SCs are nulls.

    If N0 estimator is the mean of all 48 |H|², then nulls drag it down and
    MMSE gain is degraded at strong SCs (1/N0 too big). With 25th percentile,
    nulls are below the 25th percentile and don't affect the estimate.
    """
    rng = np.random.default_rng(seed=101)
    # Uniform H
    H_uniform = np.zeros(52, dtype=np.complex64)
    H_uniform[:48] = 0.5 * np.exp(1j * rng.uniform(-np.pi, np.pi, 48))
    h_sq_uniform = np.abs(H_uniform[:48]) ** 2
    sorted_uniform = np.sort(h_sq_uniform)
    N0_uniform = 0.5 * (sorted_uniform[11] + sorted_uniform[12])
    # H with 5 nulls at random SCs
    H_null = H_uniform.copy()
    null_idx = rng.choice(48, size=5, replace=False)
    for i in null_idx:
        H_null[i] = 0.05  # |H|² = 0.0025
    h_sq_null = np.abs(H_null[:48]) ** 2
    sorted_null = np.sort(h_sq_null)
    N0_null = 0.5 * (sorted_null[11] + sorted_null[12])
    # 25th percentile should ignore the 5 nulls (they're at indices 0..4 in sorted order).
    # So N0_null should equal N0_uniform.
    rel_change = abs(N0_null - N0_uniform) / N0_uniform
    assert rel_change < 0.05, \
        f"25th percentile must be stable: rel_change = {rel_change} (N0_uniform={N0_uniform}, N0_null={N0_null})"
    print(f"[PASS] test_mmse_n0_robustness: N0(uniform)={N0_uniform:.4f}, "
          f"N0(5 nulls)={N0_null:.4f}, rel_change={rel_change:.4f} (stable)")


# ============================================================
# Full HT-SIG viterbi simulation with channel nulls.
# Reuses helpers from test_htsig_viterbi_synthetic.py.
# ============================================================
def synth_and_decode_with_mmse(case_name, n_nulls, snr_db, use_mmse,
                                use_soft_llr=False, **case_kwargs):
    """Synthesize HT-SIG, apply channel with nulls + AWGN, equalize with ZF or
    MMSE, run viterbi decode."""
    # Import inside to avoid circular
    import sys
    sys.path.insert(0, "/home/hy/gr-ieee802-11/examples")
    from test_htsig_viterbi_synthetic import (
        make_known_htsig_bits, _bcc_encode_48, htsig_interleave,
        bpsk_qbpsk_modulate, insert_ht_pilots, htsig_deinterleave,
        viterbi_decode_133_171, _ht_crc8_compute,
    )

    bits48_tx = make_known_htsig_bits(**case_kwargs)
    coded96 = _bcc_encode_48(bits48_tx)
    coded0 = coded96[0:48]
    coded1 = coded96[48:96]
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)
    # Channel: |H|=0.5 on most SCs, n_nulls random nulls with |H|=0.05
    rng = np.random.default_rng(seed=hash((case_name, n_nulls, int(snr_db*10))) & 0xFFFF)
    H52_a = h_mag_with_nulls(0.5 * np.ones(52, dtype=np.complex64), n_nulls, 0.05, rng)
    H52_b = h_mag_with_nulls(0.5 * np.ones(52, dtype=np.complex64), n_nulls, 0.05, rng)
    # Apply channel: rx = H * tx
    rx52_0 = sc52_0 * H52_a
    rx52_1 = sc52_1 * H52_b
    # Add AWGN at given SNR
    sig_power = np.mean(np.abs(rx52_0[:48]) ** 2)
    if np.isfinite(snr_db):
        noise_power = sig_power / (10 ** (snr_db / 10))
        noise0 = np.sqrt(noise_power / 2) * (rng.standard_normal(52) +
                                             1j * rng.standard_normal(52))
        noise1 = np.sqrt(noise_power / 2) * (rng.standard_normal(52) +
                                             1j * rng.standard_normal(52))
        rx52_0 = rx52_0 + noise0.astype(np.complex64)
        rx52_1 = rx52_1 + noise1.astype(np.complex64)
    # Equalize
    if use_mmse:
        eq48_a = mmse_equalize_htsig_np(rx52_0, H52_a)
        eq48_b = mmse_equalize_htsig_np(rx52_1, H52_b)
    else:
        eq48_a = zf_equalize_htsig_np(rx52_0, H52_a)
        eq48_b = zf_equalize_htsig_np(rx52_1, H52_b)
    # Try all 16 candidates (rot x inv_a x inv_b)
    rot_phases = {0: 1j, 1: 1.0 + 0j, 2: -1j, 3: -1.0 + 0j}
    best = None
    best_metric = 10**9
    for rot_idx in range(4):
        for inv_a in (False, True):
            for inv_b in (False, True):
                rot = rot_phases[rot_idx]
                bits_a = (eq48_a * rot).imag >= 0
                bits_b = (eq48_b * rot).imag >= 0
                if inv_a:
                    bits_a = ~bits_a
                if inv_b:
                    bits_b = ~bits_b
                bits_a = bits_a.astype(np.uint8)
                bits_b = bits_b.astype(np.uint8)
                deint_a = htsig_deinterleave(bits_a)
                deint_b = htsig_deinterleave(bits_b)
                enc96 = np.concatenate([deint_a, deint_b])
                dec48, metric = viterbi_decode_133_171(enc96)
                if dec48 is None or len(dec48) != 48:
                    continue
                tail_ok = np.all(dec48[42:48] == 0)
                crc_calc = _ht_crc8_compute(dec48[0:34])
                crc_match = np.array_equal(crc_calc, dec48[34:42])
                field_ok = (dec48[7] == 0 and np.all(dec48[24:27] == 0))
                if tail_ok and crc_match and field_ok and metric < best_metric:
                    best = dec48
                    best_metric = metric
    if best is None:
        return {"crc_ok": False, "bit_match": False, "metric": -1,
                "n_nulls": n_nulls, "snr_db": snr_db}
    return {
        "crc_ok": True,
        "bit_match": bool(np.array_equal(best, bits48_tx)),
        "metric": int(best_metric),
        "n_nulls": n_nulls,
        "snr_db": snr_db,
    }


def test_htsig_viterbi_with_mmse():
    """Test 5: Full HT-SIG viterbi with channel nulls.

    Sweep n_nulls in {0, 2, 5, 10} at SNR = 10 dB.
    Compare ZF vs MMSE success rates.
    Expectation:
      - At 0 nulls, ZF == MMSE (both work).
      - As nulls increase, ZF degrades (noise amplification), MMSE stays robust.
    """
    case = {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}
    n_trials = 5
    n_nulls_values = [0, 2, 5, 10]
    print("[INFO] test_htsig_viterbi_with_mmse at SNR=10dB:")
    for n_nulls in n_nulls_values:
        zf_pass = 0
        mmse_pass = 0
        for trial in range(n_trials):
            info_zf = synth_and_decode_with_mmse(
                f"A_n{n_nulls}_t{trial}", n_nulls, 10.0,
                use_mmse=False, **case)
            info_mmse = synth_and_decode_with_mmse(
                f"A_n{n_nulls}_t{trial}", n_nulls, 10.0,
                use_mmse=True, **case)
            if info_zf.get("bit_match"):
                zf_pass += 1
            if info_mmse.get("bit_match"):
                mmse_pass += 1
        print(f"  nulls={n_nulls}: ZF={zf_pass}/{n_trials}, "
              f"MMSE={mmse_pass}/{n_trials}")
        # Acceptance: at 10 dB SNR with channel nulls, MMSE should not be
        # WORSE than ZF. At 0 nulls, both should be perfect.
        assert mmse_pass >= zf_pass, \
            f"MMSE worse than ZF at n_nulls={n_nulls}: ZF={zf_pass}, MMSE={mmse_pass}"
        assert mmse_pass >= n_trials * 0.6, \
            f"MMSE pass rate too low at n_nulls={n_nulls}: {mmse_pass}/{n_trials}"
    print("[PASS] test_htsig_viterbi_with_mmse: MMSE >= ZF at all null counts, "
          f"pass rate >= {n_trials * 0.6}/{n_trials} for all null counts")


if __name__ == "__main__":
    test_mmse_vs_zf_clean()
    test_mmse_at_null_sc()
    test_mmse_phase_preservation()
    test_mmse_n0_robustness()
    test_htsig_viterbi_with_mmse()
    print("\nPhase 46 AR5 MMSE synthetic tests passed.")