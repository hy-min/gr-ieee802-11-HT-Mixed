# Per-SC H52 Null Detection + LLR Weighting for USRP HT-SIG

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: Design approved, awaiting implementation plan
**Verdict reference**: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` (CLOSED — investigation reopened for Layer 1+2 attempt)

## Background

After 41 phases of investigation and 12 REFUTED hypotheses, the USRP HT-SIG viterbi
failure was attributed to **Hhdr52 channel nulls** at the air interface. Quantified by
Phase 38:

- `|Hhdr52[i]| ∈ [0.02, 0.14]` at null subcarriers (vs 0.5-1.0 at strong SCs)
- Equalized HT-SIG signal: `std_im = 1.1-1.9` on imaginary axis (QBPSK decision)
- L-SIG BPSK (real axis, 90° margin) survives
- HT-SIG QBPSK (45° margin) cannot
- Final verdict concluded this is a "channel-physics limitation" requiring architectural change

This design proposes a **two-layer architectural fix** to address the channel null
issue at the algorithmic level, isolating the changes behind environment variables
so the default behavior is unchanged.

## Goals

**Primary**: Achieve `FCS_OK > 0` on USRP for HT-SIG frames with `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14 IEEE80211_TIMING_OFFSET_APPLY=1` baseline plus the two new env vars.

**Secondary**: Quantify improvement on USRP `HT_SIG_PARSE_FAIL` (Phase 41 baseline = 8 events / 1 frame / 30s).

**Non-goals**: Fixing the underlying USRP air-interface channel physics. Touching
viterbi algorithm (Phase 37 confirmed correct). Touching L-LTF/H52 estimation
algorithm itself.

## Architecture

Two independent layers, each behind an environment variable (default OFF):

```
H52[52] raw (estimated from L-LTF0+L-LTF1)
   │
   ├─ [Layer 1: NULL_DETECT+INTERPOLATE]   (env: IEEE80211_H52_NULL_INTERPOLATE=1)
   │     is_null[i] = (|H[i]| < 0.3 * median(|H|))
   │     H_interp[i] = mean(H[L], H[R]) for null SCs
   │
   ▼
H52_corrected[52]
   │
   ▼
equalization: rx / H52_corrected  (already wrapped in safe_div)
   │
   ▼
equalized HT-SIG symbols[52]
   │
   ├─ [Layer 2: LLR_WEIGHT]   (env: IEEE80211_HTSIG_LLR_WEIGHT=1)
   │     weight[i] = |H[i]| / max(|H|)
   │     eqsym[i] *= weight[i]
   │
   ▼
viterbi soft input  (algorithm unchanged)
```

**Independence**: Layer 1 modifies H52 (used by all downstream equalization including
L-SIG). Layer 2 modifies only the HT-SIG viterbi soft input. Either can be enabled
alone; both can be enabled together.

## Components

### Component 1: `estimate_h52_null_index(H52)` — null detection

**File**: `lib/frame_equalizer_impl.cc` (new static helper)

**Signature**:
```cpp
static std::array<bool, 52> estimate_h52_null_index(const gr_complexd H52[52]);
```

**Algorithm**:
1. Compute `abs_H[i] = |H52[i]|` for all 52 SCs
2. `median_abs = median(abs_H)`
3. `is_null[i] = (abs_H[i] < 0.3 * median_abs)` for each i
4. Return `is_null[]`

**Rationale**: Median is robust to outliers (real nulls are minority). Threshold
factor `k = 0.3` is empirically derived from Phase 38 evidence: null SCs had
`|H| = 0.02-0.14`, strong SCs had `|H| = 0.5-1.0`. The ratio `0.14/0.5 = 0.28`
suggests `k ≈ 0.3` cleanly separates the two populations.

**Tunability**: `k` is a constant in code; can be promoted to env var in follow-up
if needed.

### Component 2: `interpolate_h52_nulls(H52, is_null)` — null recovery

**File**: `lib/frame_equalizer_impl.cc` (new static helper)

**Signature**:
```cpp
static void interpolate_h52_nulls(gr_complexd H52[52],
                                  const std::array<bool, 52>& is_null);
