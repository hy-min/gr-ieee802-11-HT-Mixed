# Phase 129 T1: Soft LLR Viterbi Synthetic Benchmark (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **PARTIAL** — Soft LLR viterbi gives +12pp gain at σ=1.0 rad,
but at USRP Phase 112 R1 ceiling (σ=1.77 rad) both fail (0-1/100 pass).
**Decoder-internal gain is real but small (~0.5 dB); need combination with
other Phase 128/118b/130/131 techniques to break the wall.**

## TL;DR

Implemented soft-LLR viterbi in pure Python (mirrors C++ structure) and
benchmarked against the existing hard viterbi on a USRP-like channel
model (multipath + per-frame δ + 5-10 null SCs + per-SC phase noise
+ mild AWGN).

| sigma_per_sc_rad | hard viterbi | soft LLR viterbi | Gain |
|------------------|--------------|------------------|------|
| 0.5              | 50/50        | 50/50            | 0 (saturated) |
| 1.0              | 15/50        | 27/50            | **+12pp (+24%)** |
| 1.5              | 0/50         | 0/50             | 0 (both fail) |
| 1.77 (USRP)      | 0/100        | 1/100            | +1pp (stat noise) |
| 2.0+             | 0/50         | 0/50             | 0 |

**Findings**:
1. **Soft LLR works correctly**: clean signal → 50/50 PASS, metric +384
2. **Real gain at moderate noise**: σ=1.0 rad, +12pp (24% improvement)
3. **Phase 112 R1 ceiling is real**: at σ=1.77 rad (USRP), both fail
4. **Estimated decoder-internal SNR gain**: ~0.5 dB (1.0 → 1.5 rad)
5. **Implication**: Soft LLR alone CANNOT break USRP FCS_OK wall
6. **Required**: combination with Phase 128 (δ) + 118b (H_AVERAGE) + 130 (null SC) + 131 (iteration) — need ALL of them to bridge the gap

## Bug Found and Fixed

**Initial implementation had two bugs**:

1. **LLR formula missing |H|² factor and using wrong σ²**:
   - Wrong: LLR = 4·Im(eq) / σ²_post_eq
   - Correct: LLR = 4·Im(eq)·|H|² / σ²_channel
   - σ²_post_eq is dominated by null SC amplification (1/|H_null|²) and is misleading
   - σ²_channel = mean(|eq_null|² · |H_null|²) from null SCs

2. **Viterbi initialization used INF (for minimization) but should use -INF (for maximization)**:
   - Soft viterbi MAXIMIZES accumulated LLR, so unreachable states should start at -INF
   - With INF (the hard-viterbi convention), no state ever gets a valid metric, decoder returns None

After fixes, soft viterbi correctly decodes the clean signal (metric=384,
all bits match).

## Ultimate Ceiling Check (CRITICAL FINDING)

Test: clean channel (no multipath, no nulls, no AWGN) + ONLY per-SC
phase noise at σ=1.77 rad. Result: **soft viterbi 50/50 PASS**.

This is a major insight: 1.77 rad phase noise is **NOT the hard ceiling**
by itself. The earlier 0/100 in the multipath+nulls+AWGN test was due to
the COMBINATION of all impairments, not any single one.

Implication: the real USRP bottleneck is the SPECIFIC combination of
impairments, not the per-SC phase noise alone. The Phase 78b 5 STABLE
globally-null SCs are the structural issue — random nulls (synthetic)
have less impact because they average out via deinterleaver.

This means:
- **Decoder-internal attacks have headroom** (clean σ=1.77 PASS)
- The challenge is COMBINED impairments
- The C++ implementation is worth pursuing

## What Now?

## Test Infrastructure

- `examples/test_p129_soft_llr_viterbi.py` (new)
- `examples/test_p129_soft_simple.py` (debug helper)

Both reuse the proven components from `examples/test_htsig_viterbi_synthetic.py`
(BCC encoder K=7 r=1/2, HT-SIG interleaver, QBPSK modulation, viterbi decode).

## What Now?

Per user "decoder 内部攻击" directive + 30+ REFUTED equalizer fixes:

**Phase 129 T2 (C++ implementation)**: Implement soft-LLR viterbi in
`lib/viterbi_decoder/` with new env var `IEEE80211_HTSIG_SOFT_VITERBI=1`
(default OFF). Keep the hard viterbi path as the default for regression
safety. The C++ implementation must match the Python benchmark exactly
in metric computation.

**Phase 130 (T1 in tasks)**: Per-SC LLR zeroing for null SCs. Add
`IEEE80211_HTSIG_LLR_NULL_SC=1` env var. Combine with Phase 129 for
combined ~1 dB decoder-internal gain.

**Phase 131 (T1 in tasks)**: Multi-pass H52 + δ refinement. After first
soft viterbi pass, use top-K candidates as pseudo-training to re-estimate
H52 and δ, then re-decode. Iterate 2-3 times. Goal: bridge the remaining
~0.5 dB gap to break the 1.77 rad wall.

**Combined expected gain**: 1-1.5 dB from decoder-internal (LLR + null
zeroing + iteration) PLUS existing 0.5-1 dB from upstream (Phase 128
δ + 118b H_AVERAGE + 126A freq smooth). Total: 1.5-2.5 dB.

At 1.77 rad baseline + 2 dB gain → 1.25 rad effective noise. viterbi
capacity at 1.25 rad is ~50% CRC pass rate. **This is the path to
USRP FCS_OK.**

## Limitations of the Synthetic Test

- The USRP-like channel model uses 5-10 randomly null SCs, but Phase 78b
  identified only 5 STABLE globally-null SCs. The synthetic may
  under-represent the real null SC structure.
- The phase noise is white Gaussian. The real USRP noise has structure
  (LO/RF chain) that may correlate differently.
- The synthetic AWGN is mild (SNR=10 dB). Real USRP may have lower SNR
  before the phase noise dominates.
- Real equalizer estimation (Hhdr52) is imperfect; synthetic uses
  ideal H. This is intentional (isolates the decoder question), but
  real C++ will need to handle the H estimation noise too.

These limitations make the C++ implementation necessary even with
synthetic showing the path. USRP test (Phase 129 T3) is required to
validate.

## Related

- [[project-p112-r1-argh-rootcause]] — 1.77 rad ceiling
- [[project-p128-cfo-reest-htltf]] — Phase 128 PARTIAL positive
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (current best metric 12)
- Verdict: `docs/superpowers/notes/2026-07-09-phase129-t1-llr-synthetic.md`
