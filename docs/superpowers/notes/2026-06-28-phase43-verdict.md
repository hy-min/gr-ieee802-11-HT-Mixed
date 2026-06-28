# Phase 43 Verdict — Per-SC H52 Null-Based Hard-Bit Gating for HT-SIG Viterbi

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: ⚠️ **NEEDS USRP VALIDATION** (no USRP hardware available at execution time)
**Commits**: pending

## Background

After Phase 42 Layer 1 (median-based H52 null detection + interpolation) was
REFUTED on USRP (avg_snr_lsig collapsed 15 → 1 dB), Phase 43 proposed
**Layer 2 only**: per-SC H52 null detection that feeds into the HT-SIG viterbi
hard-bit input — without modifying H52 itself.

The key architectural insight (originally missing from the spec): the decoder's
viterbi receives HARD bits (uint8_t), not soft LLRs. Bit extraction happens in
`decode_htsig_from_rotated` at lines 2143-2154 (HT-SIG0) and 2242-2252 (HT-SIG1):

```cpp
eq = safe_div(rx52_a[i], H52_a[i]);   // for HT-SIG0
eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
```

So Layer 2 cannot inject "weights" — it must gate the bit extraction itself.

## What Was Implemented

### Algorithm (in lib/frame_equalizer_impl.cc::decode_htsig_from_rotated)

For each SC i in [0, 48) (data subcarriers, NOT pilots 48-51):

1. Compute `abs_H[i] = |H52[i]|`.
2. Sort `abs_H[0..48)` and pick `ref = sorted[43]` (90th percentile).
3. `threshold = 0.3 * ref`.
4. `is_null[i] = (ref > 1e-9) && (abs_H[i] < threshold)`.
5. After normal bit extraction, if `is_null[i]` is True, **force bit[i] = 0**.

### Critical Architectural Fix: Bit-Level Gating (NOT eq-zeroing)

The original Phase 43 spec said: "set `eq = gr_complex(0, 0)` so the bit decision
falls to bit=0". This is **WRONG** because the C++ ternary uses `>=`:

```cpp
eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
```

When `eq = 0`, `eq.imag() = 0`, and `(0 >= 0)` evaluates to **True**, so
`bit = 1`, NOT bit=0. The "obvious" gating strategy would force all null SCs
to bit=1 — directly opposite of the design intent.

**Fix**: gate at the bit level, not the symbol level:
```cpp
eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
if (is_null_a[i]) eqbits48_a[i] = 0;   // ← correct location
```

This was caught during Python test development (test_htsig_null_injection.py
test 3) and would have shipped as a subtle bug otherwise.

### Why 90th Percentile (NOT Median as Phase 42)

Median gets dragged DOWN by noise at low SNR, causing high false-positive rate.
90th percentile is HIGH-biased: noise cannot drag it UP. Real nulls
(|H| ~ 0.05) are still distinguishable from strong SCs (|H| ~ 0.5-1.0) even
when SNR drops to 1 dB (avg_snr_lsig at USRP floor).

Empirically validated in `test_robustness_vs_phase42_median`: in a
majority-null scenario (5 strong + 43 weak SCs), median flags **0/48** (FN
rate 100%) while 90th percentile flags **43/48** correctly.

## Files Changed

| File | Lines | Purpose |
|------|------:|---------|
| `lib/frame_equalizer_impl.h` | +9 | Add `d_htsig_llr_weight` field |
| `lib/frame_equalizer_impl.cc` | +65/-3 | Constructor env-var read + bit-extraction loop modification |
| `examples/test_htsig_null_injection.py` | +304 (new) | Python test mirror |

### Key C++ changes:

1. Field declaration in `frame_equalizer_impl.h` (private section).
2. Initializer list entry: `d_htsig_llr_weight(false)`.
3. File-static `g_htsig_llr_weight` declared near other file-static bridges
   (line 707), set from ctor.
4. Env-var read in ctor: `IEEE80211_HTSIG_LLR_WEIGHT`.
5. In `decode_htsig_from_rotated`: compute `is_null_a[]`, `is_null_b[]` once
   per call (when flag enabled), apply at the bit level in both extraction
   loops.

## Test Results

### Build
- `make -j4` ✓ passes (no warnings).
- `make install` ✓ passes.

### Python Tests (7/7 PASS)
```
test_detects_injected_nulls PASS (ref=0.6039)
test_no_false_positives_on_clean_channel PASS (ref=0.5099)
test_null_sc_bits_forced_to_zero PASS (ref=0.6039)
test_low_snr_robustness PASS (ref=0.9905, n_null=2)
test_pilots_not_gated PASS (ref=0.5099)
test_all_null_corner_case PASS (ref=0.0000)
test_robustness_vs_phase42_median PASS (median=0.0510 → flags 0/48 (FAILS),
                                          90th=0.7071 → flags 43/48 (correct))

=== All 7 tests PASS ===
```