```

**Algorithm**:
1. Count `n_null`. If `n_null == 0` or `n_null == 52`, return (no-op).
2. For each null SC `i`:
   - Find left non-null neighbor `L`: scan `i-1, i-2, ...` until non-null or `L = -1`
   - Find right non-null neighbor `R`: scan `i+1, i+2, ...` until non-null or `R = 52`
   - If both `L >= 0` and `R < 52`: `H52[i] = (H52[L] + H52[R]) / 2.0`
   - Else if `L >= 0`: `H52[i] = H52[L]`
   - Else if `R < 52`: `H52[i] = H52[R]`
   - Else: keep `H52[i]` (all-null corner case — see error handling)

**Rationale**: Frequency-domain interpolation exploits 802.11n channel coherence
across adjacent SCs. Simple mean (vs linear interpolation) is more robust to noise
on the boundary SCs and avoids extrapolation. Complexity O(n_null × 52), bounded by
52×52 = 2704 ops (negligible vs FFT).

### Component 3: `estimate_llr_confidence_from_h52(H52)` — LLR weights

**File**: `lib/frame_equalizer_impl.cc` (new static helper)

**Signature**:
```cpp
static std::array<double, 52> estimate_llr_confidence_from_h52(const gr_complexd H52[52]);
```

**Algorithm**:
1. Compute `abs_H[i] = |H52[i]|`
2. `max_abs = max(abs_H)`
3. `weight[i] = (max_abs > 0) ? (abs_H[i] / max_abs) : 1.0`
4. Return `weight[]`

**Rationale**: Per-SC LLR weighting reflects the well-known principle that
equalization SNR varies per subcarrier in frequency-selective channels. Multiplying
the equalized symbol by `|H[i]| / max(|H|)` linearly de-weights low-confidence SCs.
This is equivalent to soft-decision LLR scaling without modifying the viterbi
algorithm.

**Why this might work when Phase 30 "per-SC SNR drop" failed**: Phase 30 modified
the equalization step itself. This modifies the *viterbi input*, which is downstream.
The two are independent.

## Integration Points

### Layer 1 integration

**Location**: Immediately after `estimate_channel()` returns H52, before any
equalization.

```cpp
// existing code
estimate_channel(...);  // produces H52[52]
H52_ptr = d_H52;

// NEW (Layer 1)
if (d_h52_null_interpolate) {
    auto is_null = estimate_h52_null_index(H52_ptr);
    interpolate_h52_nulls(H52_ptr, is_null);
}
```

### Layer 2 integration

**Location**: In the HT-SIG equalization block, after equalization, before viterbi.

```cpp
// existing code: d_early_eqsym[kHtSig0Rel][i] = rx[i] / H52[i]

// NEW (Layer 2)
if (d_htsig_llr_weight) {
    auto weight = estimate_llr_confidence_from_h52(H52_ptr);
    for (int i = 0; i < 52; i++) {
        d_early_eqsym[kHtSig0Rel][i] *= weight[i];
        d_early_eqsym[kHtSig1Rel][i] *= weight[i];
    }
}
```

### Environment variable wiring

**File**: `lib/frame_equalizer_impl.h` (new private fields)

```cpp
bool d_h52_null_interpolate;   // IEEE80211_H52_NULL_INTERPOLATE
bool d_htsig_llr_weight;       // IEEE80211_HTSIG_LLR_WEIGHT
```

**File**: `lib/frame_equalizer_impl.cc` (constructor or `init` method)

```cpp
const char* env1 = std::getenv("IEEE80211_H52_NULL_INTERPOLATE");
d_h52_null_interpolate = (env1 && std::atoi(env1) == 1);

