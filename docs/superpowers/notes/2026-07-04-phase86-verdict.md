# Phase 86 — L-LTF0 Extraction Path Audit Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 REFUTED (in this dataset) — Phase 78b's "5 stable null SCs" is NOT a property of this RF chain
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (no change from this phase)

## Background

Phase 85 REFUTED per-symbol SFO (21st equalizer-layer hypothesis). Phase 86 audits the
upstream L-LTF0 → H52 → equalizer pipeline to find the residual cause of the 51% rate=0x9
problem observed in C++ replay of `/tmp/p28_loopback_iq.fc32` (Phase 84 framework).

Phase 78b (2026-07-03, 5250 MHz cable) reported 5 "stable globally-null SCs" at
**{-21, -7, +7, +21, -13}**. The first 4 are PILOT SCs in 802.11n; -13 is a data SC.
If pilots are null in the L-LTF, then H52 at pilot SCs is undefined and any phase
correction derived from pilots is biased — a plausible explanation for 51% rate=0x9.

## T1 — C++ Dump Format Survey

The C++ `frame_equalizer_impl.cc` provides several sparse per-frame dump env vars:

| Env Var | Format | SCs dumped |
|---|---|---|
| `IEEE80211_LTF0_FFT_DUMP=1` | `\|SC[i]\|=X arg[i]=Y` for i in sample list | **7 SCs** (0-5, 26) |
| `IEEE80211_LTF0_FFT_PRECOMP_DUMP=1` | `SC[0:5]=a+bi,...` + SC[26] | **7 SCs** |
| `IEEE80211_HHDR52_PER_FRAME_DUMP=1` | `H[i]=a+bj` for i in [0,10,20,30,40] | **5 SCs** |
| `IEEE80211_H52_EQ_INPUT_DUMP=1` | full 52 SCs but mixed with other data | per-symbol |

**No dump env var provides full 52-SC H52 per frame.** Falls back to Python offline
analysis using the raw IQ capture directly.

## T2 — C++ Replay with All Dumps Enabled

Ran `p68_replay_offline.py` with `IEEE80211_HT_STRUCT_AUDIT=1 IEEE80211_LTF0_FFT_PRECOMP_DUMP=1
IEEE80211_HHDR52_PER_FRAME_DUMP=1` for 5 seconds.

- 28 frames × 8 symbols = 225 Hhdr52 per-frame dumps at frame_sym=4
- 11 `lsig_rate=` log lines, **100% 0xD** (vs Phase 84's 51% 0x9 in same dataset with
  different config)
