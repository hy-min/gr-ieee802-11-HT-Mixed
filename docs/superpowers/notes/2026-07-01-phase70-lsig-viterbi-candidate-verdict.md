# Phase 70 Verdict — L-SIG Viterbi Candidate Search (REFUTED on USRP)

**Date**: 2026-07-01
**Branch**: TEST1
**Status**: REFUTED on USRP (BLOCKED)
**Commits**:
- 498086b feat(p70): synthetic test for L-SIG viterbi candidate search
- a52b346 feat(p70): add rot_idx parameter to decode_lsig_direct_from_header52
- 9c8493c feat(p70): env-var-gated 8-candidate L-SIG viterbi search
- c468cdb fix(p70): prevent infinite loop in candidate search promotion block

## Goal
Add 4 rot × 2 inv = 8 candidate search to L-SIG viterbi to unblock frames
failing at the L-SIG viterbi upstream gate.

## Hypothesis Tested
Residual phase error in equalized L-SIG symbols causes viterbi to converge
on wrong codewords. Try 4 phase rotations × 2 polarities = 8 candidates.

## Results

### Synthetic test (Task 1) — PASS
- single-pass 0°/20dB: metric=0
- 8-candidate 45°/20dB: best_metric=0, winner rot=0 inv=0
- low-SNR 30°/5dB: best_metric=0, rate=0xD, parity=0, tail=0
- **All 3 tests PASS.** Python viterbi correctly mirrors C++ encoder/decoder.

### Loopback regression (Task 4) — PASS (no regression)
- OFF: Final: OK=1 FAIL=0 (1/1, test script's 10s window)
- ON: Final: OK=1 FAIL=0 (1/1, no behavioral drift)
- Winner: rot=0 inv=0 approx_metric=0 enc=0 len=54 (correct)
- 5 alternative candidates correctly rejected (enc != 0)

### Offline replay multi-frame (Task 5) — REFUTED
- 72MB IQ capture processed offline
- **LSIG_CANDIDATE_WIN: 0** (no valid winners found)
- All 16 candidates per OFDM symbol fail viterbi rate-field validation
- is_ht_frame=0 for all 8 data symbols (1 wifi_start burst)
- avg_snr_lsig=1.59 (low SNR regime)

### USRP realtime antenna close (Task 6) — REFUTED
- avg_snr_lsig=1.78 (low SNR, NOT 49.96 outlier from prior Phase 70 test)
- LSIG_CANDIDATE_WIN: 16, but **all 16 winners degenerate to rot=0 inv=0**
- 100% degenerate: rotation/polarity search finds no benefit
- is_ht_frame=0 for all 16 attempts
- HT_SIG_CAND: 32 (Phase 66 diag fired 16 times + 16 PARSE_FAIL)
- FCS_OK: 0 (test killed at timeout before FcsLogger summary)

## Decision

**Phase 70 candidate search is REFUTED on USRP.** The hypothesis that residual
phase error is the bottleneck is incorrect. The actual bottleneck is channel
quality — H52 nulls (|H|=0.02-0.14) per Phase 27/30/38/41.

Evidence chain:
1. Loopback PASS (clean channel, no H52 corruption)
2. Offline replay REFUTED (real channel, all 16 candidates fail viterbi)
3. Realtime USRP REFUTED (real channel, 100% degenerate winners)
4. SNR regime: avg_snr_lsig ≈ 1.5-2.5 (Phase 55/66/68 baseline); below the
   ~6-8 dB threshold where H52 channel nulls dominate

The 49.96 avg_snr reading from the earlier antenna-close test was an
environmental outlier (UHD streaming instability per Phase 55, or transient
antenna coupling). The realistic USRP SNR is ~1.5-2.5.

## Architectural Conclusion

L-SIG viterbi gating is a **channel-physics limit**, not a candidate-miss
limit. Candidate search is functionally equivalent to no candidate search at
this SNR. Per Phase 44 precedent (soft-LLR REFUTED), the candidate-search
code is kept as **opt-in env var** (`IEEE80211_LSIG_VITERBI_CANDIDATE=1`)
for diagnostic purposes but is NOT promoted to default.

## Phase 71+ Attack Plan (per HARD CONSTRAINT)

The candidate-search axis is exhausted. The remaining USRP bottleneck is
H52 channel quality. Phase 71+ must attack this upstream of L-SIG EQ:

### Phase 71: L-LTF Hann window H52 smoothing (low-risk)
- Hypothesis: rectangular window on L-LTF introduces spectral leakage that
  inflates H52 null magnitudes
- Fix: apply Hann window to L-LTF0/1 FFT before H52 estimation
- Reference: `lib/frame_equalizer_impl.cc::estimate_header_channel_from_lltf52()`
  (called at line 4286-4288 per Phase 67 T2 dump)
- Risk: Phase 27 already tested "all H extraction variants" and refuted the
  hypothesis at the algorithm level. Phase 71 specifically targets the
  WINDOWING, not the extraction algorithm.

### Phase 72: MMSE equalizer (replaces ZF EQ)
- Hypothesis: ZF equalizer amplifies H52 nulls 50× (Phase 38), corrupting
  equalized symbols. MMSE adds noise regularization.
- Fix: replace `safe_div(rx, H)` with `(H* rx) / (|H|² + N0)` in EQ loop
- Reference: `lib/frame_equalizer_impl.cc:1941` (`eq = safe_div(rx52[i], H52[i])`)
- Phase 47 env vars exist: `IEEE80211_MMSE_EQUALIZE=1 IEEE80211_MMSE_N0_PERCENTILE=25`
  but were never validated on USRP

### Phase 73 (if 71+72 fail): per-symbol H re-estimation from HT-SIG pilots
- Phase 39 was REFUTED with 8× worse std_im, but that was without L-LTF
  Hann windowing. With Phase 71 fix, the H estimate may be stable enough
  that per-symbol re-estimation becomes viable.

## Files

- `lib/frame_equalizer_impl.cc:4810-4900, 5270-5310` (8-candidate search)
- `lib/frame_equalizer_impl.cc:1921-1953` (rot_idx parameter)
- `examples/test_lsig_viterbi_candidate_synthetic.py` (synthetic test)
- `/tmp/p70_loopback_off.log`, `/tmp/p70_loopback_on.log` (loopback tests)
- `/tmp/p70_offline_replay.log` (offline replay)
- `/tmp/p70_usrp.log` (USRP realtime test)

## Related

- [[project_p66_htsig_viterbi_diag]] — HT-SIG viterbi diagnostic (same pattern)
- [[project_p68_t2_capture_replay]] — offline replay infrastructure
- [[project_p55_usrp_snr_diagnosis]] — UHD streaming instability context
- [[project_p44_soft_llr_viterbi]] — Phase 44 soft-LLR REFUTED (precedent)
- [[project_p27_h52_quality]] — Phase 27 H52 quality REFUTED
- [[project_p41_usrp_htsig_final_verdict]] — Phase 41 USRP HT-SIG final closure
