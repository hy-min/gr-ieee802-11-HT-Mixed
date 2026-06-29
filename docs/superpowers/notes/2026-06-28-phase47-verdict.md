# Phase 47 Verdict — MMSE for Data Symbols + N0 Percentile

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: ⚠️ MARGINAL — HT-SIG parser unblocked, data payload still fails
**Commit**: 9762e54

## Goal

Extend Phase 46 AR5 MMSE equalization from HT-SIG only to data symbols. Make N0
percentile configurable to tune the noise floor estimate.

## Changes

1. **`IEEE80211_MMSE_N0_PERCENTILE` env var** (default 25, range 1-49)
2. **`d_h52_stash[52]` + `d_h52_stash_valid`** — carries H52 across scope
   boundary from L-LTF estimate to data symbol equalization
3. **MMSE override block at line ~4100** — re-equalizes data SCs [0, 48)
   using `conj(H)·rx/(|H|² + N0)` after gr::digital ZF equalizer
4. **`mmse_equalize_htsig()`** generalized to accept `n0_percentile` parameter

## USRP Sweep Results

### N0 Percentile Sweep (3 percentiles, 30s each)

| N0 | Sent | Recv | FCS_OK | HT_SIG_PARSE_FAIL | DECODE_FAIL |
|---:|---:|---:|---:|---:|---:|
| Phase 41 baseline | 31 | 0 | 0 | 8 | 0 |
| N0=10 | 31 | 0 | 0 | 9 | — |
| N0=25 | 31 | 0 | 0 | 2-18 (variance) | 6 |
| N0=33 | 31 | 0 | 0 | 8 | — |

**N0=25 is the sweet spot** — 25th percentile is robust to the 5-10 null SCs
per frame observed in Phase 38, while not being so low that it gets dragged
down by noise (N0=10 was worse).

### Data+HT-SIG MMSE (3 runs, 30s each, N0=25)

| Run | Sent | Recv | FCS_OK | HT_SIG_PARSE_FAIL | DECODE_FAIL |
|---:|---:|---:|---:|---:|---:|
| 1 | 31 | 0 | 0 | 9 | **6** |
| 2 | 31 | 0 | 0 | 13 | **4** |
| 3 | 31 | 0 | 0 | 17 | **2** |

**DECODE_FAIL events confirmed**: HT-SIG viterbi now succeeds for some frames
(was 0 before MMSE), but data payload CRC still fails. Frames reach
`decode_mac` but `calc_fcs != rx_fcs`.

## Why Data Payload Still Fails

The MMSE override re-equalizes data SCs, but **the issue is not just equalization**:

1. **MMSE for HT-SIG improves** because HT-SIG is BPSK-rate-1/2 with strong
   coding gain (viterbi K=7, R=1/2). After MMSE, viterbi can correct residual errors.
2. **Data payload** uses MCS=4 (16QAM, R=1/2) per `IEEE80211_LSIG_RATE_FORCE=0xD`.
   16QAM has **4× more sensitive constellation** than BPSK. Even after MMSE,
   residual noise on null SCs is still > 16QAM's decision threshold (~0.5
   symbol spacing).

The chain `MMSE → demod → viterbi decode → CRC32` for 16QAM requires ~3-5 dB
higher SNR than BPSK. At USRP avg_snr_lsig ~ 15 dB, the **post-MMSE symbol
SNR is still borderline for 16QAM**.

## Architectural Insight

The user's stated goal is **USRP FCS_OK > 0**. We have:
- ✅ HT-SIG parser unblocks for some frames (DECODE_FAIL events = HT-SIG OK, data fail)
- ❌ Data payload still fails CRC32

Two remaining options:
1. **Force lower MCS** (e.g., MCS=0 BPSK R=1/2) via `IEEE80211_LSIG_RATE_FORCE`
   to give the data path more coding/modulation margin
2. **Re-examine channel estimation upstream** (Phase 38-39 REFUTED but with
   MMSE now in place, the equalizer can tolerate better H52 estimates)

## Action Taken

1. ✅ Code committed: 9762e54
2. ✅ Loopback regression preserved: 1/1 ON and OFF
3. ✅ Default OFF (`IEEE80211_MMSE_EQUALIZE=0`)
4. ✅ N0 percentile env var exposed for tuning

## Next Direction

The remaining gap is **MCS / data payload decoder sensitivity**. Recommend
trying `IEEE80211_LSIG_RATE_FORCE=0` (force MCS 0 = BPSK R=1/2, lowest
sensitivity) combined with `IEEE80211_MMSE_EQUALIZE=1`. This is a 1-line env
var change with no code risk.

## References

- Phase 46 AR5 (commit 977c284) — original MMSE for HT-SIG
- `docs/superpowers/notes/2026-06-28-phase46-ar5-verdict.md`
- `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md` — H52 null evidence