const char* env2 = std::getenv("IEEE80211_HTSIG_LLR_WEIGHT");
d_htsig_llr_weight = (env2 && std::atoi(env2) == 1);
```

## Error Handling

### Boundary cases — Layer 1

| Case | Behavior |
|------|----------|
| `median_abs == 0` (all SCs at zero) | `0.3 * 0 = 0`, all SCs flagged null. Layer 1 returns early (`n_null == 52`), no change to H52 |
| `n_null == 0` (no nulls) | Layer 1 returns early, zero overhead |
| `n_null == 52` (all nulls) | Layer 1 returns early (degraded but graceful — same behavior as default) |
| Isolated single non-null SC | All other SCs interpolate from this single anchor — degraded but coherent |
| Edge SC null (i=0 or i=51) | Single-side neighbor used (handled in interpolation logic) |
| Multiple consecutive nulls (>5 in a row) | Linear-ish interpolation via mean of two distant neighbors |

### Boundary cases — Layer 2

| Case | Behavior |
|------|----------|
| `max_abs == 0` | All `weight[i] = 1.0` (Layer 2 effectively disabled) |
| Normal channel (no nulls) | All `weight[i] ≈ 1.0`, no change to viterbi input |
| One SC very low (`|H| ≈ 0.05`, others `≈ 0.5`) | That SC gets `weight ≈ 0.1`, effectively zeroed at viterbi |

### Defensive guarantees

- Default OFF: zero behavior change for existing USRP / loopback runs
- All new code wrapped in env-var gates
- No changes to existing call sites
- No changes to viterbi algorithm
- No changes to H52 estimation algorithm itself (only post-processing)

## Testing Strategy

### Test 1: H estimation synthetic with injected nulls

**File**: `examples/test_h_estimation_synthetic.py` (augmented)

**New test case**:
1. Generate known `H_true[52]` with K=3 nulls at SCs `{3, 17, 31}` where
   `|H_true[i]| = 0.05` (within Phase 38's measured null range)
2. Add Gaussian noise: `H_obs = H_true + N(0, σ²)`
3. Run `estimate_h52_null_index(H_obs)`:
   - Verify ≥ 80% of injected nulls detected (false negatives allowed)
4. Run `interpolate_h52_nulls(H_obs_corrected, is_null)`:
   - Verify `|H_obs_corrected[i] - H_true[i]| < 5 × σ` at previously-null SCs

**Acceptance**: Test case passes on first run after implementation.

### Test 2: HT-SIG viterbi with injected nulls (NEW file)

**File**: `examples/test_htsig_null_injection.py` (new)

**Approach**: Mirror Phase 37's `test_htsig_viterbi_synthetic.py` but inject
channel nulls before equalization.

**Test matrix**:

| Variant | K nulls | SNR (dB) | Expected result |
|---------|--------:|---------:|-----------------|
| Baseline (no nulls, no fix) | 0 | 20 | PASS (Phase 37 Layer 1) |
| Null injection, no fix | 5 | 20 | FAIL (H nulls cause equalization failure) |
| Null injection + Layer 1 only | 5 | 20 | PASS expected |
| Null injection + Layer 1 + Layer 2 | 5 | 20 | PASS expected |
| Null injection + Layer 1 + Layer 2, low SNR | 5 | 6 | PASS expected (Phase 37 SNR threshold) |
| Null injection + Layer 1 only, all-nulls corner | 52 | 20 | PASS (no-op behavior) |

**Acceptance**: 6/6 PASS for Layer 1 enabled. 3/3 PASS for Layer 2 enabled.

### Test 3: Loopback regression

**File**: `examples/test_direct_loopback.py`

**Test matrix**:

| Env config | Expected |
|------------|----------|
| Default (both OFF) | 3/3 PASS (unchanged from Phase 38 baseline) |
| `IEEE80211_H52_NULL_INTERPOLATE=1` only | 3/3 PASS (loopback H52 has no nulls) |
| `IEEE80211_HTSIG_LLR_WEIGHT=1` only | 3/3 PASS |
| Both ON | 3/3 PASS |

### Test 4: USRP validation

**Command**:
```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  IEEE80211_H52_NULL_INTERPOLATE=1 \
  IEEE80211_HTSIG_LLR_WEIGHT=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 60
