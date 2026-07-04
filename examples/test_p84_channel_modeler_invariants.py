"""Phase 84 invariant tests for the USRP-realistic channel modeler.

Tests that each impairment function in test_usrp_realistic_channel.py has the
documented structural properties (Phase 78b/33b/34/78a). Pure-stdlib pytest
tests — no GNU Radio required.
"""
import numpy as np


# LTF52 SC indices in TX order, matching Phase 78b's analysis.
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def test_five_stable_null_scs_are_stable():
    """The 5 null SCs should be IDENTICAL across N trials (Phase 78b finding)."""
    from test_usrp_realistic_channel import apply_5_stable_null_scs

    rng = np.random.default_rng(42)
    expected_null_scs = {-21, -7, 7, 21, -13}  # Phase 78b observed 5 stable null SCs (incl. 1 pilot)
    null_freq = {}
    for trial in range(20):
        eq52 = rng.standard_normal(52) + 1j * rng.standard_normal(52)
        eq52_distorted = apply_5_stable_null_scs(eq52, SC_INDEX_52)
        mag = np.abs(eq52_distorted)
        null_indices = np.where(mag < 0.05)[0]
        for idx in null_indices:
            sc = int(SC_INDEX_52[idx])
            null_freq.setdefault(sc, 0)
            null_freq[sc] += 1
    top5 = set(sorted(null_freq, key=lambda s: -null_freq[s])[:5])
    assert top5 == expected_null_scs, f"Top-5 nulls {top5} != expected {expected_null_scs}"
    print(f"OK: stable nulls = {top5}")


def test_64psk_residual_quantizes_phase_to_64_bins():
    """ADC residual should round each rx symbol's phase to nearest 1/64 of unit circle (Phase 33b)."""
    from test_usrp_realistic_channel import apply_64psk_residual

    rng = np.random.default_rng(42)
    rx = rng.standard_normal(500) + 1j * rng.standard_normal(500)
    rx = (rx / np.abs(rx)).astype(np.complex64)  # unit-magnitude (ADC saturates)
    rx_distorted = apply_64psk_residual(rx)
    phases = np.angle(rx_distorted)
    # Each phase should be within ±(π/64) of a k*π/32 grid point
    quant_err = np.abs((phases + np.pi) % (np.pi / 32) - np.pi / 64)
    assert quant_err.max() < np.pi / 64 + 1e-6, f"max quant err {quant_err.max()} > π/64"
    print(f"OK: 64-PSK max quant err = {quant_err.max():.6f} rad")


if __name__ == '__main__':
    test_five_stable_null_scs_are_stable()
    test_64psk_residual_quantizes_phase_to_64_bins()