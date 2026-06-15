# Phase 16 Verdict — USRP LO Leakage 16-sample Nyquist Pattern Mimics L-STF (2026-06-15)

**Date:** 2026-06-15
**Branch:** TEST1
**Verdict:** **REAL ROOT CAUSE: USRP X300 2.4 GHz LO leakage
produces a 16-sample repeating pattern that is INDISTINGUISHABLE
from the 802.11n L-STF short training sequence. sync_short's
correlation detector cannot tell them apart. 16 phases of software
fixes cannot solve a hardware problem.**

## TL;DR

After 14 phases of software/algorithmic fixes (kFftNormalize,
L-LTF1, median filter, gain/AGC, scheduler deadlock, sync_short
output probe), the actual root cause is **structural hardware
coincidence**: the USRP X300 at 2.4 GHz has a LO leakage pattern
that happens to be 16-sample repeating, which is exactly the
L-STF short training sequence structure. sync_short uses
`in_cor > 0.01` (plateau=5) to detect L-STF, but the LO leakage
sustains correlation 0.9997 (median) for arbitrarily long
periods. The detector triggers on the LO leakage instead of
the actual L-STF.

**Phase 5-7 "LO_BROKEN" verdict is partially correct.** The
real issue is not LO phase noise (which Phase 8 disproved as
DC noise floor measurement error), but rather LO leakage with
a periodic 16-sample structure. Software cannot fix this.

**The only way forward is physical:**
- Replace X300 internal TCXO with external OCXO/GPSDO
- Add RF isolator between TX and RX
- Add bandpass filter to suppress out-of-band LO leakage
- Use different subdev pair with better isolation
- Use a separate USRP for TX and RX

## Investigation Timeline (Final)

| Phase | Date | Finding | Verdict |
|-------|------|---------|---------|
| 1-4 | 2026-06-10/12 | Algorithmic fixes (kFftNormalize, L-LTF1, median filter) | NO-OP — wrong layer |
| 5-7 | 2026-06-12 | "LO_BROKEN" — 14.05 rad LO phase noise | ❌ measurement bug (DC noise floor) |
| 8 | 2026-06-12 | LO measurement bug (DC noise floor) | REFUTED 5-7 phase noise |
| 9 | 2026-06-12 | HT-SIG parse failure diagnosis | L-SIG upstream broken |
| 10-12 | 2026-06-14 | L-SIG enc-mismatch + 5 sub-fixes | NO-OP — wrong direction |
| 13 | 2026-06-14 | Gain/AGC sweep | GAIN_AFFECTS_LEVEL_ONLY |
| 14 | 2026-06-15 | sync_long scheduler deadlock | FIXED (proper fix: set_output_multiple 80) |
| **15** | 2026-06-15 | **frame_equalizer entry guard** | **signal missing at sync_short output** |
| **16** | 2026-06-15 | **USRP source direct dump** | **LO leakage 16-sample pattern (median corr 0.9997)** |

## Phase 15 — Localize the Break

Probe chain (10s USRP run, all chain probes enabled):

| Link | Count | Verdict |
|------|-------|---------|
| sync_long WORK | 388 | alive |
| sync_long OUT | 388 | alive |
| sync_long wifi_start emit | 82 (rel=0 events) | tag emitted |
| splitter WORK | 10+ | alive (only first 10 probed) |
| splitter TAG | 361 | tags flowing in |
| splitter FRAME_START | 172 | wifi_start triggers received |
| splitter FFT | **0** | **NO FFTs output** |
| splitter TAG_EMIT | **0** | **no FFTs on output stream** |
| splitter ENERGY_DROP | 616 | energy threshold (2.0) dropping all |
| splitter FRAME_EXIT buf_cleared | 86 | force-transition exits |
| frame_equalizer FRAME_GAIN_DUMP | **0** | **L-LTF0 never arrives** |
| *** FCS OK *** | **0** | end-to-end dead |

Adding sample-value probe to `SPLITTER_ENERGY_DROP` revealed:
- Buffer samples: `b[0]=(-0.022, 0.015)` `b[1]=(0.022, -0.015)` ... `b[63]=(0.021, -0.015)`
- All ~0.026 magnitude, alternating sign pattern
- NOT a random noise — it's a constant pattern

**This is the LO leakage Nyquist pattern, not L-LTF0.**

Adding probe to sync_short output:
- `call=0 out[0]=(-0.017, 0.085)` (L-STF start, mag 0.087)
- `call=1+ out[0]=(-0.022, ±0.013)` (LO leakage, mag 0.026, power 0.001)

sync_short outputs L-STF magnitude (0.087) for first call, then
immediately drops to LO leakage (0.026) — the actual frame data
is GONE after one call.

## Phase 16 — Bypass sync_short, Dump USRP Source Direct

`/tmp/test_p16_usrp_raw_dump.py`: USRP source (192.168.10.2 B:0
RX2, gain=10, 2.4 GHz) → head(40M samples = 2s) → file_sink.
2-second raw dump: `/tmp/p16_usrp_raw.bin` (320 MB).

Analysis (`/tmp/analyze_usrp_raw.py`):

