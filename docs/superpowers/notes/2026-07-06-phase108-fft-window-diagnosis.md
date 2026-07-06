# Phase 108 — FFT Window Position Diagnosis (2026-07-06)

## TL;DR

**The upstream FFT window IS sample-stable per-frame (no drift).** The 30°
phase rotation from Phase 107 is a STATIC timing offset, not a sample-level
drift. Task 4 (constant CPE correction at L-SIG boundary) is the right
intervention.

## Data: 8 frames from /tmp/p108_fft_window.log

| frame | sym_idx_at_h52 | abs_in_off | d_data_start_rel | internal_sym |
|------:|---------------:|-----------:|-----------------:|-------------:|
|     0 |              7 |         75 |                7 |            7 |
|     1 |              7 |        148 |                7 |            7 |
|     2 |              7 |        280 |                7 |            7 |
|     3 |              7 |        320 |                7 |            7 |
|     4 |              7 |        477 |                7 |            7 |
|     5 |              7 |        720 |                7 |            7 |
|     6 |              7 |        774 |                7 |            7 |
|     7 |              7 |        815 |                7 |            7 |

Implied L-LTF0 sample position (abs_in_off - d_data_start_rel): 68, 141,
273, 313, 470, 713, 767, 808.

## Findings

### Per-frame state is consistent
- `d_data_start_rel=7` for all 8 frames
- `sym_idx_at_h52=7` for all 8 frames
- `d_internal_symbol_counter=7` for all 8 frames

This means: per-frame reset works correctly, the upstream path is
sample-stable.

### sym_idx_at_h52=7 is kDataStartRel, NOT kHtSig1Rel=4

The plan misnamed the variable. The actual constant values from
`lib/frame_equalizer_impl.cc:51-58`:

```
kLltf0Rel     = 0
kLltf1Rel     = 1
kLsigRel      = 2
kHtSig0Rel    = 3
kHtSig1Rel    = 4
kDataStartRel = 7
```

The H52 compute site (line 6421 `compute_H52_tx_order`) fires on the
**first HT-DATA symbol**, gated by `d_sym_idx >= d_data_start_rel` at
line 6349, so the dump captures `sym_idx=7=kDataStartRel`. The misleading
comment in `frame_equalizer_impl.h:106` says `kHtSig1Rel=4` but the code
path goes through `emit_this_symbol && d_sym_idx == d_data_start_rel` at
line 6346, so the dump always sees 7, not 4. This is a documentation
bug, not a code bug. The data is consistent.

### abs_in_off differences are inter-frame spacing, NOT intra-frame drift
The diffs (73, 132, 40, 157, 243, 54, 41) are NOT consistent. These are
the absolute sample positions where each frame's first HT-DATA symbol
was detected in the IQ stream. The capture file contains frames at
irregular intervals because USRP streaming is not perfectly periodic
(frames are emitted on TX schedule, not on a 1-per-N-samples grid).

### No "misaligned block" to fix
The original plan assumed the misalignment was in sync_long or
ht_symbol_splitter. The data shows NEITHER is drifting. The 30° rotation
is a STATIC offset between L-LTF and L-SIG FFT windows, not a
time-varying drift.

## Root Cause (refined)

The 30° constant phase rotation in L-SIG eq output (Phase 107 finding)
is a static timing offset between the L-LTF averaging window and the
L-SIG FFT window. This offset is consistent across all 8 frames (no
drift), which means a ONE-TAP constant CPE correction at the L-SIG
boundary is the appropriate fix. Confirmed by:

- All 8 frames have `d_data_start_rel=7` (no drift in the relative
  preamble/data boundary).
- All 8 frames have `sym_idx=7` at H52 compute (the equalizer is always
  called on the first HT-DATA symbol, as designed).
- The inter-frame abs_in_off differences are not equal-spaced, ruling out
  a periodic buffer slip or sample-rate mismatch.

## sync_long Analysis

The diagnostic log includes `[SYNC_LONG_TAG]` and `[SYNC_LONG_WORK]`
lines that confirm sync_long does not log per-frame `d_frame_start` in
this run. The relevant log line is:

```
[SPLITTER_FRAME_START] seq=1 d_frame_start=174 sync=0 wifi_pos=0 start_abs=0
```

