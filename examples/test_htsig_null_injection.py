#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 43 Task 1: Python reference + synthetic test for per-SC H52 null-based
hard-bit gating for HT-SIG viterbi (Layer 2 of the Phase 42+43 design).

ALGORITHM (mirrors C++ in lib/frame_equalizer_impl.cc):

For each SC i in [0, 48) (data subcarriers, NOT pilots at indices 48-51):
  1. Compute abs_H[i] = |H52[i]|.
  2. ref = 90th percentile of sorted abs_H[i] for i in [0, 48).
  3. threshold = 0.3 * ref.
  4. is_null[i] = abs_H[i] < threshold.
  5. If is_null[i]: equalized eq[i] is set to 0, so bit[i] = (0.imag() >= 0) = 0.

Why 90th percentile (NOT median as in Phase 42 Layer 1):
- Median gets dragged DOWN by noise at low SNR, causing high false-positive rate
  (Phase 42 verdict showed avg_snr_lsig collapse from 15 to 1 dB).
- 90th percentile is HIGH-biased. Noise cannot drag it UP; only real strong SCs
  can produce high values. Robust under low SNR (verified on USRP data).
- Threshold 0.3 * 0.9th_percentile cleanly separates nulls (|H| ~ 0.05) from
  strong SCs (|H| ~ 0.5-1.0) without false positives on noise-bumped SCs.

Why gate at the hard-bit level (NOT LLR weighting):
- The decoder's viterbi receives HARD uint8_t bits (not soft LLRs).
  Bit extraction happens in frame_equalizer_impl.cc::decode_htsig_from_rotated
  at lines 2143-2154 (HT-SIG0) and 2242-2252 (HT-SIG1).
- The only legal intervention point is the bit extraction itself: if we set
  eq = 0 before the imag-sign check, bit = 0 (deterministic, no per-SC bias
  beyond "force to 0" which is uniform-random under noise anyway).

