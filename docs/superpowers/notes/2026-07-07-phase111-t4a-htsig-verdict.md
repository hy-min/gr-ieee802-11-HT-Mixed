# Phase 111 T4a — HT-SIG Viterbi Wall Diagnosis (2026-07-07)

**Branch**: TEST1
**Status**: 🔴 **T4a null-SC erasure NOT VIABLE** — HT-SIG has 12-18 bit errors
out of 96, far beyond viterbi d_free=10 capacity (max 4 correctable).
Root cause is global H52 quality, NOT specific null SCs.

## TL;DR

Hypothesis (T4a): If we mark low-|H|² SCs as erasures (LLR=0), viterbi's
erasure-decoding capacity (d_free-1 = 9 erasures) could handle 5-9 null SCs.

**REFUTED by 3 independent tests on `/tmp/p110_t10_capture.fc32`**:

1. **H52 distribution is healthy** (D1): mean null SCs = 2.0/48 (max 5/48).
   This is BELOW the viterbi erasure capacity of 9. So even with hard
   erasures, the pipeline SHOULD work — but doesn't.

2. **HT-SIG viterbi metric = 12-18 errors** (D1.5): 4 candidate rotations ×
   4 inv = 16 candidates, all fail CRC. Metric range 12-18 (Hamming distance
   to closest valid HT-SIG codeword), but viterbi can only correct 4 errors.

3. **HTSIG_H_REESTIMATE doesn't help**: Using HT-SIG pilot-based H refinement
   gives metric 12-18, same as L-LTF H. Pilots are also corrupted.

**Root cause (refined)**: Per-symbol phase drift between L-LTF and HT-SIG.
H52 argH changes ~108° per symbol (per Phase 107). L-LTF H52 is wrong at
HT-SIG time by a random phase rotation. This makes equalized symbols at
HT-SIG have wrong CPE per SC, causing 12-18 bit errors.

**T4a (null SC erasure)**: Not the right fix. The issue is global H52
phase coherence, not specific null SCs.

## Diagnostic Evidence (D1 + D1.5)

### D1: H52 null SC distribution on p110 T10 capture (5250 MHz, --tx-gain 20)

```
SC |H|² statistics across 30 frames:
  SC  38 (idx 26): mean |H|² = 0.351  ← lowest, edge SC
  SC  40 (idx 28): mean |H|² = 0.408
  SC  39 (idx 27): mean |H|² = 0.427  ← pilot
  SC  41 (idx 29): mean |H|² = 0.432
  SC  42 (idx 30): mean |H|² = 0.504
  SC  43 (idx 31): mean |H|² = 0.582  ← pilot -21
  SC  47 (idx 35): mean |H|² = 0.739
  SC  46 (idx 34): mean |H|² = 0.773
  ...
  All other SCs: mean |H|² > 0.5

Per-frame null SC count (threshold |H|² < 0.05):
  0 null: 13.3% of frames
  1 null: 23.3%
  2 null: 30.0%
  3 null: 20.0%
  4 null: 6.7%
  5 null: 6.7%  (max=5)

Phase 78b's "5 globally-null SCs at {-21,-7,+7,+21,-13}" REFUTED on this
capture. Pilots have |H|² > 0.5 on average. The 5 SCs with lowest |H|²
are {38, 39, 40, 41, 42} = edge SCs (-26, -25, -24, -23, -22) +
SC 39 (pilot -25).
```

### D1.5: HT-SIG viterbi with various env var combinations

