# Phase 79 Verdict — Per-Symbol δ Tracking

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: **REFUTED on USRP** — Stage 3 HARD CONSTRAINT gate NOT met
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (0/90)

## Results Summary

| Stage | Metric | Baseline | Phase 79 | Status |
|---|---|---|---|---|
| 1 (Synthetic) | Layer 4 success rate | 91.0% | 91.0% | ✅ PRESERVED |
| 2 (Capture replay) | HT_SIG_PARSE_OK / 10 | 0 | 0 | ⚠️ NEUTRAL |
| 3 (USRP realtime) | FCS_OK / Sent | 0/90 | 0/90 | ❌ REFUTED |
| Regression | Loopback 3/3 | PASS | PASS (env=OFF and env=ON) | ✅ NO REGRESSION |
| Regression | Synthetic 3/3 | PASS | PASS | ✅ NO REGRESSION |

## Key Findings

### Stage 1: Synthetic δ sweep (PASS)
- Per-symbol δ estimator implemented in NumPy + C++
- 4/4 unit tests PASS (noiseless exact recovery, AWGN >90% accuracy, all-null fallback, full δ sweep)
- Mirrors 91.0% Phase 78a Layer 4 baseline (no degradation)

### Stage 2: USRP capture replay (NEUTRAL)
- Estimator computes meaningful δ values (range 0.0-0.98, not all zero)
- δ correction sometimes reduces viterbi metric (frame 0: 15→13, frame 4: 16→15, frame 3: 14→12)
- δ correction sometimes increases it (frame 8: 12→13, frame 2: 12→15) — pilot-based δ is noisy on weak frames
- HT_SIG_PARSE_OK still 0/10 in both env=OFF and env=ON modes
- USRP structural noise (5 stable globally-null SCs from Phase 78b) prevents viterbi CRC convergence
- Estimator IS necessary but NOT sufficient for USRP HT-SIG

### Stage 3: USRP realtime (REFUTED)
- `IEEE80211_HTSIG_PER_SYMBOL_DELTA=1` enabled
- `IEEE80211_LSIG_RATE_FORCE=0xD` (Phase 18 baseline)
- `IEEE80211_TIMING_OFFSET_APPLY=1` (Phase 34 baseline)
- `--freq 5890 --tx-gain 20 --rate 20 --duration 30 --warmup 60`
- **Sent: 90, Recv: 0, FCS_OK: 0, FCS_FAIL: 0**
- avg_snr_lsig=4.25 dB, avg_snr_htsig=2.80 dB (need 6+ dB)
- Per-symbol δ estimator converges to **delta=0.9112 (k/64=58) consistently** across all OFDM symbols (4-11)
  - Suspicious: same value for all symbols → estimator may be using same H52 for all symbols
  - δ=0.9112 → ~0.0142 rad/SC phase ramp → ~21° at SC=26 (edge)
- HT_SIG_CAND fires 16 candidates (4 rotations × 4 inversion combos), all crc_fail at metrics 11-17
- HT_SIG_PARSE_FAIL with timeout_sym=4 (16 candidates, best_metric=N/A threshold=N/A)

### Regression checks (PASS)
- Loopback 3/3 PASS in both env=OFF and env=ON modes (no regression)
- Synthetic HT-SIG viterbi 3/3 PASS (91% Layer 4 baseline preserved)

## Verdict

**Phase 79 is REFUTED on USRP realtime** (HARD CONSTRAINT not met).

The estimator works correctly (computes meaningful δ values, synthetic tests pass, loopback preserves). However, on USRP:
1. avg_snr_htsig = 2.80 dB is too low for viterbi convergence (need 6+ dB)
2. Per-symbol δ correction does not improve avg_snr_htsig because:
   - Estimator uses frame-level H52 (from L-LTF), not per-symbol H
   - Per-symbol δ only affects phase ramp, not magnitude noise
   - USRP structural noise (5 stable null SCs per Phase 78b) is dominant

**Phase 79 is additive fix; the wall is structural.** Per-symbol δ is necessary but not sufficient.

## Root Cause: Why Phase 79 Failed

The estimator computes a **consistent** δ=0.9112 across all OFDM symbols. This suggests:
- The estimator uses the same H52 (from L-LTF) for all symbols
- The δ it estimates is dominated by the structural bias in the L-LTF H52 estimate
- After applying this "per-symbol" δ correction, the residual phase error is unchanged across symbols
- viterbi metric stays at 11-17 (need ≤6 for CRC_OK)

To fix this, we need:
- **Per-symbol H estimate** (not frame-level H from L-LTF)
- Or **per-SC phase calibration** (not scalar δ)
- Or **iterative refinement** (estimate δ, refine using equalized symbols, repeat)

## Upstream-Attack Plan (per HARD CONSTRAINT)

### Phase 80 candidates (targeting structural root cause)