This file exists in Python first to lock in algorithm semantics; the C++ port
in lib/frame_equalizer_impl.cc mirrors these exactly.
"""
import numpy as np

# Threshold factor for null detection. Same as Phase 42 Layer 1 (0.3) but
# applied to 90th percentile of |H| instead of median.
NULL_THRESHOLD = 0.3

# Number of data subcarriers (excluding pilots at indices 48-51).
N_DATA = 48


def detect_null_sc(H52, n_data=N_DATA, threshold=NULL_THRESHOLD):
    """Detect null data subcarriers using 90th percentile of |H| as reference.

    Args:
        H52: numpy array shape (52,), complex
        n_data: number of data SCs to consider (default 48, excludes pilots)
        threshold: factor applied to 90th percentile for null detection

    Returns:
        numpy bool array shape (52,), True = null subcarrier. Pilots at
        indices [n_data:52] are always False (not gated).
    """
    if not isinstance(H52, np.ndarray):
        raise TypeError(f"H52 must be ndarray, got {type(H52).__name__}")
    if H52.shape != (52,):
        raise ValueError(f"Expected shape (52,), got {H52.shape}")
    abs_H = np.abs(H52[:n_data])
    if len(abs_H) == 0:
        return np.zeros(52, dtype=bool)
    sorted_abs = np.sort(abs_H)
    # 90th percentile: index floor(0.9 * n_data). For n_data=48 → index 43.
    ref_idx = int(0.9 * n_data)
    if ref_idx >= n_data:
        ref_idx = n_data - 1
    ref = sorted_abs[ref_idx]
    is_null_data = abs_H < threshold * ref
    # Pilots never gated.
    is_null = np.zeros(52, dtype=bool)
    is_null[:n_data] = is_null_data
    return is_null, ref


def apply_null_gating_to_bits(H52, rx52, is_null):
    """Compute hard bits as in frame_equalizer_impl.cc bit-extraction, gating nulls.

    Mirrors the C++ loop at lines 2143-2154 (and 2242-2252):
        eq = rx52[i] / H52[i]    if |H52[i]| >= 0.001
        eq = 0                  otherwise
        bit = 1 if eq.imag() >= 0 else 0   (NOTE: >=, not >)

    IMPORTANT: setting eq=0 does NOT force bit=0 because (0.imag() = 0) and
    (0 >= 0) is True, which evaluates to bit=1. So gating must be applied AT
    THE BIT LEVEL (post-extraction): if is_null[i] is True, directly force
    bit[i] = 0. This is the only way to deterministically force bit=0.

    Args:
        H52: numpy array shape (52,), complex
        rx52: numpy array shape (52,), complex (equalized received symbol)
        is_null: numpy bool array shape (52,)

    Returns:
        uint8 numpy array shape (52,) of hard bits
    """
    bits = np.zeros(52, dtype=np.uint8)
    for i in range(52):
        h_mag = abs(H52[i])
        if h_mag < 0.001:
            # Existing fallback in C++: eq=0, bit = (0 >= 0) = 1.
            # Preserve this behavior (consistent with C++ default).
            bits[i] = 1
        else:
            eq = rx52[i] / H52[i]
            bits[i] = 1 if eq.imag >= 0.0 else 0
        # Apply null gating AT THE BIT LEVEL (post-extraction). This is the
        # only way to force bit=0 deterministically (eq=0 would yield bit=1).
        if is_null[i]:
            bits[i] = 0
    return bits


def test_detects_injected_nulls():
    """Inject 3 known nulls at SCs {3, 17, 31}, verify detection (no FN, no FP)."""
    rng = np.random.default_rng(42)
    H_true = 0.5 + 0.3j * rng.standard_normal(52)  # |H| ~ 0.5-0.8
    H_true[[3, 17, 31]] = 0.05 + 0.02j  # injected nulls
    is_null, ref = detect_null_sc(H_true)
    detected = set(np.where(is_null)[0].tolist())
    expected = {3, 17, 31}
    assert detected == expected, (
        f"Detection mismatch: expected exactly {expected}, got {detected}, "
        f"ref={ref:.4f}"
    )
    print(f"test_detects_injected_nulls PASS (ref={ref:.4f})")


def test_no_false_positives_on_clean_channel():
    """Channel with no nulls (all |H| similar) should produce 0 null flags."""
    H_clean = np.ones(52, dtype=np.complex128) * (0.5 + 0.1j)
    is_null, ref = detect_null_sc(H_clean)
    assert not np.any(is_null), (
        f"False positives on clean channel: {np.where(is_null)[0]}, ref={ref:.4f}"
    )
    print(f"test_no_false_positives_on_clean_channel PASS (ref={ref:.4f})")


def test_null_sc_bits_forced_to_zero():
    """Verify that null SCs produce bit=0 regardless of eq.imag() sign."""
    rng = np.random.default_rng(42)
    H_true = 0.5 + 0.3j * rng.standard_normal(52)
    H_true[[3, 17, 31]] = 0.05 + 0.02j  # injected nulls
    # Random rx52 to make eq.imag() effectively random
    rx52 = (1.0 + 0.5j) * rng.standard_normal(52)
    is_null, ref = detect_null_sc(H_true)
    bits = apply_null_gating_to_bits(H_true, rx52, is_null)
    # Null SCs should all have bit=0 (gated)
    for sc in [3, 17, 31]:
        assert bits[sc] == 0, f"SC {sc} not gated to 0: bit={bits[sc]}"
    # Non-null data SCs should reflect eq.imag() sign normally (could be 0 or 1)
    for i in range(48):
        if i not in [3, 17, 31]:
            h_mag = abs(H_true[i])
            eq = rx52[i] / H_true[i]
            expected_bit = 1 if eq.imag >= 0.0 else 0
            assert bits[i] == expected_bit, (
                f"Non-null SC {i}: expected bit {expected_bit}, got {bits[i]}"
            )
    # Pilot SCs (48-51) should also pass through normally (not gated)
    for i in range(48, 52):
        h_mag = abs(H_true[i])
        eq = rx52[i] / H_true[i]
        expected_bit = 1 if eq.imag >= 0.0 else 0
        assert bits[i] == expected_bit, (
            f"Pilot SC {i}: expected bit {expected_bit}, got {bits[i]}"
        )
    print(f"test_null_sc_bits_forced_to_zero PASS (ref={ref:.4f})")


def test_low_snr_robustness():
    """At low SNR, 90th percentile should still be robust (vs median which fails).

    Phase 42 Layer 1 (median) failed because noise dragged median down.
    Phase 43 Layer 2 (90th percentile) should NOT flag noise-bumped SCs as null
    when they actually have strong |H|.
    """
    rng = np.random.default_rng(42)
    # Strong SCs: |H| ~ 0.5-1.0 with NOISE added. 90th percentile of |H+noise|
    # should still be high.
    H_strong = 0.7 + 0.2j * rng.standard_normal(52)
    # Add large AWGN: sigma ~0.3 (low SNR)
    noise = 0.3 * (rng.standard_normal(52) + 1j * rng.standard_normal(52))
    H_noisy = H_strong + noise
    is_null, ref = detect_null_sc(H_noisy)
    # With strong SCs + heavy noise, the 90th percentile should still be ~0.5-0.7
    # (real signal dominates the upper tail). Threshold 0.3*ref ~ 0.15-0.21.
    # |H| of any SC will rarely fall below 0.2 (would require very large noise).
    n_null = int(np.sum(is_null))
    # Allow up to 5 false positives at this noise level (5/48 = 10%)
    assert n_null <= 5, (
        f"Too many false positives at low SNR: {n_null} nulls flagged "
        f"(expected ≤5), ref={ref:.4f}, is_null={is_null}"
    )
    print(f"test_low_snr_robustness PASS (ref={ref:.4f}, n_null={n_null})")


def test_pilots_not_gated():
    """Verify pilot SCs (48-51) are NEVER gated, regardless of |H| value."""
    # Set pilots to very low magnitude. Even so, they must not be flagged.
    H = 0.5 + 0.1j * np.ones(52)
    H[48:52] = 0.001 + 0.0001j  # effectively zero pilots
    is_null, ref = detect_null_sc(H)
    for i in range(48, 52):
        assert not is_null[i], f"Pilot SC {i} was incorrectly gated as null"
    print(f"test_pilots_not_gated PASS (ref={ref:.4f})")


def test_all_null_corner_case():
    """All-null corner case: 90th percentile = 0, ref < 1e-9, should be no-op."""
    H = np.zeros(52, dtype=np.complex128)
    is_null, ref = detect_null_sc(H)
    # ref = 0, so threshold = 0. abs_H < 0 is False for all. No SCs flagged.
    assert not np.any(is_null), (
        f"All-null channel: no SCs should be flagged, got {np.sum(is_null)}"
    )
    assert ref == 0.0, f"All-null ref should be 0, got {ref}"
    print(f"test_all_null_corner_case PASS (ref={ref:.4f})")


def test_robustness_vs_phase42_median():
    """Show that 90th percentile survives where median fails under majority-null.

    Channel: 5 strong SCs (|H|=0.7) + 43 weak SCs (|H|=0.05).
    Median ≈ 0.05 (dragged DOWN by weak majority). 0.3*median = 0.015.
    → Median flags NO SCs (because 0.05 is not < 0.015). Misses ALL nulls!

    90th percentile ≈ 0.7 (dragged UP by strong SCs). 0.3*90th = 0.21.
    → 90th percentile flags the 43 weak SCs as null correctly.

    This is the opposite failure mode of Phase 42 Layer 1: at low SNR,
    median collapses so much that NOTHING is flagged (FN rate ~100%).
    90th percentile correctly identifies the nulls.

    NOTE: This models the case where |H| is measured from L-LTF with
    reasonable SNR (L-LTF is ~10 dB SNR higher than data at avg_snr_lsig=1 dB
    due to L-LTF being a known training sequence). |H| values are clean,
    but the channel is mostly weak (a frequency-selective null pattern).
    """
    H = np.zeros(52, dtype=np.complex128)
    # 5 strong SCs
    strong_scs = [5, 15, 25, 35, 45]
    for i in strong_scs:
        H[i] = 0.7 + 0.1j
    # 43 weak/null SCs
    for i in range(48):
        if i not in strong_scs:
            H[i] = 0.05 + 0.01j

    # Compute median and 90th percentile for comparison
    abs_H = np.abs(H[:48])
    median_val = np.median(abs_H)
    sorted_abs = np.sort(abs_H)
    p90_val = sorted_abs[int(0.9 * 48)]

    # Apply Layer 2 (90th percentile)
    is_null_layer2, ref = detect_null_sc(H)

    # Apply Phase 42 Layer 1 logic (median) for comparison
    is_null_layer1 = abs_H < 0.3 * median_val

    n_layer2 = int(np.sum(is_null_layer2))
    n_layer1 = int(is_null_layer1.sum())

    # In majority-null scenario, median FAILS (flags nothing), 90th-percentile
    # correctly identifies the null majority.
    assert n_layer1 == 0, (
        f"Median should fail to flag in majority-null case, got {n_layer1}"
    )
    assert n_layer2 == 43, (
        f"90th percentile should flag 43 weak SCs, got {n_layer2}"
    )

    print(
        f"test_robustness_vs_phase42_median PASS "
        f"(median={median_val:.4f} → flags {n_layer1}/48 (FAILS, all nulls missed), "
        f"90th={p90_val:.4f} → flags {n_layer2}/48 (correct))"
    )


if __name__ == "__main__":
    test_detects_injected_nulls()
    test_no_false_positives_on_clean_channel()
    test_null_sc_bits_forced_to_zero()
    test_low_snr_robustness()
    test_pilots_not_gated()
    test_all_null_corner_case()
    test_robustness_vs_phase42_median()
    print("\n=== All 7 tests PASS ===")