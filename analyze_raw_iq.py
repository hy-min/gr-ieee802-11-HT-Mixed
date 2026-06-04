#!/home/hy/conda/envs/gnuradio/bin/python
"""
Offline analysis of USRP captured raw IQ samples.
Simulates sync_short correlation to diagnose frame detection issues.
Usage: python analyze_raw_iq.py /tmp/usrp_rx_capture.fc32
"""
import argparse
import numpy as np
import struct

def read_fc32(filename, max_samples=None):
    """Read fc32 (complex float32) file."""
    with open(filename, 'rb') as f:
        data = f.read()
    nsamples = len(data) // 8
    if max_samples:
        nsamples = min(nsamples, max_samples)
    samples = np.frombuffer(data[:nsamples*8], dtype=np.complex64)
    return samples

def moving_average(x, window_size):
    """Compute moving average."""
    return np.convolve(x, np.ones(window_size)/window_size, mode='valid')

def auto_correlation(samples, window_size=48):
    """
    Simulate sync_short auto-correlation detector.
    Returns: (correlation, power, normalized_correlation)
    """
    n = len(samples)
    # Delayed conjugate multiply (like sync_short)
    delayed = samples[16:]
    conj_prod = samples[:-16] * np.conj(delayed)
    
    # Moving average over window_size
    ma_conj = moving_average(conj_prod, window_size)
    ma_power = moving_average(np.abs(samples[:-16])**2, window_size)
    ma_delayed_power = moving_average(np.abs(delayed)**2, window_size)
    
    # Normalized correlation
    corr = np.abs(ma_conj)
    denom = np.sqrt(ma_power * ma_delayed_power) + 1e-12
    norm_corr = corr / denom
    
    return corr, ma_power, norm_corr