| Config | LSIG rate/length | HT_SIG_CAND | HT-SIG metric | CRC? |
|--------|-----------------|-------------|---------------|------|
| Baseline (no env) | viterbi_fail (all) | 0 | n/a | n/a |
| + SOFT_LLR_VITERBI=1 | viterbi_fail | 0 | n/a | n/a |
| + LSIG_VITERBI_CANDIDATE=1 | found 16 OK dec | 16 per sym | 14-16 | fail |
| + HTSIG_H_REESTIMATE=1 | 16 OK dec | 16 per sym | 12-18 | fail |
| + LSIG_CAND + HTSIG_H_REESTIMATE | 16 OK dec | 16 per sym | 12-18 | fail |
| + LSIG_CAND + SOFT_LLR | 16 OK dec | 16 per sym | 14222-14806 (Q8.8) | fail |

All 16 HT_SIG_CAND candidates (4 rot × 4 inv) fail CRC. Metric 12-18
errors out of 96 bits = 12-19% bit error rate (BER).

### L-SIG viterbi breakthrough (Phase 70 candidate search)

`IEEE80211_LSIG_VITERBI_CANDIDATE=1` enables 4 phase rotation candidates
in L-SIG viterbi. With this enabled:
- L-SIG viterbi finds 16 valid decodes (4 rot × 4 inv)
- 1 frame with enc=0 (HT-Mixed BPSK 1/2), len=1080
- is_ht_frame=1 fires
- HT_SIG_CAND starts firing

**This is a USRP pipeline breakthrough** — first time is_ht_frame=1 on
this capture. The L-SIG wall is broken.

## T4a Erasure Marking Analysis

If we mark SCs with |H|² < 0.05 as erasures (LLR=0):
- Mean erasures: 2.0/48 SCs → 4 erasures in HT-SIG (HT-SIG1+HT-SIG2)
- Max erasures: 5/48 → 10 erasures in HT-SIG
- Viterbi erasure capacity: d_free-1 = 9 erasures

Math:
- Mean: 4 erasures < 9 → SHOULD WORK
- Max: 10 erasures > 9 → marginal

But we observe 12-18 errors, not 2-10. So even if we erasure-mark 5 SCs,
the viterbi is still seeing 7-13 extra errors from somewhere else.

**The 7-13 extra errors are NOT from null SCs.** They're from:
- Per-symbol phase drift (H52 wrong at HT-SIG time)
- Frequency-selective fading within HT-SIG symbols
- CFO/SFO residual

## Root Cause: Per-Symbol H52 Phase Drift

Per Phase 107 verdict:
- Per-SC argH std = 108° across symbols (huge)
- L-SIG eq |eq|² median = 0.81, but 32.7% < 0.5 and 23.2% >= 2.0
- This means: H52 phase changes ~108° per symbol

If H52 phase changes by 108° per symbol, equalizing HT-SIG with L-LTF
H52 will rotate all 48 SCs by random angle. The BPSK constellation becomes
a random ring, and viterbi sees 48 random bits → 24 expected errors (50% BER).

Observed 12-18 errors is consistent with this model (less than 50% because
some SCs have similar phase as L-LTF, some have rotated further).

**To fix this**:
1. **HT-SIG pilot-based H re-estimation** (Phase 39, env var
   IEEE80211_HTSIG_H_REESTIMATE=1): re-estimates H from 4 HT-SIG pilots.
   DID NOT help (metric still 12-18). Why? Pilots are also affected by
   the same phase drift, and 4 pilots can only constrain 4 SCs. Other 44
   SCs are interpolated from L-LTF H52, which has the same drift.

2. **Per-symbol H52 tracking** (Phase 111 T3, only for DATA symbols):
   Uses 4 HT-DATA pilots + Kalman. Doesn't help HT-SIG because it's
   applied AFTER HT-SIG is decoded.

3. **Joint L-LTF + HT-LTF H52 estimation** (NEVER TRIED): HT-LTF comes
   AFTER HT-SIG. But HT-SIG is decoded BEFORE HT-LTF. To use HT-LTF for
   HT-SIG, would need to BUFFER HT-SIG and decode after HT-LTF. This is
   non-causal but theoretically possible.

## T4a Recommendation: REFUTED

