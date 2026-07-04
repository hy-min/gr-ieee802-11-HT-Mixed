#!/usr/bin/env python3
"""Phase 88 T2b: autocorrelation profile around Python L-STF positions.

C++ sync_short uses threshold=0.01 (sensitivity=0.01 in p68).
C++ MIN_PLATEAU=2: needs 2 consecutive samples above threshold.

If C++ doesn't detect L-STF, possible reasons:
  - The plateau > 0.01 is too short (< 2 consecutive samples)
  - The samples are 49 samples AFTER Python's rising edge
  - C++ might have a different correlation smoothing window

Let's measure:
  - Length of plateau > 0.01 around L-STF
  - Length of plateau > 0.05 around L-STF
  - Length of plateau > 0.062 (Python's threshold) around L-STF
"""
import numpy as np
import sys


CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'


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
    return np.array(starts)


def compute_smoothed_autocorr(iq, chunk_size=10_000_000, period=16, win=16):
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
    print("[P88-T2b] Loading...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    py_positions = find_l_stf_starts(iq)
    print(f"[P88-T2b] Python L-STF starts: {len(py_positions)}")

    print("[P88-T2b] Computing autocorrelation...")
    corr = compute_smoothed_autocorr(iq)

    # For each Python L-STF, find plateau lengths above each threshold
    thresholds = [0.01, 0.05, 0.062, 0.1, 0.5]

    print(f"\n[P88-T2b] === Plateau lengths above each threshold (first 10 L-STF) ===")
    print(f"  {'L-STF pos':>12}  ", end='')
    for thr in thresholds:
        print(f"plat>{thr:>5} ", end='')
    print()

    plateau_stats = {thr: [] for thr in thresholds}
    for py_pos in py_positions[:20]:
        # Look in window [py_pos-50, py_pos+200]
        window_start = max(0, py_pos - 50)
        window_end = min(len(corr), py_pos + 200)
        local = corr[window_start:window_end]
        line = f"  {py_pos:>12}  "
        for thr in thresholds:
            above = local > thr
            # Find longest contiguous run
            runs = []
            current = 0
            for v in above:
                if v:
                    current += 1
                else:
                    if current > 0:
                        runs.append(current)
                    current = 0
            if current > 0:
                runs.append(current)
            max_run = max(runs) if runs else 0
            plateau_stats[thr].append(max_run)
            line += f"{max_run:>9} "
        print(line)

    print(f"\n[P88-T2b] === Statistics over {len(plateau_stats[0.01])} L-STF ===")
    for thr in thresholds:
        arr = np.array(plateau_stats[thr])
        print(f"  thr>{thr}: min={arr.min()}, max={arr.max()}, mean={arr.mean():.1f}, "
              f"# <2: {(arr < 2).sum()}, # >=2: {(arr >= 2).sum()}")

    # Also check: when sync_short's state=COARSE flicker happens, what are the values?
    # Look at autocorrelation values in non-L-STF regions (between L-STF peaks)
    print(f"\n[P88-T2b] === Non-L-STF autocorr statistics (between peaks) ===")
    if len(py_positions) >= 2:
        for i in range(min(5, len(py_positions) - 1)):
            gap_start = py_positions[i] + 200  # past L-STF peak
            gap_end = py_positions[i + 1] - 200  # before next L-STF
            if gap_end > gap_start:
                gap_corr = corr[gap_start:gap_end]
                n_above_01 = int((gap_corr > 0.01).sum())
                n_above_05 = int((gap_corr > 0.05).sum())
                print(f"  Gap [{py_positions[i]}+200 .. {py_positions[i+1]}-200]: "
                      f"len={len(gap_corr)}  >0.01: {n_above_01} ({100*n_above_01/len(gap_corr):.3f}%)  "
                      f">0.05: {n_above_05}")


if __name__ == '__main__':
    main()