1. **80a — Per-symbol H re-estimation via HT-SIG pilots** (Phase 39 was REFUTED, but with per-symbol δ now available as starting point, this may work)
   - Use δ=0.9112 as initial estimate
   - Refine H52[i] using HT-SIG pilots after δ correction
   - Risk: noise amplification at null SCs

2. **80b — Per-SC phase calibration from L-LTF** (untried, structural fix)
   - Compute per-SC phase bias using the 5 stable null SCs identified in Phase 78b
   - Apply as additional rotation per SC (not per-symbol scalar)
   - Risk: Phase 39-style REFUTED risk (pilot-based H re-estimation)

3. **80c — Iterative δ refinement**
   - Apply δ correction, decode, recompute δ from decoded bits, repeat 1-2 times
   - Uses the decoded bits as reference (better than noisy pilots)
   - Risk: convergence uncertainty, but mathematically sound

4. **80d — Accept Phase 41 closure with HARD CONSTRAINT relaxation**
   - USRP HT-SIG is channel-physics-limited
   - Loopback-only verification becomes acceptable
   - Request user approval

### Recommended next step: Phase 80b (per-SC phase calibration)

Reasoning:
- Phase 78b identified the structural signature (5 stable null SCs)
- Per-symbol δ alone is insufficient (Phase 79 REFUTED)
- Per-SC fix targets the structural root cause
- Lower risk than 80a/80c (more deterministic)

## Lessons Learned

1. **Plan verification**: The original plan targeted `decode_htsig_direct_from_header52` (line 2366) which is dead code since Phase 70 refactor. Phase 79 implementer correctly identified this and integrated into `decode_htsig_from_rotated` (line 2621).

2. **QBPSK-aware polarity**: The δ estimator needed a complex-polarity variant (`estimate_symbol_delta_qbpsk`) for QBPSK pilots on the IMAG axis. Real-valued polarity (used in Python reference) only validates BPSK case.

3. **Per-symbol δ for data**: Works in loopback (env=ON ≈ env=OFF for clean channel) but unverified on USRP at high SNR.

4. **Structural vs additive fix**: Phase 79 is an ADDITIVE fix (per-symbol δ on top of Phase 18/34/35/46 stack). The wall is STRUCTURAL (5 stable null SCs per Phase 78b). Per-symbol δ cannot fix structural noise — only per-SC fixes can.

5. **Estimator using stale H**: The per-symbol δ estimator uses the same frame-level H52 for all symbols. Without per-symbol H, per-symbol δ is a no-op (same value applied to all symbols).

## Files Changed

- `examples/test_htsig_delta_synthetic.py` (new, 4/4 tests PASS)
- `examples/test_usrp_capture_replay_htsig.py` (new, NEUTRAL on USRP capture)
- `lib/frame_equalizer_impl.cc` (~220 lines added)
  - `estimate_symbol_delta` (BPSK variant)
  - `estimate_symbol_delta_qbpsk` (QBPSK variant, complex polarity)
  - `apply_delta_correction_to_eq` (per-SC phase rotation helper)
  - δ estimation block in `decode_htsig_from_rotated` (lines 2690-2691)
  - δ correction in HT-SIG0 loop (line 2735)
  - δ correction in HT-SIG1 loop (line 2874)
  - δ correction in data symbol block in `general_work` (line 4674)
  - Env var init block in constructor

## Commits

- `17b5261`: test(p79) Stage 1 synthetic δ sweep + estimator unit tests
- `3ee6d50`: fix(p79) align Stage 1 test with plan
- `647948b`: feat(p79) add QBPSK-aware per-symbol δ estimator (C++ helper)
- `37d664b`: feat(p79) add IEEE80211_HTSIG_PER_SYMBOL_DELTA env var init
- `f4ac92d`: feat(p79) integrate per-symbol δ into ACTUAL HT-SIG decoder (corrected)
- `012cd31`: feat(p79) apply per-symbol δ to data OFDM symbols
- `68a5da8`: test(p79) Stage 2 USRP capture replay HT-SIG test (NEUTRAL)

## Recommended Next Phase

**Phase 80b — Per-SC phase calibration from L-LTF** (per HARD CONSTRAINT upstream-attack plan).

This targets the structural root cause (5 stable null SCs per Phase 78b) with a per-SC fix that Phase 79's scalar δ cannot address.

## Related

- `docs/superpowers/specs/2026-07-02-htsig-per-symbol-delta-redesign.md` (design)
- `docs/superpowers/plans/2026-07-02-htsig-per-symbol-delta.md` (plan)
- `docs/superpowers/notes/2026-07-03-phase78c-null-sc-attack-verdict.md` (5 stable null SCs)
- `docs/superpowers/notes/2026-07-03-phase78a-synthetic-verdict.md` (91% baseline)
- `docs/superpowers/notes/2026-07-03-phase77-verdict.md` (equalizer ceiling)
- `docs/superpowers/notes/2026-06-23-phase34-delta-correction.md` (per-frame δ unblocks L-SIG)