#!/usr/bin/env python3
"""
Offline analysis for [PHASE_RESIDUAL] dumps from frame_equalizer.

Parses phase residual dumps, computes per-frame statistics, and classifies
each frame as CLEAN_MODEL / COMMON_PHASE_ERROR / MODEL_INCOMPLETE / NOISE_LIKE.

Usage:
    python examples/test_phase_residual_offline.py /tmp/usrp_phase_residual.log

Output:
    Per-frame verdict to stdout, plus aggregate summary at the end.

See spec: docs/superpowers/specs/2026-06-10-phase-noise-lsig-design.md
"""

import sys
import re
import math
import argparse
from collections import defaultdict

PHASE_RESIDUAL_RE = re.compile(
    r'\[PHASE_RESIDUAL\]\s+counter=(\d+)\s+eq_phase=([\d\.\-,]+)\s+mean=(-?[\d\.]+)\s+std=(-?[\d\.]+)'
)


def parse_log(path):
    """Yield (counter, mean_phase, std_phase, per_sc_phases_list) per line."""
    with open(path, 'r', errors='replace') as f:
        for line in f:
            m = PHASE_RESIDUAL_RE.search(line)
            if not m:
                continue
            counter = int(m.group(1))
            phases_str = m.group(2).rstrip(',')
            try:
                per_sc = [float(x) for x in phases_str.split(',') if x]
            except ValueError:
                continue
            try:
                mean = float(m.group(3))
                std = float(m.group(4))
            except ValueError:
                continue
            yield counter, mean, std, per_sc


def classify_frame(mean_phase, std_phase, per_sc_phases):
    """Classify a single frame's phase residual.

    Returns one of: CLEAN_MODEL, COMMON_PHASE_ERROR, MODEL_INCOMPLETE, NOISE_LIKE.
    """
    # NOISE_LIKE: histogram of phases is roughly uniform across [-pi, +pi]
    if len(per_sc_phases) >= 24:
        bins = [0] * 8  # 8 bins across [-pi, +pi]
        for p in per_sc_phases:
            idx = min(7, max(0, int((p + math.pi) / (2 * math.pi) * 8)))
            bins[idx] += 1
        expected = len(per_sc_phases) / 8
        chi2 = sum((b - expected) ** 2 / expected for b in bins) if expected > 0 else 0
        # chi2 critical at 7 df, p=0.05 is 14.07
        if chi2 < 14.07 and std_phase > 0.5:
            return 'NOISE_LIKE'

    # CLEAN_MODEL: small mean and small std
    if abs(mean_phase) < 0.1 and std_phase < 0.3:
        return 'CLEAN_MODEL'

    # COMMON_PHASE_ERROR: small std but mean is non-trivial
    if std_phase < 0.3:
        return 'COMMON_PHASE_ERROR'

    # MODEL_INCOMPLETE: large std
    return 'MODEL_INCOMPLETE'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log_path', help='Path to USRP log with [PHASE_RESIDUAL] lines')
    args = parser.parse_args()

    verdicts = defaultdict(int)
    frames = []
    for counter, mean, std, per_sc in parse_log(args.log_path):
        verdict = classify_frame(mean, std, per_sc)
        verdicts[verdict] += 1
        frames.append((counter, mean, std, verdict))

    print(f'Total frames analyzed: {len(frames)}')
    print('Verdict distribution:')
    for v, c in sorted(verdicts.items(), key=lambda x: -x[1]):
        pct = 100.0 * c / max(1, len(frames))
        print(f'  {v:25s}: {c:4d} ({pct:5.1f}%)')

    if frames:
        mean_means = [f[1] for f in frames]
        mean_stds = [f[2] for f in frames]
        agg_mean = sum(mean_means) / len(mean_means)
        agg_std = sum(mean_stds) / len(mean_stds)
        print(f'\nAggregate over {len(frames)} frames:')
        print(f'  mean of mean_phase: {agg_mean:+.3f} rad')
        print(f'  mean of std_phase:  {agg_std:.3f} rad')

    return 0


if __name__ == '__main__':
    sys.exit(main())
