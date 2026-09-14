#!/usr/bin/env python3
"""
p179_compare_geometry.py — overlay two P179 power-sweep runs (different antenna
geometry) on one axes.

Reads the "[SWEEP] gain=..." console lines produced by p179_cosr_power_sweep.py
and plots per-level victim FCS_OK/est_sent for each run, so the far-geometry
(1 m, coexistence) and near-geometry (<10 cm, coupling) curves can be read
against each other.

Usage:
    python3 p179_compare_geometry.py \
        --run "far(1m):/home/hy/captures/p179_console.log" \
        --run "near(<10cm):/home/hy/captures/p179_console_neargeo2.log" \
        --out /home/hy/captures/p179_geometry_compare.png
"""
import argparse
import re
import sys
from pathlib import Path

LINE_RE = re.compile(
    r'\[SWEEP\] gain=(\S+) \((asc|desc)\): A FCS_OK=(\d+)/(\d+) = ([\d.]+)%')


def parse(path):
    """-> (levels_in_first_seen_order, {level: {arm: rate}})"""
    levels, data = [], {}
    for line in Path(path).read_text(errors='replace').splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        gain, arm, _ok, _est, rate = m.groups()
        lvl = gain if gain == 'OFF' else f'{float(gain):g}'
        if lvl not in levels:
            levels.append(lvl)
        data.setdefault(lvl, {})[arm] = float(rate)
    return levels, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='append', required=True,
                    help='LABEL:console_log_path (repeatable)')
    ap.add_argument('--out', default='/home/hy/captures/p179_geometry_compare.png')
    ap.add_argument('--title', default='P179 Co-SR power sweep — geometry comparison')
    a = ap.parse_args()

    runs = []
    for spec in a.run:
        label, _, path = spec.partition(':')
        if not Path(path).is_file():
            print(f'SKIP {label}: {path} not found', file=sys.stderr)
            continue
        levels, data = parse(path)
        if not data:
            print(f'SKIP {label}: no [SWEEP] gain= lines in {path}', file=sys.stderr)
            continue
        runs.append((label, levels, data))
    if not runs:
        sys.exit('no parsable runs')

    # Common x axis: union of levels across runs, preferring OFF first then
    # numeric order (so every run lines up on the same power axis).
    all_lv = []
    for _, levels, _ in runs:
        for lvl in levels:
            if lvl not in all_lv:
                all_lv.append(lvl)
    all_lv.sort(key=lambda l: (l != 'OFF', float(l) if l != 'OFF' else -1))
    xpos = {l: i for i, l in enumerate(all_lv)}

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for label, _levels, data in runs:
        xs, ys = [], []
        for lvl in all_lv:
            arms = data.get(lvl)
            if not arms:
                continue
            xs.append(xpos[lvl])
            ys.append(sum(arms.values()) / len(arms))
        ax.plot(xs, ys, marker='o', linewidth=1.8, label=label)
        # asc/desc arms as faint markers where both exist
        for lvl, arms in data.items():
            if len(arms) == 2:
                ax.scatter([xpos[lvl]] * 2, list(arms.values()),
                           alpha=0.25, s=18,
                           color=ax.lines[-1].get_color())

    ax.set_xticks(range(len(all_lv)))
    ax.set_xticklabels(all_lv)
    ax.set_xlabel('Box B (interferer) TX gain (dB)  [OFF = silent]')
    ax.set_ylabel('Box A victim FCS_OK / est_sent (%)')
    ax.set_ylim(0, 105)
    ax.grid(alpha=0.3)
    ax.legend()
    ax.set_title(a.title + '\n(A victim MCS0 len38 fixed; B MCS0 len61 swept; 5890 MHz)')
    fig.tight_layout()
    fig.savefig(a.out, dpi=150)
    print(f'curve: {a.out}')
    for label, levels, data in runs:
        print(f'\n{label}:')
        for lvl in all_lv:
            if lvl in data:
                arms = data[lvl]
                mean = sum(arms.values()) / len(arms)
                detail = '  '.join(f'{k}={v:.1f}' for k, v in sorted(arms.items()))
                print(f'  {lvl:>6s}  mean={mean:6.2f}%   {detail}')


if __name__ == '__main__':
    main()