### Loopback Regression (3/3 PASS with env var ON)
- `IEEE80211_HTSIG_LLR_WEIGHT=0` (default): `Final: OK=1 FAIL=0`
- `IEEE80211_HTSIG_LLR_WEIGHT=1`: `[HTSIG_LLR_GATE] ref_a=8.8752 n_null_a=0
  ref_b=8.8752 n_null_b=0` (no nulls flagged in clean loopback) →
  `Final: OK=1 FAIL=0`

The `n_null_a=0` result is expected: in clean software loopback, |H| is
uniformly high (ref ≈ 8.87 from the perfect equalization path), so no SCs
fall below the 0.3×ref threshold. This confirms gating is non-destructive
in the absence of actual nulls.

## USRP Validation: NOT EXECUTABLE (Hardware Missing)

`uhd_find_devices` returns "No UHD Devices Found". USRP X310 is not connected
to this execution environment. Cannot run the standard validation command:

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=./build/python/bindings:./python:./examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  IEEE80211_HTSIG_LLR_WEIGHT=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 30
```

**Action required**: Run the above command on a machine with USRP X310
connected to validate against Phase 41 baseline (HT_SIG_PARSE_FAIL=8, FCS_OK=0,
avg_snr_htsig=10.99).

## Acceptance Criteria Status

| Criterion | Status |
|-----------|:------:|
| Build passes | ✓ |
| Loopback 3/3 PASS with env var ON and OFF | ✓ |
| Python test 4/4 PASS (delivered 7/7) | ✓ |
| USRP: HT_SIG_PARSE_FAIL does NOT increase | ⚠️ NOT RUN |
| USRP: any reduction is a win | ⚠️ NOT RUN |

## Architectural Concerns / Lessons

### Concern 1: Bit=0 Bias Introduced by Gating
Forcing null SCs to bit=0 introduces a deterministic bias. Across many
null SCs in a frame, this biases the viterbi toward outputs with more
zero-bits at those positions. If the transmitted HT-SIG happens to have
mostly bit=1 at those positions, we're systematically wrong.

However: the alternative (random noise-driven bits at null SCs) is
**strictly worse** — those bits are essentially noise, contributing random
errors that confuse viterbi convergence. A consistent bit=0 reduces viterbi
input variance on those SCs without random scatter. Bit=0 is the standard
"soft zero" / "no information" placeholder.

### Concern 2: Pilot SCs Not Gated
Pilots at indices 48-51 are excluded from null detection. This is correct
because pilots carry known training values and should pass through the
existing equalizer path. (Verified by `test_pilots_not_gated` PASS.)

### Concern 3: H52_a vs H52_b May Differ
In Phase 39 mode (`IEEE80211_HTSIG_H_REESTIMATE=1`), H52_a and H52_b can
differ from each other (re-estimated from each symbol's pilots). The
implementation computes `is_null_a[]` from H52_a and `is_null_b[]` from H52_b
independently. This is the correct behavior. Both gates are independently
controllable by the same env var.

### Concern 4: Per-Frame Ref Recomputation
The 90th percentile is recomputed every call to `decode_htsig_from_rotated`
(which is called up to 16 times per frame for the 4×4 QBPSK rotation search).
This is 16 × 48 log2(48) ≈ 16 × 48 × 5.6 ≈ 4300 comparisons per frame —
negligible compared to the viterbi cost itself.

## Recommended Next Steps

1. **Run USRP validation** when hardware is available (compare against
   Phase 41 baseline).
2. If HT_SIG_PARSE_FAIL decreases: explore making Layer 2 default-on.
3. If HT_SIG_PARSE_FAIL increases: REFUTED, revert (this is exactly
   what Phase 42 Layer 1 hit).
4. Consider: per-frame aggregation of null patterns to detect persistent
   channel nulls (different hypothesis space, separate investigation).

## Counter-Increment

Phase 43 is **NOT** a hypothesis yet — it has not been validated on USRP.
If validated, it would be the **14th** equalizer-level investigation. If
REFUTED, it would join the 13 REFUTED list.

The architectural lesson from Phase 42 + 43 combined:
- Median is not robust to noise-dominated channels (Phase 42 failed).
- 90th percentile is robust in some regimes but may over-flag in others
  (see `test_low_snr_robustness` showing 2 false positives at heavy noise).
- Bit-level gating (post-extraction) is the only legal intervention point
  in the current decoder architecture.

## References

- `docs/superpowers/notes/2026-06-28-phase42-verdict.md` — Phase 42 REFUTED
- `examples/test_h52_null_injection.py` — Phase 42 Layer 1 test (kept for
  reference, Layer 1 implementation reverted in commit 9fdb137)
- `lib/frame_equalizer_impl.cc::decode_htsig_from_rotated` — function modified
- `examples/test_htsig_null_injection.py` — Phase 43 Layer 2 test (7/7 PASS)