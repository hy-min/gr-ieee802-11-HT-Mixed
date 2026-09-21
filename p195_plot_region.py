#!/usr/bin/env python3
"""p195_plot_region.py — plot the Co-SR region from raw p186 run output.

Parses the `[P186] A= .. B= .. A= ..% B= ..%` lines out of one or more
p186_cosr_feasibility.py stdout logs, so the figures are always regenerated
from the measurement rather than from numbers copied by hand.

Cells whose arm zero-ran (P152) are skipped: p186 prints
`INVALID -- zero-run arm(s) ...` for those, and their 0.0% is an artifact of
a dead arm, not a measurement.

Usage:
  python3 p195_plot_region.py --out <dir> <run1.out> [run2.out ...]
"""
import argparse
import re
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

POINT_RE = re.compile(
    r'^\[P186\]\s+A=\s*(\S+)\s+B=\s*(\S+)\s+A=\s*(\S+|--)%\s+B=\s*(\S+|--)%\s*(.*)$')
SOLO_RE = re.compile(r'^\[P186\]\s+A=\s*(\S+)\s+B=silent\s+A=\s*([\d.]+)%.*solo')
SOLOB_RE = re.compile(r'^\[P186\]\s+A=silent\s+B=\s*(\S+)\s+A=\s*--%\s+B=\s*([\d.]+)%.*solo')


def parse(paths):
    """-> (grid{(gA,gB):(A,B)}, soloA{ga:rate}, soloB{gb:rate}, invalid:set)"""
    grid, solo_a, solo_b, invalid = {}, {}, {}, set()
    for p in paths:
        for line in Path(p).read_text(errors='replace').splitlines():
            if 'INVALID' in line:
                m = re.search(r'^\[P186\]\s+(\S+):\s+INVALID', line)
                if m:
                    tag = m.group(1)
                    ga, _, gb = tag[1:].partition('_')
                    cell = (float(ga.replace('p', '.')), float(gb.replace('p', '.')))
                    invalid.add(cell)
                    # Applied inline, not as a final pass: files must be given in
                    # chronological order, so a later successful re-run of the
                    # same cell overrides the earlier INVALID marker.
                    grid.pop(cell, None)
                continue
            m = SOLO_RE.match(line)
            if m:
                solo_a[float(m.group(1))] = float(m.group(2))
                continue
            m = SOLOB_RE.match(line)
            if m:
                solo_b[float(m.group(1))] = float(m.group(2))
                continue
            m = POINT_RE.match(line)
            if m and m.group(3) != '--' and m.group(4) != '--':
                gA, gB = float(m.group(1)), float(m.group(2))
                grid[(gA, gB)] = (float(m.group(3)), float(m.group(4)))
    return grid, solo_a, solo_b, invalid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--out', required=True)
    ap.add_argument('--title', default='Co-SR region (MCS0 len1000, A=100ms B=101ms, phi swept)')
    ap.add_argument('--solo-a', type=float, default=None)
    ap.add_argument('--solo-b', type=float, default=None)
    a = ap.parse_args()

    grid, solo_a, solo_b, invalid = parse(a.runs)
    gvals = sorted({g for g, _ in grid})
    G = np.full((len(gvals), len(gvals)), np.nan)
    H = np.full_like(G, np.nan)
    for (gA, gB), (ra, rb) in grid.items():
        i, j = gvals.index(gA), gvals.index(gB)
        G[i, j], H[i, j] = ra, rb
    # Both matrices index [box A gain, box B gain].  Every plot below wants
    # [THIS box's gain, the OTHER box's gain], so B's matrix is transposed:
    # without it the B curves and the B map come out with the axes swapped --
    # they look like "B vs B's own gain", which rises smoothly and reads as a
    # plausible result instead of an obvious bug.
    H = H.T
    sa = a.solo_a if a.solo_a is not None else (max(solo_a.values()) if solo_a else None)
    sb = a.solo_b if a.solo_b is not None else (max(solo_b.values()) if solo_b else None)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    print(f'cells={len(grid)}  gains={gvals}  invalid={sorted(invalid)}')
    print(f'solo A={sa}  solo B={sb}')

    # ---- curves: each box's own rate vs the OTHER box's power ----
    for who, (M, solo, mk) in (('A', (G, sa, 'o')), ('B', (H, sb, 's'))):
        fig, ax = plt.subplots(figsize=(7.4, 5.0))
        for i, g in enumerate(gvals):
            ax.plot(gvals, M[i, :], marker=mk, lw=2, ms=6,
                    label=f'own TX gain = {g:g} dB')
        if solo is not None:
            ax.axhline(solo, ls='--', c='gray', lw=1.4,
                       label=f'solo baseline = {solo:.1f}%')
        ax.set_xlabel("the OTHER box's TX gain  [dB]", fontsize=11)
        ax.set_ylabel(f"Box {who}'s own FCS_OK  [%]", fontsize=11)
        ax.set_title(f"Box {who}'s own link vs the other box's power\n{a.title}", fontsize=10.5)
        ax.set_xticks(gvals); ax.set_ylim(0, 100); ax.grid(alpha=.3)
        ax.legend(fontsize=9)
        fig.tight_layout()
        f = out / f'p195_{who}_rate_vs_other_power.png'
        fig.savefig(f, dpi=150); plt.close(fig)
        print(' ', f)

    # ---- side-by-side per-link maps ----
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.4))
    for ax, M, name, solo in ((axes[0], G, 'Box A own FCS_OK %', sa),
                              (axes[1], H, 'Box B own FCS_OK %', sb)):
        im = ax.imshow(M, origin='lower', cmap='RdYlGn', vmin=0, vmax=100)
        for i in range(len(gvals)):
            for j in range(len(gvals)):
                if not np.isnan(M[i, j]):
                    ax.text(j, i, f'{M[i,j]:.0f}', ha='center', va='center', fontsize=11)
        ax.set_xticks(range(len(gvals))); ax.set_xticklabels([f'{g:g}' for g in gvals])
        ax.set_yticks(range(len(gvals))); ax.set_yticklabels([f'{g:g}' for g in gvals])
        ax.set_xlabel('the OTHER box TX gain (dB)')
        ax.set_ylabel('this box TX gain (dB)')
        ax.set_title(f'{name}\n(solo baseline {solo:.1f}%)' if solo else name, fontsize=10.5)
        fig.colorbar(im, ax=ax, label='FCS_OK %')
    fig.suptitle(a.title, fontsize=11.5, y=1.005)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    f = out / 'p195_maps_per_link.png'
    fig.savefig(f, dpi=150); plt.close(fig)
    print(' ', f)

    # ---- row-wise dip analysis: is the minimum a single-point notch? ----
    print('\nrow-wise shape of Box A (own gain fixed, other sweeping):')
    print(f'{"A gain":>7} ' + ''.join(f'{g:>8g}' for g in gvals) + '   min@   notch')
    for i, g in enumerate(gvals):
        row = G[i, :]
        if np.isnan(row).all():
            continue
        jm = int(np.nanargmin(row))
        # notch = how far the min sits below the average of its neighbours
        nb = [row[j] for j in (jm - 1, jm + 1) if 0 <= j < len(gvals) and not np.isnan(row[j])]
        notch = (np.mean(nb) - row[jm]) if nb else float('nan')
        print(f'{g:>7g} ' + ''.join(f'{v:>8.1f}' if not np.isnan(v) else f'{"--":>8}'
                                    for v in row)
              + f'  {gvals[jm]:>4g}  {notch:>7.1f}')


if __name__ == '__main__':
    main()
