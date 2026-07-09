# Phase 129 T2: Soft LLR Viterbi C++ Implementation (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **IMPLEMENTED, file-replay REFUTED at USRP noise floor** — proper LLR
formula `LLR = 4·Im(eq)·|H|²/σ²_channel` is correctly implemented and σ² estimation
from null SCs works (σ²_a=0.0777, σ²_b=0.0487 stable across 16 candidates), but
CRC pass rate remains 0/120s on USRP cross-board capture. Confirms Phase 129 T1
synthetic finding: 1.77 rad phase noise dominates; decoder-internal gain is
insufficient alone.

## TL;DR

Added new env var `IEEE80211_HTSIG_SOFT_LLR_V2=1` (default OFF) that uses the
proper log-likelihood ratio LLR formula instead of Phase 44's `sign·|H|/max|H|`.

**File-replay results on `/tmp/p125_xboard_burst.fc32` (cross-board capture)**:

| Config                          | Duration | FCS_OK | Best Metric |
|---------------------------------|----------|--------|-------------|
| baseline (hard viterbi)         | 5s       | 0      | 11          |
| Phase 44 (IEEE80211_SOFT_LLR_VITERBI=1) | 5s | 0      | 13700 (Q8.8) |
| Phase 129 v2 alone              | 10s      | 0      | 396913 (Q8.8) |
| Phase 129 v2 + Phase 128 + null mask | 15s | 0    | 5921445 (Q8.8) |

**Metrics are not directly comparable** across schemes (different LLR scales).
**CRC pass rate is the honest comparison: all 0**.

## Implementation

**Files**:
- `lib/frame_equalizer_impl.h` — added `d_use_soft_llr_v2`, `d_sigma2_htsig_a/b`
- `lib/frame_equalizer_impl.cc` — added env var, σ² estimation, new LLR formula

### Env var + flag

```cpp
// IEEE80211_HTSIG_SOFT_LLR_V2=1 (requires IEEE80211_SOFT_LLR_VITERBI=1)
d_use_soft_llr_v2 = (env_sllr_v2 && env_sllr_v2[0] == '1');
```

### σ² estimation

Two strategies, in priority order:

1. **From null SC mask** (`IEEE80211_HTSIG_NULL_SCS='12'` etc.):
   `σ² = mean(|eq_null|² · |H_null|²)`. Requires user to specify which data loop
   positions to use.

2. **Auto-detect bottom-quartile |H|²** (fallback): pick 6 SCs with smallest |H|²
   and compute same statistic. Used when no mask is provided.

Guard: σ² < 1e-3 → σ² = 1e-3 (avoid div-by-zero).

### LLR formula

**Phase 44** (kept as baseline):
```cpp
llr[i] = sign(eq.imag()) * (|H[i]| / max(|H|))  // ∈ [-1, +1]
```

**Phase 129 v2** (new, when v2 enabled):
```cpp
llr[i] = 4.0f * eq.imag() * |H[i]|² / σ²_channel  // unbounded, weighted
```

For null SCs (in mask), llr[i] = 0 (viterbi ignores the bit) — same as Phase 102.

### Diagnostic log

```
[HTSIG_SOFT_LLR_V2] rot=0 inv_a=0 inv_b=0 sigma2_a=0.0777 sigma2_b=0.0487 metric=396913
```

σ² values are stable across the 16 (rot × inv_a × inv_b) candidates for a given
frame (good — means σ² estimation is dominated by |H_null|² not by the rotation).

## File-Replay Results (USRP cross-board capture, Phase 125b)

### Baseline (no soft LLR)
```
metric distribution (1712 candidates):
11=8 12=40 13=158 14=362 15=522 16=454 17=136 18=32
```
- Best metric: 11
- 0 FCS_OK

### Phase 44 alone (`IEEE80211_SOFT_LLR_VITERBI=1`)
```
metric distribution: 13700-15780 range (Q8.8 fixed-point from squared-error)
```
- Best metric: 13700 (Q8.8)
- 0 FCS_OK
- Metric magnitude reflects squared-error sum across 96 bits with LLR ∈ [-1, +1]

