# Phase 91 — Energy Gate Bypass Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 **REGRESSION** — BYPASS_ENERGY_GATE=1 alone doesn't fix adaptive threshold
(median stuck at 0); avg_snr dropped to 2.02 dB
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved

## Background

Phase 90 verdict identified energy gate defeating adaptive threshold (median=0 due to
zero output). Phase 91 attempted fix: use existing `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`
env var to disable the energy gate, so out2 would always be the actual boxcar value.

## Test Setup

```
IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1
IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_TIMING_OFFSET_APPLY=1
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 --warmup 30 --duration 30
```

## Results

```
Sent: 60 | Recv: 0 | FCS_OK=0 | FCS_FAIL=0 | Success Rate: 0.0%
sync_short detections: 10 at corr=0.090-1.271
avg_snr=2.02 avg_snr_ht=2.14 is_ht_frame=1
LSIG_PARSE_FAIL: viterbi_fail (rate=-1, length=-1)
HT_SIG_CAND: 0 entries
```

**ALL adaptive calls** show `median=0.000000 adaptive_thresh=0.010000`.

## Smoking Gun

The energy gate bypass DID work — confirmed via BOXCAR dump showing non-zero values:
```
[SYNC-SHORT-FUSED-BOXCAR] call=0 n=1954 batch_power=0.000000 max_out2=1.6108 min_out2=0.0000
[SYNC-SHORT-FUSED-BOXCAR] call=3 n=3908 batch_power=0.000000 max_out2=1.6419 min_out2=0.8870
[SYNC-SHORT-FUSED-BOXCAR] call=0 n=3908 batch_power=0.000000 max_out2=1.7154 min_out2=0.9645
```

`max_out2=1.6-1.7` confirms boxcar is producing non-zero values at L-STF.
`min_out2=0.0` for some calls means gaps between frames still have out2=0.

But the adaptive median is STILL 0 because:
- 30s warmup period: RX receives no signal, all out2=0 → window of 4096 zeros
- During test (30s): frames arrive every 1s, but each frame is ~500 samples
  out of 4096 window = 12% non-zero
- 88% zeros + 12% non-zero → median = 0
- Adaptive threshold = max(0*10, 0.01) = 0.01 (floored at constructor arg)

**Median is too robust to outliers for this use case.**

## Why It Got WORSE

avg_snr dropped from 14.61 (T2 baseline) → 5.06 (T3 Phase 89) → 2.02 (T4 Phase 91).
Hypothesis: detector now fires on different positions (corr=1.27 vs T2's noise spikes),
and these positions happen to align with USRP buffer boundaries / packet drops, producing
worse equalizer output.

## What's Needed (Phase 92 attack plan)

To fix median=0 stuck issue, options:

1. **Option A — Percentile-based threshold**:
   Replace median*10 with `percentile_90 * 1.5` (or `percentile_95 * 1.5`).
   Percentile is more robust to zero contamination than median.
   Risk: low; CPU cost similar.

2. **Option B — Reset window on activity**:
   When batch_power (or out2 max) exceeds threshold for N consecutive samples,
   reset the window. Ensures median reflects current noise floor.
   Risk: medium; adds state machine complexity.

3. **Option C — Use raw batch_power EMA as threshold**:
   Track d_noise_floor in sync_short_fused, expose via separate stream tag or output.
   sync_short uses d_noise_floor instead of in_cor median.
   Risk: medium; requires API change.

4. **Option D — Track noise floor via second stream**:
   Add 4th output port to sync_short_fused for noise floor estimate.
   sync_short uses that instead of in_cor median.
   Risk: medium; biggest code change.

5. **Option E — Reverse the gate direction**:
   Instead of zeroing out2 when batch_power < threshold, ZERO out2 when batch_power
   EXCEEDS threshold × signal_factor. Then median reflects only noise floor.
   Risk: low; small change.

**Recommended**: Option A (percentile) — lowest risk, smallest change.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved**
- Cable runs used: **1 of 5 budget** (Phase 90 only — Phase 91 used same data set)
- avg_snr on USRP: 2.02 dB (need 6+ dB)
- Phase 89 fix works in algorithm; defeated by window initialization issue

## Files of Record

- T1: `/tmp/p91_disable_gate.log` (sent=60, recv=0, REGRESSION)
- Implementation: `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1` (existing env var)
- Boxcar dump: confirmed active (max_out2=1.6 at L-STF)

## Related

- Phase 90 verdict: `docs/superpowers/notes/2026-07-04-phase90-verdict.md`
- Phase 89 verdict: `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 88 verdict: `docs/superpowers/notes/2026-07-04-phase88-verdict.md`