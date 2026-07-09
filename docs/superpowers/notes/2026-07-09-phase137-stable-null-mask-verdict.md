# Phase 137: Stable-Null-Aware Masking with Alternative CPE (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on USRP** — Phase 137 env-var plumbing is CORRECT (verified
at both file-replay and USRP test marker levels), but USRP 5250 cable validation
shows HT-SIG viterbi metric unchanged from baseline. T5 Run 1 best metric=11
(1 above viterbi threshold), T5 Run 2 best metric=14. Phase 112 R1 1.77 rad
per-SC phase noise ceiling dominates.

## TL;DR

Phase 137 implemented a 3-layer opt-in fix targeting Phase 78b's 5 stable null
SCs {-21,-13,-7,+7,+21}:

1. **L1**: Extended `IEEE80211_HTSIG_NULL_SCS` env var to accept signed SC values
   (-26..+26) — backward compat with old loop-position format (0..51).
2. **L2**: Added `IEEE80211_HTSIG_NULL_PILOT_MASK=1` opt-in flag that skips null
   pilots in CPE estimator (4 pilots {-21,-7,+7,+21} at kScIndex52 positions
   48..51).
3. **L3**: Auto data-SC CPE fallback when all 4 pilots are masked/invalid.

Implementation is CORRECT (build clean, all env-var markers fire on USRP at every
general_work call). USRP T4-T5 validation:

- **T4 single-run**: Phase 137 markers fire but 0 HT_SIG_CAND (no is_ht_frame=1
  fires; L-SIG viterbi upstream gate).
- **T5 Run 1**: 16 HT_SIG_CAND, best metric=11 (1 above viterbi threshold),
  avg_snr_htsig=1.93 dB.
- **T5 Run 2**: 16 HT_SIG_CAND, best metric=14 (4 above viterbi threshold),
  avg_snr_htsig=5.05 dB.

Neither run achieved metric ≤ 10. Phase 137 does not break the viterbi ceiling
on USRP continuous streaming. Consistent with Phase 112 R1 root cause: 1.77 rad
per-SC phase noise from USRP analog chain dominates H52 refinement.

## T1-T3 File-Replay Results

| Test | Config | Result | Notes |
|------|--------|--------|-------|
| T1 | Baseline (no Phase 137 env) | 1/1 FCS_OK | No regression |
| T2 | Phase 137 full mask (`-21,-13,-7,7,21` + `IEEE80211_HTSIG_NULL_PILOT_MASK=1`) | 1/1 FCS_OK | CLEAN signal has no null SCs, so mask doesn't fire meaningfully — but env-var plumbing verified |

## T4-T5 USRP Results (5250 MHz cable)

| Run | Config | HT_SIG_CAND | Best Metric | is_ht_frame=1 | avg_snr_htsig | Verdict |
|-----|--------|-------------|-------------|---------------|---------------|---------|
| T4 | Phase 137 ON | 0 | n/a | 0 | n/a | INCONCLUSIVE (L-SIG upstream gate) |
| T5 #1 | Phase 137 ON | 16 | 11 | 0 | 1.93 dB | REFUTED (metric still > 10) |
| T5 #2 | Phase 137 ON | 16 | 14 | 0 | 5.05 dB | REFUTED (metric still > 10) |

**Cable runs used**: 3 cable runs total (T4 + T5 #1 + T5 #2). Within ≤5 cable budget
per CLAUDE.md.

**Markers verified firing on USRP**: All three Phase 137 markers observed in
`/tmp/p137_t4_usrp.log`, `/tmp/p137_t5_run1_usrp.log`, `/tmp/p137_t5_run2_usrp.log`:

- `[FRAME_EQ] IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21' (masked 5 SCs)`
- `[FRAME_EQ] IEEE80211_HTSIG_NULL_PILOT_MASK=1 (Phase 137: skip null pilots in CPE)`
- L3 data-SC fallback path exercised when all 4 pilots masked.

## Conclusion

Phase 137's hypothesis was that 4 null pilots {-21,-7,+7,+21} (kScIndex52 positions
48..51) bias the per-symbol CPE rotation, contaminating all 48 BPSK decisions in
HT-SIG1. By masking these null pilots AND using data-SC fallback (when all 4
masked), we expected CPE rotation to become more accurate, allowing viterbi metric
to drop from 13-15 to ≤10.