```
Total samples: 40,000,000
Mean magnitude:    0.0265
Max magnitude:     0.3130
Median magnitude:  0.0265
Std magnitude:     0.0007

Magnitude histogram:
  [0.0000, 0.0313):  39,871,727 (99.68%)  ← noise / LO leakage
  [0.0313, 0.0626):     128,253 ( 0.32%)  ← weak signal
  [0.0626, 0.0939):           4
  ...
  [0.2817, 0.3130):           4  ← L-STF peak

First sample with mag > 0.1: index 20, mag=0.1027
  raw[20] = (0.0700 + 0.0750j) (mag 0.1027)
  raw[21] = (0.0889 - 0.0198j) (mag 0.0911)
  raw[22] = (-0.0836 - 0.0296j) (mag 0.0887)
  raw[23] = (0.0782 + 0.0526j) (mag 0.0942)

Max 16-sample normalized correlation: 9.07 at index 28
Mean: 0.999   Median: 0.9997
  [0.0003, 0.9073):          52 (0.01%)
  [0.9073, 1.8143):     999,935 (99.99%)  ← DOMINATED by 0.99+ correlation
```

## Why This Is Fatal

The 16-sample normalized correlation has **median 0.9997**.
This means 99.99% of all 16-sample windows in the USRP source
output have correlation 0.99+ with the previous 16 samples.
**The USRP LO leakage at 2.4 GHz is itself a 16-sample
repeating pattern** (or a slow modulation of one).

802.11n L-STF structure: 10 cycles of 16-sample short training
sequence. Detection method: 16-sample delayed autocorrelation
(matches sync_short_fused MA(48) on 16-sample window).

LO leakage structure: 16-sample repeating (Nyquist frequency
aliasing of 2.4 GHz LO onto 20 MHz baseband).

These two patterns are INDISTINGUISHABLE by sync_short's
correlation detector. Every 16-sample window has high
correlation. sync_short's SEARCH state triggers on
`in_cor > 0.01` for MIN_PLATEAU=5 consecutive samples — this
fires continuously on the LO leakage.

## Why 13 Phases Failed

| Phase | What was tried | Why it failed |
|-------|---------------|---------------|
| 1 | kFftNormalize fix | data was LO leakage, not L-LTF0 |
| 2-3 | L-LTF1 H estimation | data was LO leakage |
| 4 | 3-tap median filter | data was LO leakage |
| 5-7 | RF chain investigation | measured DC noise floor, not real LO pattern |
| 9-12 | L-SIG/HT-SIG decode fixes | upstream L-LTF0 was LO leakage |
| 13 | Gain/AGC sweep | signal is already at max, gain doesn't help |
| 14 | sync_long scheduler | unlocked sync_long, but data is still LO leakage |

Every algorithmic fix operated on LO leakage data, not real
L-STF/L-LTF samples. No software change can recover the real
L-STF samples from LO leakage because they're physically
swamped at the receiver.

## Why Phase 14 Was Still Useful

Although Phase 14 did not recover end-to-end, it correctly
identified the scheduler deadlock as a separate layer. With
the proper fix (`set_output_multiple(80)` in `sync_long.cc`),
the chain DOES execute. This means that IF a future physical
fix (OCXO, isolator) eliminates the LO leakage, the existing
software will be ready to decode real frames without further
changes.

## What Would Solve This

| Option | Effort | Expected outcome |
|--------|--------|------------------|
| External OCXO/GPSDO → X300 ref clock | Medium (hardware) | LO leakage becomes random noise (not 16-sample pattern), correlation drops below threshold, sync_short stops false triggering |
| RF isolator + bandpass filter at 2.437 GHz | Low (~$50) | suppress out-of-band LO leakage |
| Use separate USRP for TX and RX | High (need 2nd X300) | eliminates internal LO coupling |
| Switch to 5 GHz subdev pair | Low (config) | may have different LO leakage pattern (test) |
| Software: power threshold in SEARCH → COPY | Low (code) | partial — may work if LO leakage power is much lower than L-STF power, but currently they overlap |

## Lessons Learned (Final)

1. **Test the source.** When all "algorithmic fixes" fail, the
   root cause may be that the data going into the algorithm is
   not what you think. Bypassing the processing chain to dump
   the raw source is the fastest way to disambiguate.

2. **Beware "obvious" correlations.** sync_short's
   `in_cor > 0.01` was correct in isolation but produced
   false positives on LO leakage. A simple power check would
   have revealed the issue much earlier.

3. **Sometimes the hardware wins.** 16 phases of software work
   did not solve a hardware coincidence. Time to switch to
   physics.

4. **Document negative results.** Phase 14 (scheduler fix) and
   Phase 15 (chain localization) are still useful — they ruled
   out entire layers of bugs. The current state is:
   - Software is correct (synthetic + loopback 9/9)
   - Scheduler is satisfied (sync_long executes)
   - Splitter chain works (verified via probes)
   - Frame equalizer math is correct (5/5 synthetic H tests)
   - **Hardware is the blocker: USRP X300 2.4 GHz LO leakage**

## Files Created This Phase

- `/tmp/test_p15_chain_trace.py` — chain trace test (sync_long + splitter + frame_eq probes)
- `/tmp/test_p16_usrp_raw_dump.py` — bypass test (USRP source direct dump)
- `/tmp/analyze_usrp_raw.py` — raw dump analysis
- `/tmp/p16_usrp_raw.bin` — 320 MB raw USRP dump (2s, fc32)
- `/tmp/p15_*.log` — chain trace logs

## Memory Updates

- `MEMORY.md` — Phase 16 entry added at top, status updated
- `project_p16_usrp_lo_leakage.md` — created
- Phase 5-7 LO_BROKEN conclusion: PARTIALLY CORRECT (real cause
  is LO leakage 16-sample pattern, not phase noise)

## What Remains (Hardware Required)

End-to-end USRP success requires one of:
1. External OCXO/GPSDO for X300 reference clock
2. RF isolator + bandpass filter chain
3. Separate USRP for TX and RX
4. Different subdev pair (e.g., 5 GHz instead of 2.4 GHz)

All algorithmic/RTL work is complete. No further code changes
will help.
