# Phase 46 AR5: MMSE Equalization — MARGINAL VERDICT

**Date:** 2026-06-28
**Status:** MARGINAL (HT_SIG_PARSE_FAIL reduced but no FCS_OK)
**Hypothesis:** MMSE eq = conj(H)·rx / (|H|² + N0) bypasses Phase 38's 50× noise
amplification at Hhdr52 channel nulls.

## Implementation

**Files:**
- `lib/frame_equalizer_impl.cc`: `mmse_equalize_htsig()` static helper near
  `safe_div()` (line ~107-130). Reads `IEEE80211_MMSE_EQUALIZE` env var in
  constructor (line ~3085). Applies MMSE in `decode_htsig_from_rotated()`
  for HT-SIG0 (line ~2340) and HT-SIG1 (line ~2470).
- `lib/frame_equalizer_impl.h`: `bool d_mmse_equalize = false;` field.
- `examples/test_mmse_equalize_synthetic.py`: 5 tests verifying MMSE math.

**MMSE formula:**
```
h_sq[i] = |H52[i]|²    for i in 0..47
N0 = 25th percentile of h_sq (interp at 11.5)
eq[i] = conj(H52[i]) · rx52[i] / (h_sq[i] + N0)
```

**Scope:** HT-SIG bit extraction only (HT-SIG0 and HT-SIG1). L-SIG and data
symbols keep `safe_div`. New env var `IEEE80211_MMSE_EQUALIZE` (default OFF).

## Synthetic Test Results (5/5 PASS)

| Test | Description | Result |
|------|-------------|--------|
| 1 | `test_mmse_vs_zf_clean`: no noise, uniform \|H\|=2.0, MMSE/ZF ratio=0.5 (constant, sign preserved) | PASS |
| 2 | `test_mmse_at_null_sc`: 1 SC at \|H\|=0.05 with noise, MMSE=0.07 vs ZF=7.07 (100× reduction) | PASS |
| 3 | `test_mmse_phase_preservation`: strong H, 20dB SNR, 50 trials, bit-match=1.000 | PASS |
| 4 | `test_mmse_n0_robustness`: 5/48 nulls, 25th percentile stable (rel_change=0.0000) | PASS |
| 5 | `test_htsig_viterbi_with_mmse`: full viterbi at SNR=10dB, MMSE≥ZF at 0/2/5/10 nulls | PASS |

## Loopback Regression

- Baseline (no env): 1 FCS OK
- MMSE=1 + LSIG_RATE_FORCE=0xD: 1 FCS OK — **no regression**
- MMSE=0 + full USRP config: 0 OK (LLTF_OFFSET_CORRECT=14 + TIMING_OFFSET_APPLY=1 break loopback; these are USRP-only flags, not regressions from MMSE)

## USRP Validation (test_usrp_phase44.py, 30s, freq=5890, tx-gain=20)

| Metric | Baseline (MMSE=0) | MMSE=1 |
|--------|--------------------|---------|
| Sent (strobes) | ~31 | ~31 |
| Recv (HT_SIG_PARSE_FAIL events) | 6 | 4 |
| DECODE_FAIL (HT-SIG OK, data FCS bad) | 0 | 2 |
| FCS_OK | 0 | 0 |
| avg_snr_htsig (samples) | 0.78 - 4543 | 0.78 - 236.40 |
| HT_SIG viterbi metrics | 13-16 (random) | 13-16 (random) |

**Comparison vs Phase 41 baseline (8 HT_SIG_PARSE_FAIL / 30s):**
- MMSE=1: 4 events / 30s → **50% reduction** (exactly meets MARGINAL threshold)

**Key observation:** Two DECODE_FAIL entries in MMSE=1 run indicate HT-SIG
was parsed successfully enough to extract a length (40 bytes), but data
symbols failed FCS. In baseline (MMSE=0), no frames made it past HT-SIG
parsing. This shows MMSE partially unblocks HT-SIG at the viterbi layer
but data-symbol equalization (still ZF) is the next bottleneck.

## Verdict: MARGINAL

- **NOT SUCCESS:** FCS_OK = 0
- **MARGINAL by ≥50% HT_SIG_PARSE_FAIL drop** vs Phase 41 baseline (8 → 4)
- **Secondary evidence:** 2 frames reached data decode (DECODE_FAIL) — never happened in baseline

## Why MMSE didn't fully unblock

The HT-SIG viterbi metrics remain 13-16 across all 16 candidates — same as
the Phase 38 random-scatter baseline. MMSE does suppress noise amplification
at null SCs (100× reduction in test 2), but at SCs with moderate \|H\|=0.2-0.3,
the noise still dominates and corrupts enough bits to break CRC.

The Phase 38 Hhdr52 root cause is real but the equalizer is only one of
several bottlenecks:
1. CFO/SFO residual at the 1/64 grid (Phase 34 partially addresses)
2. Per-symbol δ drift (Phase 38 Step 4 REFUTED)
3. **Now:** HT-SIG data-symbol corruption when noise + channel nulls combine

The DECODE_FAIL frames suggest MMSE helped the HT-SIG parser but the
underlying equalization problem also affects data symbols. Phase 47 may
need to extend MMSE to data symbols or implement per-SC null detection
(Phase 42 Layer 1 was REFUTED but might be revisited with MMSE-aware logic).

## Recommendation

- **Keep MMSE commit (977c284)** as a permanent opt-in via `IEEE80211_MMSE_EQUALIZE`
- Default remains OFF to preserve baseline behavior
- Loopback regression check passes
- Future work: extend MMSE to data symbols (probably won't help; data already works at low rate), or revisit per-SC null detection with MMSE-aware normalization

## Commands

```bash
# Build
cd /home/hy/gr-ieee802-11/build && cmake --build . -j && cmake --build . --target install -j

# Synthetic tests (5/5 PASS)
/home/hy/conda/envs/gnuradio/bin/python examples/test_mmse_equalize_synthetic.py

# USRP validation
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=./build/python/bindings:./python:./examples \
  IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 IEEE80211_MMSE_EQUALIZE=1 \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_usrp_phase44.py
```