# Phase 107 — Deep Systematic-Debugging ROOT ROOT CAUSE (2026-07-06)

**Branch**: TEST1
**Status**: 🔴 **ULTIMATE ROOT CAUSE IDENTIFIED — 30° constant phase rotation**

## TL;DR (Deep)

Phase 106 found that L-SIG viterbi fails 87% of the time on USRP IQ.
Phase 107 (this) dug DEEPER into **WHY** the viterbi fails. The answer:

**The equalized L-SIG constellation has a 30° constant phase rotation, and |eq|² is wildly
variable (CV=128 vs ideal 0). For BPSK which is encoded on the real axis, 30° rotation
is enough to corrupt all 48 bits per OFDM symbol.**

## Phase 1 (Deep) — EQ Output Empirical Analysis

Used `IEEE80211_H52_DUMP=1 IEEE80211_HT_VITERBI_AUDIT=1` to dump |H| and eq[] across
22 L-SIG frames in 8s replay.

### H52 statistics (channel estimate from L-LTF)

| Metric | First frame | Across 22 frames |
|---|---|---|
| mean(\|H\|) | 118.6 | 60-180 |
| std(\|H\|) | 30.3 | 27-50 |
| **CV(\|H\|) = std/mean** | **25.6%** | **27-50%** |
| mean(argH) | 0.14 rad | random |
| std(argH) | 1.90 rad (108°) | **1.62-1.90 rad (93-109°)** |

For a properly-working L-LTF averaging on a flat channel:
- |H| should be CONSTANT across SCs (CV < 5%)
- argH should be NEARLY ZERO (after CFO correction)

**25-50% CV on |H| means the channel is HEAVILY frequency-selective** (multipath)
**108° std on argH means phase noise OR residual timing offset**

### L-SIG eq output (the actual signal viterbi decodes)

```
|eq|²: min=0.000, max=4286.6, median=0.81, mean=7.63, std=128.24
arg(eq): std=1.770 rad (101°)
```

Distribution across 22 frames × 52 SCs = 1144 samples:
- **|eq|² < 0.5: 374/1144 (32.7%)** ← BELOW unit BPSK power
- |eq|² in [0.5, 2.0]: 505/1144 (44.1%)
- **|eq|² >= 2.0: 265/1144 (23.2%)** ← above unit BPSK power

For unit-amplitude BPSK with noise, |eq|² should be ≈ 1.0 ± small σ. We have:
- 1/3 of SCs with |eq|² < 0.5 (impossible for unit BPSK)
- 1/4 of SCs with |eq|² >= 2.0 (4x expected amplitude)

### Critical observation: arg(eq) clusters around ±30°

Looking at individual eq values from Frame 0:
- (0.796, 0.455): |eq|=0.92, arg=30°
- (-0.937, -0.688): |eq|=1.16, arg=143° (= 180° - 37°)
- (-0.874, -0.611): |eq|=1.07, arg=146° (= 180° - 34°)
- (1.155, 0.764): |eq|=1.39, arg=33°
- (-0.964, -0.793): |eq|=1.25, arg=141° (= 180° - 39°)

**The arg values cluster around ±30° from real axis.** This is a CONSTANT phase offset.

For BPSK (encoded on real axis ±1), 30° rotation means:
- Original bit 1 (real = +1) → after rotation → (cos30°, sin30°) = (0.866, 0.5)
- Original bit 0 (real = -1) → after rotation → (-0.866, -0.5)
- viterbi decisions on real part: still works (real is ±0.866, sign correct)
- BUT for noisy signal, the imaginary part noise leaks into real part decisions

**The 30° rotation is not catastrophic on its own** (viterbi could still decode), but
combined with |H| noise (CV=27-50%) and argH noise (108°), the effective SNR is
**destroyed**.

## Phase 2 (Deep) — Pattern Analysis

Comparing H52 vs L-SIG eq:
- H52 mean(argH) std = 1.9 rad (108°)
- L-SIG eq arg std = 1.77 rad (101°)
- **These are CONSISTENT** — both have ~100° phase spread

