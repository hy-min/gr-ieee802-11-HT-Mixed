---
name: project-p107-deep-root-cause
description: "Phase 107 deep systematic-debugging 2026-07-06. ULTIMATE ROOT CAUSE: 30° constant phase rotation in eq + 27-50% |H| CV. Phase 106 'L-SIG viterbi fails' is symptom; real cause is FFT window timing issue upstream. Phase 108+ must fix sync_long or ht_symbol_splitter windowing, NOT equalizer."
metadata:
  node_type: memory
  type: project
  originSessionId: 8ed80cbf-5a63-480c-a944-f315c0e05623
---

# Phase 107 — Deep Systematic-Debugging ROOT ROOT CAUSE (2026-07-06)

## Status: 2-LEVEL deep root cause identified

**Phase 106 finding:** L-SIG viterbi fails 87% on USRP IQ.
**Phase 107 finding:** WHY the viterbi fails — the EQ input is corrupted.

## Phase 1 Empirical (H52_DUMP + LSIG_EQ_FULL)

H52 statistics (channel estimate from L-LTF):
- mean|H| = 60-180
- **std|H| / mean|H| = 27-50%** (heavy frequency-selective)
- mean(argH) = random
- **std(argH) = 1.62-1.90 rad (93-109°)** (huge phase spread)

L-SIG eq (the actual signal viterbi decodes):
- median |eq|² = 0.81
- mean |eq|² = 7.63, **std = 128** (HUGE variance)
- **32.7% of SCs have |eq|² < 0.5** (impossible for unit BPSK)
- **23.2% of SCs have |eq|² >= 2.0** (way above unit BPSK)
- arg(eq) clusters at ±30° (CONSTANT 30° phase offset!)

## Phase 2 Pattern

H52 noise propagates to L-SIG eq:
- H52 std(argH) = 108°
- L-SIG eq std(arg) = 101°
- These are CONSISTENT

## Phase 3 Hypothesis

**TWO independent issues in H estimation:**
1. Per-SC H noise (CV=27-50%) — frequency-selective channel or L-LTF averaging window issue
2. **Constant 30° phase offset** — between L-LTF and L-SIG FFT window timing

The 30° offset is consistent with **SFO (sample frequency offset)** between L-LTF and
L-SIG capture windows, OR **CP removal** in ht_symbol_splitter not aligning samples
correctly.

## Phase 4 Implementation (NOT DONE)

The fix is **upstream of the equalizer**:
- sync_long.cc: verify FFT window position is constant across L-LTF0, L-LTF1, L-SIG
- ht_symbol_splitter.cc: verify CP removal is sample-accurate
- Apply constant CPE at L-SIG boundary to absorb 30° offset

Equalizer-layer fixes 28+ REFUTED because they all try to improve OUTPUT, but the
INPUT (H estimate) is already corrupted.

## Why File-Replay of Clean IQ Works (Phase 103)

Clean IQ has:
- No CFO
- No SFO
- No multipath
- L-LTF estimate is essentially perfect (CV < 1%)
- No 30° offset (no clock drift)
- viterbi decodes correctly → 1/1 PASS

## Files

- Verdict: `docs/superpowers/notes/2026-07-06-phase107-deep-root-cause.md` (commit `077c1ea`)
- Diagnostic log: `/tmp/p107_h52.log` (22 LSIG_EQ_FULL dumps, 22 H52_DUMP dumps)
- Phase 106 verdict: `docs/superpowers/notes/2026-07-06-phase106-fcs-ok-loss-verdict.md`