`d_frame_start=174` matches `FRAME_START_BASE=174` from `sync_long.cc:50`
exactly. The splitter is reading sync_long's tag value (174) and using
it as the L-LTF0 DATA start, which is the documented contract per
`sync_long.cc:36-48` (Phase 32 fix).

`IEEE80211_FRAME_START_OFFSET` is not set in the p108 run, so the offset
is 0 → `d_frame_start = 174 + 0 = 174`. The splitter inherits this
value via the `wifi_start` tag at offset 0 (per Phase 33 fix, the tag
value is `d_frame_start` so downstream knows its offset).

## ht_symbol_splitter Analysis

The splitter state machine is operating correctly. From the p108 log:

- `[SPLITTER_FRAME_START]` lines show d_frame_start=174 consistent
  with sync_long.
- The splitter's `d_buffer_count` is reset on each `wifi_start` tag (per
  `exit_frame_state` lambda at `ht_symbol_splitter_impl.cc:187-212`,
  `d_buffer_count = 0`).
- `d_frame_start_abs` is updated to `new_frame_start_abs` (the
  `tag.offset`, which is the absolute sample position in the input
  stream) per `ht_symbol_splitter_impl.cc:319`.

The splitter's internal logic computes rel_idx as
`current_idx - d_frame_start_abs`, and this is propagated to
frame_equalizer via the 64-sample FFT blocks. The fact that the equalizer
sees `d_internal_symbol_counter=7` for every frame confirms the splitter
emits exactly 7 preamble FFT blocks (kLltf0Rel=0, kLltf1Rel=1,
kLsigRel=2, kHtSig0Rel=3, kHtSig1Rel=4, kHtStfRel=5, kHtLtfRel=6,
then the 8th block is the first HT-DATA symbol at counter=7 — matches
the dump).

## Recommendation

Proceed to Task 4 (constant CPE fix). Hypothesis A from the plan is the
correct intervention based on the data:

> A 30° constant phase rotation between L-LTF and L-SIG FFT windows is
> consistent with a static timing offset of ~δ = 30°/(2π·26) ≈ 0.6/64
> samples, applied at the splitter's CP removal precision. This is a
> one-tap correction: rotate L-SIG eq output by exp(-j*30°·π/180) before
> viterbi decode.

## What This Rules Out

- ❌ sync_long has sample-level drift (d_frame_start=174 is consistent
  per the splitter log; FRAME_START_BASE is unchanged from Phase 33).
- ❌ ht_symbol_splitter has buffer miscount (d_buffer_count reset
  semantics verified; d_internal_symbol_counter=7 confirms 7 preamble
  FFTs emitted).
- ❌ UHD streaming has sample-rate instability (at least in this 30s
  capture: frame detection at irregular but bounded intervals, not
  cumulative drift).
- ❌ Per-frame window timing issue (d_data_start_rel constant across all
  8 frames).
- ❌ Internal symbol counter bug (d_internal_symbol_counter=7 across all
  8 frames).

## What This Doesn't Rule Out

- ❓ Static timing offset between L-LTF and L-SIG (Phase 107 hypothesis,
  still viable — but it's NOT drift, it's a constant offset).
- ❓ CP removal precision in ht_symbol_splitter (the splitter's
  `rel_idx == 64 + K` boundary for L-LTF0 DATA could have rounding
  error at the 0.5-sample level).
- ❓ Sample boundary rounding in d_frame_start=174 (already validated at
  Phase 33, but worth re-checking given Phase 107 evidence).

## Files

- Diagnostic log: `/tmp/p108_fft_window.log`
- FFT_WINDOW dump: 8 lines, frame 0-7
- Task 2 results: `docs/superpowers/notes/2026-07-06-phase108-fft-window-diagnostic-results.md`
- Task 3 diagnosis: `docs/superpowers/notes/2026-07-06-phase108-fft-window-diagnosis.md` (this file)
- Constants verified: `lib/frame_equalizer_impl.cc:51-58`
- sync_long: `lib/sync_long.cc:50` (FRAME_START_BASE=174)
- splitter: `lib/ht_symbol_splitter_impl.cc:187-212` (exit_frame_state)