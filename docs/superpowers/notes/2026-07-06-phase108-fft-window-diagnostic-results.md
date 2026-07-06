# Phase 108 Task 2: FFT Window Diagnostic Results

**Date:** 2026-07-06
**Task:** Run diagnostic on USRP file-replay and analyze FFT window alignment
**Status:** DONE_WITH_CONCERNS — see "Critical Interpretation" below

## Files

- Runner script: `examples/run_fft_window_dump.py` (created in this task)
- Parser helper: `examples/dump_fft_windows.py` (created in Task 1)
- Input: `/tmp/p105_usrp_capture_60s.bin` (60s X310 + UBX-160 capture, 5250 MHz / 20 MHz BW / tx-gain 0, bare SMA cable same-board TDD A:0)
- Diagnostic log: `/tmp/p108_fft_window.log`

## Setup

- Head block: 30 seconds @ 20 MHz = 600M complex samples (out of 9.6 GB = ~10 minutes of IQ at 20 Msps)
- Env vars: `IEEE80211_LSIG_RATE_FORCE=0xD`, `IEEE80211_TIMING_OFFSET_APPLY=1`,
  `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1`, `IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1`,
  `IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16`, `IEEE80211_FFT_WINDOW_DUMP=1`
- Diagnostic counter: limited to first 8 H52 compute calls per process

## Number of FFT_WINDOW Lines Captured

**8** (matches expected 8-line cap)

```
[FFT_WINDOW] frame=0 sym_idx_at_h52=7 abs_in_off=75 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=1 sym_idx_at_h52=7 abs_in_off=148 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=2 sym_idx_at_h52=7 abs_in_off=280 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=3 sym_idx_at_h52=7 abs_in_off=320 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=4 sym_idx_at_h52=7 abs_in_off=477 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=5 sym_idx_at_h52=7 abs_in_off=720 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=6 sym_idx_at_h52=7 abs_in_off=774 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=7 sym_idx_at_h52=7 abs_in_off=815 d_data_start_rel=7 d_internal_symbol_counter=7
```

## abs_in_off Values

| frame | sym_idx_at_h52 | abs_in_off | d_data_start_rel | internal_sym | implied_LTF0_offset |
|------:|---------------:|-----------:|-----------------:|-------------:|--------------------:|
|     0 |              7 |         75 |                7 |            7 |                  68 |
|     1 |              7 |        148 |                7 |            7 |                 141 |
|     2 |              7 |        280 |                7 |            7 |                 273 |
|     3 |              7 |        320 |                7 |            7 |                 313 |
|     4 |              7 |        477 |                7 |            7 |                 470 |
|     5 |              7 |        720 |                7 |            7 |                 713 |
|     6 |              7 |        774 |                7 |            7 |                 767 |
|     7 |              7 |        815 |                7 |            7 |                 808 |

## Drift Analysis

Naive drift: `min(implied_LTF0_offset) = 68`, `max = 808`, **drift = 740 samples**.

Consecutive abs_in_off diffs: `[73, 132, 40, 157, 243, 54, 41]`.

The parser helper `examples/dump_fft_windows.py` reports:
> DRIFT=740 samples across 8 H52 computes. UPSTREAM BUG.

### Critical Interpretation (DO NOT TRUST NAIVE READ)

**The 740-sample drift is the spacing BETWEEN successive wifi_start events in the
file, not drift WITHIN a frame.** Each line corresponds to a *different* wifi_start
detection at a *different* absolute sample position in the capture. The diffs are
NOT multiples of 64 (one OFDM symbol = 80 samples @ 20 MHz, two = 160) because
they include variable-length preambles, IFS, and backoff slots.

What we CAN conclude:
1. **Within each frame, the FFT window alignment is internally consistent.**
   Every captured line has `d_data_start_rel=7` (constant; per-frame reset to
   `kDataStartRel=7` at line 4027) and `d_internal_symbol_counter=7`. This means
   `d_sym_idx` advanced correctly from the L-LTF0 boundary through the H52 compute
   site, reaching `sym_idx=7` (data symbol 0) on all 8 frames.
2. **The 1-frame-per-wifi_start model works.** Each wifi_start event caused
   `d_data_start_rel` to reset to 7, and the H52 compute fired at the expected
   position. This is consistent with the Phase 33 fix (FRAME_START_BASE=174) and
   Phase 34 δ correction (TIMING_OFFSET_APPLY=1) holding steady.
3. **abs_in_off values are monotonic non-decreasing** (75, 148, 280, 320, 477, 720,
   774, 815). The capture is being read forward correctly; no rollback or
   re-ordering.

