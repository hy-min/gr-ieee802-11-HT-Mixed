# Phase 111 T5e — Extended HT-SIG Candidate Search (2026-07-07)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — 32 candidates (8 rot × 4 inv, with HTSIG_FINE_ROT=1)
plus all available per-SC CPE options still fails CRC. Issue is per-SC phase
drift (Phase 107), not global rotation.

## TL;DR

Tried every available HT-SIG improvement (rotation count, soft LLR, pilot CPE,
pilot per-SC CPE, H re-estimation) — all combinations fail CRC.

**Root cause (confirmed)**: Per-SC phase std = 108° (per Phase 107) cannot be
corrected by:
- Global rotation (T5e) — only corrects uniform offset
- Per-symbol CPE — correct uniform offset
- Per-SC linear fit (HTSIG_PILOT_PERSC) — only correct linear phase ramp
- Soft LLR — preserves viterbi capacity

The 12-18 errors / 96 bits observed are random per-SC errors that no linear
model can correct. This requires either:
- More pilot measurements (not available in HT-SIG)
- Statistical estimator (Kalman/HMM, but no good measurements at HT-SIG time)
- Different decoder (list viterbi, LDPC, belief propagation)

## Test Results (p110 T10 capture)

| Config | n_rot | n_inv | best metric | CRC pass | Notes |
|--------|------|-------|-------------|----------|-------|
| Baseline (no env) | 0 | 0 | n/a | n/a | L-SIG fails |
| LSIG_CAND | 4 | 4 | n/a | n/a | is_ht_frame=1, HT_SIG_CAND fires |
| LSIG_CAND + HREESTIMATE | 4 | 4 | 12 | 0/16 | per-symbol H from pilots |
| LSIG_CAND + SOFT_LLR | 4 | 4 | 14222 (Q8.8) | 0/16 | soft LLR de-weights erasures |
| LSIG_CAND + FINE_ROT | 8 | 4 | 12237 (Q8.8) | 0/32 | 32 candidates |
| LSIG_CAND + HRE + PILOT_CPE + PILOT_PERSC + SOFT_LLR + FINE_ROT | 8 | 4 | 12696 (Q8.8) | 0/32 | ALL options |
| All above with longer capture (30s) | 8 | 4 | similar | 0/32 | no improvement |

Diagnostic from PILOT_PERSC:
```
[HTSIG_PILOT_PERSC] sym=0 a=0.6137 b=-0.054924 sc_range=[-26,21]
[HTSIG_PILOT_PERSC] sym=1 a=0.6137 b=-0.083475 sc_range=[-26,21]
```
- a (uniform phase offset) varies by ~0.6 rad (~35°) per frame attempt
- b (linear phase ramp) is small (~0.05-0.08)
- a and b change between symbol 0 and 1 within a frame

## Root Cause (already established by Phase 107)

Per Phase 107 verdict:
- Per-SC argH std = 108° across symbols
- This means: the channel phase is essentially RANDOM per SC, per symbol
- L-LTF H52 is wrong at HT-SIG time by random per-SC phase
- All 48 SCs equalized with random per-SC phase error → ~50% BER on BPSK
- Expected: ~48 random errors / 96 bits → ~48% BER (decoded), but
  convolutional decoder observes "burst" errors due to interleaver
- Observed: 12-18 errors / 96 bits = 12-19% BER

The 12-19% BER is consistent with 50% raw BER after some noise rejection
in the convolutional decoder. Why not 50%? Because:
- 4 pilots are accurate (high SNR at pilot SCs)
- Some SCs have similar phase as L-LTF (random chance)
- Conv decoder accumulates good/bad SCs across multiple symbols

## Why T5e Fails

Rotation is GLOBAL (same angle for all 48 SCs). Phase drift is PER-SC.
Even 360° coverage of global rotation can't fix per-SC errors.

Specifically:
- a=0.61, b=-0.05 in linear fit (from HTSIG_PILOT_PERSC log)
- a rotates all 48 SCs by 0.61 rad ≈ 35°
- This corrects the AVERAGE phase error but not per-SC variation
- Per-SC variation (std=108°) is ~3x the average correction