```

**Comparison vs Phase 41 baseline** (TBD columns filled by actual USRP run):

| Metric | Phase 41 (no fix) | Layer 1 only | Layer 1+2 |
|--------|------------------:|-------------:|----------:|
| `HT_SIG_PARSE_FAIL` | 8 | _measured_ | _measured_ |
| `FCS_OK` | 0 | _measured_ | **target: >0** |
| `LSIG_DECODE OK` | 104 | _measured_ (should not regress) | _measured_ |
| `avg_snr_htsig` | 10.99 | _measured_ | _measured_ |

_TBD = to be determined by actual USRP measurement during implementation / validation._

**Acceptance criteria**:
- ✅ `FCS_OK > 0` on USRP (primary success)
- ✅ `LSIG_DECODE OK` unchanged (no regression)
- ✅ `HT_SIG_PARSE_FAIL` reduced
- ❌ If `FCS_OK = 0` AND `HT_SIG_PARSE_FAIL` increases: REFUTED, revert + verdict

## Failure Modes and Rollback

| Outcome | Action |
|---------|--------|
| Layer 1 ON: `FCS_OK > 0` | ✅ Success: commit, write verdict "Phase 42: per-SC null detection unblocks HT-SIG" |
| Layer 1 ON: No improvement, no regression | Try Layer 2 alone, then Layer 1+2 |
| Layer 1 ON: `HT_SIG_PARSE_FAIL` increases | ❌ REFUTED, revert env-var to OFF, write verdict |
| Layer 1+Layer 2: still `FCS_OK = 0` | ❌ REFUTED combined, write verdict: "channel nulls are not the bottleneck — investigate elsewhere" |

**Rollback safety**:
- Env vars default OFF → no behavior change for existing runs
- All changes in `lib/frame_equalizer_impl.{h,cc}` only → localized revert
- No viterbi or H52-estimation-algorithm changes → no risk of breaking decoder correctness

## Files Affected

| File | Change | Lines |
|------|--------|------:|
| `lib/frame_equalizer_impl.h` | New private fields `d_h52_null_interpolate`, `d_htsig_llr_weight` | ~+3 |
| `lib/frame_equalizer_impl.cc` | New helpers + integration points | ~+60 |
| `examples/test_h_estimation_synthetic.py` | New test case for null injection | ~+50 |
| `examples/test_htsig_null_injection.py` | NEW test file | ~+200 |

**No changes to**:
- `lib/viterbi_decoder*.cc` — viterbi algorithm unchanged (Phase 37 verified)
- `lib/ht_symbol_splitter_impl.cc` — splitter unchanged (Phase 40 verified)
- `lib/sync_long.cc` — sync_long unchanged (Phase 33 verified)
- `lib/estimate_channel*.cc` — H estimation algorithm unchanged

## Open Questions

1. **Layer 1 only vs Layer 1+2 first**: Plan tests Layer 1 alone first to isolate
   the contribution of each layer. If Layer 1 alone achieves `FCS_OK > 0`, skip
   Layer 2 implementation for now. If not, add Layer 2.

2. **Threshold factor `k = 0.3`**: Could be promoted to env var for tuning
   post-implementation. Initial value based on Phase 38 measurements.

3. **Linear vs mean interpolation in Component 2**: Mean of two neighbors chosen
   for robustness. If interpolation quality is poor on real channels, switch to
   weighted interpolation (e.g., `(R-i)*H[L] + (i-L)*H[R] / (R-L)`) in follow-up.

## References

- `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` — final verdict, lists
  this as future work item #3
- `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md` — Hhdr52 null quantification
- `docs/superpowers/notes/2026-06-24-phase36-t4-verdict.md` — Phase 36 per-SC CPE REFUTED
- `docs/superpowers/notes/2026-06-25-phase39-htsig-h-reestimate-verdict.md` — Phase 39 H re-est REFUTED
- `docs/superpowers/notes/2026-06-25-phase40-verdict.md` — Phase 40 splitter REFUTED
- `docs/superpowers/notes/2026-06-28-phase41-verdict.md` — Phase 41 baseline for USRP comparison
- `examples/test_htsig_viterbi_synthetic.py` — Phase 37 synthetic test (basis for Test 2)
- `examples/test_h_estimation_synthetic.py` — existing H estimation test (basis for Test 1)
- `examples/test_direct_loopback.py` — loopback regression test