T4a (null SC erasure marking) is REFUTED. The issue is global H52 quality
degradation between L-LTF and HT-SIG, not specific null SCs.

## Alternative Approaches (T5+)

### T5a: Buffer-and-decode (non-causal)
Buffer HT-SIG1+SIG2 samples. Receive HT-LTF1+HT-LTF2, estimate fresh H52
from HT-LTF (4× SNR improvement over L-LTF alone). Decode buffered HT-SIG
with fresh H52. Latency cost: 8 µs (4 OFDM symbols).

**Theoretical benefit**:
- HT-LTF has same 4-pilot structure as L-LTF, but at a different time
- H52 from HT-LTF should reflect channel at HT-SIG time
- BUT: HT-LTF is AFTER HT-SIG, so phase drift between L-LTF and HT-LTF
  is also present (per Phase 107). HT-LTF H52 may not be perfect either.

### T5b: Improved HT-SIG pilot re-estimation
Current Phase 39 re-estimates H52 from 4 pilots + L-LTF interpolation.
If pilots are also corrupted by phase drift, the result is bad.
**Try**: use HT-SIG pilots for the 4 pilot bins, but for other 44 SCs,
fall back to L-LTF H52 with per-symbol phase correction derived from
the pilot phases.

### T5c: List viterbi
Keep top-K paths from viterbi (instead of just the best). For each path,
check CRC. If K=64, explore most possible paths.
**Cost**: 64x more computation per candidate.

### T5d: Soft LLR HT-SIG with stricter threshold
Currently soft LLR uses |LLR| = |H|/max_h. With max_h=0.58 (lowest pilot),
many SCs have |LLR| < 0.5. Try: |LLR| = clip(|H|/max_h, 0, 0.3) to
suppress unreliable SCs further. May help viterbi focus on confident SCs.

### T5e: HT-SIG viterbi candidate search extension
Currently 4 rot × 4 inv = 16 candidates. Per Phase 95, HTSIG_FINE_ROT=1
doubles to 32. Try 8 rot × 4 inv = 32 candidates at 22.5° step. May
find a candidate that aligns with the actual phase drift.

## Conclusion

**T4a (null SC erasure) REFUTED** as standalone solution. The HT-SIG
viterbi wall is not caused by null SCs on the p110 T10 capture — it's
caused by per-symbol H52 phase drift corrupting all 48 SCs equally.

**T5 (next direction)**: Per-symbol H52 phase tracking at HT-SIG time.
This requires either:
- Buffer-and-decode (T5a): use HT-LTF for H52
- Or improved pilot re-estimation (T5b): use pilots for phase tracking
- Or list viterbi (T5c): explore multiple paths
- Or extended candidate search (T5e): find the right rotation

Per user directive "不可能接受现状, equalizer attacks MUST continue",
T5 is the recommended next step. Choose based on effort vs likelihood:
- T5e (extended candidate search): LOW effort, MEDIUM likelihood
- T5a (buffer-and-decode): HIGH effort, MEDIUM likelihood
- T5b (improved pilot re-est): MEDIUM effort, MEDIUM likelihood
- T5c (list viterbi): HIGH effort, LOW likelihood

**Recommended**: T5e (extended candidate search) as the first attempt
because it can be tested on existing p110 T10 capture without HW changes.

## Files Modified

None — T4a was diagnostic-only, no C++ changes.

## Test Results Summary

- D1: H52 null SC count on p110 T10 = 2.0/48 (mean), 5/48 (max)
- D1.5: HT-SIG viterbi metric = 12-18 errors (4× viterbi capacity)
- L-SIG viterbi: PASS with VITERBI_CANDIDATE=1 (16 candidates, is_ht_frame=1)
- HT-SIG viterbi: FAIL (16 candidates, all CRC fail)
- HTSIG_H_REESTIMATE: no improvement (still 12-18)
- SOFT_LLR_VITERBI: no improvement (metric 14222-14806 in Q8.8)
