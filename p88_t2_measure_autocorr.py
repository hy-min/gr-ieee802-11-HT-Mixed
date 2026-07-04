#!/usr/bin/env python3
"""Phase 88 T2: measure autocorrelation statistics in /tmp/p28_loopback_iq.fc32.

The C++ sync_short uses threshold=0.01 (sensitivity=0.01 from p68_replay_offline.py).
Python uses threshold = max(median * 10, 0.01).

If C++ doesn't detect L-STF but Python does, the question is:
- What is the smoothed period-16 autocorrelation peak at real L-STF positions?
- What is the median in non-L-STF regions?
- Is the peak > 0.01? If yes, why does C++ still fail?
- Is the peak < 0.01? If yes, the threshold is too high.

Method:
  1. Compute period-16 autocorrelation for the entire capture.
  2. Smooth with period-16 boxcar (matching C++ in_cor computation).
  3. At each Python-detected L-STF position, find the peak autocorrelation
     in a ±100 sample window.
  4. Compute median autocorrelation in non-L-STF regions.
  5. Compare: peak vs threshold (0.01).
"""
import numpy as np
import sys


CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

# Reuse L-STF detection
def find_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000,
                     threshold_factor=10.0):
    n = len(iq)
    period = 16; win = 16; starts = []
    last_peak_pos = -min_distance
    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]; b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        median_corr = float(np.median(corr_smooth))
        threshold = max(median_corr * threshold_factor, 0.01)
        above = corr_smooth > threshold
        rising_edges = np.where(np.diff(above.astype(np.int32)) == 1)[0]
        for r in rising_edges:
            abs_pos = chunk_start + int(r)
            if abs_pos - last_peak_pos >= min_distance:
                starts.append(abs_pos)
                last_peak_pos = abs_pos
        del chunk, a, b, corr_raw, corr_smooth, above
    return np.array(starts), threshold


def compute_smoothed_autocorr(iq, chunk_size=10_000_000, period=16, win=16):
    """Compute period-16 smoothed autocorrelation, return values + global median."""
    n = len(iq)
    all_corrs = []
    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]; b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        all_corrs.append(corr_smooth)
        del chunk, a, b, corr_raw, corr_smooth
    return np.concatenate(all_corrs)


def main():
    print("[P88-T2] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P88-T2] Total samples: {len(iq)}")

    print("[P88-T2] Finding L-STF starts (Python detection)...")
    py_positions, py_threshold = find_l_stf_starts(iq)
    print(f"[P88-T2] Python L-STF starts: {len(py_positions)}, threshold={py_threshold:.4f}")

    print("[P88-T2] Computing smoothed autocorrelation (this may take a while)...")
    corr_smooth = compute_smoothed_autocorr(iq)
    print(f"[P88-T2] Autocorrelation length: {len(corr_smooth)}")

    # Global statistics
    median_corr = float(np.median(corr_smooth))
    print(f"\n[P88-T2] === Global autocorrelation statistics ===")
    print(f"  median: {median_corr:.4f}")
    print(f"  max:    {corr_smooth.max():.4f}")
    print(f"  min:    {corr_smooth.min():.4f}")
    print(f"  mean:   {corr_smooth.mean():.4f}")
    print(f"  std:    {corr_smooth.std():.4f}")

    # Check how much > 0.01
    n_above_01 = int((corr_smooth > 0.01).sum())
    n_above_05 = int((corr_smooth > 0.05).sum())
    n_above_10 = int((corr_smooth > 0.10).sum())
    n_above_50 = int((corr_smooth > 0.50).sum())
    print(f"  > 0.01: {n_above_01} ({100*n_above_01/len(corr_smooth):.2f}%)")
    print(f"  > 0.05: {n_above_05} ({100*n_above_05/len(corr_smooth):.2f}%)")
    print(f"  > 0.10: {n_above_10} ({100*n_above_10/len(corr_smooth):.2f}%)")
    print(f"  > 0.50: {n_above_50} ({100*n_above_50/len(corr_smooth):.2f}%)")

    # At each Python L-STF position, find peak autocorrelation in ±100 sample window
    print(f"\n[P88-T2] === Autocorrelation peak at Python L-STF positions ===")
    # corr_smooth index matches sample index since period=16 and kern=16 means
    # corr_smooth[i] uses samples [i:i+32], so position of sample is offset by ~16
    # Use position from L-STF detection as-is (slight offset is acceptable)

    peaks_at_l_stf = []
    for py_pos in py_positions[:20]:  # first 20 only for speed
        # Look in window [py_pos-50, py_pos+50]
        window_start = max(0, py_pos - 50)
        window_end = min(len(corr_smooth), py_pos + 50)
        local_corr = corr_smooth[window_start:window_end]
        peak = float(local_corr.max())
        peak_pos = window_start + int(np.argmax(local_corr))
        peaks_at_l_stf.append(peak)
        if len(peaks_at_l_stf) <= 5:
            print(f"  L-STF @ {py_pos:>10}: peak autocorr = {peak:.4f} (at sample {peak_pos}, "
                  f"delta = {peak_pos - py_pos})")

    if len(peaks_at_l_stf) > 5:
        peaks_arr = np.array(peaks_at_l_stf)
        print(f"  ... (skipping {len(peaks_at_l_stf) - 5} more)")
        print(f"\n[P88-T2] Statistics over first {len(peaks_arr)} L-STF peaks:")
        print(f"  min:  {peaks_arr.min():.4f}")
        print(f"  max:  {peaks_arr.max():.4f}")
        print(f"  mean: {peaks_arr.mean():.4f}")
        print(f"  median: {np.median(peaks_arr):.4f}")
        print(f"  # peaks < 0.01: {(peaks_arr < 0.01).sum()}/{len(peaks_arr)}")
        print(f"  # peaks < 0.05: {(peaks_arr < 0.05).sum()}/{len(peaks_arr)}")
        print(f"  # peaks > 0.10: {(peaks_arr > 0.10).sum()}/{len(peaks_arr)}")
        print(f"  # peaks > 0.50: {(peaks_arr > 0.50).sum()}/{len(peaks_arr)}")


if __name__ == '__main__':
    main()