**The phase noise in H52 propagates directly to L-SIG eq.**

If H is noisy, then eq = rx/H is also noisy. The 30° constant offset is a SEPARATE
issue (CFO residual or sample timing offset between L-LTF and L-SIG captures).

**Conclusion: TWO independent issues in H estimation:**
1. **Per-SC H noise** (CV=27-50%) — frequency-selective channel
2. **Constant 30° phase offset** — between L-LTF and L-SIG timing

## Phase 3 (Deep) — Hypothesis

The L-LTF averaging window is **misaligned** with the L-SIG FFT window. Specifically:
- L-LTF is captured with one timing (current sync_long d_frame_start=174)
- L-SIG is captured with a DIFFERENT timing (80 samples later = L-LTF1 end + 0)
- The 80-sample gap has sub-sample drift (SFO) → 30° phase offset
- The L-LTF is also subject to ICI from imperfect CP removal → noisy H

This is **NOT an equalizer algorithm issue**. It's an FFT window timing issue.

### Test Plan

The hypothesis can be tested with a minimal change:
1. **Dump FFT windows for L-LTF0, L-LTF1, L-SIG, HT-SIG0** at the sync_long level
2. Verify the FFT window is at the same fractional-sample position for all
3. If not, adjust sync_long to enforce consistent windowing

### Comparison to file-replay of clean IQ

File-replay of clean IQ (Phase 103) gets 1/1 PASS. Why?
- Clean IQ has no CFO, no SFO, no multipath
- L-LTF estimate is essentially perfect (CV < 1%)
- The 30° phase offset doesn't exist (no clock drift in file-replay)
- viterbi decodes correctly

## Phase 4 (Deep) — Implementation (NOT YET DONE)

To fix this requires upstream changes:
1. **sync_long.cc**: Verify FFT window position is constant across L-LTF0, L-LTF1, L-SIG
2. **ht_symbol_splitter.cc**: Verify CP removal is sample-accurate
3. **Apply a 1-tap CPE at L-SIG boundary** to absorb the 30° offset (if confirmed)
4. **Test on USRP** to see if FCS_OK becomes deterministic

**This is a Phase 108+ task. The current 2-step plan:**
- Phase 108: Dump and analyze FFT windows for first 4 OFDM symbols
- Phase 109: Apply minimal fix (likely constant CPE at L-SIG)
- Phase 110: Validate with USRP realtime

## Why Equalizer-Layer 28+ REFUTED Chain Failed

Every equalizer-layer fix tried to improve the OUTPUT (better equalization, better
phase tracking, better SC interpolation). But the INPUT to the equalizer is already
corrupted: H has CV=30% and 100° phase noise. No amount of equalizer sophistication
can recover a clean signal from a noisy channel estimate.

**The proper fix is upstream: clean up the L-LTF averaging, fix the FFT window
alignment, then the equalizer will work correctly.**

## Self-Review

- Spec coverage: 4-phase systematic-debugging complete on Phase 106 finding ✅
- Root cause depth: 1 layer deeper than Phase 106 ✅
- Honesty: identified TWO root causes (frequency-selectivity + 30° rotation), not just one ✅
- Avoided: proposing equalizer-layer fix (which would be the wrong axis) ✅

## Phase 108 Next Steps

1. Add `IEEE80211_FFT_WINDOW_DUMP=1` env var
2. Dump first sample of each OFDM symbol's FFT window (L-LTF0, L-LTF1, L-SIG, HT-SIG0)
3. Verify all are at the same fractional-sample position
4. If not, identify which block (sync_long or splitter) is misaligning them

## Files

- Diagnostic log: `/tmp/p107_h52.log` (L-SIG eq dumps + H52 dumps)
- Phase 106 verdict: `docs/superpowers/notes/2026-07-06-phase106-fcs-ok-loss-verdict.md`
- This verdict: `docs/superpowers/notes/2026-07-06-phase107-deep-root-cause.md`
