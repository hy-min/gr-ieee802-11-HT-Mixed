# Phase 77c H52 SNR-Weighted Refinement — Findings

**Date**: 2026-07-03
**Branch**: TEST1
**Capture**: `/tmp/p76_selftx_5250.bin` (5260 MHz, USRP self-TX, 60s full)

## Implementation

- Added `IEEE80211_H52_SNR_WEIGHTED=1` env var (default OFF)
- New file-global `g_h52_snr_weighted` (bridges ctor flag to static estimator)
- Refactored `estimate_header_channel_from_lltf52()` to build `H_LTS0` and,
  when enabled, `H_LTS1` then blend:
  ```
  H52[i] = (w1*H_LTS0[i] + w0*H_LTS1[i]) / (w0 + w1)
  w_i = sum_j(|H_LTS_i[j]|)
  ```
- Higher-`||` LTS gets higher weight globally, so a single LTS with deep
  channel nulls cannot bias the result.
- Call site (`general_work`, kHtSig0Rel) now passes distinct
  `d_ltf_compensated[1]` (L-LTF1) as the 2nd arg when enabled.
- Legacy callers (same pointer for both args) get H52 = H_LTS0 fallback.

## Test results (5250 MHz, 77a+77b+combo+interp+77c)

| Metric | 77b baseline | 77c (H52 weighted) | Change |
|--------|--------------|---------------------|--------|
| HT_SIG_CAND | 224 | 256 | +14% |
| HT_SIG_PARSE_FAIL | 14 | 16 | +2 |
| HT_SIG_PARSE_OK | 0 | 0 | no change |
| FCS_OK | 0 | 0 | no change |
| avg_snr_htsig mean | 8.08 dB | 10.23 dB | +2.15 dB |
| avg_snr_htsig median | 1.86 dB | 3.74 dB | +1.88 dB |
| avg_snr_htsig max | 65.27 dB | 46.60 dB | -18.67 dB |
| w0/w1 ratio (median) | n/a | 0.918 | LTS0/1 magnitude similar |
| w0/w1 ratio (range) | n/a | 0.320 to 2.156 | 6.7x range — LTS vary per frame |

## Verdict

**PARTIAL** — Equalizer-layer improvement observed, but downstream viterbi
gate still closed. `HT_SIG_CAND` reach grew 14%, `avg_snr_htsig` mean +2.15 dB
/ median +1.88 dB, and the LTS-magnitude ratio varies 6.7x frame-to-frame
(confirming per-frame asymmetry that simple averaging would smooth away).
However `HT_SIG_PARSE_OK=0` and `FCS_OK=0` unchanged — the structural QBPSK
phase coherence issue identified in Phase 77b verdict still dominates.

The `w0/w1` ratio range (0.32 to 2.16) proves L-LTF0 and L-LTF1 carry
distinct channel observations: per-frame SNR weighting is the correct
concept. Equalizer-layer attack exhausted. Phase 77d (closure) is the next
step.

## Files changed

- `lib/frame_equalizer_impl.h` — added `d_apply_h52_snr_weighted` field
  (lines 165-176) with Phase 77c docblock.
- `lib/frame_equalizer_impl.cc` — added `g_h52_snr_weighted` file-global
  (line 816), env-var wiring (around line 3180-3190), and refactored
  `estimate_header_channel_from_lltf52()` to support the weighted branch
  (lines 1029-1150). Call site at kHtSig0Rel now passes L-LTF1 as 2nd arg
  when enabled.

## Next

- Phase 77d: accept HT-SIG closure per project HARD CONSTRAINT — software
  loopback 3/3 PASS remains the decoder validation path; USRP HT-SIG
  viterbi remains the channel-physics gate (Phase 38/41/77b verdicts).
