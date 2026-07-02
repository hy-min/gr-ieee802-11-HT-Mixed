# Phase 78b Task 1b: HTSIG_EQ_DUMP Diagnosis

**Date:** 2026-07-03
**Status:** FIXED (DONE)

## Problem

Phase 78b-1 replay (p76_selftx_5250.bin) with all dumps enabled produced:
- DELTA_PER_SYMBOL: 10 actual entries (counter reached 10)
- HTSIG_EQ_DUMP: **0 actual entries** (only the init line)
- HT_SIG_CAND: 80, HT_SIG_PARSE_FAIL: 5, is_ht_frame=1: 14

## Root Cause

The HTSIG_EQ_DUMP block at `lib/frame_equalizer_impl.cc:5225` was nested
inside THREE additional gates that DELTA_PER_SYMBOL did not have:

1. **per-(rot_lsig, inv_lsig) loop** (lines 5068-5069): the candidate-search
   loop introduced in Phase 70.
2. **`if (!lsig_ok) continue;`** at line 5118: any frame whose L-SIG viterbi
   failed was skipped at this point, before reaching the dump block.
3. **`if (n_rot > 1) continue;`** at line 5127: Phase 70 candidate-search
   mode (`IEEE80211_LSIG_VITERBI_CANDIDATE=1`) skips the body for all
   candidate iterations; only the post-loop `goto lsig_body_entry` fires
   once with the winning candidate.

The result: on USRP, where 5/8 HT_SIG_PARSE_FAIL frames had L-SIG viterbi
failures, the dump block never fired. DELTA_PER_SYMBOL sits at line 4862,
sibling to `if (ht_parse_condition)` and OUTSIDE the rot/inv candidate loop,
so it fired correctly.

## Fix

Moved the HT-SIG diagnostic dump block from line 5225 (inside the rot/inv
candidate loop) to line 4772, immediately after Phase 34 δ correction
completes, sibling to the DELTA_PER_SYMBOL block.

The new location:
- Fires for **every** HT-capable frame reaching counter=4, regardless of
  whether L-SIG viterbi succeeded
- Sees post-CFO+SFO+δ state, matching the original comment intent
- Does NOT include Phase 35 per-symbol pilot CPE or Phase 60 H52 null
  interp (those run later in the candidate loop). If post-CPE constellation
  is needed, a second dump site should be added after the loop completes.

Code change: `lib/frame_equalizer_impl.cc`
- Removed: lines 5220-5363 (~143 lines of dump block)
- Inserted: lines 4754-4908 (~169 lines including a longer comment
  explaining the move and Phase 78b-1b's motivation)
- Net: +26 lines, +1 comment block

## Verification

After `make && make install`, re-ran the same replay:

```
OLD: HTSIG_EQ_DUMP: 0 actual entries
NEW: HTSIG_EQ_DUMP: 10 actual entries (g_htsig_dump_counter saturated)
```

Other counts in the new replay (`/tmp/p78b_dump_v2.log`):
- HTSIG_EQ_DUMP: 11 (1 init + 10 dumps)
- HTSIG_BIN_DUMP: 11 (also previously gated, now fires)
- DELTA_PER_SYMBOL: 11 (unchanged, was already at correct location)
- HT_SIG_CAND: 96 (was 80)
- HT_SIG_PARSE_FAIL: 6 (was 5)
- is_ht_frame=1: 28 (was 14)
- LSIG_DECODE OK: 0 (was 34 — Phase 60 pre-clean now rejects L-SIG; orthogonal
  to this fix)

EQ_DUMP summary statistics from first 3 frames confirm Phase 38's pathology:
- frame 0: htsig0 mean|re|=0.817 mean_im=0.094 std_im=1.471
- frame 1: htsig0 mean|re|=0.784 mean_im=0.159 std_im=1.251
- frame 2: htsig0 mean|re|=1.979 mean_im=0.664 std_im=4.427

QBPSK should give |real|/|imag| < 0.3 with std_im < 0.3 (well-formed
constellation). Observed mean|re| is 0.8-2.0 and std_im is 1.3-4.4 — the
equalizer wall is real and EQ_DUMP now has the data to characterize it.

## Files

- `/home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc` (commit pending)
- `/home/hy/gr-ieee802-11/docs/superpowers/notes/p78b_dump_v2.log`
  (verification log, 25128 lines)
- `/home/hy/gr-ieee802-11/docs/superpowers/notes/p78b_dump.log`
  (original Phase 78b-1 log, 25416 lines, 0 dumps)

## Implications for Phase 78b-2 / 78b-3 / 78b-4

- EQ_DUMP data is now available for the first 10 HT-capable frames.
- These include both L-SIG OK frames AND L-SIG viterbi_fail frames.
- Synthetic reference (78b-3) and USRP comparison (78b-4) can now
  compute per-frame EQ stats over the same population that DELTA_PER_SYMBOL
  captures, giving a complete equalizer-wall characterization.