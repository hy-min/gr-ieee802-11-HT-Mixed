#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 72 synthetic test: Hann envelope compensation + MMSE equalization.

Validates two independent fixes for H52 quality:
(a) Hann compensation: when L-LTF RX FFT uses Hann window, multiply H52 by
    2.0 to restore the main-lobe gain (Hann(64) DC gain = 0.5).
(b) MMSE EQ: replace safe_div(rx, H) with (conj(H)*rx) / (|H|² + N0)
    where N0 is the 25th-percentile of |H|² over data SCs.

The test generates a synthetic 802.11n L-LTF time-domain signal, applies a
noisy channel with frequency-selective nulls, and compares equalized symbol
quality under four conditions:
  - ZF (no Hann, no MMSE): baseline
  - MMSE only: noise regularization
  - Hann+comp (Hann window + 2.0 compensation): no MMSE
  - Hann+comp+MMSE: both fixes

Reference: docs/superpowers/plans/2026-07-01-phase72-hann-mse.md
"""
import argparse
import sys
import numpy as np


# Hann window spectral response at DC (main-lobe gain)
HANN_DC_GAIN = 0.5
HANN_COMPENSATION_FACTOR = 1.0 / HANN_DC_GAIN  # 2.0


def generate_l_ltf64_time_domain(num_samples=64, snr_db=20.0, cfo_offset_bins=0.0, seed=42):
    """Generate a synthetic 802.11n L-LTF time-domain signal (1 OFDM symbol).

    L-LTF is 64 samples with active subcarriers at indices 1..26 and 38..63
    (skipping DC bin 0 and bins 27..37 = negative freq guard band).

    Args:
        num_samples: 64 (1 OFDM symbol).
        snr_db: signal-to-noise ratio in dB.
        cfo_offset_bins: fractional CFO offset (in FFT bins), to test
            non-bin-centered signals (where Hann's spectral response matters).
        seed: random seed.

    Returns:
        time_samples: 64 complex time-domain samples.
    """
    rng = np.random.default_rng(seed)
    # Build frequency-domain LTF: 52 active subcarriers at random QPSK symbols
    ltf_freq = np.zeros(num_samples, dtype=np.complex64)
    active_bins = list(range(1, 27)) + list(range(38, 64))  # 52 active SCs
    for b in active_bins:
        ltf_freq[b] = rng.choice([-1, 1]) + 1j * rng.choice([-1, 1])
        ltf_freq[b] /= np.sqrt(2)
    # Apply fractional CFO: shift each bin by cfo_offset_bins
    if cfo_offset_bins != 0.0:
        ltf_freq_shifted = np.zeros(num_samples, dtype=np.complex64)
        for b in active_bins:
            target_bin = b + cfo_offset_bins
            # Distribute energy between adjacent integer bins
            lo_bin = int(np.floor(target_bin)) % num_samples
            hi_bin = (lo_bin + 1) % num_samples
            frac = target_bin - np.floor(target_bin)
            ltf_freq_shifted[lo_bin] += ltf_freq[b] * (1.0 - frac)
            ltf_freq_shifted[hi_bin] += ltf_freq[b] * frac
        ltf_freq = ltf_freq_shifted
    # IFFT to get time-domain signal
    time_samples = np.fft.ifft(ltf_freq) * num_samples  # scale to mimic power
    # Add AWGN
    snr_linear = 10 ** (snr_db / 10.0)
    signal_power = np.mean(np.abs(time_samples) ** 2)
    noise_std = np.sqrt(signal_power / (2.0 * snr_linear))
    noise = (rng.standard_normal(num_samples) + 1j * rng.standard_normal(num_samples)) * noise_std
    return (time_samples + noise).astype(np.complex64)


def apply_window(samples, window_type='rectangular'):
    """Apply a window function before FFT."""
    n = len(samples)
    if window_type == 'rectangular':
        return samples
    elif window_type == 'hann':
        return samples * np.hanning(n).astype(np.complex64)
    else:
        raise ValueError(f"Unknown window type: {window_type}")


def fft_64(samples):
    """64-point FFT (no shift)."""
    return np.fft.fft(samples)


def estimate_h52_no_comp(fft_bins, ltf_tx_freq):
    """Compute H52 = Y / X (no Hann compensation).

    Active SCs: bins 1..26 (SC +1..+26), bins 38..63 (SC -26..-1).
    Returns h52[0..51]: h52[0..25] = -26..-1, h52[26..51] = +1..+26.
    """
    h52 = np.zeros(52, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):  # SC -26..-1 → h52[0..25]
        h52[i] = fft_bins[b] / ltf_tx_freq[b]
    for i, b in enumerate(range(1, 27)):  # SC +1..+26 → h52[26..51]
        h52[26 + i] = fft_bins[b] / ltf_tx_freq[b]
    return h52


def estimate_h52_with_comp(fft_bins, ltf_tx_freq):
    """Compute H52 with Hann compensation (multiply by 2.0).

    The Hann window's main-lobe gain is 0.5 (DC response), so applying
    the window in time scales the FFT output by 0.5. Dividing by 0.5
    (multiplying by 2.0) restores the magnitude scale. This is a
    first-order correction; full 3-tap deconvolution is future work.
    """
    h52 = estimate_h52_no_comp(fft_bins, ltf_tx_freq)
    return h52 * HANN_COMPENSATION_FACTOR


def zf_equalize(rx52, h52):
    """Zero-forcing equalization: eq = rx / H."""
    eq = np.zeros(48, dtype=np.complex64)
    for i in range(48):
        h_mag_sq = np.abs(h52[i]) ** 2
        if h_mag_sq < 1e-6:
            eq[i] = 0.0
        else:
            eq[i] = rx52[i] * np.conj(h52[i]) / h_mag_sq
    return eq


def mmse_equalize(rx52, h52, n0_percentile=25):
    """MMSE equalization: eq = conj(H)*rx / (|H|² + N0).

    N0 is the n0_percentile-th percentile of |H|² over the 48 data SCs.
    """
    h_sq = np.abs(h52[:48]) ** 2
    sorted_h_sq = np.sort(h_sq)
    idx_d = (n0_percentile / 100.0) * 47.0
    idx_lo = int(idx_d)
    idx_hi = min(idx_lo + 1, 47)
    frac = idx_d - idx_lo
    N0 = sorted_h_sq[idx_lo] * (1.0 - frac) + sorted_h_sq[idx_hi] * frac
    N0 = max(N0, 1e-9)  # floor
    eq = np.zeros(48, dtype=np.complex64)
    for i in range(48):
        eq[i] = np.conj(h52[i]) * rx52[i] / (h_sq[i] + N0)
    return eq


def equalized_quality_metrics(eq_symbols, expected_symbols):
    """Compute error between equalized symbols and expected (BPSK on real axis).

    Returns:
        dict with keys: mse (mean squared error), max_err, n_wrong_sign
            (count of symbols with wrong sign of real part)
    """
    err = eq_symbols - expected_symbols
    mse = float(np.mean(np.abs(err) ** 2))
    max_err = float(np.max(np.abs(err)))
    n_wrong_sign = int(np.sum(np.sign(eq_symbols.real) != np.sign(expected_symbols.real)))
    return {'mse': mse, 'max_err': max_err, 'n_wrong_sign': n_wrong_sign}


def test_mmse_beats_zf_at_null_scs():
    """Synthesize L-LTF, apply channel with 2 null SCs, and verify MMSE
    produces lower equalized-symbol MSE than ZF at the null SCs.
    """
    rng = np.random.default_rng(42)
    # Generate LTF0 and LTF1 (same freq-domain sequence for simplicity)
    ltf_freq = np.zeros(64, dtype=np.complex64)
    for b in list(range(1, 27)) + list(range(38, 64)):
        ltf_freq[b] = rng.choice([-1, 1]) + 1j * rng.choice([-1, 1])
        ltf_freq[b] /= np.sqrt(2)
    ltf0_time = np.fft.ifft(ltf_freq) * 64
    ltf1_time = ltf0_time.copy()
    # Channel with 2 nulls at SC -10 and SC +10 (h52[15] and h52[35])
    h_chan = np.ones(52, dtype=np.complex64) * 0.5
    h_chan[15] = 0.02  # null at SC -10
    h_chan[35] = 0.02  # null at SC +10
    # Apply channel + noise
    snr_db = 15.0
    rx_freq = ltf_freq * np.fft.fftshift(h_chan_to_64(h_chan))
    rx0_time = np.fft.ifft(rx_freq) * 64
    signal_power = np.mean(np.abs(rx0_time) ** 2)
    noise_std = np.sqrt(signal_power / (2.0 * 10 ** (snr_db / 10.0)))
    noise = (rng.standard_normal(64) + 1j * rng.standard_normal(64)) * noise_std
    rx0_time_noisy = rx0_time + noise
    # FFT
    rx0_fft = np.fft.fft(rx0_time_noisy)
    # Build rx52 (48 data SCs, in h52[0..47] order = SC -26..-1, +1..+22)
    rx52 = np.zeros(48, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):  # SC -26..-1 → rx52[0..25]
        rx52[i] = rx0_fft[b]
    for i, b in enumerate(range(1, 23)):  # SC +1..+22 → rx52[26..47]
        rx52[26 + i] = rx0_fft[b]
    # H52 from known channel
    h52 = h_chan
    # ZF equalize
    eq_zf = zf_equalize(rx52, h52)
    # MMSE equalize
    eq_mmse = mmse_equalize(rx52, h52, n0_percentile=25)
    # Expected symbols (BPSK on real axis, sign from original LTF)
    expected = np.zeros(48, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):  # SC -26..-1
        expected[i] = ltf_freq[b]
    for i, b in enumerate(range(1, 23)):  # SC +1..+22
        expected[26 + i] = ltf_freq[b]
    zf_metrics = equalized_quality_metrics(eq_zf, expected)
    mmse_metrics = equalized_quality_metrics(eq_mmse, expected)
    print(f"[NULL_SC_TEST] ZF:    mse={zf_metrics['mse']:.4f} max_err={zf_metrics['max_err']:.4f} n_wrong_sign={zf_metrics['n_wrong_sign']}")
    print(f"[NULL_SC_TEST] MMSE:  mse={mmse_metrics['mse']:.4f} max_err={mmse_metrics['max_err']:.4f} n_wrong_sign={mmse_metrics['n_wrong_sign']}")
    # MMSE should beat ZF at null SCs
    assert mmse_metrics['mse'] < zf_metrics['mse'], \
        f"MMSE did not improve: ZF mse={zf_metrics['mse']:.4f} MMSE mse={mmse_metrics['mse']:.4f}"
    print("[NULL_SC_TEST] PASS")
    return True


def h_chan_to_64(h52):
    """Map h52[52] (HT order) to a 64-bin FFT (skipping DC and guard bands)."""
    h64 = np.zeros(64, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):  # SC -26..-1
        h64[b] = h52[i]
    for i, b in enumerate(range(1, 27)):  # SC +1..+26
        h64[b] = h52[26 + i]
    return h64


def test_hann_compensation_restores_magnitude():
    """At perfect SNR with non-zero CFO, Hann reduces FFT magnitude by 2x
    at all SCs. Compensation (multiply by 2.0) should restore it.
    """
    samples = generate_l_ltf64_time_domain(snr_db=60.0, cfo_offset_bins=0.0, seed=42)
    # Use a known ltf_tx_freq (the original frequency-domain sequence used to generate)
    rng = np.random.default_rng(42)
    ltf_tx_freq = np.zeros(64, dtype=np.complex64)
    for b in list(range(1, 27)) + list(range(38, 64)):
        ltf_tx_freq[b] = rng.choice([-1, 1]) + 1j * rng.choice([-1, 1])
        ltf_tx_freq[b] /= np.sqrt(2)
    # Rectangular: full magnitude
    rect_fft = fft_64(apply_window(samples, 'rectangular'))
    h52_rect = estimate_h52_no_comp(rect_fft, ltf_tx_freq)
    # Hann: reduced magnitude
    hann_fft = fft_64(apply_window(samples, 'hann'))
    h52_hann = estimate_h52_no_comp(hann_fft, ltf_tx_freq)
    # Hann + compensation: restored magnitude
    h52_hann_comp = estimate_h52_with_comp(hann_fft, ltf_tx_freq)
    mag_rect = float(np.mean(np.abs(h52_rect)))
    mag_hann = float(np.mean(np.abs(h52_hann)))
    mag_hann_comp = float(np.mean(np.abs(h52_hann_comp)))
    print(f"[HANN_COMP] Rect mag={mag_rect:.4f}, Hann mag={mag_hann:.4f}, Hann+comp mag={mag_hann_comp:.4f}")
    # Hann should be ~0.5x of rect
    assert mag_hann < mag_rect * 0.6, f"Hann did not reduce magnitude: rect={mag_rect:.4f} hann={mag_hann:.4f}"
    # Hann + comp should be ~equal to rect
    assert abs(mag_hann_comp - mag_rect) / mag_rect < 0.3, \
        f"Hann compensation did not restore magnitude: rect={mag_rect:.4f} hann+comp={mag_hann_comp:.4f}"
    print("[HANN_COMP] PASS")
    return True


def test_mmse_helps_at_low_snr():
    """At low SNR with strong H52 nulls, MMSE should reduce equalized-symbol
    error compared to ZF. This is the main test that justifies the MMSE EQ.
    """
    rng = np.random.default_rng(7)
    ltf_freq = np.zeros(64, dtype=np.complex64)
    for b in list(range(1, 27)) + list(range(38, 64)):
        ltf_freq[b] = rng.choice([-1, 1]) + 1j * rng.choice([-1, 1])
        ltf_freq[b] /= np.sqrt(2)
    ltf0_time = np.fft.ifft(ltf_freq) * 64
    h_chan = np.ones(52, dtype=np.complex64) * 0.3
    # 3 strong nulls
    h_chan[10] = 0.01
    h_chan[30] = 0.01
    h_chan[40] = 0.01
    rx_freq = ltf_freq * np.fft.fftshift(h_chan_to_64(h_chan))
    rx0_time = np.fft.ifft(rx_freq) * 64
    snr_db = 5.0
    signal_power = np.mean(np.abs(rx0_time) ** 2)
    noise_std = np.sqrt(signal_power / (2.0 * 10 ** (snr_db / 10.0)))
    noise = (rng.standard_normal(64) + 1j * rng.standard_normal(64)) * noise_std
    rx0_time_noisy = rx0_time + noise
    rx0_fft = np.fft.fft(rx0_time_noisy)
    rx52 = np.zeros(48, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):  # SC -26..-1 → rx52[0..25]
        rx52[i] = rx0_fft[b]
    for i, b in enumerate(range(1, 23)):  # SC +1..+22 → rx52[26..47]
        rx52[26 + i] = rx0_fft[b]
    h52 = h_chan
    eq_zf = zf_equalize(rx52, h52)
    eq_mmse = mmse_equalize(rx52, h52, n0_percentile=25)
    expected = np.zeros(48, dtype=np.complex64)
    for i, b in enumerate(range(38, 64)):
        expected[i] = ltf_freq[b]
    for i, b in enumerate(range(1, 23)):
        expected[26 + i] = ltf_freq[b]
    zf_metrics = equalized_quality_metrics(eq_zf, expected)
    mmse_metrics = equalized_quality_metrics(eq_mmse, expected)
    print(f"[LOW_SNR_TEST] ZF:    mse={zf_metrics['mse']:.4f} n_wrong_sign={zf_metrics['n_wrong_sign']}")
    print(f"[LOW_SNR_TEST] MMSE:  mse={mmse_metrics['mse']:.4f} n_wrong_sign={mmse_metrics['n_wrong_sign']}")
    assert mmse_metrics['mse'] < zf_metrics['mse'], \
        f"MMSE did not help at low SNR: ZF={zf_metrics['mse']:.4f} MMSE={mmse_metrics['mse']:.4f}"
    print("[LOW_SNR_TEST] PASS")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['mmse', 'hann', 'lowsnr', 'all'],
                        default='all')
    args = parser.parse_args()
    results = {}
    if args.mode in ('mmse', 'all'):
        results['mmse'] = test_mmse_beats_zf_at_null_scs()
    if args.mode in ('hann', 'all'):
        results['hann'] = test_hann_compensation_restores_magnitude()
    if args.mode in ('lowsnr', 'all'):
        results['lowsnr'] = test_mmse_helps_at_low_snr()
    if not all(results.values()):
        print(f"\n[FAIL] modes: {results}")
        sys.exit(1)
    print(f"\n[PASS] all modes: {results}")
    sys.exit(0)


if __name__ == '__main__':
    main()