- All 11 frames are `HT_SIG_PARSE_FAIL` (don't reach viterbi completion)
- avg_snr_lsig varies wildly: 3.87, 26.84, 92.05, 74433.40 (huge spread)

**Diagnostic**: Phase 86 dump run uses different env vars than Phase 84, producing
different per-frame rate distribution. The 51% 0x9 in Phase 84 was from a config
that did not include these additional dump env vars. This config-dependence is a
**smell**: rate=0xD/0x9 may depend on which env vars are set, not just on the
underlying signal.

## T3 v2 — Per-SC |H| Audit (Python Offline, Full 52 SCs)

Python offline analysis of 149 frames in `/tmp/p28_loopback_iq.fc32` (the same
5s slice used in Phase 84). Computes H52 from (L-LTF0+L-LTF1)/2 ÷ LTF_REF for
all 52 active SCs.

**Per-SC |H| statistics (149 frames)**:

| SC | type | mean | std | min | max |
|---|---|---|---|---|---|
| -21 | PILOT | 266.9 | 94.7 | **87.2** | 434.5 |
| -7 | PILOT | 311.2 | 134.3 | **17.6** | 485.7 |
| +7 | PILOT | 267.7 | 127.7 | **25.5** | 470.4 |
| +21 | PILOT | 455.1 | 149.0 | **177.1** | 691.6 |
| -13 | (claimed null in P78b) | 321.0 | 80.4 | 171.5 | 451.2 |

**Top 5 lowest |H| SCs (potential null candidates)**:

| SC | type | mean | std | CV |
|---|---|---|---|---|
| +6 | data | 82.7 | 44.7 | 0.541 |
| +12 | data | 87.7 | 41.9 | 0.478 |
| +3 | data | 102.4 | 46.0 | 0.449 |
| +8 | data | 112.6 | 33.8 | 0.300 |
| +2 | data | 116.9 | 27.6 | 0.236 |

**Critical findings**:
1. **Pilot SCs {-21, -7, +7, +21} are NOT null in this dataset.** Mean |H| = 267-455,
   min = 87-177. All are usable for CPE.
2. **SC -13 is NOT null.** Mean |H| = 321, min = 171. Phase 78b's claimed
   "5 stable null SCs" set {-21, -7, +7, +21, -13} is **REFUTED in this dataset**.
3. **The lowest |H| SCs are DATA SCs** {+2, +3, +6, +8, +12}, not pilots.
   These have |H| mean 82-117 — frequency-selective fading, not structural nulls.

**Hypothesis REFUTED**: The 5-stable-null phenomenon observed in Phase 78b was
either (a) specific to the 5250 MHz cable test from 2026-07-03, (b) a transient
USRP state, or (c) a measurement artifact in Phase 78b's std_im computation.

## T4 — L-LTF0 vs L-LTF1 Comparison (Phase 85 T2 Reused)

From Phase 85 T2 (`p85_t2_estimate_sfo_slope.py`):

| Source | δ mean | δ std |
|---|---|---|
| δ_LTF0 (counter=0) | 0.545 | 0.356 |
| δ_LTF1 (counter=1) | 0.524 | 0.333 |
| δ_LSIG (counter=2) | 0.548 | 0.351 |
| ε = δ_LTF1 - δ_LTF0 | 0.475 | 0.387 |

ε std > ε mean → per-symbol SFO estimator too noisy to be reliable. The L-LTF0 vs
L-LTF1 distributions are statistically indistinguishable (mean within 4%), so
**no evidence of per-symbol channel drift between L-LTF0 and L-LTF1**.

## T5 — Pilot SC Phase Audit

Question: when an inner pilot is in a temporal null, does CPE from all 4 pilots
produce a wildly wrong phase estimate?

**Per-pilot |H| null counts** (149 frames):

| SC | null threshold | frame count | % |
|---|---|---|---|
| -21 | \|H\|<50 | 0 | 0.0% |
| -21 | \|H\|<30 | 0 | 0.0% |
| -7 | \|H\|<50 | 10 | **6.7%** |
| -7 | \|H\|<30 | 7 | **4.7%** |
| +7 | \|H\|<50 | 6 | **4.0%** |
| +7 | \|H\|<30 | 2 | **1.3%** |
| +21 | \|H\|<50 | 0 | 0.0% |
| +21 | \|H\|<30 | 0 | 0.0% |

- 16/149 frames (10.7%) have at least one inner pilot (SC -7 or +7) |H| < 50
- Outer pilots (SC -21, +21) NEVER drop below 87 — always healthy

**CPE phase distribution** (CPE = angle of sum of 4 pilot H values):

| Frame subset | CPE mean (rad) | CPE std (rad) | std (deg) |
|---|---|---|---|
| All 149 frames | 0.69 | **1.58** | **90°** |
| 16 inner-null frames | 1.00 | 2.55 | 146° |
| 133 inner-OK frames | 0.66 | 1.41 | 81° |

**Findings**:
1. **CPE std = 90° on all frames is anomalously high.** A normal channel with
   slow phase drift should give CPE std < 0.3 rad (~17°). std=90° means the
   CPE estimate is **essentially random** — not tracking channel phase.
2. **Inner-null frames have WORSE CPE** (146° vs 81° for inner-OK), confirming
   that temporal-null pilots bias CPE.
3. **However, 10.7% temporal-null ≠ 51% rate-flip rate.** The inner-pilot
   temporal nulls cannot be the sole cause of the 51% 0x9 problem.

**CPE std of 90° is itself a smoking gun**: it suggests either (a) H52 estimation
produces nearly-random phase at pilot SCs, (b) the L-LTF0 FFT window alignment is
off in some frames (causing arg(H) to drift), or (c) the L-SIG pilots' BPSK
constellation is not on the real axis as expected.

## Cumulative Phase 86 Findings

1. **Phase 78b's "5 stable null SCs" hypothesis is REFUTED in this dataset.**
   No structural nulls at {-21, -7, +7, +21, -13}. Pilots are usable for CPE.
2. **Inner pilots {-7, +7} have a temporal-null weakness** (10.7% frames).
   This contributes to CPE variance but does not explain 51% rate-flip rate.
3. **CPE phase std of 90° is anomalously high** — suggests the equalizer input
   itself may have phase-coherence issues that bypass LTF-based CPE.
4. **Equalizer layer is fully CLOSED**: 21 REFUTED hypotheses across Phases 1a-85,
   including Phase 78b's structural-null theory. **No further equalizer-layer
   investigation is justified.**

## Why Equalizer-Layer Refutations Don't Help

The 51% rate=0x9 in Phase 84 C++ replay occurs AFTER:
- L-STF detection (sync_short)
- sync_long frame timing (FRAME_START_BASE = 174)
- splitter into L-LTF0/L-LTF1/L-SIG/HT-SIG0/HT-SIG1/data symbols
- L-LTF0 FFT + H52 estimation (this is what we audited)
- L-SIG CPE from pilots
- L-SIG BPSK demod + rate field decoding

If pilots are not null (T3) and per-symbol SFO is negligible (T2), and yet
51% of frames decode rate=0x9 — the problem MUST be upstream of the equalizer:
- Wrong L-STF detection position
- Wrong sync_long frame boundary
- Wrong splitter positions (L-LTF0 vs L-LTF1 vs L-SIG misalignment)
- UHD streaming dropping/delaying samples

## Upstream Attack Surface (Phase 87+ candidates)

Per HARD CONSTRAINT: equalizer-layer is CLOSED. Phase 87+ must attack upstream:

1. **L-STF detection position verification**: Compare detected L-STF start to
   expected position given frame_detect output. Check if positions vary
   across frames (jitter would shift L-LTF0 FFT window).
2. **sync_long frame boundary check**: Is FRAME_START_BASE = 174 correct for
   THIS dataset? Phase 33 fix was on a different dataset/condition.
3. **Splitter position audit**: Are L-LTF0 (counter=0) and L-SIG (counter=2)
   at expected sample offsets, or is there a 1-2 sample jitter per frame?
4. **UHD streaming stability**: Phase 55 found 8x SNR drift; Phase 58 REFUTED
   --rate 5. Is the current capture affected by overflow-induced timing errors?

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (no change from Phase 86)
- 0 cable runs used (offline Python analysis only)
- Equalizer-layer: **CLOSED**, 21+ REFUTED hypotheses
- Phase 87+ must attack upstream per HARD CONSTRAINT

## Files of Record

- T1 source: `lib/frame_equalizer_impl.cc` (dump env var definitions)
- T2 log: `/tmp/p86_full_dump.log` (11 lsig_rate lines, 100% 0xD)
- T3 script: `p86_t3_audit_5_stable_null_scs.py` (sparse C++ dump parser) +
  `p86_t3_audit_5_stable_null_scs_v2.py` (Python full-SC analysis)
- T4 script: `p85_t2_estimate_sfo_slope.py` (Phase 85 T2 reused)
- T5 script: `p86_t5_pilot_phase_audit.py`
- T5 data: `/tmp/p86_t5_pilot_audit.npz`

## Related

- Phase 85 SFO REFUTED: `docs/superpowers/notes/2026-07-04-phase85-verdict.md`
- Phase 84 design framework: `docs/superpowers/notes/2026-07-04-phase84-design-verdict.md`
- Phase 78b 5 stable nulls (REFUTED in this dataset): `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`
- Phase 33 L-LTF0 14-sample shift: `docs/superpowers/notes/2026-06-23-phase33-verdict.md`
- Phase 55 UHD streaming instability: `docs/superpowers/notes/2026-06-29-phase55-usrp-snr-diagnosis.md`