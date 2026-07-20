#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 158 TDD unit test: COPY-state smart re-detection ("refractory but not blind").

Feeds scripted (power, cor) streams straight into ieee802_11.sync_short and
counts wifi_start tags on the output. No hardware, fully deterministic.

Scenarios:
  A (feature ON, trap + real L-STF mid-trap)  -> expect 2 tags: [0, lstf_out_start + 24]
  B (feature ON, clean real frame)            -> expect 1 tag:  [0]
     (L-LTF's strong corr + CP-like 16-sample corr spikes must NOT re-trigger)
  C (feature OFF, same stream as A)           -> expect 1 tag:  [0]  (baseline preserved)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p158_redetect_unit.py
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


def run_stream(power, cor, redetect_on):
    if redetect_on:
        os.environ['IEEE80211_SYNC_SHORT_COPY_REDETECT'] = '1'
    else:
        os.environ.pop('IEEE80211_SYNC_SHORT_COPY_REDETECT', None)
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)  # in_abs: unused (CFO disabled)
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


def build_scenario_a():
    """fill(4500) + false-detect(30) + trap(8000) + L-STF(160) + data(400) + gap(600)."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)          # fill 4096-sample adaptive window, no detection
    seg(0.005, 0.40, 30)            # weak false detection (25th sample -> COPY)
    # trap: noise hovers below gap threshold, power spike every 100 samples
    # resets the gap counter -> COPY never exits (the Phase 153 trap).
    # Trap length is chosen to keep the L-STF out of the false-detection
    # chunk's adaptive-window look-ahead: the SEARCH branch fills the
    # 4096-sample window with the WHOLE chunk before scanning, so a trap
    # shorter than ~4096 samples would let the L-STF/data pollute p90 and
    # kill the false detection. Assumes the environment's ~4096-sample
    # chunking.
    for k in range(80):
        seg(0.005, 0.15, 99)
        seg(0.02, 0.15, 1)          # gap-counter reset spike
    lstf_in_start = len(power)      # = 4500+30+8000 = 12530
    seg(3.0, 1.8, 160)              # real L-STF arrives DURING the trap
    seg(3.0, 0.3, 400)              # rest of frame (high power, weak corr)
    seg(0.005, 0.1, 600)            # gap -> exit COPY
    # Output starts at input index 4524 (25th false-detect sample, tag1 at out 0).
    # Re-detect tag lands at out (lstf_in_start - 4524) + 24.
    expected_redetect_out = (lstf_in_start - 4524) + 24
    return power, cor, [0, expected_redetect_out]


def build_scenario_b():
    """fill(4500) + L-STF(160) + L-LTF(160 strong corr) + data w/ CP spikes(2000) + gap(600)."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)
    seg(3.0, 1.8, 160)              # L-STF -> detection at 25th sample
    seg(3.0, 1.8, 160)              # L-LTF: strong corr, must NOT re-trigger (EMA high)
    seg(3.0, 0.3, 960)              # data part 1
    seg(3.0, 1.8, 16)               # CP-like corr spike #1 (16 < 25 -> rejected)
    seg(3.0, 0.3, 464)
    seg(3.0, 1.8, 16)               # CP-like corr spike #2
    seg(3.0, 0.3, 544)
    seg(0.005, 0.1, 600)
    return power, cor, [0]


def main():
    failures = []

    # Scenario A: trap + real L-STF mid-trap, feature ON
    power, cor, expected = build_scenario_a()
    tags = run_stream(power, cor, redetect_on=True)
    print(f"[A] redetect ON  tags={tags} expected={expected}")
    if tags != expected:
        failures.append(f"A: tags={tags} expected={expected}")

    # Scenario B: clean real frame, feature ON -> exactly 1 tag
    power, cor, expected = build_scenario_b()
    tags = run_stream(power, cor, redetect_on=True)
    print(f"[B] redetect ON  tags={tags} expected={expected}")
    if tags != expected:
        failures.append(f"B: tags={tags} expected={expected}")

    # Scenario C: same stream as A, feature OFF -> baseline (1 tag, no re-detect)
    power, cor, expected_a = build_scenario_a()
    tags = run_stream(power, cor, redetect_on=False)
    print(f"[C] redetect OFF tags={tags} expected=[0]")
    if tags != [0]:
        failures.append(f"C: tags={tags} expected=[0]")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print("\nPASS: all 3 scenarios")


if __name__ == '__main__':
    main()
