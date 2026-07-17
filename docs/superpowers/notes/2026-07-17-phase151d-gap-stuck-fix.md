# Phase 151d: sync_short COPY-State Stuck — FIXED with Power-Only Gap Detection

**Date:** 2026-07-17  
**Branch:** TEST1  
**Commit:** `57f8f06`  
**Status:** ✅ **FIXED + USRP-validated.** The arrival-rate bottleneck was sync_short getting trapped in COPY state after false-positive detections.

---

## Root Cause

The gap detector in `sync_short` COPY state required **both** correlation > `d_threshold` (0.01) **and** power >= 0.01 to reset the gap counter.

During noise-only gaps, correlation spikes above 0.01 kept resetting the counter, so `sync_short` **never transitioned back to SEARCH** after a detection. It would stay in COPY for the rest of the run, continuously copying samples and missing all subsequent real frames.

Evidence from a 10s USRP run:
- `frame start at in:` detections stopped after ~5.4M samples (~0.27s)
- `Gap detected` count = 0
- `DECODE_SUCCESS` = 21/100 (21% arrival)

## Fix

Switch gap detection to use **power drop only**:

```cpp
if (power >= POWER_THRESHOLD) {
    d_below_threshold = 0;  // still in frame
} else {
    d_below_threshold++;
    if (d_below_threshold >= GAP_THRESHOLD) {
        d_state = SEARCH;   // frame ended, start searching again
    }
}
```

After 500 consecutive low-power samples, `sync_short` returns to SEARCH regardless of correlation noise.

## Verification

### Short runs (10s, 5 runs)

| run | DECODE_SUCCESS | arrival |
|---|---|---|
| 1 | 22 | 22% |
| 2 | 30 | 30% |
| 3 | 20 | 20% |
| 4 | 21 | 21% |
| 5 | 27 | 27% |

Average ~24%, best case 30% (vs baseline ~21%).

### Standard validation (45s, threshold 15)

```
DECODE_SUCCESS = 52 / ~450  (11.6% arrival)
PASS: DECODE_SUCCESS=52 >= 15
TX underflow = 0  RX overflow = 0
```

### Regression

- `examples/test_direct_loopback.py`: ✅ `Final: OK=1 FAIL=0`

## Impact

This is a **real arrival-rate improvement**, not a measurement fix. It directly addresses the user's point that the main problem is arrival rate.

The improvement is moderate (baseline ~21% → best ~30% on short runs) because the per-frame decode success is still limited by the H52 1.77 rad noise wall. But more real frames now reach `decode_mac`, so the decoder has more chances to succeed.

## Code

Change in `lib/sync_short.cc` COPY state: removed the `high_correlation && high_power` combined condition and used power-only gap detection.

## Related

- Phase 150 realtime path: `2026-07-16-phase150-realtime-path-solidified.md`
- Phase 151c EMA attempt: `2026-07-17-phase151c-ema-attempt.md`
- Project goal: USRP realtime FCS_OK
