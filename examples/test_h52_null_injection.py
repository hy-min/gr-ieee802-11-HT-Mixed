#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 42 Task 1: Reference implementation + synthetic test for H52 null detection
and frequency-domain interpolation. This file exists in Python first to lock in
algorithm semantics; C++ port in Task 2 mirrors these exactly.

The two algorithms:

  estimate_h52_null_index(H52):
      Returns a length-52 boolean array. is_null[i] = (|H52[i]| < NULL_THRESHOLD * median(|H|))

  interpolate_h52_nulls(H52, is_null):
      For each null SC, replaces H52[i] with the mean of its two nearest
      non-null neighbors (left and right). If only one neighbor exists,
      uses that one. If no neighbor exists (all nulls), keeps H52 unchanged.

NOTE on median robustness: median(|H|) is robust to outliers only when nulls are
a minority of SCs (< ~25%). For pathological cases (> 30% nulls), the median
itself gets pulled toward null values and detection fails. This matches the
Phase 38 observation of 5-10 null SCs per frame (10-20% of 52), so the
assumption holds in practice.
"""
import numpy as np

# Threshold factor for null detection. Empirically chosen from Phase 38 data:
# null SCs had |H| ∈ [0.02, 0.14], strong SCs had |H| ∈ [0.5, 1.0]. Ratio 0.28 ≈ 0.3.
NULL_THRESHOLD = 0.3


def estimate_h52_null_index(H52):
    """Detect null subcarriers where |H| < NULL_THRESHOLD * median(|H|).

    Args:
        H52: numpy array shape (52,), complex (must be ndarray for in-place contract)

    Returns:
        numpy bool array shape (52,), True = null subcarrier
    """
    if not isinstance(H52, np.ndarray):
        raise TypeError(f"H52 must be ndarray, got {type(H52).__name__}")
    if H52.shape != (52,):
        raise ValueError(f"Expected shape (52,), got {H52.shape}")
    abs_H = np.abs(H52)
    median_abs = np.median(abs_H)
    return abs_H < NULL_THRESHOLD * median_abs


def interpolate_h52_nulls(H52, is_null):
    """Replace null H52 entries with mean of two nearest non-null neighbors.

    Args:
        H52: numpy array shape (52,), complex (MUST be ndarray; modified in-place)
        is_null: numpy bool array shape (52,)

    Returns:
        None. Modifies H52 in-place.
    """
    H52 = np.asarray(H52, dtype=np.complex128)
    is_null = np.asarray(is_null, dtype=bool)
    if H52.shape != (52,) or is_null.shape != (52,):
        raise ValueError("Expected shape (52,) for both arrays")

    n_null = int(np.sum(is_null))
    if n_null == 0 or n_null == 52:
        return  # no-op: nothing to interpolate, or all-null is degenerate

    for i in np.where(is_null)[0]:
        L, R = i - 1, i + 1
        while L >= 0 and is_null[L]:
            L -= 1
        while R < 52 and is_null[R]:
            R += 1
        if L >= 0 and R < 52:
            H52[i] = (H52[L] + H52[R]) / 2.0
        elif L >= 0:
            H52[i] = H52[L]
        elif R < 52:
            H52[i] = H52[R]
        # else: all-null corner case; keep H52[i]


def test_detects_injected_nulls():
    """Inject 3 known nulls at SCs {3, 17, 31}, verify detection (no FN, no FP)."""
    rng = np.random.default_rng(42)
    H_true = 0.5 + 0.3j * rng.standard_normal(52)  # |H| ~ 0.5-0.8
    H_true[[3, 17, 31]] = 0.05 + 0.02j  # injected nulls
    is_null = estimate_h52_null_index(H_true)
    detected = set(np.where(is_null)[0].tolist())
    expected = {3, 17, 31}
    assert detected == expected, (
        f"Detection mismatch: expected exactly {expected}, got {detected}"
    )
    print("test_detects_injected_nulls PASS")


def test_no_false_positives_on_clean_channel():
    """Channel with no nulls (all |H| similar) should produce 0 null flags."""
    H_clean = np.ones(52, dtype=np.complex128) * (0.5 + 0.1j)
    is_null = estimate_h52_null_index(H_clean)
    assert not np.any(is_null), f"False positives on clean channel: {np.where(is_null)[0]}"
    print("test_no_false_positives_on_clean_channel PASS")


def test_interpolation_recovers_null_within_noise():
    """After interpolation, null SC values should be close to true (smooth) value."""
    H_baseline = 0.5 + 0.1j * np.ones(52)  # smooth channel (no null)
    H_true = H_baseline.copy()
    # Injected null at SC 26 (middle)
    H_true[26] = 0.05 + 0.01j
    # Add Gaussian noise sigma=0.02 to all SCs
    noise = 0.02 * (np.random.default_rng(0).standard_normal(52)
                    + 1j * np.random.default_rng(1).standard_normal(52))
    H_noisy = H_true + noise

    is_null = estimate_h52_null_index(H_noisy)
    assert is_null[26], "Did not detect injected null at SC 26"

    interpolate_h52_nulls(H_noisy, is_null)
    # Interpolated value should be within tolerance of the BASELINE smooth value
    # (not the injected null). Tolerance 0.15 is loose: σ=0.02 per component on
    # two averaged noisy neighbors gives expected std ≈ 0.02·sqrt(2)/2 ≈ 0.014,
    # so 10× std ≈ 0.14. 0.15 leaves headroom for cross-component coupling.
    err = abs(H_noisy[26] - H_baseline[26])
    assert err < 0.15, f"Interpolation error {err:.4f} exceeds tolerance 0.15"
    print(f"test_interpolation_recovers_null_within_noise PASS (err={err:.4f})")


def test_interpolation_edge_null_uses_single_neighbor():
    """Null at SC 0 should fall back to right neighbor only."""
    H = np.ones(52, dtype=np.complex128) * 0.5
    H[0] = 0.01 + 0j  # null at leftmost SC
    is_null = estimate_h52_null_index(H)
    assert is_null[0], "Did not detect null at SC 0"
    interpolate_h52_nulls(H, is_null)
    # SC 0 should now equal SC 1 (right neighbor)
    assert abs(H[0] - H[1]) < 1e-9, f"SC 0 not interpolated from SC 1: H[0]={H[0]}"
    print("test_interpolation_edge_null_uses_single_neighbor PASS")


def test_no_op_on_all_null():
    """All-null corner case: should not crash, H52 unchanged."""
    H = np.zeros(52, dtype=np.complex128)
    is_null = np.ones(52, dtype=bool)
    H_before = H.copy()
    interpolate_h52_nulls(H, is_null)  # should be no-op
    assert np.array_equal(H, H_before), "All-null should be no-op"
    print("test_no_op_on_all_null PASS")


def test_no_op_on_no_null():
    """No-null case: should not modify H52."""
    H = np.ones(52, dtype=np.complex128) * (0.5 + 0.1j)
    H_before = H.copy()
    is_null = np.zeros(52, dtype=bool)
    interpolate_h52_nulls(H, is_null)  # should be no-op
    assert np.array_equal(H, H_before), "No-null should be no-op"
    print("test_no_op_on_no_null PASS")


if __name__ == "__main__":
    test_detects_injected_nulls()
    test_no_false_positives_on_clean_channel()
    test_interpolation_recovers_null_within_noise()
    test_interpolation_edge_null_uses_single_neighbor()
    test_no_op_on_all_null()
    test_no_op_on_no_null()
    print("\n=== All 6 tests PASS ===")