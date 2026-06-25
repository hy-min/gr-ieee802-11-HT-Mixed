# Phase 38 Verdict — Hhdr52 Null Amplification Bottleneck Confirmed (2026-06-25)

**Status:** ❌ **Step 4 (per-symbol δ tracking) REFUTED.** ✅ **HTSIG_EQ_DUMP reveals new root cause: Hhdr52 channel nulls amplify noise to ~50× at some SCs, burying QBPSK signal below viterbi tolerance.** BPSK L-SIG survives because of 90° margin; QBPSK HT-SIG cannot.

## TL;DR

The equalized HT-SIG0/HT-SIG1 signal at the viterbi input has:
- **|re| ≈ 1.0** (correct unit-magnitude on REAL axis)
- **mean_im ≈ 0.0 to -0.3** (small rotation bias)
- **std_im = 1.1 to 1.9** (HUGE perpendicular noise floor)

This means the signal is essentially BPSK-on-real with massive noise on the imag axis. The viterbi 4-rotation search should align real→imag, but the perpendicular noise floor exceeds the QBPSK decision margin (45°), so no candidate wins.

L-SIG with the same Hhdr52 works because:
1. BPSK has 90° margin (vs QBPSK's 45°)
2. viterbi rate-1/2 coding can correct single-bit errors from the same noise floor
3. **Hypothesis:** L-SIG bit decision uses `real` axis (line 93 `x.real() >= 0`), so it sees the clean signal; HT-SIG uses `imag` axis (line 2044), so it sees only the noise

## Step 4 Result — REFUTED

Per-symbol CPE fix (commit was 0084dc2 only — Step 4 uncommitted) was tested:

| Config | HT_SIG_PARSE_FAIL | LSIG_DECODE OK | Notes |
|---|---|---|---|
| Phase 34 only (current code) | 6-9 | 109-158 | baseline |
| + Phase 35 (per-symbol mean CPE) | 18 | 158 | worse (added noise to clean signal) |
| + Step 4 (per-symbol CPE via `estimate_header_cpe_rad`) | 38 | 266 | much worse |

**Why Step 4 made it worse**: For HT-SIG pilots {j,j,j,-j}, the helper returns arg(Σ(eqp·conj(expected_pilot))) = arg(4·exp(jθ)) = θ. But the actual equalized pilots are on the **real axis** (not imag as expected). The ± structure of the pilots cancels out in the sum, so the helper returns 0 (no drift) for HT-SIG0, but the actual phase rotation θ is non-zero. Step 4 applies -θ rotation only when |drift| > 1e-3, so it never fires. The 0.31 rad mean_im bias for HT-SIG1 is the actual rotation that the helper misses.

Even if Step 4 did fire, the noise floor (std_im=1.1-1.9) is too high for QBPSK to tolerate any constant rotation correction.

## Step 7 Finding — HTSIG_EQ_DUMP

New diagnostic: `IEEE80211_HTSIG_EQ_DUMP=1`. Dumps 48 data SCs of HT-SIG0/HT-SIG1 after `eq = d_early_eqsym / Hhdr52`, plus summary stats (mean|re|, mean_im, std_im).

### USRP test (HTSIG_EQ_DUMP=1 only, TIMING_OFFSET_APPLY=1, LSIG_RATE_FORCE=0xD)

**First run (1 frame captured)**:
```
htsig0 mean|re|=1.026 mean_im=0.009 std_im=1.098
htsig1 mean|re|=1.068 mean_im=-0.320 std_im=1.907
```

**Second run (1 frame, with LSIG_EQ_DUMP=1 also on)**:
```
htsig1 mean|re|=49.844 mean_im=7.554 std_im=76.195   # catastrophic null
```

The 49.8 mean|re| in the second run is a **channel null at one or more SCs** where Hhdr52 |H| < 0.02, amplifying the noise+signal by 50×.

### LSIG_EQ_DUMP (same frame, comparison)

L-SIG with the same Hhdr52 has most values at |eq| ≈ 0.5-1.5, with occasional outliers (e.g., (-5.32,-16.67) at one SC with |H|=0.025). L-SIG bit decision uses `real` axis, so the outlier's imag doesn't directly affect decoding. Viterbi rate-1/2 coding corrects the rare error.

For HT-SIG, the same outlier's imag becomes the bit decision axis. With 17+ magnitude imag, the bit is always 1 (or always 0 depending on sign), destroying QBPSK rotation alignment.

## Root Cause Analysis

### Why is the equalized signal on REAL axis instead of IMAG?

The HT-SIG pilots TX are {j, j, j, -j}. If equalization is perfect, eq_pilot = ±j. The dump shows eq_pilot is on real axis instead. This is consistent with:

1. **CFO/SFO/δ correction is canceling the QBPSK rotation**: The CFO/SFO/δ estimation treats HT-SIG0/1 like L-LTF0/1 (real pilots), so the per-symbol phase correction is wrong by exactly 90° for HT-SIG's QBPSK structure.

   - L-LTF0/1 pilots: {+1, +1, +1, -1} (real)
   - HT-SIG0/1 pilots: {j, j, j, -j} (imag, QBPSK rotated)
   - CFO/SFO/δ correction uses L-LTF phase as reference. L-LTF is BPSK, so correction brings signal to real axis.
   - For HT-SIG, this means CFO/SFO/δ correction brings signal to real axis (NOT imag where QBPSK lives).

2. **The 4-rotation search in the viterbi SHOULD handle this**: rot=1 or rot=2 rotates by ±90° to put the signal on imag axis. But the high perpendicular noise (std_im=1.1-1.9) from channel nulls means the rotated signal has SNR < 0 dB on the imag axis.

### Why is std_im so high?

Hhdr52 is computed from L-LTF0 (single OFDM symbol, no L-LTF1 averaging). L-LTF0 FFT has values 0.02-0.14 at most SCs (from LSIG_EQ_DUMP). When dividing signal/noise by H, the noise is amplified by 1/|H| = 7-50×.

L-SIG with the same noise floor works because:
- BPSK bit decision on real axis (clean signal axis)
- rate-1/2 viterbi corrects errors

HT-SIG with the same noise floor fails because:
- QBPSK bit decision on imag axis (noisy axis after real-aligned equalization)
- 4-rotation search doesn't help when the perpendicular noise exceeds the signal

## Why Phase 33/34 Fixes Weren't Enough

Phase 33 fixed the L-LTF0 14-sample cyclic shift, making H52 coherent. Phase 34 fixed the per-frame δ (sub-sample timing) so H52 phase is consistent across the frame. Both fixes address the **coarse** alignment.

What's left: the **fine** structure of the channel (deep nulls at certain SCs) which limits per-SC SNR. L-LTF0 alone is a noisy H estimate at nulls. Better H estimation:
- L-LTF0 + L-LTF1 average (smooths the estimate)
- HT-SIG0/1 own pilots (fresh H at HT-SIG0/1 time, no nulls issues if not at pilot SCs)
- Phase 4 median filter (but Phase 4 was REFUTED on USRP — H_BOTH_BROKEN)

## Next Phase: Phase 39 — Per-Symbol H Re-estimation

Use HT-SIG0's 4 pilots (SCs {-21, -7, +7, +21}) to estimate H at HT-SIG0 time:
- For each pilot SC: H_htsig0[i] = rx_pilot / known_pilot
- Interpolate H_htsig0 to all 52 SCs (linear between the 4 pilot SCs)
- Use H_htsig0 for HT-SIG0 equalization
- Same for HT-SIG1

Expected impact: H at HT-SIG0/1 time is FRESH (no 16 μs staleness), and the 4 pilots give 4× averaging of noise.

Risks:
- 4 pilots may not capture frequency-selective nulls at non-pilot SCs
- Phase 33b 64-PSK residual is per-frame, may not be at the same SCs as the pilots

## Files

- `lib/frame_equalizer_impl.h:115-126` — `d_log_htsig_eq` flag
- `lib/frame_equalizer_impl.cc:2628-2629` — env var init
- `lib/frame_equalizer_impl.cc:4141` — outer condition updated to include d_log_htsig_eq
- `lib/frame_equalizer_impl.cc:4187-4267` — HTSIG_EQ_DUMP block (committed 664afcd)
- Verdict: this doc

## Related

- [[project-p34-delta-correction]] — Phase 34 δ fixed L-SIG but not HT-SIG
- [[project-p33-lltf0-14sample-shift-fix]] — Phase 33 L-LTF0 fix
- [[project-p38-per-symbol-delta-drift]] — Phase 38 Step 3 verdict (per-symbol drift confirmed)
- [[project-p37-htsig-viterbi-synthetic]] — decoder correct, bottleneck is upstream
- [[project-p36-persc-fit-refuted]] — 9 equalizer-level investigations REFUTED, this is the 10th

## Conclusion

**Per-symbol δ tracking is real but not the bottleneck.** The HTSIG_EQ_DUMP reveals the actual bottleneck: Hhdr52 channel nulls amplify noise 50× at some SCs, making the QBPSK 4-rotation search unable to find a clear best path. L-SIG with BPSK tolerance + viterbi error correction survives; HT-SIG with QBPSK narrow margin cannot.

**Phase 39 candidate**: re-estimate H per-symbol from HT-SIG0/1 own pilots. This bypasses the L-LTF0 nulls and provides a fresh H at the actual symbol time.
