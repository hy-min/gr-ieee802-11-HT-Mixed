#!/usr/bin/env python3
"""
p162_crossover.py — hard-vs-soft FCS_OK crossover curve over fade depth.

Sweeps a global multiplier on the P161 band-edge fade profile and reports
hard vs soft FCS_OK rates at each point (same noise realizations per arm).
Quantifies the rescue frontier: where hard starts failing and how far soft
extends the workable fade depth.

Reuses the p162_soft_viterbi_unit.py machinery (scaffold + run_decode).

Run:
  unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
    PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p162_crossover.py
"""

import numpy as np
import p162_soft_viterbi_unit as u


def sweep_point(tx, depth_mult, sigma, n, seed0):
    """hard/soft FCS_OK rates at a given fade-depth multiplier."""
    rng = np.random.RandomState(seed0)
    eqs, h2s = [], []
    for _ in range(n):
        H = np.ones(52)
        H[u.EDGE_IDX] = np.maximum(0.04, np.array(u.EDGE_BASE) * depth_mult)
        noise = (rng.normal(0, sigma / np.sqrt(2), tx.shape)
                 + 1j * rng.normal(0, sigma / np.sqrt(2), tx.shape))
        eq = ((tx * H[None, :] + noise) / H[None, :]).astype(np.complex64)
        eqs.append(eq)
        h2s.append(H ** 2)
    hard = u.run_decode(eqs) / n
    soft = u.run_decode(eqs, h2_list=h2s, env_on=True) / n
    return hard, soft


def main():
    tx = u.tx_frame_symbols(u.build_psdu())
    sigma = 0.68          # the calibrated T3 regime from the unit test
    n = 150
    print(f'=== p162 hard-vs-soft crossover (sigma={sigma}, n={n}/point) ===')
    print(f'{"depth_mult":>10} {"min|H|":>7} {"hard":>7} {"soft":>7} {"rescued":>8}')
    for mult in [1.0, 0.7, 0.5, 0.35, 0.25, 0.18, 0.12]:
        hard, soft = sweep_point(tx, mult, sigma, n, seed0=int(mult * 1000) + 5)
        min_h = max(0.04, u.EDGE_BASE[0] * mult)
        rescued = (soft - hard) / (1 - hard) if hard < 1 else float('nan')
        print(f'{mult:10.2f} {min_h:7.3f} {hard:7.3f} {soft:7.3f} '
              f'{("n/a" if rescued != rescued else f"{rescued:8.2f}")}')


if __name__ == '__main__':
    main()
