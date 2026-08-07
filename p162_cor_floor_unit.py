#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 162b TDD unit test: absolute max_cor floor for wifi_start emission.

On-air DIAG evidence (2026-08-07, 4x 300s runs, 13114 episodes): noise
detections have episode max_cor < 100; real frames >= 593. The [100, 500]
band is EMPTY (9/13114 episodes, 0.07%). The relative gates (adaptive
threshold x trigger margin) cannot reject strong noise bursts during storm
runs (p90 inflates, bursts still cross); an ABSOLUTE floor at plateau-peak
max_cor can: real frames clear it by ~3x, noise misses it by ~2.5-5x.

Mechanism being attacked: noise-detection storms (rate swings 36x run-to-run:
43..1548/300s) pollute sync_long with false frame-starts, killing real frames
mid-flight (arrival anti-correlates with noise-detection count). Gate kills
the noise detections at emission, before they reach sync_long.

Scenarios (deterministic, no hardware; mirrors p159_margin_unit.py):
  A (floor=0.0, trap + real L-STF) -> 2 tags (baseline: both trigger)
  B (floor=1.0, trap + real L-STF) -> 1 tag (trap rejected by floor)
  C (floor=1.0, trap only)         -> 0 tags (complete rejection)
  D (floor=0.0, trap only)         -> 1 tag (baseline preserved)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p162_cor_floor_unit.py
"""
import cmath
import math
import os
import sys

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
# Determinism note (2026-08-07): the adaptive-threshold path is
# chunk-scheduling-sensitive post-P160 (trailing-window fill) — p159's
# margin test now flip-flops on it. Use a FIXED threshold (adaptive OFF,
# ctor arg 0.25) so the plateau trigger is deterministic; the floor gate
# under test sits downstream of the threshold computation and is agnostic
# to how effective_threshold was derived.
os.environ.pop('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', None)
os.environ.pop('IEEE80211_SYNC_SHORT_TRIGGER_MARGIN', None)  # margin=1.0

from gnuradio import gr, blocks
import ieee802_11

MIN_PLATEAU = 24
FIXED_THRESHOLD = 0.25       # ctor arg; fill 0.15 below, trap 0.40 above
FLOOR_ENV = 'IEEE80211_SYNC_SHORT_MIN_COR_FLOOR'


def run_stream(power, cor, floor):
    if floor is None:
        os.environ.pop(FLOOR_ENV, None)
    else:
        os.environ[FLOOR_ENV] = str(floor)
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)  # in_abs: unused
    src2 = blocks.vector_source_f(list(cor), False)
    ss = ieee802_11.sync_short(FIXED_THRESHOLD, MIN_PLATEAU, False, False)  # reads env NOW
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

    Fixed threshold 0.25: fill 0.15 never triggers; trap 0.40 > 0.25 triggers
    at margin=1.0 but is below floor=1.0; real L-STF 1.8 clears the floor.
    """
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)   # fill (below fixed threshold)
    seg(0.005, 0.40, 30)     # weak trap plateau (above thresh, below floor)
    seg(0.005, 0.15, 2000)   # long gap
    seg(3.0, 1.8, 160)       # real L-STF (above floor)
    seg(3.0, 0.1, 400)       # frame body
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
    tags_a = run_stream(power, cor, None)      # floor unset -> 0.0
    print(f'A floor=0.0 trap+real: tags={tags_a} (expect 2)')
    if len(tags_a) != 2:
        failures.append(f'A: expected 2 tags, got {tags_a}')

    tags_b = run_stream(power, cor, 1.0)
    print(f'B floor=1.0 trap+real: tags={tags_b} (expect 1)')
    if len(tags_b) != 1:
        failures.append(f'B: expected 1 tag, got {tags_b}')

    power2, cor2 = build_trap_only()
    tags_c = run_stream(power2, cor2, 1.0)
    print(f'C floor=1.0 trap-only: tags={tags_c} (expect 0)')
    if len(tags_c) != 0:
        failures.append(f'C: expected 0 tags, got {tags_c}')

    tags_d = run_stream(power2, cor2, None)
    print(f'D floor=0.0 trap-only: tags={tags_d} (expect 1)')
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
