#!/usr/bin/env python3
"""
Offline H52 comparison for [H52_DUMP] dumps from frame_equalizer.

Parses H52 dumps from two logs (e.g., loopback and USRP), computes per-SC
statistics, and emits a verdict:
  H_FINE / H_MAGNITUDE_BROKEN / H_PHASE_BROKEN / H_BOTH_BROKEN

Usage:
    python examples/test_h52_compare.py <loopback_log> <usrp_log>
    python examples/test_h52_compare.py <loopback_log> <usrp_log> --threshold 0.8

Output:
    Per-SC comparison table + aggregate verdict to stdout.

See spec: docs/superpowers/specs/2026-06-10-h52-diagnosis-design.md
"""

import sys
import re
import math
import argparse
from collections import defaultdict

# Match: [H52_DUMP] counter=N |H|=52floats arg(H)=52floats mean|H|=F std|H|=F mean(argH)=F std(argH)=F
H52_DUMP_RE = re.compile(
    r'\[H52_DUMP\]\s+counter=(\d+)\s+\|H\|=([\d\.\-,]+)\s+arg\(H\)=([\d\.\-,]+)\s+'
    r'mean\|H\|=(-?[\d\.]+)\s+std\|H\|=(-?[\d\.]+)\s+'
    r'mean\(argH\)=(-?[\d\.]+)\s+std\(argH\)=(-?[\d\.]+)'
)


def parse_log(path):
    """Yield (counter, per_sc_mag_list, per_sc_arg_list) per well-formed line."""
    parsed = 0
    skipped = 0
    with open(path, 'r', errors='replace') as f:
        for line in f:
            m = H52_DUMP_RE.search(line)
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
    """Given a list of 52-element lists, return per-SC (mean, std) tuples."""
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


def compute_diff(loopback_mag_stats, loopback_arg_stats,
                 usrp_mag_stats, usrp_arg_stats,
                 threshold):
    """Return (n_mag_ok, n_arg_ok, total_sc) given per-SC stats."""
    n_sc = 52
    n_mag_ok = 0
    n_arg_ok = 0
    for k in range(n_sc):
        lb_mag = loopback_mag_stats[k][0]
        us_mag = usrp_mag_stats[k][0]
        # Magnitude ratio: avoid div by zero
        if lb_mag > 1e-9 and 0.5 <= us_mag / lb_mag <= 2.0:
            n_mag_ok += 1
        elif lb_mag <= 1e-9 and us_mag < 0.01:
            n_mag_ok += 1
        # Phase diff: wrap-aware
        lb_arg = loopback_arg_stats[k][0]
        us_arg = usrp_arg_stats[k][0]
        diff = abs(us_arg - lb_arg)
        diff = min(diff, 2 * math.pi - diff)
        if diff < 0.5:
            n_arg_ok += 1
    return n_mag_ok, n_arg_ok, n_sc


def verdict(n_mag_ok, n_arg_ok, n_sc, threshold):
    """Return one of: H_FINE / H_MAGNITUDE_BROKEN / H_PHASE_BROKEN / H_BOTH_BROKEN."""
    mag_pct = n_mag_ok / n_sc
    arg_pct = n_arg_ok / n_sc
    mag_ok = mag_pct >= threshold
    arg_ok = arg_pct >= threshold
    if mag_ok and arg_ok:
        return 'H_FINE'
    elif not mag_ok and arg_ok:
        return 'H_MAGNITUDE_BROKEN'
    elif mag_ok and not arg_ok:
        return 'H_PHASE_BROKEN'
    else:
        return 'H_BOTH_BROKEN'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('loopback_log', help='Path to loopback H52 log')
    parser.add_argument('usrp_log', help='Path to USRP H52 log')
    parser.add_argument('--threshold', type=float, default=0.8,
                        help='Min fraction of SCs that must pass (default 0.8)')
    args = parser.parse_args()

    # Parse both logs
    loopback_mags = []
    loopback_args = []
    for _, mag, arg in parse_log(args.loopback_log):
        loopback_mags.append(mag)
        loopback_args.append(arg)

    usrp_mags = []
    usrp_args = []
    for _, mag, arg in parse_log(args.usrp_log):
        usrp_mags.append(mag)
        usrp_args.append(arg)

    if not loopback_mags:
        print('ERROR: no [H52_DUMP] lines in loopback log', file=sys.stderr)
        return 1
    if not usrp_mags:
        print('ERROR: no [H52_DUMP] lines in USRP log', file=sys.stderr)
        return 1

    print(f'Loopback: {len(loopback_mags)} frames')
    print(f'USRP:     {len(usrp_mags)} frames')
    print()

    # Aggregate per-SC stats
    lb_mag_stats = aggregate_per_sc(loopback_mags)
    lb_arg_stats = aggregate_per_sc(loopback_args)
    us_mag_stats = aggregate_per_sc(usrp_mags)
    us_arg_stats = aggregate_per_sc(usrp_args)

    # Per-SC diff table (first 8 and last 4 for brevity, plus aggregates)
    print('Per-SC statistics (sample):')
    print('  SC | |H|_loopback | |H|_usrp | ratio | argH_loopback | argH_usrp | diff_rad')
    sample_scs = list(range(0, 8)) + list(range(48, 52))
    for k in sample_scs:
        lb_m = lb_mag_stats[k]
        us_m = us_mag_stats[k]
        lb_a = lb_arg_stats[k]
        us_a = us_arg_stats[k]
        ratio = us_m[0] / lb_m[0] if lb_m[0] > 1e-9 else 0.0
        arg_diff = abs(us_a[0] - lb_a[0])
        arg_diff = min(arg_diff, 2 * math.pi - arg_diff)
        print(f'  {k:3d} | {lb_m[0]:.3f}±{lb_m[1]:.3f} | {us_m[0]:.3f}±{us_m[1]:.3f} | '
              f'{ratio:.3f} | {lb_a[0]:+.3f}±{lb_a[1]:.3f} | {us_a[0]:+.3f}±{us_a[1]:.3f} | '
              f'{arg_diff:+.3f}')
    print('  ... (omitted middle SCs)')
    print()

    # Verdict
    n_mag_ok, n_arg_ok, n_sc = compute_diff(
        lb_mag_stats, lb_arg_stats, us_mag_stats, us_arg_stats, args.threshold)
    v = verdict(n_mag_ok, n_arg_ok, n_sc, args.threshold)
    print(f'Verdict: {v}')
    print(f'  |H| ratio ∈ [0.5, 2.0]: {n_mag_ok}/{n_sc} ({100.0*n_mag_ok/n_sc:.1f}%)  '
          f'← {int(args.threshold*100)}% threshold')
    print(f'  argH diff < 0.5 rad:    {n_arg_ok}/{n_sc} ({100.0*n_arg_ok/n_sc:.1f}%)  '
          f'← {int(args.threshold*100)}% threshold')

    if n_mag_ok / n_sc < 0.3 or n_arg_ok / n_sc < 0.3:
        print('WARNING: H estimation likely broken; investigate H estimate source')

    return 0


if __name__ == '__main__':
    sys.exit(main())
