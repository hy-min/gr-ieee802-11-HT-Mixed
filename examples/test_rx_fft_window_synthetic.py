#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 71 synthetic test: Compare rectangular vs Hann windowing for L-LTF
channel estimation.

Hypothesis: Rectangular window's -13 dB sidelobes cause inter-bin spectral
leakage that inflates H52 null magnitudes. Hann window's -31 dB sidelobes
should reduce leakage and improve H52 quality.

The test generates a synthetic 802.11n L-LTF time-domain signal, applies a
noisy channel, and compares the channel estimate quality (avg |H|, min |H|,
n_nulls) under both windows.

Reference: docs/superpowers/plans/2026-07-01-phase71-h52-hann-window.md
"""
import argparse
import sys
import numpy as np


def generate_l_ltf_time_domain(num_samples=64, snr_db=20.0, phase_offset_rad=0.0,
                              cfo_offset_bins=0.0, seed=42):
    """Generate a synthetic L-LTF0 time-domain signal.

    L-LTF is the LEGACY_LTF sequence (per 802.11n standard) used for channel
    estimation. Real L-LTF puts equal-magnitude symbols on all 52 active
    subcarriers (SC -26..-1 and +1..+26). We approximate it by summing
    sinusoids across all 52 active bins with random BPSK-style phases, so
    that every active SC carries signal energy (the same property real L-LTF
    has). This makes H52 quality a meaningful test under noisy channels.

    Args:
        cfo_offset_bins: fractional-bin offset to simulate CFO / timing
            drift (Phase 33b USRP finding). Default 0.0 puts tones exactly
            on FFT bin centers (no inter-bin leakage). With nonzero
            offset, sidelobe performance of the windowing function
            (rect vs Hann) becomes observable.

    Returns:
        time_samples: 64 complex time-domain samples (1 OFDM symbol).
    """
    rng = np.random.default_rng(seed)
    t = np.arange(num_samples)
    # Sum equal-magnitude sinusoids on all 52 active subcarriers
    # (natural-order bin indices: 38..63 and 1..26)
    active_bins = np.concatenate([np.arange(38, 64), np.arange(1, 27)])
    signal = np.zeros(num_samples, dtype=np.complex128)
    for k in active_bins:
        # Random QPSK-style phase per SC (BPSK +/-1 real, but use complex
        # phase for general applicability)
        phase = rng.uniform(0, 2 * np.pi)
        effective_bin = k + cfo_offset_bins
        signal += np.exp(1j * (2 * np.pi * effective_bin * t / 64.0 + phase))
    # Apply common phase offset
    signal *= np.exp(1j * phase_offset_rad)
    # Add AWGN
    snr_linear = 10 ** (snr_db / 10.0)
    noise_std = np.sqrt(np.mean(np.abs(signal) ** 2)) / np.sqrt(2.0 * snr_linear)
    noise = (rng.standard_normal(num_samples) + 1j * rng.standard_normal(num_samples)) * noise_std
    return (signal + noise).astype(np.complex64)


def apply_window(time_samples, window_type='rectangular'):
    """Apply a window function to time-domain samples before FFT.

    Args:
        time_samples: 1D complex64 array of length 64.
        window_type: 'rectangular' or 'hann'.

    Returns:
        windowed_samples: 1D complex64 array of length 64.
    """
    n = len(time_samples)
    if window_type == 'rectangular':
        window = np.ones(n, dtype=np.float32)
    elif window_type == 'hann':
        window = np.hanning(n).astype(np.float32)
    else:
        raise ValueError(f"Unknown window type: {window_type}")
    return time_samples * window


def fft_64(samples):
    """Compute 64-point FFT (no normalization).

    Returns:
        fft_bins: 1D complex64 array of length 64 (natural order, no shift).
    """
    return np.fft.fft(samples)


def estimate_h52_from_fft(fft_bins):
    """Extract 52 active subcarriers from 64 FFT bins.

    802.11n uses 52 subcarriers: SC -26..-1 and SC +1..+26 (skip DC).
    Natural-order bin indices: bins 38..63 (-26..-1) and bins 1..26 (+1..+26).

    Returns:
        h52: 1D complex64 array of length 52.
    """
    h52 = np.zeros(52, dtype=np.complex64)
    # Negative subcarriers: SC -26 (bin 38) to SC -1 (bin 63)
    h52[0:26] = fft_bins[38:64]
    # Positive subcarriers: SC +1 (bin 1) to SC +26 (bin 26)
    h52[26:52] = fft_bins[1:27]
    return h52


def h52_quality_metrics(h52):
    """Compute H52 quality metrics.

    Returns:
        dict with keys: avg_abs, min_abs, n_nulls, std_arg
    """
    abs_h = np.abs(h52)
    arg_h = np.angle(h52)
    n_nulls = int(np.sum(abs_h < 0.1))  # threshold for "null"
    return {
        'avg_abs': float(np.mean(abs_h)),
        'min_abs': float(np.min(abs_h)),
        'n_nulls': n_nulls,
        'std_arg': float(np.std(arg_h)),
    }


def test_hann_window_reduces_n_nulls():
    """At low SNR with off-bin energy (simulating CFO/timing offset), Hann
    window should reduce the number of H52 nulls compared to rectangular.

    The rectangular window's high sidelobes cause inter-bin leakage that
    pushes nearby SC magnitudes below the null threshold. Hann's -31 dB
    sidelobes (vs -13 dB for rectangular) reduce this leakage.

    CRITICAL: this test uses cfo_offset_bins=0.3 to put tones off bin
    centers. Without this offset, all energy is exactly on FFT bins and
    windowing has no effect (trivially passes — fail-safe).
    """
    # Use a synthetic signal with fractional-bin offset to expose
    # inter-bin leakage differences between rectangular and Hann windows.
    samples = generate_l_ltf_time_domain(snr_db=10.0, phase_offset_rad=0.3,
                                        cfo_offset_bins=0.3)
    # Rectangular window
    rect_windowed = apply_window(samples, 'rectangular')
    rect_fft = fft_64(rect_windowed)
    rect_h52 = estimate_h52_from_fft(rect_fft)
    rect_metrics = h52_quality_metrics(rect_h52)
    # Hann window
    hann_windowed = apply_window(samples, 'hann')
    hann_fft = fft_64(hann_windowed)
    hann_h52 = estimate_h52_from_fft(hann_fft)
    hann_metrics = h52_quality_metrics(hann_h52)
    print(f"[HANN_TEST] Rect: avg_abs={rect_metrics['avg_abs']:.4f} min_abs={rect_metrics['min_abs']:.4f} n_nulls={rect_metrics['n_nulls']}")
    print(f"[HANN_TEST] Hann: avg_abs={hann_metrics['avg_abs']:.4f} min_abs={hann_metrics['min_abs']:.4f} n_nulls={hann_metrics['n_nulls']}")
    # The actual hypothesis: Hann should produce same-or-fewer nulls than
    # rectangular when there is off-bin energy. May produce same n_nulls
    # if rect already has 0; but min_abs should remain comparable.
    assert hann_metrics['n_nulls'] <= rect_metrics['n_nulls'], \
        f"Hann has more nulls than rect: rect={rect_metrics['n_nulls']} hann={hann_metrics['n_nulls']}"
    # Hann's min_abs should remain at least 30% of rect's min_abs. Hann
    # has ~50% scalloping loss on its own main lobe, but with sidelobe
    # reduction the net effect on min_abs is favorable at moderate SNR.
    assert hann_metrics['min_abs'] >= rect_metrics['min_abs'] * 0.3, \
        f"Hann min_abs collapsed vs rect: rect={rect_metrics['min_abs']:.4f} hann={hann_metrics['min_abs']:.4f}"
    print("[HANN_TEST] PASS")
    return True


def test_hann_window_preserves_signal_at_high_snr():
    """At high SNR (clean channel), Hann window should not catastrophically
    distort the H52 estimate. The min |H| should remain reasonable."""
    samples = generate_l_ltf_time_domain(snr_db=30.0, phase_offset_rad=0.0)
    rect_h52 = estimate_h52_from_fft(fft_64(apply_window(samples, 'rectangular')))
    hann_h52 = estimate_h52_from_fft(fft_64(apply_window(samples, 'hann')))
    rect_metrics = h52_quality_metrics(rect_h52)
    hann_metrics = h52_quality_metrics(hann_h52)
    print(f"[HIGH_SNR] Rect: avg_abs={rect_metrics['avg_abs']:.4f} min_abs={rect_metrics['min_abs']:.4f}")
    print(f"[HIGH_SNR] Hann: avg_abs={hann_metrics['avg_abs']:.4f} min_abs={hann_metrics['min_abs']:.4f}")
    # At 30 dB SNR, both should have non-zero |H| at all SCs
    assert hann_metrics['min_abs'] > 0.01, f"Hann collapsed at high SNR: min_abs={hann_metrics['min_abs']:.4f}"
    print("[HIGH_SNR] PASS")
    return True


def test_loopback_3_3_pass_with_hann_window():
    """Simulate the loopback test (no noise, perfect channel) and verify
    Hann window doesn't break it. This mirrors the loopback regression
    check that will be done in Task 3."""
    # At perfect SNR (100 dB = essentially no noise), H52 should be a clean estimate
    samples = generate_l_ltf_time_domain(snr_db=100.0, phase_offset_rad=0.0)
    rect_h52 = estimate_h52_from_fft(fft_64(apply_window(samples, 'rectangular')))
    hann_h52 = estimate_h52_from_fft(fft_64(apply_window(samples, 'hann')))
    # At perfect SNR, both should produce a clean H52 estimate with no nulls
    rect_metrics = h52_quality_metrics(rect_h52)
    hann_metrics = h52_quality_metrics(hann_h52)
    print(f"[LOOPBACK] Rect: avg_abs={rect_metrics['avg_abs']:.4f} min_abs={rect_metrics['min_abs']:.4f} n_nulls={rect_metrics['n_nulls']}")
    print(f"[LOOPBACK] Hann: avg_abs={hann_metrics['avg_abs']:.4f} min_abs={hann_metrics['min_abs']:.4f} n_nulls={hann_metrics['n_nulls']}")
    assert rect_metrics['n_nulls'] == 0, f"Rectangular has nulls at perfect SNR: {rect_metrics['n_nulls']}"
    assert hann_metrics['n_nulls'] == 0, f"Hann has nulls at perfect SNR: {hann_metrics['n_nulls']}"
    print("[LOOPBACK] PASS")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['hann', 'highsnr', 'loopback', 'all'],
                        default='all')
    args = parser.parse_args()
    results = {}
    if args.mode in ('hann', 'all'):
        results['hann'] = test_hann_window_reduces_n_nulls()
    if args.mode in ('highsnr', 'all'):
        results['highsnr'] = test_hann_window_preserves_signal_at_high_snr()
    if args.mode in ('loopback', 'all'):
        results['loopback'] = test_loopback_3_3_pass_with_hann_window()
    if not all(results.values()):
        print(f"\n[FAIL] modes: {results}")
        sys.exit(1)
    print(f"\n[PASS] all modes: {results}")
    sys.exit(0)


if __name__ == '__main__':
    main()
