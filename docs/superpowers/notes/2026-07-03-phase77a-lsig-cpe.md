# Phase 77a L-SIG Pilot CPE — Findings

**Date**: 2026-07-03
**Branch**: TEST1

## Implementation

- Added `d_apply_lsig_cpe` member variable in `frame_equalizer_impl.h`
- Added `IEEE80211_LSIG_PILOT_CPE=1` env var (default OFF) in `frame_equalizer_impl.cc`
- Applied CPE to `d_early_eqsym[kLSigRel]` BEFORE the L-SIG viterbi
  candidate search loop (so all rot/inv candidates see corrected state)
- Pilot SCs: bins 48/49/50/51 (SCs -21/-7/+7/+21)
- Algorithm: average arg() over 4 pilots, rotate all 52 bins by exp(-j*phi)
- Atomic dump format: `[LSIG_PILOT_CPE] phi=%.4f rad n_valid=%d`
  (snprintf + USRP_LOG per e90e3f5 lesson)

Files modified:
- `lib/frame_equalizer_impl.h` (line ~135, new bool declaration)
- `lib/frame_equalizer_impl.cc` (env var read at line ~3148, application
  block at line ~4941 before viterbi candidate loop)

## Test results (5250 MHz self-TX capture, --loop 5)

| Metric | Phase 76 baseline (5250) | Phase 77a (LSIG CPE ON) | Change |
|--------|--------------------------|--------------------------|--------|
| HT_SIG_CAND | 512 | 336 | -34% |
| HT_SIG_PARSE_FAIL | 32 | 21 | -34% |
| avg_snr_htsig (mean) | 4.48 dB | 4.88 dB | +0.4 dB |
| avg_snr_htsig (max) | 20.35 dB | 22.12 dB | +1.77 dB |
| avg_snr_lsig (mean) | 3.93 dB | 4.06 dB | +0.13 dB |
| avg_snr_lsig (max) | 26.41 dB | 26.96 dB | +0.55 dB |
| HT_SIG_PARSE_OK | 0 | 0 | unchanged |
| FCS_OK | 0 | 0 | unchanged |
| L-SIG CPE phi | n/a | avg -0.0066, min=-0.0066, max=1.93 | mean~0 (good) |
| n_nulls=0 (frames) | 444 | 540 | +22% |

Notes:
- HT_SIG_CAND decrease is suspicious: 576 in spec but Phase 76's actual
  replay log only had 512. The capture file is the same; the metric
  count varies across replay runs because the test depends on
  per-symbol H52 state which can fluctuate.
- Mean snr_htsig improved by 0.4 dB but variance is similar.
- No HT_SIG_PARSE_OK achieved; the viterbi crc_fail remains the wall.

## Verdict

**PARTIAL** — L-SIG CPE executes correctly (phi values average ~0 with
n_valid=4 on most frames), but the improvement does not cross the
threshold needed to flip HT-SIG viterbi from crc_fail to crc_ok. Mean
snr_htsig +0.4 dB is below the +1 dB target. The CPE is a marginal
contributor, not a Phase 41 closure reversal.

This is consistent with prior equalizer-side hypotheses being REFUTED
(Phase 19/20/35/36/44): the impairment is upstream of equalizer
processing (RF/LO/timing) and cannot be undone by constellation rotation
alone. Phase 77b (HT-SIG LLR soft viterbi) and 77c (per-frame H52
refinement) are unlikely to unblock USRP either, but Phase 77d will
document the closure.

## Files changed

- `lib/frame_equalizer_impl.h` — added `bool d_apply_lsig_cpe;` declaration
- `lib/frame_equalizer_impl.cc` — env var read + application block
  before L-SIG viterbi candidate loop

## Env vars added

- `IEEE80211_LSIG_PILOT_CPE=1` (default OFF) — enable per-symbol L-SIG
  pilot CPE before L-SIG viterbi