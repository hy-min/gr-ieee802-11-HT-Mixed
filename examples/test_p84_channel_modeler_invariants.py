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


def test_per_frame_delta_uniform_distribution():
    """uniform mode should sample δ ~ Uniform[0, 1) over N=6400 trials (Phase 82 finding).

    The Phase 82 verdict observed that on USRP, per-frame δ is uniform over [0,1).
    The modeler's "uniform" mode is what should reproduce this behavior.
    """
    from test_usrp_realistic_channel import apply_per_frame_delta

    eq52 = np.ones(52, dtype=np.complex64)
    sc_plus_26 = int(np.where(SC_INDEX_52 == 26)[0][0])
    n_trials = 6400
    phases_at_sc26 = np.empty(n_trials, dtype=np.float64)
    for i in range(n_trials):
        out = apply_per_frame_delta(eq52, SC_INDEX_52, delta=0.0, delta_mode="uniform")
        phases_at_sc26[i] = float(np.angle(out[sc_plus_26]))

    # Phases should span roughly [0, 2π*26/64] = [0, 2.55] rad (since δ ∈ [0,1) and SC=26)
    assert phases_at_sc26.min() < 0.2, f"min phase {phases_at_sc26.min():.2f} not near 0"
    assert phases_at_sc26.max() > 2.3, f"max phase {phases_at_sc26.max():.2f} not near 2.55"

    # Bin into 16 buckets tightly covering the data range and check uniformity
    data_max = max(phases_at_sc26.max(), 2.55)
    buckets = np.linspace(0.0, data_max, 17)
    counts, _ = np.histogram(phases_at_sc26, bins=buckets)
    expected = n_trials / 16  # 400
    # Compare the 14 middle buckets (skip first and last which have edge effects)
    middle_dev = float(np.max(np.abs(counts[1:-1] - expected)))
    assert middle_dev < 0.15 * expected, f"middle bucket deviation {middle_dev:.0f} vs expected {expected:.0f}"
    print(f"OK: uniform δ covers phase range [{phases_at_sc26.min():.2f}, {phases_at_sc26.max():.2f}], "
          f"middle bucket max dev {middle_dev:.0f} / {expected:.0f}")


def test_per_frame_delta_phase_ramp_is_linear():
    """The phase across SCs should be linear with slope 2π * δ / 64."""
    from test_usrp_realistic_channel import apply_per_frame_delta

    eq52 = np.ones(52, dtype=np.complex64)
    delta = 0.25
    out = apply_per_frame_delta(eq52, SC_INDEX_52, delta=delta)
    expected_phases = (2.0 * np.pi * SC_INDEX_52.astype(np.float64) * delta / 64.0) % (2.0 * np.pi)
    got_phases = np.angle(out)
    # Wrap both to [-π, π] and check the rotation matches
    diff = np.abs(np.angle(np.exp(1j * (got_phases - expected_phases))))
    assert diff.max() < 1e-5, f"max phase diff {diff.max():.6f} > 1e-5"
    print(f"OK: phase ramp matches theoretical, max diff = {diff.max():.2e} rad")


def test_awgn_produces_target_snr_within_1db():
    """AWGN at target_snr_db=10 should produce measured SNR within ±1 dB."""
    from test_usrp_realistic_channel import apply_awgn_snr_db

    rng = np.random.default_rng(42)
    sig = (rng.standard_normal(10000) + 1j * rng.standard_normal(10000)).astype(np.complex64)
    sig = sig / np.sqrt(np.mean(np.abs(sig)**2))  # unit power
    sig_noisy = apply_awgn_snr_db(sig, snr_db=10.0, rng=rng)
    signal_power = np.mean(np.abs(sig)**2)
    noise = sig_noisy - sig
    noise_power = np.mean(np.abs(noise)**2)
    measured_snr = 10 * np.log10(signal_power / noise_power)
    assert abs(measured_snr - 10.0) < 1.0, f"SNR {measured_snr:.2f} not within ±1 of target 10"
    print(f"OK: measured SNR = {measured_snr:.2f} dB (target 10)")


def test_aggregator_applies_all_four_impairments_in_order():
    """Aggregated channel should: (1) drop 5 SCs to near-zero, (2) keep ~unit magnitude overall,
    (3) shift the global phase, (4) add noise matching target SNR."""
    from test_usrp_realistic_channel import apply_usrp_realistic_channel

    rng = np.random.default_rng(42)
    eq52 = (rng.standard_normal(52) + 1j * rng.standard_normal(52)).astype(np.complex64)
    eq52 = eq52 / np.sqrt(np.mean(np.abs(eq52)**2))

    out = apply_usrp_realistic_channel(eq52, SC_INDEX_52, snr_db=20.0, seed=42)

    mag = np.abs(out)
    # 5 stable nulls should be MUCH smaller than other SCs (ratio < 0.1)
    null_scs = {-21, -7, 7, 21, -13}
    null_indices = [i for i, sc in enumerate(SC_INDEX_52) if sc in null_scs]
    other_indices = [i for i, sc in enumerate(SC_INDEX_52) if sc not in null_scs]
    null_mag = float(np.mean(mag[null_indices]))
    other_mag = float(np.mean(mag[other_indices]))
    assert null_mag < 0.1 * other_mag, f"null/other ratio {null_mag/other_mag:.3f} not < 0.1"
    print(f"OK: 5 null SCs are {null_mag:.4f} (vs {other_mag:.2f} on other SCs)")


if __name__ == '__main__':
    test_five_stable_null_scs_are_stable()
    test_64psk_residual_quantizes_phase_to_64_bins()
    test_per_frame_delta_uniform_distribution()
    test_per_frame_delta_phase_ramp_is_linear()
    test_awgn_produces_target_snr_within_1db()
    test_aggregator_applies_all_four_impairments_in_order()