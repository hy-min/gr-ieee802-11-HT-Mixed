# Phase 146 Verdict: Realtime Capture-Truncation ROOT CAUSE (GNU Radio Scheduler Stall)

**Date:** 2026-07-15
**Branch:** TEST1
**Status:** ROOT CAUSE FOUND + culprit block isolated + fix validated in principle.
**Method:** superpowers:systematic-debugging (Iron Law: root cause before fix).

---

## Goal

Phase 145c proved the RX decoder is CORRECT (file-replay FCS_OK=5) but realtime
capture truncates to ~0.03–0.3s. Determine the true mechanism.

## Root Cause (FINAL)

> **`wifi_phy_rx` is a full transceiver hier used for RX only. Its IDLE TX path
> contains tag-starved blocks (`ofdm_cyclic_prefixer`, and likely
> `tagged_stream_mux`) that wait for a `packet_len` tag that never arrives
> (`mac_in` unconnected). A tag-starved `ofdm_cyclic_prefixer` STALLS the entire
> GNU Radio flowgraph ~5000× (263 MHz → 0.035 MHz).**

The decode algorithm is fast and correct. The realtime blocker is a fixable
flowgraph-structure bug — **NOT** the 1.77 rad noise floor / equalizer wall that
consumed 100+ phases.

## Evidence (all hardware-free, reproducible on `/tmp/p146_real_iq.fc32`)

| Test | Result |
|---|---|
| Baseline `src→head→null` | 299 MHz |
| Front-end `sync_short_fused→sync_short→sync_long` | **263 MHz** |
| +splitter (depth 2) | 248 MHz |
| +stream_to_vector+FFT (depth 3) | 177 MHz |
| +frame_equalizer (depth 4) | 160 MHz, `[LSIG_DECODE] OK` |
| Full manual RX chain +decode_mac (depth 5) | **207–263 MHz, 38 L-SIG OK + 1 FCS_OK** |
| Same blocks inside `wifi_phy_hier` | **~0.035 MHz** |
| Per-thread CPU during stall (`/proc/PID/task`) | **0 jiffies → NOT compute-bound** |
| Fast chain + idle `wifi_phy_hier` in same graph | **STALLS (killed 90s)** |
| Fast chain + single idle `ofdm_cyclic_prefixer` | **STALLS (exit 124), any buffer size** |

## Consistency check (explains 145c)

- `capture_usrp_txrx.py` works: wifi_phy_hier used for TX only (TX driven, RX-idle
  path has NO ofdm_cyclic_prefixer) + manual RX capture → no starved cp → complete.
- `test_usrp_minimal_loopback.py` fails: wifi_phy_rx (full hier) has idle TX path
  WITH ofdm_cyclic_prefixer → stall → truncated capture.

## REFUTED levers / hypotheses this session

- **L2 sync_long early-out** (`IEEE80211_SYNC_LONG_EARLYOUT`, default ON, committed):
  correctness PASSES (FCS_OK=1, no regression) but REFUTED as effective — sync_long
  is fast (263 MHz front-end), not the bottleneck. Kept as behavior-identical opt-out.
- "sync_long consuming without producing" (145c phrasing): physically inconsistent
  with GNU Radio fan-out (that would drain, not backpressure).
- "compute-bound": real-IQ shows 0 CPU across all threads; it is a scheduler stall.
  (The earlier /dev/null test was confounded by a gaussian-noise false-frame storm
  that does not occur on real USRP noise — only 4 false frames on real capture.)

## FIX (validated in principle)

Make the realtime RX path **RX-only** (no idle TX blocks). Manual RX-only chain
runs 207–263 MHz and decodes frames. Options:
- (A) RX-only flowgraph in the realtime test (replace wifi_phy_rx full hier).
- (B) `rx_only` flag in `wifi_phy_hier` that skips TX-block creation (structural).
- (C) Un-starve the idle TX path (feed packet_len tag) — hacky, NOT recommended.

Because the decoder is PROVEN correct (145c), an RX path at 260 MHz in realtime
should yield realtime FCS_OK. Needs hardware validation (cable budget).

## Harnesses built (reusable, hardware-free)

- `p146_rx_throughput_probe.py` — RX-chain throughput (gaussian noise or `--file`),
  prints MHz + verdict.
- `p146_bisect.py` — incremental sub-chain bisection depth 0–5, plus
  `--with-idle-hier` / `--with-ofdm-cp` culprit toggles.
- `/tmp/p146_real_iq.fc32` — 5s real USRP capture (100M samples, 5250 cable tx-gain 0).

## Code changes (committed)

- `lib/sync_long.cc`: Phase 146 L2 noise early-out in `search_frame_start()`
  (max-scan before sort), gated by `IEEE80211_SYNC_LONG_EARLYOUT` (default ON).
  Behavior-identical; correctness-verified. (Kept; not the bottleneck but harmless.)
- `CLAUDE.md`: Phase 146 root-cause note (corrects 145c mechanism).

## Related

- Phase 145c: `docs/superpowers/notes/2026-07-14-phase145c-file-replay-breakthrough.md`
- Phase 145: `docs/superpowers/notes/2026-07-13-phase145-lsig-noise-like-rootcause.md`
- User goal (feedback_no_closure_usrp_fcs_ok): this is a NEW attack path toward
  realtime FCS_OK, orthogonal to the equalizer/noise wall.
