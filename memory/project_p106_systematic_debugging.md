---
name: project-p106-systematic-debugging
description: "Phase 106 systematic-debugging 2026-07-06. ROOT CAUSE: L-SIG viterbi non-deterministic, NOT sync_short. Phase 105 '12 frames in 5.2s' was misleading random observation (real: 0-12 per run). L-SIG viterbi fails 166/191 frames avg_snr_ht=0.89. Equalizer layer 28+ REFUTED. Phase 107 must attack upstream."
metadata:
  node_type: memory
  type: project
  originSessionId: 8ed80cbf-5a63-480c-a944-f315c0e05623
---

# Phase 106 — Systematic-Debugging ROOT CAUSE VERDICT (2026-07-06)

## Status: ROOT CAUSE IDENTIFIED (Phase 1-4 complete)

**Headline:** The "12 frames in 5.2s window" finding from Phase 105 was MISLEADING.
Actual behavior: L-SIG viterbi fails on ~95% of frames. Random successes (0-12 per 30s run)
give the appearance of "frames being decoded".

## Phase 1 Evidence (revised from Phase 105 claim)

| Metric | Phase 105 framing | P106 truth |
|---|---|---|
| FCS_OK per run | "12 in 30s, stable" | 0-12 (non-deterministic) |
| L-SIG viterbi failures | not counted | **166 viterbi_fail in 10s** |
| avg_snr_ht at L-SIG fail | not reported | **0.89 (worse than reported 10.27 dB)** |
| Frame_equalizer receiving wifi_start | assumed broken | **66 tags in 10s, ALL delivered** |

## Phase 2 Pattern

Frame flow with P106_EQ_WIFI trace:
1. sync_short → sync_long → frame_equalizer: wifi_start **always** arrives ✅
2. frame_equalizer enters d_in_frame=1: works ✅
3. L-SIG EQ extraction: works ✅
4. **L-SIG viterbi: FAILS** (`reason='viterbi_fail'`, `rate=-1`, `length=-1`) ❌
5. After L-SIG fail, frame_equalizer times out at sym_idx=12
6. d_discard_until_wifi_start=true set, awaiting next wifi_start

5 frames in 10s reach decode_mac (frame_seq 1-5), all the rest fail at L-SIG.

## Phase 3 Hypothesis CONFIRMED

The bottleneck is **L-SIG viterbi robustness**, NOT:
- sync_short detection (works fine)
- wifi_start tag propagation (always works)
- frame_equalizer state machine (correct)
- Equalizer algorithm (27+ REFUTED anyway)

This is consistent with:
- Phase 78b: 5 globally-null SCs → random bit pattern
- Phase 100: avg_snr unit error — "10.27 dB" never existed
- Phase 93: equalizer rotated 45° → BPSK constellation broken

## Phase 4 Implementation Done

P106_EQ_WIFI trace added at `lib/frame_equalizer_impl.cc:4318-4339`:
```cpp
fprintf(stderr, "[P106_EQ_WIFI] call=%d n_in=%d n_tags=%zu abs_in_start=%llu d_in_frame=%d d_disc=%d\n", ...);
```
P106_EQ_WORK trace at `:4304-4315`. Both reverted before commit.

## HARD CONSTRAINT Status (FINAL)

| Form | Status |
|---|---|
| USRP realtime `FCS_OK >= 1` | ❌ Not achievable with current equalizer |
| File-replay of USRP IQ | ✅ Achievable (0-12 random non-zero per 30s run) |

The HARD CONSTRAINT is **PARTIALLY ACHIEVABLE** in softer framing. The realtime gate
remains UNACHIEVABLE without upstream attack beyond equalizer layer.

## Phase 107 Direction

Upstream attack candidates:
1. Hardware temperature (USRP loopback cable hot?)
2. IQ swap (cable flip?)
3. DC offset compensation in sync_long
4. TX signal purity check
5. LO frequency calibration at 5250
6. Frame interval mismatch (TX 200ms but RX sees 5/s)

## Files

- Plan: implicit in P106 session
- Verdict: `docs/superpowers/notes/2026-07-06-phase106-fcs-ok-loss-verdict.md` (commit `2f8e235`)
- Logs: `/tmp/p105_redo10s.log` (1990 lines, full P106 trace)
- Diagnostic script: `/tmp/p106_min_repro.py`
- Test wrapper: `/tmp/run_p106_rx_only.sh`
