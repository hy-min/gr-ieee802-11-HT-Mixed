#!/usr/bin/env python3
"""
Compare [H52_DUMP] (pre-filter) vs [H52_DUMP_FILTERED] (post-filter) in a USRP log.

Reports:
  - Per-frame std before vs after filter
  - Per-SC std before vs after filter
  - Aggregate reduction ratio
  - Verdict: FILTER_EFFECTIVE / FILTER_NO_EFFECT / FILTER_HARMFUL / INSUFFICIENT_DATA

Usage:
  python examples/test_h_median_filter_pre_post.py <log_path>
  python examples/test_h_median_filter_pre_post.py <log_path> --threshold 0.5
"""

import argparse
import math
import re
import sys


# Match both [H52_DUMP] and [H52_DUMP_FILTERED]; capture the prefix to distinguish.
# Format: [H52_DUMP] counter=42 |H|=0.123,0.456,... arg(H)=0.123,0.456,... mean|H|=... std|H|=...
H52_RE = re.compile(
    r'\[H52_DUMP(_FILTERED)?\]\s+counter=(\d+)\s+\|H\|=([\d\.\-,]+)\s+'
    r'arg\(H\)=([\d\.\-,]+)\s+'
    r'mean\|H\|=(-?[\d\.]+)\s+std\|H\|=(-?[\d\.]+)\s+'
    r'mean\(argH\)=(-?[\d\.]+)\s+std\(argH\)=(-?[\d\.]+)'
)


def parse_dumps(log_path):
    """Yield (is_filtered, counter, mag_list) for each well-formed dump line.

    Yields in order of appearance. The same file can have both [H52_DUMP] and
    [H52_DUMP_FILTERED] lines; caller pairs by index.
    """
    parsed = 0
    skipped = 0
    with open(log_path, 'r', errors='replace') as f:
        for line in f:
            m = H52_RE.search(line)
            if not m:
                continue
            is_filtered = (m.group(1) == '_FILTERED')
            counter = int(m.group(2))
            mag_str = m.group(3).rstrip(',')
            arg_str = m.group(4).rstrip(',')
            try:
                mag = [float(x) for x in mag_str.split(',') if x]
            except ValueError:
                skipped += 1
                continue
            if len(mag) != 52:
                skipped += 1
                continue
            parsed += 1
            yield is_filtered, counter, mag
    print(f'[{log_path}] parsed={parsed} skipped={skipped}', file=sys.stderr)


def per_frame_std(mags):
    """std of 52 magnitudes (variance within one frame)."""
    n = len(mags)
    if n == 0:
        return 0.0
    mean = sum(mags) / n
    var = sum((v - mean) ** 2 for v in mags) / n
    return math.sqrt(var) if var > 0 else 0.0


def per_sc_mean_std(per_sc_frames):
    """Given a list of 52-element lists, return list of (mean, std) per SC."""
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


def main():
    parser = argparse.ArgumentParser(
        description='Compare pre/post H52 dumps from a USRP log to assess '
                    'the median filter effectiveness.')
    parser.add_argument('log_path', help='USRP log with [H52_DUMP] and [H52_DUMP_FILTERED]')
    parser.add_argument('--threshold', type=float, default=0.5,
                        help='Min reduction ratio (post/pre std) for FILTER_EFFECTIVE verdict '
                             '(default 0.5; i.e. ratio >= 1.5x is EFFECTIVE)')
    args = parser.parse_args()

    # Parse both
    pre_mags = []
    post_mags = []
    for is_filtered, counter, mag in parse_dumps(args.log_path):
        if is_filtered:
            post_mags.append(mag)
        else:
            pre_mags.append(mag)

    if not pre_mags or not post_mags:
        print(f'ERROR: pre_mags={len(pre_mags)}, post_mags={len(post_mags)}; both needed',
              file=sys.stderr)
        print('VERDICT: INSUFFICIENT_DATA')
        return 1

    # Pair by index (both dumps happen per frame in order)
    n = min(len(pre_mags), len(post_mags))
    pre_mags = pre_mags[:n]
    post_mags = post_mags[:n]

    if len(pre_mags) != len(post_mags):
        print(f'WARNING: pre/post count mismatch ({len(pre_mags)} vs {len(post_mags)}); '
              f'using first {n}', file=sys.stderr)

    # Per-frame std comparison
    pre_stds = [per_frame_std(f) for f in pre_mags]
    post_stds = [per_frame_std(f) for f in post_mags]
    avg_pre_std = sum(pre_stds) / n
    avg_post_std = sum(post_stds) / n
    reduction_ratio = avg_pre_std / max(avg_post_std, 1e-9)

    # Per-SC std comparison
    pre_per_sc = per_sc_mean_std(pre_mags)
    post_per_sc = per_sc_mean_std(post_mags)

    print(f'Frames compared: {n}')
    print(f'  pre-filter  per-frame std_avg: {avg_pre_std:.3f}')
    print(f'  post-filter per-frame std_avg: {avg_post_std:.3f}')
    print(f'  reduction ratio: {reduction_ratio:.2f}x')
    print()
    print('Per-SC std (sample, every ~10 SCs):')
    print('  SC | pre std | post std | reduction')
    sample_scs = list(range(0, 52, 10)) + [51]
    for k in sorted(set(sample_scs)):
        p_std = pre_per_sc[k][1]
        q_std = post_per_sc[k][1]
        r = p_std / max(q_std, 1e-9)
        print(f'  {k:3d} | {p_std:7.3f} | {q_std:7.3f}  | {r:5.2f}x')
    print()

    if reduction_ratio >= (1.0 + args.threshold):
        verdict = 'FILTER_EFFECTIVE'
    elif reduction_ratio >= 0.8:
        verdict = 'FILTER_NO_EFFECT'
    else:
        verdict = 'FILTER_HARMFUL'

    print(f'VERDICT: {verdict} (reduction={reduction_ratio:.2f}x, threshold={args.threshold}x)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
