# Phase 87 — sync_short L-STF Detection Failure Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 **CONFIRMED** — sync_short FAILS to detect L-STF in `/tmp/p28_loopback_iq.fc32`
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (root cause now identified upstream)

## Background

Phase 86 verdict identified CPE phase std=90° as a smoking gun for upstream phase-coherence
issues. Phase 87 audits L-STF detection (sync_short) and sync_long frame boundary to
determine the root cause.

Per Phase 86 recommendation: L-STF detection position verification is the most direct
upstream attack on the CPE std=90° problem.

## T1 — Re-run p68 with Splitter Timing Dumps

Replayed `/tmp/p28_loopback_iq.fc32` (5s, 600M samples) through `p68_replay_offline.py`
with `IEEE80211_SPLITTER_TIMING_DUMP=1 IEEE80211_LLTF_TIMING_DUMP=1
IEEE80211_HTSIG_TIMING_DUMP=1` enabled.

Output: 156 unique frame_start_abs values, 176840 total log lines.

## T2 — Per-Frame rel_idx Jitter Analysis

**Critical finding #1: All 156 "frames" have rel_idx=0 → rel_idx=8**

```
min_rel_idx distribution: unique values = [0]
max_rel_idx distribution: unique values = [8]
All frames have rel_idx range [0..8]: True
```

The splitter correctly receives frame_start tag. **No jitter AT THE SPLITTER level**.

**Critical finding #2: C++ produces 156 "frames" BEFORE Python's first real L-STF**

| Source | Frames | Position range |
|---|---|---|
| Python L-STF detection | 149 | [4,077,626, 596,282,295] |
| C++ sync_long | 156 | [0, 3,057,878] |

**The two position sets DON'T OVERLAP.** C++'s "frames" are all in samples [0, 3M] —
pure noise region before Python's first detected L-STF at sample 4,077,626.

**Critical finding #3: None of C++'s "frames" align with Python's 149 real L-STF starts**

For each of Python's 149 L-STF positions, the closest C++ frame_start_abs is at minimum
1,019,748 samples away (>1M samples = ~50ms at 20 MHz = way more than 174-sample offset).

**Conclusion: C++ sync_long is detecting NOISE, not real L-STF starts.**

## T2b — sync_short State Distribution

Inspected `SYNC-SHORT` log lines from the 5s replay:

```
state=0 (SEARCH):  112455 calls (98.9%)
state=1 (COARSE):    1223 calls ( 1.1%)
state=2 (FINE):         0 calls ( 0.0%)
```

**sync_short NEVER reaches FINE state.** It stays in SEARCH 98.9% of the time and
occasionally flickers to COARSE for 1.1% of calls but never confirms an L-STF detection.

Per sync_short state machine (`lib/sync_short.cc`):
- SEARCH → looking for period-16 autocorrelation peak above threshold
- COARSE → found initial peak, refining via plateau/min_plateau logic
- FINE → confirmed L-STF, emit wifi_start tag

Without state=FINE, **no wifi_start signal goes to sync_long**.

## T2c — Why sync_long Still Detects 156 "Frames"

`lib/sync_long.cc:555`: `d_frame_start = best_ht_lower_peak + 1 - offset_compensation;`

This is the **correlation-search fallback path**. When sync_short doesn't emit wifi_start,
sync_long runs its OWN L-LTF autocorrelation search and uses the best peak position as
`d_frame_start`. This path fires regardless of whether sync_short detected anything.

The 156 "frames" detected by sync_long correlation search are **FALSE FRAMES** triggered
by noise peaks in samples 0-3M (the part of the capture BEFORE the first real L-STF at
sample 4,077,626).

## What This Means for the 51% rate=0x9 Problem

The Phase 84 finding of "51% rate=0x9" was based on this same C++ replay. With this Phase
87 finding, we now know:

1. **All 156 "frames" in the C++ replay are NOISE FRAMES**, not real USRP frames.
2. **The 11 `lsig_rate=0xD` lines** from Phase 86 are from these noise frames.
3. **The 28 HHDR52_PER_FRAME dumps** from Phase 86 are from noise frames too.
4. **The 100% 0xD rate distribution** from Phase 86 is meaningless — it's from garbage input.

**Phase 86's empirical findings on this dataset are INVALID because the dataset's input to
the equalizer chain is NOISE, not real frames.**

## Why Phase 86's Per-SC Analysis Was Misleading

Phase 86 found:
- Pilot SCs NOT null (|H| mean 267-455)
- 10.7% inner-pilot temporal null
- CPE phase std=90°

These findings are technically correct for the EQUALIZER INPUT, but the equalizer INPUT
itself was garbage. The 90° CPE phase std is the equalizer's response to noise input —
not a property of the USRP channel.

## Why Equalizer-Layer Refutations Did Not Catch This

The equalizer-layer refutations (Phases 1a-86) all assumed the equalizer was receiving
REAL frames. With Phase 87's finding, the equalizer was receiving NOISE. Therefore:

- All "frequency-selective fading" findings = noise patterns, not channel effects
- All "null SCs" findings = noise patterns, not channel nulls
- The CPE std=90° = noise response, not channel phase coherence issue

**The equalizer-layer CLOSURE is technically correct (21+ REFUTED hypotheses on equalizer
algorithms) but the REASON for failure is upstream: sync_short doesn't detect L-STF.**

## Upstream Attack Plan (Phase 88+)

The root cause is `sync_short` failing to detect L-STF. Candidates:

1. **sync_short threshold tuning**: current threshold=0.010 may be too high for some
   signal conditions. Compare with Python's adaptive threshold (10x median).
2. **sync_short energy gate**: there may be an energy-gate filter that's blocking
   detection in low-energy regions. Check `sync_short_fused.cc` for energy_gate_factor.
3. **sync_short state machine debugging**: why does COARSE flicker but never FINE?
   The plateau detection logic might be too strict.
4. **sync_long correlation search vs wifi_start**: improve sync_long to TRUST sync_short
   (or vice versa) when both disagree.

Per HARD CONSTRAINT: this is the FIRST actionable upstream root cause identified in
22+ REFUTED investigations. **Phase 88 should attack sync_short.**

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (root cause identified but not fixed)
- 0 cable runs used (offline Python analysis + C++ offline replay only)
- Equalizer-layer: **CLOSED** (21+ REFUTED, but the failure was upstream all along)
- Upstream-layer: **Phase 88 attack plan ready**

## Files of Record

- T1 replay log: `/tmp/p87_splitter_timing_5s.log` (176840 lines, 156 SPLITTER_TIMING)
- T2 script: `p87_t2_rel_idx_jitter.py`
- T2b script: `p87_t2b_compare_python.py`
- T2 data: `/tmp/p87_t2_jitter.npz`

## Related

- Phase 86 verdict (CPE std=90° smoking gun): `docs/superpowers/notes/2026-07-04-phase86-verdict.md`
- Phase 67/68 T1 frozen-input REFUTED: Phase 67's "8 bit-identical frames" was actually
  8 data symbols of 1 frame; sync_short DID detect L-STF in that 1-frame capture.
- Phase 84 51% rate=0x9: `docs/superpowers/notes/2026-07-04-phase84-design-verdict.md`
- sync_short state machine: `lib/sync_short.cc`
- sync_long fallback path: `lib/sync_long.cc:555` (`best_ht_lower_peak`)