### Phase 129 v2 alone
```
metric distribution: 396913-684399 range (Q8.8)
```
- Best metric: 396913
- 0 FCS_OK
- σ²_a=0.0777, σ²_b=0.0487 (stable)
- Metric magnitude much larger due to LLR magnitudes ~100 (4·Im·|H|²/σ²)

### Phase 129 v2 + Phase 128 (CFO/SFO from HT-LTF) + null mask at SC 12
```
metric distribution: 5.9M-34.8M range (Q8.8)
```
- Best metric: 5.9M (Q8.8)
- 0 FCS_OK
- σ²_a varies across candidates (Phase 128 δ correction changes effective null SC selection)

## Why v2 alone doesn't break the wall

Per Phase 129 T1 synthetic benchmark:
- σ_per_sc=1.0 rad: soft LLR gives +12pp gain
- σ_per_sc=1.77 rad (USRP): 0/100 PASS for both hard and soft

The decoder-internal gain from proper LLR is **too small at 1.77 rad phase noise**
to bridge the viterbi free-distance=10 ceiling. The noise is so large that even
the correct LLR formula yields the same bit errors as hard decisions.

This confirms the Phase 112 R1 finding: the 1.77 rad per-SC phase noise is from
the USRP analog chain (LO/RF), not decoder-fixable. Decoder-internal attacks
(DD / Kalman / proper LLR) cannot bridge this gap alone.

## What's needed to break the wall

Per Phase 129 verdict's "What Now?" section, the path forward requires:

1. **Phase 130**: Per-SC LLR zeroing for null SCs — extends v2 with explicit
   erasure handling for the 5 Phase 78b stable null SCs. Adds ~0.3 dB gain.
2. **Phase 131**: Multi-pass H52+δ refinement — use top-K viterbi candidates
   as pseudo-training to refine H52, iterate 2-3 times. Adds ~0.5 dB gain.
3. **Combined with existing Phase 128 (CFO/SFO) + 118b (H_AVERAGE) + 126A
   (FreqSmooth)**: total potential gain ~1.5-2.5 dB, which at 1.77 rad baseline
   brings effective noise to ~1.0 rad → CRC pass rate ~10-20%.

Per user's "不可能接受现状" directive, equalizer + decoder attacks MUST continue.
This T2 result confirms Phase 129 v2 alone won't break the wall, but it is a
necessary building block for the combined approach.

## Limitations

- **σ² estimation depends on null SC mask**: if user doesn't set
  `IEEE80211_HTSIG_NULL_SCS`, the fallback uses bottom-quartile |H|² which may
  not capture the true null SCs. Future work: auto-detect stable null SCs from
  |H|² distribution.
- **LLR scale not comparable to Phase 44**: Phase 44 LLR ∈ [-1, +1]; v2 LLR
  can be ±100+. Both feed into the same squared-error branch metric, but the
  relative ordering (best vs worst candidate) may differ.
- **σ² estimation happens per candidate**: 16 candidates × 2 (HT-SIG0 + HT-SIG1)
  = 32 σ² estimates per frame. They're stable across rotations (good) but
  re-computed for each (rot, inv_a, inv_b) trial.

## Code Diff Summary

- **Frame_equalizer_impl.h**: +12 lines (flag declaration, σ² fields)
- **Frame_equalizer_impl.cc**: +110 lines (env var, σ² estimation, LLR formula,
  diagnostic log, 3 call sites updated to pass new params)
- **Build**: clean compile + link, 0 warnings
- **Install**: make install succeeded
- **No baseline regression**: Phase 44 path (`IEEE80211_SOFT_LLR_VITERBI=1`
  alone) still produces same metric distribution as before T2.

## Related

- [[project-p112-r1-argh-rootcause]] — 1.77 rad ceiling
- [[project-p128-cfo-reest-htltf]] — Phase 128 PARTIAL
- Verdict T1: `docs/superpowers/notes/2026-07-09-phase129-t1-llr-synthetic.md`
- Verdict T2 (this file): `docs/superpowers/notes/2026-07-09-phase129-t2-cpp-verdict.md`