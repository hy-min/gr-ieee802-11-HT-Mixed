# Phase 42 Verdict — Per-SC H52 Null Detection + Interpolation REFUTED

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: ❌ REFUTED on USRP
**Commits**: b1dc16f (implementation), 9fdb137 (revert)

## Background

After 41 phases / 12 REFUTED hypotheses, USRP HT-SIG viterbi failure was attributed
to **Hhdr52 channel nulls** (`|H| ∈ [0.02, 0.14]`) causing 50× noise amplification,
putting equalized HT-SIG on REAL axis where QBPSK rotation detection fails.

Phase 42 proposed **per-SC H52 null detection + frequency-domain interpolation** as
a two-layer architectural fix, isolated behind environment variables.

This verdict documents **Layer 1 only** (H52 null detection + interpolation).

## Test Configuration

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=./build/python/bindings:./python:./examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  IEEE80211_H52_NULL_INTERPOLATE=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 30
```

Standard 5 GHz A:0 subdev, 30 second capture, 31 frames transmitted.

## Results

| Metric | Phase 41 baseline (no fix) | Phase 42 Layer 1 ON | Delta |
|---|---:|---:|---:|
| Sent | 31 | 31 | 0 |
| Recv | 0 | 0 | 0 |
| FCS_OK | 0 | 0 | 0 |
| FCS_FAIL | 0 | 0 | 0 |
| HT_SIG_PARSE_FAIL | 8 | **18** | **+125% (worse)** |
| LSIG_DECODE OK | 104 | 89 | -15 (regression) |
| LSIG_PARSE_FAIL | — | 117 | (regression) |
| avg_snr_htsig | 10.99 | 0.94–3.32 | **-7 to -10 dB collapse** |
| avg_snr_lsig | 15.12 | 0.99 | **-14 dB collapse** |

## Root Cause: Median-Based Null Detection Has High False Positive Rate Under Low SNR

The Layer 1 algorithm used `median(|H|)` as the reference for null detection. At
the observed USRP SNR (`avg_snr_lsig ≈ 1 dB`), noise variance is high enough to
**drag the median down**, causing most subcarriers to fall below the `0.3 × median`
threshold.

Consequence: the algorithm flagged many healthy subcarriers as null, then
**interpolated over them**, replacing good H estimates with averaged (and thus
noisy) neighbors. The equalizer then divided by these corrupted H values, producing
**catastrophic noise amplification across nearly all 52 subcarriers**, not just the
true nulls.

This is observable in the diagnostics:
- `avg_snr_lsig` collapsed from 15.12 → 0.99 (a 14 dB regression)
- `avg_snr_htsig` collapsed from 10.99 → 0.94–3.32 (a 7–10 dB regression)
- Even L-SIG (BPSK, 90° margin) viterbi failure rate increased, because Layer 1
  modifies H52 used by **all** downstream equalization, not just HT-SIG

## Why This Wasn't Visible in the Python Synthetic Test

`examples/test_h52_null_injection.py` uses `H_true[i] = 0.5 + 0.3j * randn()` with
**|H| ≈ 0.5–0.8** (clean channel, strong SCs) and `|H_null[i]| = 0.05` (deep nulls).
In that regime, the `0.3 × median` threshold cleanly separates the two populations.

Real USRP air-interface channels at SNR ≈ 1 dB do not match this distribution.
**Phase 38's measured `|H| = 0.02–0.14` at null SCs and `|H| = 0.5–1.0` at strong
SCs assumed the noise was signal-bounded, not noise-dominated.**

## Verdict

❌ **Phase 42 Layer 1 REFUTED on USRP**.

Median-based null detection is **too fragile under low-SNR USRP air-path conditions**.
The very channels where nulls are a problem (low SNR) cause false-positive null
detection, which then actively destroys the equalizer by replacing good H with
average-of-noise.

## Action Taken

1. **Reverted** commit `b1dc16f` (Layer 1 C++ implementation) via `git revert`.
2. **Rebuilt + installed** to restore default behavior.
3. **Layer 1 env var (`IEEE80211_H52_NULL_INTERPOLATE`) defaults to OFF** — code
   in git history, no behavior change for existing runs.

## Why Not Layer 2 (LLR Weighting)?

Layer 2 (`estimate_llr_confidence_from_h52`) is independent of Layer 1 and only
modifies the HT-SIG viterbi soft input. It uses `|H[i]| / max(|H|)` as LLR weights,
which is robust to the median-drag failure mode that broke Layer 1.

**However**, Layer 2 was originally planned as a complement to Layer 1 (Layer 1
fixes H52 first, Layer 2 then weights the viterbi input). With Layer 1 REFUTED,
Layer 2 alone is being evaluated as Task 6, since the question "does per-SC LLR
weighting help HT-SIG?" remains open.

## Counter-Increment

13 REFUTED hypotheses on USRP HT-SIG (Phases 25, 26, 27, 29.2, 30, 35, 36, 37, 38,
39, 40, 41, **42**).

## Architectural Lesson

The **median is not robust to noise-dominated channels**. For per-SC channel
processing on USRP air interface, a more robust reference statistic is needed:
- Trimmed mean (drop top/bottom 10% before averaging)
- Mean of |H|² (energy, less sensitive to outliers)
- Or detection based on **smoothness in time** rather than magnitude alone

Future work should consider these alternatives, but given the 13 REFUTED count
and the spec's "investigation at wall" status, the **recommended path forward
remains accepting the channel-physics limitation** and using software loopback as
the decoder validation path (Phase 37 verdict).

## References

- `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` — Phase 41 closure
- `docs/superpowers/specs/2026-06-28-usrp-htsig-per-sc-null-detection.md` — Phase 42 spec
- `examples/test_h52_null_injection.py` — synthetic test (6/6 PASS, but doesn't model USRP noise)
- `lib/frame_equalizer_impl.cc` — Layer 1 implementation (reverted)