def analyze_signal(samples, sample_rate=20e6):
    """Analyze signal characteristics."""
    print("=" * 60)
    print("RAW IQ ANALYSIS REPORT")
    print("=" * 60)
    print(f"Total samples: {len(samples)}")
    print(f"Duration: {len(samples)/sample_rate:.3f} s")
    
    # Basic stats
    power = np.abs(samples)**2
    mean_power = np.mean(power)
    max_power = np.max(power)
    min_power = np.min(power)
    std_power = np.std(power)
    
    print(f"\n--- Power Statistics ---")
    print(f"Mean power:   {mean_power:.6f} ({10*np.log10(mean_power+1e-12):.1f} dB)")
    print(f"Max power:    {max_power:.6f} ({10*np.log10(max_power+1e-12):.1f} dB)")
    print(f"Min power:    {min_power:.6f}")
    print(f"Std power:    {std_power:.6f}")
    print(f"Peak/Mean:    {max_power/mean_power:.1f} ({10*np.log10(max_power/mean_power):.1f} dB)")
    
    # Check for clipping (saturation)
    # UBX with gain 20 might clip if strong signal
    peak_amp = np.max(np.abs(samples))
    if peak_amp > 0.95:
        print(f"\n⚠️  WARNING: Possible clipping detected! Peak amplitude = {peak_amp:.3f}")
        clip_count = np.sum(np.abs(samples) > 0.95)
        print(f"   Samples > 0.95: {clip_count} ({clip_count/len(samples)*100:.3f}%)")
    
    # Compute auto-correlation (sync_short simulation)
    print(f"\n--- Auto-Correlation Analysis (sync_short simulation) ---")
    corr, ma_power, norm_corr = auto_correlation(samples)
    
    print(f"Correlation array length: {len(norm_corr)}")
    print(f"Max normalized corr: {np.max(norm_corr):.4f}")
    print(f"Mean normalized corr: {np.mean(norm_corr):.4f}")
    print(f"Std normalized corr: {np.std(norm_corr):.4f}")
    
    # Check against threshold
    threshold = 0.01  # same as test_mcs_usrp.py sensitivity
    above_thresh = np.sum(norm_corr > threshold)
    print(f"\nSamples above threshold {threshold}: {above_thresh} ({above_thresh/len(norm_corr)*100:.2f}%)")
    
    # Find correlation peaks
    peaks = []
    for i in range(1, len(norm_corr)-1):
        if norm_corr[i] > norm_corr[i-1] and norm_corr[i] > norm_corr[i+1] and norm_corr[i] > threshold * 2:
            peaks.append((i, norm_corr[i]))
    
    # Sort by correlation value
    peaks.sort(key=lambda x: x[1], reverse=True)
    print(f"\nTop 10 correlation peaks:")
    for i, (idx, val) in enumerate(peaks[:10]):
        time_ms = idx / sample_rate * 1000
        print(f"  #{i+1}: idx={idx:8d}  corr={val:.4f}  time={time_ms:.3f}ms")
    
    # Energy distribution over time (1ms windows)
    window_samples = int(sample_rate * 0.001)  # 1ms
    n_windows = len(samples) // window_samples
    window_powers = []
    for i in range(n_windows):
        start = i * window_samples
        end = start + window_samples
        window_powers.append(np.mean(np.abs(samples[start:end])**2))
    
    print(f"\n--- Energy Distribution (1ms windows) ---")
    wp = np.array(window_powers)
    print(f"Window power mean: {np.mean(wp):.6f}")
    print(f"Window power std:  {np.std(wp):.6f}")
    print(f"Window power max:  {np.max(wp):.6f}")
    print(f"Window power min:  {np.min(wp):.6f}")
    
    # Detect quiet periods (potential frame gaps)
    quiet_thresh = np.mean(wp) * 0.5
    quiet_windows = np.sum(wp < quiet_thresh)
    print(f"Quiet windows (< {quiet_thresh:.6f}): {quiet_windows}/{n_windows} ({quiet_windows/n_windows*100:.1f}%)")
    
    # Check DC offset
    dc = np.mean(samples)
    dc_power_ratio = np.abs(dc)**2 / mean_power
    print(f"\n--- DC Analysis ---")
    print(f"DC offset: {dc}")
    print(f"DC/Signal power ratio: {dc_power_ratio:.6f} ({10*np.log10(dc_power_ratio+1e-12):.1f} dBc)")
    if dc_power_ratio > 0.01:
        print("⚠️  WARNING: High DC offset detected!")
    
    # Spectrum analysis (simple FFT)
    print(f"\n--- Spectrum Snapshot ---")
    fft_size = 1024
    n_ffts = min(100, len(samples) // fft_size)
    spectrum = np.zeros(fft_size)
    for i in range(n_ffts):
        sig = samples[i*fft_size:(i+1)*fft_size]
        spectrum += np.abs(np.fft.fftshift(np.fft.fft(sig)))**2
    spectrum /= n_ffts
    
    # Find in-band power vs out-of-band
    center_bin = fft_size // 2
    inband_bins = fft_size // 5  # ~20% of bandwidth
    inband_power = np.sum(spectrum[center_bin-inband_bins:center_bin+inband_bins])
    total_power = np.sum(spectrum)
    print(f"In-band power ratio: {inband_power/total_power:.3f}")
    
    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE")
    print("=" * 60)
    
    return {
        'mean_power': mean_power,
        'max_power': max_power,
        'peak_amp': peak_amp,
        'max_corr': np.max(norm_corr),
        'n_peaks': len(peaks),
        'dc_ratio': dc_power_ratio,
    }

def main():
    parser = argparse.ArgumentParser(description='Analyze USRP captured raw IQ')
    parser.add_argument('file', help='Input fc32 file')
    parser.add_argument('--rate', type=float, default=20e6, help='Sample rate in Hz')
    parser.add_argument('--max-samples', type=int, default=None, help='Max samples to read')
    args = parser.parse_args()
    
    print(f"Reading {args.file}...")
    samples = read_fc32(args.file, args.max_samples)
    
    if len(samples) == 0:
        print("ERROR: No samples read!")
        return
    
    analyze_signal(samples, args.rate)

if __name__ == '__main__':
    main()
