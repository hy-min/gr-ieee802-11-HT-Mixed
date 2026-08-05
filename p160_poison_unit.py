#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 160 TDD unit test: adaptive-threshold self-poisoning (look-ahead bug).

Root cause (offline evidence 2026-08-05): the SEARCH branch fills the
4096-sample adaptive window with the WHOLE current chunk BEFORE scanning.
A real frame's strong-correlation region (~2000 samples at boxcar ~646 on
USRP scale) exceeds 10% of the window -> p90 jumps to the frame's own level
-> threshold ~969 > frame level -> the frame kills its own detection.
Explains the ~28% realtime / ~16.5% replay detection miss rate on STRONG
frames (missed L-STF peak 646.1 == detected 646.4).

Scenarios (deterministic, no hardware):
  A (no fix, strong frame)       -> current buggy behavior: NO tag (RED)
  B (trailing window, strong)    -> tag fires (GREEN after fix)
  C (trailing window, noise)     -> no tags (noise rejection preserved)
  D (trailing window, mid cor=10.0) -> tag fires (mid-strength not blocked)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p160_poison_unit.py
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


def run_stream(power, cor):
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)
    src2 = blocks.vector_source_f(list(cor), False)
    ss = ieee802_11.sync_short(0.01, MIN_PLATEAU, False, False)
    sink = blocks.vector_sink_c()
    tb = gr.top_block()
    tb.connect(src0, (ss, 0))
    tb.connect(src1, (ss, 1))
    tb.connect(src2, (ss, 2))
    tb.connect(ss, sink)
    tb.run()
    return sorted(t.offset for t in sink.tags())


def build_frame(cor_level):
    """noise fill + 2000-sample strong frame region + gap."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)      # fill window with noise
    seg(3.0, cor_level, 2000)   # frame region (L-STF + body, strong cor)
    seg(0.005, 0.1, 600)        # gap
    return power, cor


def build_noise():
    power, cor = [], []
    power.extend([0.005] * 7100)
    cor.extend([0.15] * 7100)
    return power, cor


def main():
    failures = []

    power, cor = build_frame(646.0)
    tags_a = run_stream(power, cor)
    print(f'A strong frame (current behavior): tags={tags_a} '
          f'(buggy expectation: none — informational)')

    tags_b = run_stream(power, cor)
    print(f'B strong frame: tags={tags_b} (expect 1 after trailing-window fix)')
    if len(tags_b) != 1:
        failures.append(f'B: expected 1 tag, got {tags_b}')

    pn, cn = build_noise()
    tags_c = run_stream(pn, cn)
    print(f'C noise only: tags={tags_c} (expect 0)')
    if len(tags_c) != 0:
        failures.append(f'C: expected 0 tags, got {tags_c}')

    power2, cor2 = build_frame(10.0)
    tags_d = run_stream(power2, cor2)
    print(f'D mid frame (cor=10.0): tags={tags_d} (expect 1)')
    if len(tags_d) != 1:
        failures.append(f'D: expected 1 tag, got {tags_d}')

    if failures:
        print('\nFAIL:')
        for f in failures:
            print('  ' + f)
        sys.exit(1)
    print('\nALL SCENARIOS PASS')
    sys.exit(0)


if __name__ == '__main__':
    main()
