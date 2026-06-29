# Phase 41 Verdict — IEEE80211_LSIG_RATE_FORCE=0xD on USRP (is_ht_frame=0 investigation)

**Date:** 2026-06-28
**Status:** REFUTED (with caveat)
**Verdict:** Phase 18 fix DOES help L-SIG filtering but does NOT flip is_ht_frame on USRP HT-SIG failures. HT-SIG viterbi still CRC-fails.

## Test Setup

- USRP X310 + UBX-160, 5 GHz A:0, freq=5890, tx-gain=20, duration=30s
- Env vars: `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14 IEEE80211_HTSIG_TIMING_DUMP=1`
- Log: /tmp/p41_lsig_rate_force.log (1072 lines)

## Key Metrics (vs Phase 40 baseline)

| Metric | Phase 40 (no FORCE) | Phase 41 (FORCE=0xD) |
|---|---|---|
| LSIG_DECODE OK | 128 | 104 |
| HT_SIG_PARSE_FAIL events | 112 | **8** (1 frame, 8 symbols) |
| is_ht_frame=0 (total) | (Phase 40: 112 in HT_SIG failures) | 104 total (8 in HT_SIG_PARSE_FAIL) |
| is_ht_frame=1 (total) | 0 in HT_SIG failures | 96 (all in LSIG_PARSE_FAIL only) |
| FCS OK | 0 | **0** |
| HTSIG_TIMING delta=0 | (all 0) | 747/766 (97.5%) |

## Critical Findings

### 1. HT_SIG_PARSE_FAIL count DRAMATICALLY reduced (112 → 8 events / 1 frame)

The Phase 18 fix IS working. By rejecting L-SIGs with rate_field ≠ 0xD, it prevents the brute-force HT-SIG decode from being triggered on noise-induced wrong-rate L-SIGs. Only **1 frame** out of 30s reached the HT-SIG brute-force path (vs 14+ frames in Phase 40).

### 2. is_ht_frame STILL = 0 in the 1 remaining HT_SIG_PARSE_FAIL frame

The single frame that did reach HT-SIG brute-force still shows `is_ht_frame=0`. This is the failure mode Phase 40 highlighted. The Phase 18 fix does not address this.

This means: even when L-SIG decodes successfully with rate=0xD, the equalizer's `ratio_ht > 1.2` heuristic (frame_equalizer_impl.cc:3620) does not flip `is_ht_frame=true`. The HT-SIG constellation is being misclassified as Legacy rather than HT-Mixed.

### 3. avg_snr_htsig=10.99 — below the ratio_ht threshold

The remaining frame has:
- avg_snr_lsig = 17.36 (good)
- avg_snr_htsig = 10.99 (lower — borderline)

For `ratio_ht > 1.2` (E_Q/E_I threshold), the equalized HT-SIG needs QBPSK rotation to be detectable above the noise floor. At 10.99 dB SNR, this likely fails.

### 4. is_ht_frame=1 appears in LSIG_PARSE_FAIL (96 events)

This is a SECOND failure mode where `is_ht_frame=1` is set (from the early-eqsym scan during L-SIG processing) but L-SIG itself fails viterbi. **All 96 of these are `reason='viterbi_fail'`** with `avg_snr=2.62` and `avg_snr_ht=6.46` — very low SNR. These are noise/LO events that incorrectly look like HT frames (random QBPSK ratio hits 1.2).

### 5. HTSIG_TIMING delta still 0 for 97.5% of frames

Confirms Phase 40 finding: splitter timing is NOT the issue. delta=4 is the K offset for L-LTF1 (LLTF_OFFSET_CORRECT=14 fix), not a residual timing error.

## Verdict

**REFUTED with caveat.** Phase 18 fix DOES help (112 → 8 HT_SIG_PARSE_FAIL), but the underlying is_ht_frame=0 anomaly is NOT addressed by this fix.

The `is_ht_frame` flag is set based on a SEPARATE heuristic (ratio_ht > 1.2) BEFORE HT-SIG viterbi runs. This heuristic operates on equalized HT-SIG raw constellation (early_eqsym) without using the rate_field from L-SIG. So even if L-SIG has rate=0xD (forced), if the equalized HT-SIG constellation doesn't show QBPSK rotation, is_ht_frame stays 0.

## Implications

The `is_ht_frame=0` anomaly observed in Phase 40 is NOT caused by missing L-SIG rate validation. It's caused by **the equalized HT-SIG failing the ratio_ht threshold** (likely due to insufficient SNR at HT-SIG pilots, or H52 channel nulls — Phase 38 finding).

## Next Steps (Pivot)

Per Phase 40's recommendations:
- **Option A (DONE):** Test IEEE80211_LSIG_RATE_FORCE=0xD — REFUTED for is_ht_frame flip, but USEFUL for reducing false-positive HT-SIG brute-force attempts.
- **Option B:** Run with `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` to investigate per-symbol δ drift on HT-SIG0/HT-SIG1.
- **Option C:** Accept that USRP HT-SIG is not solvable with current equalizer, and rely on:
  - Software loopback (3/3 PASS) for decoder validation
  - L-SIG δ correction (Phase 34) for at least L-SIG viterbi
  - Document USRP HT-SIG limitation

The 1 surviving HT_SIG_PARSE_FAIL frame had avg_snr_htsig=10.99. Phase 38 showed HT-SIG viterbi needs ratio_ht to be reliably above 1.2. May need to LOWER the threshold (1.2 → 1.0) as a separate experiment.

## Conclusion

HT-SIG viterbi is NOT unblocked by this fix. Phase 18 fix is useful (reduces false positives) but doesn't address the real is_ht_frame bottleneck. USRP HT-SIG verification remains BLOCKED. Recommend either Option B (per-symbol δ dump) or accepting current state.

## Files Referenced

- /tmp/p41_lsig_rate_force.log — full Phase 41 log
- /tmp/p41_results.txt — extracted metrics
- /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc:3620 — ratio_ht heuristic
- /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc:4555-4580 — failure logging
- /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc:1919-1937 — LSIG_RATE_FORCE implementation
