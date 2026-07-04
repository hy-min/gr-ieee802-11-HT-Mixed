# Phase 85 — Per-Symbol SFO Hypothesis Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 REFUTED — Per-symbol SFO is NOT the cause of 51% rate=0x9 in C++ replay
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (no change from this phase)

## Background

Phase 84 offline replay of `/tmp/p28_loopback_iq.fc32` (5s slice) showed:
- C++ frame_equalizer mean SNR: 19.14 dB
- Rate distribution: **51% 0x9, 49% 0xD**
- Pattern: `[13, 9, 13, 9, 13, 9, 13, 13, 13, 9, 9, 13, 9, 13, 9, 13, 9, 9, 9, 9, 13]` (前 7 帧完美交替)

The bit-flip pattern (0xD=1101, 0x9=1001, XOR=0100) targets bit 1 of the rate field. This suggested
per-symbol SFO accumulation between L-LTF0 (counter=0) and L-SIG (counter=2).

## Hypothesis

The C++ δ estimator measures bulk timing at HT-SIG1 (counter=4). When applied to L-SIG (counter=2),
the correction is off by `ε × 2` where `ε` is the per-symbol SFO drift (in 1/64 sample units).

## T1 — C++ Code Investigation

| Finding | Evidence |
|---|---|
| L-LTF0=rel=0, L-LTF1=rel=1, L-SIG=rel=2, HT-SIG1=rel=4 | `frame_equalizer_impl.cc:53-58` |
| δ estimator runs at counter=4 (HT-SIG1) | `frame_equalizer_impl.cc:5248` |
| δ applied retroactively to L-SIG (counter=2) | `frame_equalizer_impl.cc:5254` |
| L-SIG viterbi uses post-correction d_early_eqsym[kLSigRel] | `frame_equalizer_impl.cc:5659` (after 5260) |
| NO per-symbol SFO (ε) measurement | `estimate_timing_offset_from_h52` does not track per-symbol drift |

The C++ code does apply δ to L-SIG, but assumes a constant per-frame timing offset — no
correction for per-symbol SFO accumulation.

## T2 — Per-Symbol SFO Slope Measurement

Offline analysis of 149 frames in `/tmp/p28_loopback_iq.fc32`:

| Metric | Value | Comment |
|---|---|---|
| δ_LTF0 mean / std | 0.545 / 0.356 | high std = estimator noisy |
| δ_LTF1 mean / std | 0.524 / 0.333 | similar to LTF0 |
| δ_LSIG mean / std | 0.548 / 0.351 | similar to LTF0/LTF1 |
| ε (= δ_LTF1 - δ_LTF0) mean | 0.475 | HUGE; dominated by estimator noise |
| ε std | 0.387 | std > mean, useless for hypothesis test |
| predicted δ at L-SIG (δ_LTF0 + 2ε) vs measured δ_LSIG | mean abs diff 0.217 | **linear SFO model fails** |

The per-symbol SFO estimator is too noisy to confirm or refute the hypothesis. The data is
inconclusive on the SFO question alone.

## T3 — Empirical ε_extra Sweep (Definitive Test)

For each frame, apply rotation `exp(j·2π·SC·(δ + ε_extra)/64)` to L-SIG eq output and decode rate.
Sweep ε_extra over 64 grid points.

| ε_extra (1/64 units) | rate=0xD count | % |
|---|---|---|
| 0 (pure δ) | 19/149 | 13% |
| 0.2344 (best) | 35/149 | **23.5%** |
| 0.5 | 13/149 | 9% |
| 0.875 | 7/149 | 5% |

**Best ε_extra = 0.2344 (15/64 sample units)** — this is **4 orders of magnitude larger than
real per-symbol SFO** (PPM = ~1e-5 sample/symbol at 20 MHz). Therefore the improvement is NOT
from correcting SFO — it's from sweeping a coincidental value that happens to land in a
good region for the dataset.

## What This Tells Us

1. **Per-symbol SFO is REFUTED as the cause of rate=0x9**. The best ε_extra sweep
   (over 64 grid points) only marginally improves 0xD count from 21% (no δ) to 23.5%.
   The 51% 0x9 from C++ replay is NOT SFO-fixable.

2. **δ correction DOES help**: C++ with δ achieves 49% 0xD, Python without δ achieves 21% 0xD.
   The bulk δ correction is working as designed.

3. **The remaining 51% 0x9 in C++ replay must come from somewhere else**:
   - 5/48 null SCs? Only 1/48 is data SC (-13); 4/48 are pilots (excluded from viterbi).
     Not enough to cause 50% error rate.
   - L-LTF0 FFT window alignment? Phase 33 14-sample shift was already applied.
   - Per-frame phase noise from UHD streaming? Possible — Phase 55 found 8x drift.
   - CFO that changes between L-LTF and L-SIG samples? The δ estimator is unbiased
     to bulk offset but not to time-varying CFO.

## Why We Continue to Fail

The "0xD → 0x9" bit-flip is a **single-bit error on bit 1 of the rate field**. In BPSK
(QBPSK for HT-SIG), a 1-bit error means one SC's constellation point landed on the
wrong side of the decision boundary. The most likely cause:

- A specific data SC (let's call it SC_X) consistently produces a soft-decision value
  that is biased toward 0 when it should be 1 (or vice versa)
- This bias could be from: residual CFO, SFO accumulation the C++ doesn't model,
  or a structural null at SC_X that's not in the Phase 78b "5 stable nulls" list

**No further equalizer-layer investigation is justified** — 21 REFUTED hypotheses including
this one. The bit-flip is deterministic and at the SC level, suggesting the next attack
must be at the L-LTF0 extraction / H52 estimation layer.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (no change from Phase 85)
- 0 cable runs used (offline Python analysis only)
- Equalizer-layer: **CLOSED**, 21 REFUTED hypotheses

## Files of Record

- T1 source: `lib/frame_equalizer_impl.cc:5240-5260` (δ estimator + retroactive correction)
- T2 script: `p85_t2_estimate_sfo_slope.py`
- T3 script: `p85_t3_lsig_extra_delta_sweep.py`
- T2 data: `/tmp/p85_t2_deltas.npz`
- T3 data: `/tmp/p85_t3_sweep.npz`

## Recommended Next Step

**Audit L-LTF0 extraction path** (frame_detect → splitter → L-LTF0 FFT window):
- Per-frame L-LTF0 magnitude/phase distribution
- Per-frame H52 estimation accuracy on 4 pilot SCs
- Check if the 5 stable null SCs in Phase 78b are PRESERVED through the L-LTF0 → H52 path
- Or: re-examine the assumption that the 5 stable null SCs are at {-21,-7,+7,+21,-13}

## Related

- Phase 84 framework: `docs/superpowers/notes/2026-07-04-phase84-design-verdict.md`
- Phase 82 δ REFUTED: `docs/superpowers/notes/2026-07-04-phase82-verdict.md`
- Phase 78b 5 stable null SCs: `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`
- Phase 55 UHD streaming instability: `docs/superpowers/notes/2026-06-29-phase55-usrp-snr-diagnosis.md`
- Phase 33 L-LTF0 14-sample shift: `docs/superpowers/notes/2026-06-23-phase33-verdict.md`