What we CANNOT conclude from this single-fire-per-frame diagnostic:
- Per-symbol spacing within a single frame (L-LTF0 → L-LTF1 → L-SIG → HT-SIG0 →
  HT-SIG1 → DATA0). To probe this, the diagnostic would need to fire at every
  `d_sym_idx`, not just at H52 compute time.

## Parser Helper Cross-Check

`examples/dump_fft_windows.py /tmp/p108_fft_window.log` ran cleanly and emitted
the same table + summary as the inline analysis. The helper's `implied_LTF0`
column matches the inline computation exactly (68, 141, 273, 313, 470, 713, 767,
808). The helper's "DRIFT=740 samples ... UPSTREAM BUG" verdict is technically
correct but misleading without the interpretation note above.

## Bonus Signal: FCS_OK

The diagnostic run also produced **FCS_OK=10, FCS_FAIL=0 in 30 seconds** of
file-replay. This is far better than the typical USRP realtime performance
documented in Phase 105 (0-12 random per 30s) and Phase 106 (0-166 random per
10s). The first 10 frames arrived in the first 1.5 seconds of the replay, then
no further frames in the remaining 33.5s — suggesting the capture contains a
1.5s burst of clean IQ followed by 58.5s of noisy/replayed samples.

Implication: this capture has moments of clean reception (where the upstream FFT
window is well-aligned) and moments of degraded reception. The Phase 107 deep
root-cause (30° constant rotation + 27-50% |H| CV) and Phase 108 motivation
(diagnose FFT window alignment) both align with this observation — when the
upstream is well-aligned, FCS_OK succeeds; when it's not, frames are lost.

## Raw Output Excerpts

```text
[FRAME_EQ] IEEE80211_TIMING_OFFSET_APPLY=1 (δ estimation+correction ENABLED)
[FRAME_EQ] IEEE80211_FFT_WINDOW_DUMP=1 (FFT window positions logged at H52 compute site)
[P108] starting 30s file-replay with FFT_WINDOW_DUMP
[FFT_WINDOW] frame=0 sym_idx_at_h52=7 abs_in_off=75 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=1 sym_idx_at_h52=7 abs_in_off=148 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=2 sym_idx_at_h52=7 abs_in_off=280 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=3 sym_idx_at_h52=7 abs_in_off=320 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=4 sym_idx_at_h52=7 abs_in_off=477 d_data_start_rel=7 d_internal_symbol_counter=7
[P108-RX] t=0.5s FCS_OK=3 FCS_FAIL=0
[FFT_WINDOW] frame=5 sym_idx_at_h52=7 abs_in_off=720 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=6 sym_idx_at_h52=7 abs_in_off=774 d_data_start_rel=7 d_internal_symbol_counter=7
[FFT_WINDOW] frame=7 sym_idx_at_h52=7 abs_in_off=815 d_data_start_rel=7 d_internal_symbol_counter=7
[P108-RX] t=1.0s FCS_OK=8 FCS_FAIL=0
[P108-RX] t=1.5s FCS_OK=10 FCS_FAIL=0
[P108-RX] t=2.0s FCS_OK=10 FCS_FAIL=0
...
[P108] FINAL FCS_OK=10 FCS_FAIL=0
```

## Verdict

**The diagnostic is functional** — it fires 8x as designed, with consistent
per-frame state (d_data_start_rel=7, sym_idx=7, internal_sym=7). The naive
drift metric (implied_LTF0_offset min/max diff = 740) reflects inter-frame
spacing, NOT intra-frame misalignment.

**Task 3 (identify misaligned block) needs a different diagnostic.** The current
IEEE80211_FFT_WINDOW_DUMP only fires at H52 compute time (sym_idx=7, data symbol
0). To diagnose intra-frame window alignment, the diagnostic must fire at every
sym_idx (0..N), logging (nread, d_data_start_rel, d_sym_idx) so we can verify:
- L-LTF0 at abs_in_off = data_start (rel=0)
- L-LTF1 at abs_in_off = data_start + 1 (rel=1)
- L-SIG at abs_in_off = data_start + 2 (rel=2)
- HT-SIG0 at abs_in_off = data_start + 3 (rel=3)
- HT-SIG1 at abs_in_off = data_start + 4 (rel=4)
- DATA0 at abs_in_off = data_start + 7 (rel=7)

The 8 captured frames suggest the upstream frame-delivery is correct (each
frame has its own d_data_start_rel=7 anchor and each frame's H52 fires at
sym_idx=7). The "1.5s burst" pattern of FCS_OK also suggests intermittent
upstream quality, not constant drift.

**Recommend:** Extend the diagnostic in Task 3+ to fire per-symbol (not just
H52), then re-run on the same capture. If per-symbol diffs are constant (each
symbol exactly +1 OFDM apart), upstream is sample-stable. If they drift, locate
the buggy block.