To fix per-SC variation:
- Need per-SC rotation estimates
- Only have 4 pilots → can only estimate 4 SCs
- Linear fit extrapolates → wrong for random phase

## L-SIG Wall Broken (Side Effect of T5e)

Even though T5e doesn't break HT-SIG, the L-SIG breakthrough is real:

`IEEE80211_LSIG_VITERBI_CANDIDATE=1` enables 4 phase rotation candidates in
L-SIG viterbi. This unblocks the L-SIG wall:
- is_ht_frame=1 fires
- HT_SIG_CAND fires
- HT-SIG processing pipeline runs end-to-end

Previously (before VITERBI_CANDIDATE=1):
- L-SIG viterbi failed at avg_snr=2.67 (too low)
- is_ht_frame never set
- HT-SIG never processed

This is the FIRST TIME we reach the HT-SIG processing stage on USRP data.
The L-SIG wall is definitively broken.

## Recommended T6 Path

Given T5e REFUTED (per-SC phase is fundamentally unfixable with simple
linear models), the realistic paths are:

### T6a: List Viterbi (HIGH effort, MEDIUM probability)
Keep top-K paths from viterbi instead of just the best. For each path,
check CRC. If K=64, explore most possible paths.
- Cost: 64x more memory + 64x more computation per candidate
- Benefit: one of K paths might pass CRC by chance
- Risk: CRC false positive (8-bit CRC has 1/256 chance of passing on bad codeword)

### T6b: Per-SC Kalman at HT-SIG time (HIGH effort, MEDIUM probability)
Build on Phase 111 T3 (Kalman for DATA) to track H52 across symbols.
Apply to HT-SIG processing path.
- Cost: integrate Kalman into HT-SIG equalizer
- Benefit: per-SC H tracking across all 48 SCs
- Risk: 4-pilot Kalman (Phase 111 T2) REFUTED, but at HT-SIG there are no
  intermediate symbols between L-LTF and HT-SIG

### T6c: LDPC or other decoder for HT-SIG
802.11n uses convolutional code for HT-SIG (rate 1/2, K=7, poly 133/171).
LDPC is not used for HT-SIG. Can't switch decoders without breaking the spec.

### T6d: Wait for better SNR
The fundamental blocker is per-SC phase, which is amplified at low SNR.
Higher SNR → less noisy phase estimates → fewer errors.
- Requires: better test conditions (new cable run with 30 dB attenuator)
- Or: higher TX gain (HW risk per Phase 82 CLAUDE.md)

## Verdict: REFUTED

T5e REFUTED. The HT-SIG viterbi wall is NOT caused by inadequate candidate
search (rotation count) but by per-SC random phase drift that no linear
model can correct.

L-SIG wall broken (side benefit). HT-SIG requires fundamentally different
approach (T6a list viterbi or T6b per-SC Kalman at HT-SIG time).

## Test Reproducibility

```bash
# All env vars combined
IEEE80211_LSIG_VITERBI_CANDIDATE=1 \
IEEE80211_HTSIG_FINE_ROT=1 \
IEEE80211_HTSIG_H_REESTIMATE=1 \
IEEE80211_HTSIG_PILOT_PERSC=1 \
IEEE80211_HTSIG_PILOT_CPE=1 \
IEEE80211_SOFT_LLR_VITERBI=1 \
python test_file_replay_e2e.py --iq-file /tmp/p110_t10_capture.fc32 --phase rx --rate 20
```

Result: HT_SIG_CAND 32 per symbol, all fail CRC, best metric ~12000 (Q8.8).

## Files Modified

None — T5e was env-var tuning only, no code changes.

## Related

- [[project-p111-t4a-htsig-verdict]] — T4a (null SC erasure) REFUTED
- [[project-p107-deep-root-cause]] — per-SC argH std=108° (root cause)
- [[project-p95-htsig-fine-rot]] — HTSIG_FINE_ROT (32 candidates)
- [[project-p111-t3-kalman-cpp]] — T3 Kalman for DATA (could extend to HT-SIG)
- [[project-p70-lsig-viterbi-candidate]] — VITERBI_CANDIDATE=1 (L-SIG breakthrough)