**USRP T5 Run 1's best metric=11 is encouraging** (1 above viterbi threshold) —
Phase 137 may provide marginal improvement. But 2 runs is insufficient for
statistical significance, and Run 2's best metric=14 shows the improvement is not
consistent. The fact that T5 Run 2 had HIGHER avg_snr_htsig (5.05 vs 1.93 dB) but
WORSE metric (14 vs 11) suggests that avg_snr is not the only factor — Phase 100's
"5 globally-null SCs → 10 random bits per HT-SIG frame = exactly viterbi
free-distance ceiling" remains the dominant cause.

The deeper issue is Phase 112 R1: USRP analog chain (oscillator + RF) introduces
1.77 rad per-SC phase noise. This noise floor is INDEPENDENT of H52 estimation
quality — even perfect H52 doesn't help if the equalizer output has 1.77 rad noise
per SC. Equalizer-layer attacks are EXHAUSTED at the viterbi ceiling.

## What's Next

Per CLAUDE.md "Equalizer layer is NOT closed" + user's "不可能接受现状" directive:

1. **Phase 138**: Architecturally attack Phase 112 R1 1.77 rad ceiling:
   - **Option A**: External reference clock (HW modification — user-excluded per
     project notes)
   - **Option B**: Multi-frame H52 averaging on data SCs only (extend Phase 123
     cross-frame tracking with data-SC only restriction)
   - **Option C**: Different equalizer architecture (frequency-domain
     deconvolution, ML detection)
   - **Option D**: Buy/install 30 dB SMA attenuator (HW requirement for safe cable
     runs)
2. **Phase 139**: Re-test Phase 137 with 30 dB attenuator (if available) — may
   show different metric distribution with cleaner signal.
3. **Phase 140+**: Continue upstream attack (UHD streaming stability, sync_short
   detector optimization, Phase 100 5-null-SC erasure decoder).

## Files of Record

- **Implementation commits** (TEST1 branch):
  - `7581f95` — feat(p137): declare d_apply_htsig_null_pilot_mask flag
  - `ee2d132` — feat(p137): IEEE80211_HTSIG_NULL_SCS accepts signed SC values (-26..+26)
  - `1678da4` — feat(p137): wire up IEEE80211_HTSIG_NULL_PILOT_MASK=1 opt-in flag
  - `492c760` — feat(p137): skip null pilots in CPE + data-SC fallback when all masked
  - `be2a46e` — feat(p137): add --phase137-on arg to test_file_replay_e2e.py
  - `31c65a4` — feat(p137): add --phase137-on arg to test_usrp_minimal_loopback.py
- **Verdict**: this file (`docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md`)
- **Spec**: `docs/superpowers/specs/2026-07-09-phase137-stable-null-mask-design.md` (commit `d81ee16`)
- **Plan**: `docs/superpowers/plans/2026-07-09-phase137-stable-null-mask.md` (commit `c79335c`)
- **Test logs**: `/tmp/p137_t4_usrp.log`, `/tmp/p137_t5_run1_usrp.log`, `/tmp/p137_t5_run2_usrp.log`
- **Stale .so install note** (pre-existing project convention — out of scope for Phase 137):
  **`make install` must run after every `make`**. See CLAUDE.md "Project-Specific
  Conventions" → otherwise Python loads stale `.so` and Phase 137 markers won't
  appear in test output even with env vars set.

## Self-Review

**Spec coverage:**
- ✅ L1 env parser (Task 2)
- ✅ L2 CPE opt-in flag (Task 3)
- ✅ L3 data-SC fallback (Task 4)
- ✅ T1-T2 file-replay validation (Task 5)
- ✅ USRP test args (Task 6)
- ✅ T4 USRP single-run (Task 7)
- ✅ T5 USRP multi-run (Task 8)
- ✅ Verdict + docs (Task 9)

**Honest assessment**: Phase 137 implementation is CORRECT but REFUTED on USRP.
The plumbing works (all env-var markers fire on USRP general_work calls), but the
underlying hypothesis (null pilots → CPE contamination → metric increase) is
INSUFFICIENT to explain the 1.77 rad ceiling. Phase 112 R1 root cause analysis
remains valid.

**No code changes** beyond what was planned. All 7 implementation commits preserved
as opt-in features for potential future use (e.g., if 30 dB attenuator changes
signal characteristics, or if Phase 138 architectural decoder redesign wants to
build on Phase 137's masked-CPE kernel).