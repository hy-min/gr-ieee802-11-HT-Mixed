#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 163 TDD unit test: buffered confirm gate in sync_short.

On-air paired measurement (2026-08-07, p163_trig2.err, 805 episodes):
  trigger-point correlation OVERLAPS noise (real p5=27.4/p50=37.4 vs noise
  max 23.3) — no trigger-point threshold can work (the 162b floor failure).
  Post-ramp (episode max) separates cleanly: real ~600, noise <= 40.
So the gate must confirm the peak over the first K samples of the episode
(post-ramp) and only then forward it; weak episodes are dropped before they
reach sync_long (no false frame-start, no FAST_SYNC churn).

Scenarios (deterministic, fixed-threshold path; mirrors p162_cor_floor_unit):
  A (gate OFF, weak + strong)  -> 2 tags (baseline: both trigger)
  B (gate ON,  weak + strong)  -> 1 tag  (weak dropped, real kept)
  C (gate ON,  weak only)      -> 0 tags
  D (gate OFF, weak only)      -> 1 tag  (baseline preserved)
  E (gate ON,  weak + strong)  -> output samples FEWER than gate OFF
                                  (noise episode actually dropped from stream)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p163_confirm_gate_unit.py
"""
import cmath
import math
import os
import sys

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
# Fixed-threshold deterministic path (adaptive OFF; see p162_cor_floor_unit).
os.environ.pop('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', None)
os.environ.pop('IEEE80211_SYNC_SHORT_TRIGGER_MARGIN', None)  # margin=1.0

from gnuradio import gr, blocks
import ieee802_11

MIN_PLATEAU = 24
FIXED_THRESHOLD = 0.25
FLOOR_ENV = 'IEEE80211_SYNC_SHORT_CONFIRM_FLOOR'
K_ENV = 'IEEE80211_SYNC_SHORT_CONFIRM_K'


def run_stream(power, cor, floor, k=48):
    if floor is None:
        os.environ.pop(FLOOR_ENV, None)
    else:
        os.environ[FLOOR_ENV] = str(floor)
        os.environ[K_ENV] = str(k)
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)
    src2 = blocks.vector_source_f(list(cor), False)
    ss = ieee802_11.sync_short(FIXED_THRESHOLD, MIN_PLATEAU, False, False)
    sink = blocks.vector_sink_c()
    tb = gr.top_block()
    tb.connect(src0, (ss, 0))
    tb.connect(src1, (ss, 1))
    tb.connect(src2, (ss, 2))
    tb.connect(ss, sink)
    tb.run()
    return sorted(t.offset for t in sink.tags()), len(sink.data())


def build_weak_plus_strong():
    """fill + weak noise burst (peak 30) + gap + strong ramping L-STF (peak 600)."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    def ramp(p, c0, c1, n):
        for i in range(n):
            power.append(p)
            cor.append(c0 + (c1 - c0) * i / max(n - 1, 1))

    seg(0.005, 0.15, 4500)        # fill (below threshold)
    seg(3.0, 30.0, 100)           # weak noise burst: above thresh(0.25), below floor(200)
    seg(0.005, 0.15, 800)         # gap
    seg(3.0, 0.3, 8)              # L-STF onset (crosses threshold)
    ramp(3.0, 0.3, 600.0, 32)     # L-STF ramp-up over 32 samples (boxcar fill)
    seg(3.0, 600.0, 120)          # L-STF plateau (real strength)
    seg(3.0, 0.1, 400)            # frame body (low cor)
    seg(0.005, 0.1, 600)          # gap
    return power, cor


def build_weak_only():
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)
    seg(3.0, 30.0, 100)           # weak noise burst only
    seg(0.005, 0.15, 2000)
    return power, cor


def main():
    failures = []

    power, cor = build_weak_plus_strong()
    tags_a, n_a = run_stream(power, cor, None)       # gate OFF
    print(f'A gate=OFF weak+strong: tags={tags_a} n_out={n_a} (expect 2 tags)')
    if len(tags_a) != 2:
        failures.append(f'A: expected 2 tags, got {tags_a}')

    tags_b, n_b = run_stream(power, cor, 200.0)      # gate ON
    print(f'B gate=ON  weak+strong: tags={tags_b} n_out={n_b} (expect 1 tag)')
    if len(tags_b) != 1:
        failures.append(f'B: expected 1 tag, got {tags_b}')

    # E: the weak episode must actually be DROPPED from the output stream
    print(f'E stream drop: OFF n_out={n_a} vs ON n_out={n_b} (expect ON < OFF)')
    if not (n_b < n_a):
        failures.append(f'E: gate ON did not drop noise episode (ON {n_b} >= OFF {n_a})')

    power2, cor2 = build_weak_only()
    tags_c, _ = run_stream(power2, cor2, 200.0)
    print(f'C gate=ON  weak-only: tags={tags_c} (expect 0)')
    if len(tags_c) != 0:
        failures.append(f'C: expected 0 tags, got {tags_c}')

    tags_d, _ = run_stream(power2, cor2, None)
    print(f'D gate=OFF weak-only: tags={tags_d} (expect 1)')
    if len(tags_d) != 1:
        failures.append(f'D: expected 1 tag, got {tags_d}')

    if failures:
        print('\nFAIL:')
        for f in failures:
            print('  ' + f)
        sys.exit(1)
    print('\nALL 5 SCENARIOS PASS')
    sys.exit(0)


if __name__ == '__main__':
    main()
