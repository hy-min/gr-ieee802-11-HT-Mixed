# Phase 151c: Adaptive Threshold EMA Smoothing — PARTIAL / INSTABILITY DISCOVERED

**Date:** 2026-07-17  
**Branch:** TEST1  
**Commit:** `2fdcb02`  
**Status:** EMA smoothing achieves determinism in the `p151_synconly` flowgraph, but **is NOT
stable across all buffer configurations** (`p148_funnel` occasionally explodes to 394 detections).
The root cause remains the `sync_short` state-machine/chunk-boundary positive-feedback loop.

---

## Background

- Phase 148 root-caused offline-replay non-determinism to a chunk-partition-dependent bug in
  `sync_long`.
- Phase 151 implemented an opt-in chunk-invariant accumulation path in `sync_long`
  (`IEEE80211_SYNC_LONG_CHUNK_INVARIANT=1`).
- Phase 151b isolated the remaining non-determinism to `sync_short`'s adaptive threshold:
  `d_corr_window` contents differ run-to-run because the SEARCH/COPY state machine + early
  `break` on frame detection changes which samples enter the window depending on chunk boundaries.

## What was tried in Phase 151c

Add opt-in EMA smoothing to the adaptive threshold:

```bash
IEEE80211_SYNC_SHORT_ADAPTIVE_EMA_ALPHA=0.75
```

Formula:
```
new_thresh = alpha * target + (1 - alpha) * prev_thresh
```

Goal: filter tiny p90 run-to-run jitter so the threshold does not flip frame detections across
identical replays.

## Results

### `p151_synconly` flowgraph (no `set_min_output_buffer`)

| alpha | 8-run detections | verdict |
|---|---|---|
| 0.0 | 40, 44, 43, 42 | non-deterministic |
| 0.7 | 100 × 8 | **DETERMINISTIC** |
| 0.75 | 100 × 8 | **DETERMINISTIC** |
| 0.9 | 48–58 | non-deterministic |

At `alpha=0.75` the sync-only chain is perfectly constant across 8 runs.

### `p148_funnel` flowgraph (`set_min_output_buffer(1e6)`)

| run | sync_long detections | frame_bytes | fcs_ok |
|---|---|---|---|
| 0 | 49 | 1 | 1 |
| 1 | 49 | 6 | 5 |
| 2 | 49 | 2 | 1 |
| 3 | 49 | 1 | 1 |
| 4 | 50 | 2 | 1 |
| 5 | 50 | 4 | 3 |
| 6 | 49 | 4 | 4 |
| **7** | **394** | 2 | 1 |

Run 7 shows the threshold can **collapse** when the chunk partition happens to hit a resonant
condition, producing ~8× more detections than normal.

## Conclusion

EMA smoothing is **not a robust fix**. It can suppress the positive-feedback loop in some chunk
partitions, but it can also amplify instability in others. The root cause is architectural:
`sync_short` couples frame-detection state transitions to chunk boundaries via early `break` +
`consume_each(i2)`.

## Code preserved

- `lib/sync_short.cc`: diagnostic `win_cksum` in adaptive dump + opt-in `IEEE80211_SYNC_SHORT_ADAPTIVE_EMA_ALPHA`
- `lib/sync_long.cc`: opt-in `IEEE80211_SYNC_LONG_CHUNK_INVARIANT`
- `p151_chunk_determinism_test.py`, `p151_synconly_determinism.py`, `_p151_synconly_flow.py`

All new behavior is **opt-in / default OFF**, so baseline is preserved.

## Recommended next step

Return to the primary project goal: **USRP realtime FCS_OK**. Use statistical ruler (N-run
mean±std) for offline algorithm evaluation. The sync-chain non-determinism is understood and
can be revisited later with an architectural refactor of `sync_short` if a trustworthy ruler
becomes critical.

## Related

- Phase 148 verdict: `2026-07-15-phase148-trustworthy-funnel-verdict.md`
- Phase 151 sync_long chunk-invariant: `lib/sync_long.cc`
- Phase 151b sync_short root-cause: diagnostic code in `lib/sync_short.cc`
