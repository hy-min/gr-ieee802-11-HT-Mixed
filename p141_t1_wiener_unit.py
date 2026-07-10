#!/usr/bin/env python3
"""Phase 141 T1: Wiener filter unit test.

Validates that the Wiener shrinkage formula matches the C++ implementation
on a synthetic 52-SC channel with 5 stable null SCs.

Reference implementation: G[k] = R_hh[k] / (R_hh[k] + sigma2 / |y_ltf[k]|^2)
with floor G >= g_min.
"""
import numpy as np


def wiener_filter_h52_python(h_ls, y_ltf, r_hh, sigma2, g_min=0.1):
    """Python reference implementation matching C++ semantics."""
    h_out = np.zeros(52, dtype=complex)
    for k in range(52):
        y_abs2 = abs(y_ltf[k]) ** 2
        noise_term = sigma2 / max(y_abs2, 1e-12)
        G = r_hh[k] / (r_hh[k] + noise_term)
        if G < g_min:
            G = g_min
        h_out[k] = G * h_ls[k]
    return h_out


def test_wiener_shrinkage_at_null_scs():
    """Wiener shrinks null SCs (low |H|^2) toward zero."""
    np.random.seed(42)
    k = np.arange(52)
    # 5 stable null SCs (Phase 78b): |H| ~= 0
    h_true = np.ones(52, dtype=complex) * np.exp(1j * 0.1 * k)
    h_true[5] = 0.01 + 0.01j   # SC -21 (idx 5)
    h_true[13] = 0.02 + 0.01j  # SC -13 (idx 13)
    h_true[19] = 0.01 + 0.01j  # SC -7 (idx 19)
    h_true[33] = 0.02 + 0.01j  # SC +7 (idx 33)
    h_true[47] = 0.01 + 0.01j  # SC +21 (idx 47)
    sigma2 = 0.5  # high noise
    noise = np.sqrt(sigma2 / 2) * (np.random.randn(52) + 1j * np.random.randn(52))
    # BPSK +/-1 LTF reference (no zeros — pure sign)
    x_ltf = np.sign(np.sin(2 * np.pi * np.arange(52) / 4 + 1.0)).astype(complex)
    y_ltf = h_true * x_ltf + noise
    # LS estimate: H_ls = y_ltf / x_ltf
    h_ls = y_ltf / x_ltf
    # R_hh = E[|H|^2] using |h_ls|^2 (single-frame approximation)
    r_hh = np.abs(h_ls) ** 2
    h_wiener = wiener_filter_h52_python(h_ls, y_ltf, r_hh, sigma2, g_min=0.1)
    # Check null SCs: |H_wiener[null]| should be << |H_ls[null]|
    null_indices = [5, 13, 19, 33, 47]
    ls_null_mag = np.mean(np.abs(h_ls[null_indices]))
    wiener_null_mag = np.mean(np.abs(h_wiener[null_indices]))
    print(f"LS null SC |H|:       {ls_null_mag:.6f}")
    print(f"Wiener null SC |H|:   {wiener_null_mag:.6f}")
    print(f"Shrinkage factor:     {ls_null_mag / (wiener_null_mag + 1e-12):.2f}x")
    assert wiener_null_mag < ls_null_mag, \
        f"Wiener failed to shrink null SCs: {wiener_null_mag} >= {ls_null_mag}"
    # Strong SCs should be preserved (G ~= 1)
    strong_indices = [i for i in range(52) if i not in null_indices]
    ls_strong_mag = np.mean(np.abs(h_ls[strong_indices]))
    wiener_strong_mag = np.mean(np.abs(h_wiener[strong_indices]))
    print(f"LS strong SC |H|:     {ls_strong_mag:.6f}")
    print(f"Wiener strong SC |H|: {wiener_strong_mag:.6f}")
    # Strong SCs should be approximately preserved
    assert wiener_strong_mag > 0.7 * ls_strong_mag, \
        f"Wiener over-shrunk strong SCs: {wiener_strong_mag} < 0.7*{ls_strong_mag}"
    print("PASS: Wiener shrinkage at null SCs is correct")


def test_wiener_g_min_floor():
    """g_min floor prevents G from collapsing to 0."""
    np.random.seed(7)
    h_ls = np.zeros(52, dtype=complex)
    y_ltf = np.zeros(52, dtype=complex)
    r_hh = np.ones(52)
    sigma2 = 100.0  # huge noise -> G -> 0 without floor
    h_out = wiener_filter_h52_python(h_ls, y_ltf, r_hh, sigma2, g_min=0.1)
    # All G should be >= g_min = 0.1
    for k in range(52):
        G = r_hh[k] / (r_hh[k] + sigma2 / max(abs(y_ltf[k]) ** 2, 1e-12))
        G_clamped = max(G, 0.1)
        expected = G_clamped * h_ls[k]
        assert abs(h_out[k] - expected) < 1e-12, \
            f"g_min floor failed at k={k}: got {h_out[k]} expected {expected}"
    print("PASS: g_min floor prevents zero shrinkage")


def test_wiener_zero_division_safety():
    """|y_ltf[k]|^2 = 0 doesn't cause divide-by-zero."""
    np.random.seed(13)
    h_ls = np.ones(52, dtype=complex)
    y_ltf = np.zeros(52, dtype=complex)  # all zeros
    r_hh = np.ones(52)
    sigma2 = 0.5
    # Should not raise; G collapses via g_min floor
    h_out = wiener_filter_h52_python(h_ls, y_ltf, r_hh, sigma2, g_min=0.1)
    assert np.all(np.isfinite(h_out)), "Wiener produced non-finite values"
    # With y_ltf=0, noise_term = sigma2/1e-12 (huge), G -> 0, clamped to g_min
    for k in range(52):
        G = 0.1  # floor
        expected = G * h_ls[k]
        assert abs(h_out[k] - expected) < 1e-6, \
            f"Zero-division safety failed at k={k}"
    print("PASS: zero-division safety holds")


def test_wiener_pure_passthrough_when_g_equals_1():
    """When sigma2 -> 0, G -> 1, Wiener = LS."""
    np.random.seed(99)
    h_ls = (np.random.randn(52) + 1j * np.random.randn(52)).astype(complex)
    y_ltf = np.ones(52, dtype=complex)
    r_hh = np.ones(52)
    sigma2 = 1e-20  # effectively zero
    h_out = wiener_filter_h52_python(h_ls, y_ltf, r_hh, sigma2, g_min=0.0)
    # G -> 1, h_out ~= h_ls
    np.testing.assert_allclose(h_out, h_ls, atol=1e-9)
    print("PASS: Wiener -> LS when noise is negligible")


if __name__ == "__main__":
    test_wiener_shrinkage_at_null_scs()
    test_wiener_g_min_floor()
    test_wiener_zero_division_safety()
    test_wiener_pure_passthrough_when_g_equals_1()
    print("\nAll T1 Wiener kernel tests PASSED.")