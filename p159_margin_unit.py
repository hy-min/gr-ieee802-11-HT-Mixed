#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 159 TDD unit test: trigger-strength margin.

On-air DIAG evidence (2026-08-04): trap episodes have max_cor 0.26-0.36
(1.3-1.8x the 0.2 adaptive floor); real frames have max_cor >= 500.
The 0.4-10 band is empty -> plateau counting gated at margin x threshold
(2.5x) rejects ALL traps and loses ZERO real frames. Traps are 46% of
sync_long's diet and force FAST_SYNC restarts / HT_MIXED ignores on real
frames (the chain-success bottleneck: 35%).

Scenarios (deterministic, no hardware):
  A (margin=1.0, trap + real L-STF) -> 2 tags (baseline: trap triggers too)
  B (margin=2.5, trap + real L-STF) -> 1 tag (trap rejected, real kept)
  C (margin=2.5, trap only)         -> 0 tags (complete trap rejection)
  D (margin=1.0, trap only)         -> 1 tag (baseline preserved)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p159_margin_unit.py
"""
import cmath
import math
import os
import sys

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH'] = '1'

from gnuradio import gr, blocks
import ieee802_11

MIN_PLATEAU = 24
MARGIN_ENV = 'IEEE80211_SYNC_SHORT_TRIGGER_MARGIN'


def run_stream(power, cor, margin):
    if margin is None:
        os.environ.pop(MARGIN_ENV, None)
    else:
        os.environ[MARGIN_ENV] = str(margin)
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)  # in_abs: unused
    src2 = blocks.vector_source_f(list(cor), False)
    ss = ieee802_11.sync_short(0.01, MIN_PLATEAU, False, False)  # reads env NOW
    sink = blocks.vector_sink_c()
    tb = gr.top_block()
    tb.connect(src0, (ss, 0))
    tb.connect(src1, (ss, 1))
    tb.connect(src2, (ss, 2))
    tb.connect(ss, sink)
    tb.run()
    return sorted(t.offset for t in sink.tags())


def build_trap_plus_real():
    """fill + weak trap plateau (cor 0.40) + long gap + real L-STF (cor 1.8).

    Strong segments are kept <10% of the 4096-sample adaptive window so the
    SEARCH branch's whole-chunk look-ahead cannot pollute p90 (P158 lesson):
    p90 stays 0.15 -> threshold 0.225 (floor path). Trap 0.40 passes at
    margin=1.0, rejected at margin=2.5 (0.5625 gate); L-STF 1.8 passes both.
    """
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)   # fill adaptive window
    seg(0.005, 0.40, 30)     # weak trap plateau
    seg(0.005, 0.15, 2000)   # long gap (trap episode exits, dilutes look-ahead)
    seg(3.0, 1.8, 160)       # real L-STF
    seg(3.0, 0.1, 400)       # frame body (low cor: no p90 pollution)
    seg(0.005, 0.1, 600)     # gap
    return power, cor


def build_trap_only():
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)
    seg(0.005, 0.40, 30)     # weak trap only
    seg(0.005, 0.15, 2000)
    return power, cor


def main():
    failures = []

    power, cor = build_trap_plus_real()
    tags_a = run_stream(power, cor, None)      # margin unset -> 1.0
    print(f'A margin=1.0 trap+real: tags={tags_a} (expect 2)')
    if len(tags_a) != 2:
        failures.append(f'A: expected 2 tags, got {tags_a}')

    tags_b = run_stream(power, cor, 2.5)
    print(f'B margin=2.5 trap+real: tags={tags_b} (expect 1)')
    if len(tags_b) != 1:
        failures.append(f'B: expected 1 tag, got {tags_b}')

    power2, cor2 = build_trap_only()
    tags_c = run_stream(power2, cor2, 2.5)
    print(f'C margin=2.5 trap-only: tags={tags_c} (expect 0)')
    if len(tags_c) != 0:
        failures.append(f'C: expected 0 tags, got {tags_c}')

    tags_d = run_stream(power2, cor2, None)
    print(f'D margin=1.0 trap-only: tags={tags_d} (expect 1)')
    if len(tags_d) != 1:
        failures.append(f'D: expected 1 tag, got {tags_d}')

    if failures:
        print('\nFAIL:')
        for f in failures:
            print('  ' + f)
        sys.exit(1)
    print('\nALL 4 SCENARIOS PASS')
    sys.exit(0)


if __name__ == '__main__':
    main()
