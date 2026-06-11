#!/usr/bin/env python3
"""
Stage 1 (reorganized) L-LTF0 FFT analysis for [LTF0_FFT_DUMP] from frame_equalizer.

Parses L-LTF0 FFT dumps from a log, computes per-subcarrier statistics, and
emits a verdict:
  STAGE_FINE / STAGE_BROKEN_TIMING / STAGE_BROKEN_GAIN / STAGE_BROKEN_FREQRESP / STAGE_AMBIGUOUS

Usage:
    python examples/test_ltf0_fft.py <log_path>
    python examples/test_ltf0_fft.py <log_path> --threshold 0.8
"""

import sys
import re
import math
import argparse

LTF0_FFT_DUMP_RE = re.compile(
    r'\[LTF0_FFT_DUMP\]\s+counter=(\d+)\s+\|LLTF\|=([\d\.\-,]+)\s+'
    r'arg\(LLTF\)=([\d\.\-,]+)\s+'
    r'mean\|LLTF\|=(-?[\d\.]+)\s+std\|LLTF\|=(-?[\d\.]+)'
)


def parse_log(path):
    parsed = 0
    skipped = 0
    with open(path, 'r', errors='replace') as f:
        for line in f:
            m = LTF0_FFT_DUMP_RE.search(line)
            if not m:
                continue
            counter = int(m.group(1))
            mag_str = m.group(2).rstrip(',')
            arg_str = m.group(3).rstrip(',')
            try:
                mag = [float(x) for x in mag_str.split(',') if x]
                arg = [float(x) for x in arg_str.split(',') if x]
            except ValueError:
                skipped += 1
                continue
            if len(mag) != 52 or len(arg) != 52:
                skipped += 1
                continue
            parsed += 1
            yield counter, mag, arg
    print(f'[{path}] parsed={parsed} skipped={skipped}', file=sys.stderr)


def aggregate_per_sc(per_sc_frames):
    n_sc = 52
    if not per_sc_frames:
        return [(0.0, 0.0)] * n_sc
    n_frames = len(per_sc_frames)
    result = []
    for k in range(n_sc):
        vals = [frame[k] for frame in per_sc_frames]
        mean = sum(vals) / n_frames
        var = sum((v - mean) ** 2 for v in vals) / n_frames
        std = math.sqrt(var) if var > 0 else 0.0
        result.append((mean, std))
    return result


def classify(per_sc_stats, per_frame_std_avg, threshold):
    n_sc = 52
    n_stable = sum(1 for _, s in per_sc_stats if s < 0.3)
    means = [m for m, _ in per_sc_stats]
    mag_mean = sum(means) / n_sc
    mag_max = max(means)
    mag_min = min(means)
    mag_range = mag_max - mag_min
    # kFftNormalize = 64/sqrt(52) ≈ 8.875. Loopback gives |LLTF|≈8.875 uniformly.
    # Gain range must accommodate this scale; check is "off-scale" (signal lost),
    # not "should be 1.0 normalized".
    gain_ok = 0.5 <= mag_mean <= 12.0
    n_uniform = sum(1 for m, _ in per_sc_stats if abs(m - mag_mean) < 0.2)

    stable_pct = n_stable / n_sc
    uniform_pct = n_uniform / n_sc

    if per_frame_std_avg > 1.0:
        return 'STAGE_AMBIGUOUS', f'per-frame std_avg={per_frame_std_avg:.3f} > 1.0'

    if not gain_ok and stable_pct >= threshold and uniform_pct >= threshold:
        return 'STAGE_BROKEN_GAIN', f'mean|LLTF|={mag_mean:.3f} (off-scale 0.5-2.0)'

    if stable_pct < threshold and uniform_pct < threshold:
        return 'STAGE_BROKEN_FREQRESP', f'uniform SCs {uniform_pct*100:.1f}% < {threshold*100:.0f}%, range={mag_range:.3f}'

    if stable_pct < threshold:
        return 'STAGE_BROKEN_TIMING', f'stable SCs {stable_pct*100:.1f}% < {threshold*100:.0f}%'

    if uniform_pct < threshold:
        return 'STAGE_BROKEN_FREQRESP', f'uniform SCs {uniform_pct*100:.1f}% < {threshold*100:.0f}%'

    return 'STAGE_FINE', f'all metrics pass (mean|LLTF|={mag_mean:.3f}, range={mag_range:.3f})'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log_path')
    parser.add_argument('--threshold', type=float, default=0.8)
    args = parser.parse_args()

    frames = []
    for _, mag, _ in parse_log(args.log_path):
        frames.append(mag)

    if not frames:
        print('ERROR: no [LTF0_FFT_DUMP] lines in log', file=sys.stderr)
        return 1

    print(f'Frames: {len(frames)}')
    per_sc_stats = aggregate_per_sc(frames)

    per_frame_stds = []
    for frame in frames:
        m = sum(frame) / 52
        s = math.sqrt(sum((v - m) ** 2 for v in frame) / 52)
        per_frame_stds.append(s)
    per_frame_std_avg = sum(per_frame_stds) / len(per_frame_stds)

    print('Per-SC statistics (sample):')
    print('  SC | mean|LLTF| | std|LLTF|')
    for k in [0, 1, 2, 25, 26, 49, 50, 51]:
        m, s = per_sc_stats[k]
        print(f'  {k:3d} | {m:.3f}     | {s:.3f}')
    print('  ... (omitted middle SCs)')
    print()

    verdict, reason = classify(per_sc_stats, per_frame_std_avg, args.threshold)
    print(f'Verdict: {verdict}')
    print(f'  Reason: {reason}')
    print(f'  per-frame std_avg: {per_frame_std_avg:.3f}')

    means = [m for m, _ in per_sc_stats]
    print(f'  mean|LLTF| mean: {sum(means)/52:.3f}')
    print(f'  mean|LLTF| range: [{min(means):.3f}, {max(means):.3f}]')
    return 0


if __name__ == '__main__':
    sys.exit(main())
