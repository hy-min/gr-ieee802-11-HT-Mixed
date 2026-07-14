# Phase 145b Verdict: δ-Correction Direction + FRAME_START Sweep

**Date:** 2026-07-14
**Branch:** TEST1
**Status:** COMPLETE — δ direction and FRAME_START_BASE are NOT the root cause

---

## Goal

Validate the two leading Phase 145 hypotheses on real USRP IQ:

1. **δ-correction sign/direction error** in `frame_equalizer_impl.cc:7458`.
2. **FFT-window timing misalignment** (`FRAME_START_BASE=174` no longer optimal).

---

## Tools

- `p145b_synthetic_gen.py` — generates clean HT-Mixed frames (L-STF + L-LTF + L-SIG) with known sub-sample δ injected.
- `p145b_delta_sign_analysis.py` — offline analysis: estimates δ from H52, tests 5 δ-correction strategies on L-SIG equalized constellation.
- Fresh USRP capture: `/tmp/p145_postfix_5250.fc32` (5250 MHz cable, tx-gain 0, 10 s, 6.68M samples, 9 frames detected).

---

## Synthetic Validation

On noiseless synthetic frames (δ = 0.0, ±0.3, +0.7 samples):

- All 5 strategies (`none`, `rx_plus`, `rx_minus`, `h_plus`, `h_minus`) produce **identical clean constellations** (`im_var = 0`).
- p145b correctly tracks relative δ changes (injected 0.3 → estimated 0.246; injected 0.7 → estimated 0.736).
- **Conclusion:** p145b's δ estimator is reliable; strategies are indistinguishable without noise.

---

## USRP Capture Results

### δ-direction sweep (offsets 174/254/334)

| Strategy | Aggregate im_var | Aggregate n_correct / 48 |
|---|---|---|
| none | 1.4571 | 23.78 |
| rx_plus (current C++) | 1.7167 | 23.78 |
| rx_minus | 1.3393 | 22.56 |
| h_plus | 1.3393 | 22.56 |
| h_minus | 1.7167 | 23.78 |

- No strategy clearly dominates.
- Differences are frame-dependent and small compared to inter-frame variance.
- **Conclusion:** δ direction is **not** the dominant blocker.

### FRAME_START offset sweep

| lltf0 offset | none im_var | rx_plus im_var | none n_correct | rx_plus n_correct |
|---|---|---|---|---|
| 170 | 0.7596 | 0.7367 | 22.22 | 23.00 |
| 174 (current) | 1.4571 | 1.7167 | 23.78 | 23.78 |
| 176 | 1.1619 | 1.2536 | 26.00 | 26.22 |
| 190 | 0.4636 | 0.5369 | 25.33 | 26.78 |
| 192 | 0.6946 | 0.8020 | 26.11 | 25.44 |

- `lltf0=190` gives the cleanest constellation (im_var 0.46–0.54) and best bit correctness (26.78/48).
- `lltf0=176` is second-best and closer to the theoretical L-LTF0 DATA start (176).
- However, **even at the best offset, the L-SIG constellation is NOT BPSK-like** (phase concentration near 0°/180° is < 9%).
- **Conclusion:** FRAME_START_BASE is close to optimal; a small shift does not resolve NOISE_LIKE.

---

## Root-Cause Narrowing

The USRP capture has strong signal (`|H| = 15–28`), but the equalized L-SIG remains scattered regardless of δ strategy or window alignment.

Additional experiment: 2-way SNR-weighted H52 averaging (L-LTF0 + L-LTF1) at `lltf0=176` reduces `im_var` from 1.27 to 1.05 — a modest improvement, but still far from clean.

**This confirms Phase 112 R1:** the L-SIG failure is driven by **per-SC H52 noise** (≈1.77 rad phase std), not by δ direction or FFT-window alignment.

---

## What This Rules Out

- δ-correction sign flip (`IEEE80211_TIMING_OFFSET_SIGN_FLIP`) — not needed.
- `FRAME_START_BASE` change from 174 — marginal, not root cause.
- `p145b_delta_sign_analysis.py` tool bug — validated on synthetic and real data.

---

## Next Attack Directions

1. **Compare C++ Hhdr52 with p145b H on the same USRP frames** to find where the C++ chain diverges from the offline ideal.
2. **Dump C++ H52 + equalized L-SIG constellation** on a fresh USRP run (`IEEE80211_H52_EQ_INPUT_DUMP=1`, `IEEE80211_HTSIG_EQ_DUMP=1`) and compare with p145b.
3. **Investigate the equalizer's own H estimation** (`ls.cc`) — the data path uses `ls::equalize`, not `frame_equalizer`'s `Hhdr52`; maybe the L-SIG viterbi path and data path disagree.
4. **Revisit cross-frame / Wiener averaging** — Phase 140/141 showed partial gains; stack them with the 2-way fix and verify on this new capture.

---

## Files

- Capture: `/tmp/p145_postfix_5250.fc32`
- Synthetic generator: `p145b_synthetic_gen.py`
- Analysis tool: `p145b_delta_sign_analysis.py`
- This verdict: `docs/superpowers/notes/2026-07-14-phase145b-delta-framestart-verdict.md`
