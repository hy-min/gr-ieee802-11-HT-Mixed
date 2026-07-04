#!/usr/bin/env python3
"""Phase 87 T2b: compare Python L-STF positions vs C++ sync_long correlation search.

If C++'s frame_start_abs positions match Python's L-STF positions, then sync_long
correlation search is correctly tracking L-STF (and sync_short is just redundant).

If they DON'T match, then sync_long correlation search is producing false frames
that don't align with actual L-STF starts.
"""
import re
import numpy as np


def parse_splitter_log(log_path):
    """Extract unique (seq, frame_start_abs) pairs."""
    LINE_RE = re.compile(
        r'\[SPLITTER_TIMING\] seq=(\d+) frame_start_abs=(\d+)'
    )
    frames = {}
    with open(log_path) as f:
        for line in f:
            m = LINE_RE.search(line)
            if m:
                seq = int(m.group(1))
                fsa = int(m.group(2))
                if seq not in frames:
                    frames[seq] = fsa
    return np.array(sorted(frames.values()))


def find_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000,
                     threshold_factor=10.0):
    """Python L-STF detection (same as Phase 82)."""
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


def main():
    # C++ frame positions
    cpp_positions = parse_splitter_log('/tmp/p87_splitter_timing_5s.log')
    print(f"[P87-T2b] C++ splitter positions: {len(cpp_positions)} frames")
    print(f"  range: [{cpp_positions.min()}, {cpp_positions.max()}]")

    # Python L-STF positions
    print("[P87-T2b] Loading capture for Python L-STF detection...")
    iq = np.memmap('/tmp/p28_loopback_iq.fc32', dtype=np.complex64, mode='r')
    py_positions = find_l_stf_starts(iq)
    print(f"[P87-T2b] Python L-STF positions: {len(py_positions)} frames")
    print(f"  range: [{py_positions.min()}, {py_positions.max()}]")

    # Compare positions
    print(f"\n[P87-T2b] === Position comparison ===")
    print(f"  C++  positions (first 10): {cpp_positions[:10]}")
    print(f"  Python positions (first 10): {py_positions[:10]}")

    # Per-Python L-STF, find closest C++ position
    if len(py_positions) > 0 and len(cpp_positions) > 0:
        distances = []
        for py_pos in py_positions:
            closest_cpp = cpp_positions[np.argmin(np.abs(cpp_positions - py_pos))]
            dist = abs(closest_cpp - py_pos)
            distances.append(dist)
        distances = np.array(distances)
        print(f"\n[P87-T2b] === Distance from each Python L-STF to nearest C++ position ===")
        print(f"  mean: {distances.mean():.1f} samples")
        print(f"  std:  {distances.std():.1f}")
        print(f"  min:  {distances.min()}")
        print(f"  max:  {distances.max()}")
        print(f"  Within 100 samples: {(distances < 100).sum()}/{len(distances)}")
        print(f"  Within 1000 samples: {(distances < 1000).sum()}/{len(distances)}")

        # Histogram
        bins = [0, 100, 1000, 10000, 100000, 1e9]
        labels = ['<100', '<1k', '<10k', '<100k', '>=100k']
        for lo, hi, lbl in zip(bins[:-1], bins[1:], labels):
            cnt = ((distances >= lo) & (distances < hi)).sum()
            print(f"  {lbl}: {cnt}")

    # Look at first Python L-STF position vs C++'s first frame
    if len(py_positions) > 0:
        first_py = py_positions[0]
        print(f"\n[P87-T2b] === First Python L-STF: {first_py} ===")
        for i, cpp in enumerate(cpp_positions[:10]):
            delta = cpp - first_py
            print(f"  C++ seq={i+1:>3} frame_start_abs={cpp:>10} delta_from_py={delta:>+10}")

    # Check: does C++'s first frame align with Python's first L-STF?
    if len(py_positions) > 0 and len(cpp_positions) > 0:
        # Allow for 174-sample offset (sync_long FRAME_START_BASE)
        OFFSET = 174  # sync_long moves 174 samples after L-STF start
        py_with_offset = py_positions + OFFSET
        # Check first 5
        print(f"\n[P87-T2b] === Python L-STF + 174 vs C++ frame_start_abs ===")
        for i in range(min(5, len(py_positions))):
            py_pos_with_off = py_positions[i] + OFFSET
            # Find closest C++ position
            closest_cpp = cpp_positions[np.argmin(np.abs(cpp_positions - py_pos_with_off))]
            print(f"  py[{i}]={py_positions[i]} +174={py_pos_with_off} "
                  f"closest_cpp={closest_cpp} delta={closest_cpp - py_pos_with_off}")


if __name__ == '__main__':
    main()