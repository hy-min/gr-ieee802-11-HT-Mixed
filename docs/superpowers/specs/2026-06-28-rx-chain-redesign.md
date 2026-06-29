# RX Chain Architectural Redesign — HT-SIG Unblock

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: Design proposal — awaiting user approval before implementation
**Supersedes**: `2026-06-28-usrp-htsig-per-sc-null-detection.md` (Phase 42 Layer 1+2)
**Verdict reference**: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` (CLOSED — reopened for structural redesign)

## Why This Document Exists

After 41 phases / 12 REFUTED hypotheses, USRP HT-SIG viterbi failure was attributed to
**Hhdr52 channel nulls at the air interface**. Phase 42 proposed a Layer 1 (null detect +
interpolate) + Layer 2 (LLR weight) mitigation. This document **supersedes** that proposal
with a more aggressive architectural redesign: replace Zero-Forcing (ZF) equalization with
**MMSE** at the algorithmic level, and exploit a long-standing bug where the L-LTF1
channel estimate is computed but never used.

The Phase 42 design addressed the *symptom* (null SCs amplified noise). This design
addresses the *root cause* (ZF equalization itself is null-intolerant) and provides two
independent architectural improvements that compose with each other.

## Goals

**Primary**: Achieve `FCS_OK > 0` on USRP for HT-SIG frames with the standard test
configuration (Phase 18 + 33 + 34) plus new env vars.

**Secondary**: Reduce `HT_SIG_PARSE_FAIL` from 8 events / 1 frame (Phase 41 baseline)
to 0 / N frames.

**Non-goals**:
- Touching the viterbi algorithm (Phase 37 confirmed correct).
- Touching sync_long (Phase 33 fixed 14-sample shift permanently).
- Touching the splitter (Phase 40 verified timing is correct).
- Touching the CFO/SFO estimation (Phase 25 / 34 stack working).

## Investigation Summary

I traced the RX chain through `lib/frame_equalizer_impl.cc` and `lib/ht_symbol_splitter_impl.cc`.
Key findings:

1. **`safe_div` (line 96) is the ZF equalizer used everywhere.** It does
   `eq = a * conj(b) / |b|²`. When `|b| < 0.02` (channel null), noise is amplified 50×.

2. **`estimate_header_channel_from_lltf52` (line 916) has an unused parameter.**
   The function signature accepts `lltf1_52` but the comment at line 912 says
   "The current implementation builds H52 from lltf0_52 only. Call sites may pass the
   same pointer for both args." A second LTS estimate exists in the code but is
   thrown away. This is a free 3 dB SNR improvement waiting to be enabled.

3. **H52 is built at HT-SIG0 time (counter=3) from L-LTF0 (counter=0).** That's a
   12 µs gap. The channel can drift in 12 µs at USRP sample rates. Per-symbol
   re-estimation was REFUTED in Phase 39 (pilots too noisy alone), but combining
   L-LTF with pilot-based refinement may work where pure pilot-based failed.

4. **HT-SIG equalization at line 3827-3834 uses the same H52 for both HT-SIG0 and
   HT-SIG1.** HT-SIG0 pilots are at SCs {-21, -7, +7, +21} (indices 48-51). These
   could be used to *refine* H52 for HT-SIG0 specifically — not replace it (Phase 39
   failure mode), but smooth it where pilots indicate the H estimate is off.

5. **The 4-rotation search in `decode_htsig_from_rotated` already handles BPSK/QBPSK
   axis ambiguity.** So any per-SC phase rotation that stays consistent across SCs
   is acceptable. The problem is *per-SC magnitude* corruption, not per-SC phase.

## AR1-AR7 Assessment

| # | Hypothesis | Impact | Risk | Verdict |
|---|-----------|:------:|:----:|---------|
| **AR1** | Sweep L-LTF FFT window offset K ∈ [-2, +2] samples | Low | Low | **SKIP.** Phase 33b showed the residual is per-frame sub-sample δ (1/64), not integer-sample offset. Phase 31c K-sweep already REFUTED integer-sample offsets. |
| **AR2** | Investigate equalizer-level HT-SIG0/1 K-offset | Low | Low | **SKIP.** Phase 40 confirmed splitter-level offsets are zero. Equalizer-level would test the same hypothesis on a downstream stage. |
| **AR3** | Re-confirm SFO is not the cause | n/a | n/a | **SKIP.** Phase 25 quantified SFO at -0.25 ppm. Over 4 µs HT-SIG0→HT-SIG1 gap, that's 1e-9 rad. Computational confirmation adds no new information. |
| **AR4** | Replace LS H estimator with MMSE | Medium | High | **SKIP for now.** Requires accurate noise estimate (N0). 802.11n null reduction is 3-5 dB in theory, but practical MMSE with bad N0 estimate can underperform LS. Re-evaluate after AR5/AR6 results. |
| **AR5** | Replace ZF equalization with MMSE | **HIGH** | Medium | **TRY (TOP 1).** Directly addresses Phase 38 bottleneck (50× noise amplification at null SCs). MMSE weight `\|H[i]\|²/(\|H[i]\|² + N0)` caps the noise gain. This is the standard equalizer upgrade in OFDM receivers. |
| **AR6** | L-LTF1 averaging + HT-SIG pilot refinement for separate HT-SIG equalizer | **HIGH** | Low | **TRY (TOP 2).** Two clean sub-changes: (a) wire up the unused L-LTF1 averaging (free 3 dB), (b) use HT-SIG0's 4 pilots to detect/correct the 12-µs channel drift. Different from Phase 39 (which replaced H; this refines it). |
| **AR7** | Multi-frame H52 accumulation | Low | Medium | **SKIP.** USRP frames are 31 in 30s (~1/sec). Time-varying channel between frames is the entire reason HT-SIG fails — averaging across frames would mix air-interface conditions. May help if channel is static, but Phase 28 confirmed TCXO error is 0.6 ppb (very stable), so the issue is RF path, not oscillator drift. |

## Selected Architecture

Two independent architectural changes (AR5 + AR6), each behind environment variables
(default OFF), composing to provide cumulative improvement:

```
L-LTF0[52] + L-LTF1[52]   (existing, currently using L-LTF0 only)
   │
   ├─ [AR6a: LLTF_AVERAGE]   (env: IEEE80211_LLTF_AVERAGE_H52=1)
   │     H52_raw[i] = (LTF0[i] + LTF1[i]) / 2 / kLltf48TX[i]
   │     Free 3 dB SNR improvement.  Unused parameter at line 916 was designed
   │     for this — call site passes same pointer twice (line 3706-3707).
   │
   ▼
H52_v1[52]
   │
   ├─ [AR6b: HTSIG_PILOT_REFINE]   (env: IEEE80211_HTSIG_PILOT_REFINE_H52=1)
   │     At HT-SIG0 time, compute H_pilot = eq_pilot / known_pilot for 4 SCs.
   │     Trust L-LTF at non-pilot SCs, blend in pilot-based H at pilot SCs.
   │     Different from Phase 39 (REFUTED) which REPLACED H with pilots.
   │
   ▼
H52_v2[52]
   │
   ├─ [AR5: MMSE_EQUALIZE]   (env: IEEE80211_MMSE_EQUALIZE=1)
   │     eq[i] = conj(H52[i]) * rx[i] / (|H52[i]|² + N0)
   │     where N0 is estimated from H52 noise floor (e.g., median over 52 SCs)
   │     Capped noise gain: max amplification = 1/N0 (bounded) vs ZF = 1/|H|²
   │     (unbounded at nulls)
   │
   ▼
equalized HT-SIG symbols[52]
   │
   ▼
viterbi (unchanged — Phase 37 verified correct)
```

**Independence**: Each env var can be enabled alone. Composing all three is the
recommended USRP test configuration. Loopback is unaffected (no env var set by
default).

## Components

### Component 1: `average_lltf52` — L-LTF averaging (AR6a)

**File**: `lib/frame_equalizer_impl.cc` (new static helper, also fixes existing bug at line 916)

**Signature**:
```cpp
static void average_lltf52(const gr_complex* lltf0_52,
                            const gr_complex* lltf1_52,
                            gr_complex H52[52]);
```

**Algorithm**:
1. For each of 52 SCs: `H52[i] = (lltf0_52[i] + lltf1_52[i]) / 2.0 / kLltf48TX[i]`
   - For data SCs (i=0..47), divide by `kLltf48TX[i]`
   - For pilot SCs (i=48..51), divide by `kLltfPilotTX[i-48]`
2. If `|tx| < 0.001`, fall back to raw LTF value (matches existing safety check at line 933-937)

**Why safe**: 802.11n spec mandates L-LTF0 and L-LTF1 transmit the *same* sequence.
Their sum is a 2× averaging of the same channel — the standard 802.11n H estimation
algorithm (per 19.3.6 / 19.3.9). The function `estimate_header_channel_from_lltf52`
*already takes both pointers* — the existing call site at line 3706-3707 passes the
same pointer twice. The change is to call this new averaging function instead.

**Why Phase 33b's 64-PSK residual doesn't kill this**: The 64-PSK residual is a
*phase* rotation `exp(-2π·k·δ/64)`. Averaging L-LTF0 and L-LTF1 reduces the *magnitude*
noise by sqrt(2) ≈ 3 dB. Phase rotation affects both LTFs identically and is preserved.

### Component 2: `refine_h52_with_htsig_pilots` — pilot-based refinement (AR6b)

**File**: `lib/frame_equalizer_impl.cc` (new static helper, called after H52 averaged)

**Signature**:
```cpp
static void refine_h52_with_htsig_pilots(gr_complex H52[52],
                                          const gr_complex htsig0_eqsym[52]);
```

**Algorithm** (DIFFERENT from Phase 39 REFUTED approach):
1. Phase 39 REPLACED H52 with linear-interpolated pilot values (4→52 SCs).
   This failed because pilots are noisy and 4→52 interpolation overshoots at
   non-pilot SCs.
2. This new design **refines** the L-LTF-based H52 at the 4 pilot SCs only,
   using a per-SC noise-weighted blend:
   ```
   for i in [48, 49, 50, 51]:  # pilot SCs only
       H_pilot = htsig0_eqsym[i] / kHeaderPilotBase[i-48]  # or QBPSK-rotated
       # Blend: trust L-LTF H unless pilot H has high confidence
       # SNR weighting based on |H_pilot| consistency across 4 pilots
       confidence = (|H_pilot - H52[i]| < 0.5 * |H52[i]|) ? 1.0 : 0.0
       H52[i] = confidence * H_pilot + (1 - confidence) * H52[i]
   ```
3. **Non-pilot SCs are NOT touched** — the L-LTF H is the source of truth at the 48
   data SCs. Only the 4 pilot SCs get refinement.
4. **Reject outliers**: if `|H_pilot|` is wildly different from `|H52[i]|`, the
   pilot is treated as noisy and ignored.

**Why this might work when Phase 39 failed**: Phase 39 replaced 48 data SCs with
interpolated pilot values. This only touches 4 SCs and uses the pilot value
*only when it agrees with L-LTF*. The risk of "4→52 linear overshoot" is
eliminated because we never extrapolate.

**Note**: pilot SCs -21, -7, +7, +21 are at indices 48, 49, 50, 51 in the 52-SC
ordering. These are the 4 SCs where the 50× noise amplification from ZF is the
worst — even small improvements here compound.

### Component 3: `estimate_noise_floor_from_h52` — N0 estimator (AR5)

**File**: `lib/frame_equalizer_impl.cc` (new static helper)

**Signature**:
```cpp
static float estimate_noise_floor_from_h52(const gr_complex* H52);
```

**Algorithm**:
1. Compute `|H52[i]|` for all 52 SCs
2. Sort magnitudes, take the 25th percentile (lower quartile) as noise floor estimate
3. `N0 = pow(median_low_quartile, 2)` — robust to outliers (real nulls are minority)

**Rationale**: Real channel nulls (|H|≈0.02-0.14) are at the low end of the magnitude
distribution. The 25th percentile gives an estimate of the noise-only H estimation
error magnitude, which is what MMSE needs. Avoids using the minimum (sensitive to
outliers) or mean (sensitive to high-magnitude SCs).

**Tunability**: Percentile constant in code (25th); can be promoted to env var if
needed.

### Component 4: `mmse_equalize` — MMSE equalizer (AR5)

**File**: `lib/frame_equalizer_impl.cc` (new static helper, replaces `safe_div` for
HT-SIG path only)

**Signature**:
```cpp
static gr_complex mmse_equalize(const gr_complex& rx,
                                 const gr_complex& H,
                                 float N0);
```

**Algorithm**:
```cpp
float h_mag_sq = std::norm(H);  // |H|²
if (h_mag_sq < 1e-12f) {
    return gr_complex(0.0f, 0.0f);
}
return std::conj(H) * rx / (h_mag_sq + N0);
```

**vs `safe_div` (line 96-103)**:
- `safe_div`: `a * conj(b) / |b|²` — noise gain = 1/|H|², **unbounded at nulls**
- `mmse_equalize`: `conj(H) * rx / (|H|² + N0)` — noise gain = 1/(|H|² + N0),
  **bounded by 1/N0** (e.g., 0.01 = 20 dB cap)

For a null SC with |H|² = 0.0004 (|H| = 0.02):
- ZF: 1/0.0004 = 2500× noise amplification
- MMSE with N0 = 0.01: 1/(0.0004 + 0.01) = 96× — 26× less amplification

For a strong SC with |H|² = 0.25 (|H| = 0.5):
- ZF: 1/0.25 = 4× noise amplification
- MMSE: 1/(0.25 + 0.01) = 3.85× — virtually identical (N0 negligible)

**MMSE asymptotically approaches ZF at strong SCs and caps the damage at weak SCs** —
exactly the right behavior for frequency-selective channels.

## Integration Points

### AR6a: Wire up the unused L-LTF1 averaging

**File**: `lib/frame_equalizer_impl.cc` line 3706-3707 (current):
```cpp
estimate_header_channel_from_lltf52(lltf_for_H,
                                    lltf_for_H,  // arg2 is unused, pass same ptr
                                    H52);
```

**Change**: Pass `d_early_eqsym[kLltf1Rel]` for arg2 when available and env var is on.
Add new function `average_lltf52` that takes both pointers properly.

**Call site** (line 3705-3707):
```cpp
// NEW (AR6a)
if (d_lltf_average_h52) {
    const gr_complex* lltf0 = lltf_for_H;
    const gr_complex* lltf1 = d_early_eqsym_valid[kLltf1Rel]
        ? d_early_eqsym[kLltf1Rel]
        : lltf_for_H;
    average_lltf52(lltf0, lltf1, H52);
} else {
    estimate_header_channel_from_lltf52(lltf_for_H, lltf_for_H, H52);
}
```

### AR6b: Pilot refinement after L-LTF averaging

**File**: `lib/frame_equalizer_impl.cc` after H52 is computed (around line 3763, after
median filter)

```cpp
// NEW (AR6b)
if (d_htsig_pilot_refine_h52 && d_early_eqsym_valid[kHtSig0Rel]) {
    refine_h52_with_htsig_pilots(H52, d_early_eqsym[kHtSig0Rel]);
}
```

Note: `d_early_eqsym[kHtSig0Rel][i]` is the RAW (unequalized) HT-SIG0 52-SC signal.
We need to equalize first to get H_pilot, so the function takes the rx signal and
does internal equalization.

### AR5: MMSE equalization for HT-SIG only

**File**: `lib/frame_equalizer_impl.cc` line 3827-3834 (HT-SIG0 equalization):
```cpp
// CURRENT (ZF)
gr_complex eq_htsig0[52];
for (int i = 0; i < 52; i++) {
    if (std::abs(H52[i]) > 0.01f) {
        eq_htsig0[i] = d_early_eqsym[kHtSig0Rel][i] / H52[i];
    } else {
        eq_htsig0[i] = gr_complex(0.0f, 0.0f);
    }
}
```

**Change to use MMSE when env var enabled**:
```cpp
// NEW (AR5)
float N0 = 0.0f;
if (d_mmse_equalize) {
    N0 = estimate_noise_floor_from_h52(H52);
}
gr_complex eq_htsig0[52];
for (int i = 0; i < 52; i++) {
    float h_mag = std::abs(H52[i]);
    if (h_mag < 0.001f) {
        eq_htsig0[i] = gr_complex(0.0f, 0.0f);
    } else if (d_mmse_equalize) {
        eq_htsig0[i] = mmse_equalize(d_early_eqsym[kHtSig0Rel][i], H52[i], N0);
    } else {
        eq_htsig0[i] = d_early_eqsym[kHtSig0Rel][i] / H52[i];  // ZF
    }
}
```

**Same change** at line 2303-2310 (in `decode_htsig_from_rotated` for HT-SIG0) and
line 2418-2425 (for HT-SIG1). L-SIG is NOT touched (L-SIG already works with ZF).

### Environment variable wiring

**File**: `lib/frame_equalizer_impl.h` (new private fields):
```cpp
bool d_lltf_average_h52;          // IEEE80211_LLTF_AVERAGE_H52
bool d_htsig_pilot_refine_h52;    // IEEE80211_HTSIG_PILOT_REFINE_H52
bool d_mmse_equalize;             // IEEE80211_MMSE_EQUALIZE
```

**File**: `lib/frame_equalizer_impl.cc` (constructor):
```cpp
d_lltf_average_h52 = (std::getenv("IEEE80211_LLTF_AVERAGE_H52") &&
                      std::atoi(std::getenv("IEEE80211_LLTF_AVERAGE_H52")) == 1);
d_htsig_pilot_refine_h52 = (std::getenv("IEEE80211_HTSIG_PILOT_REFINE_H52") &&
                            std::atoi(std::getenv("IEEE80211_HTSIG_PILOT_REFINE_H52")) == 1);
d_mmse_equalize = (std::getenv("IEEE80211_MMSE_EQUALIZE") &&
                   std::atoi(std::getenv("IEEE80211_MMSE_EQUALIZE")) == 1);
```

## Error Handling

| Case | Behavior |
|------|----------|
| `lltf1` not available (counter=1 not yet processed) | Use `lltf0` for both args (existing behavior) |
| All 4 HT-SIG0 pilots at zero energy | Skip AR6b refinement (H52 untouched) |
| N0 estimate = 0 (all SCs are nulls) | AR5 effectively disabled (N0=0 → MMSE=ZF) |
| N0 estimate very small (strong channel) | AR5 ≈ ZF (no harm done, no help either) |
| Pilot H wildly inconsistent with L-LTF H (>5× off) | Pilot treated as noisy, ignored (AR6b outlier reject) |
| AR5+AR6 combined with AR6b producing H with imaginary-only pilots | QBPSK rotation handled by 4-rotation search in viterbi |

## Testing Strategy

### Test 1: L-LTF averaging synthetic test (NEW)

**File**: `examples/test_lltf_average_synthetic.py` (new)

**Approach**: Take the existing `test_h_estimation_synthetic.py` test cases and verify
that L-LTF0 + L-LTF1 averaging reduces H estimation noise by sqrt(2).

**Test matrix**:
| Variant | H noise σ (L-LTF) | H estimate std (LS) | H estimate std (Avg) | Expected |
|---------|------------------:|--------------------:|---------------------:|---------|
| Clean (no noise) | 0 | 0 | 0 | Equal (baseline) |
| Mild noise | 0.05 | 0.05 | 0.035 | sqrt(2) reduction |
| Heavy noise | 0.2 | 0.2 | 0.14 | sqrt(2) reduction |

**Acceptance**: averaging reduces H estimation noise variance by 1.7-2.0× on synthetic
data. Failure mode: averaging doesn't help (e.g., if L-LTF0 and L-LTF1 are correlated
in a way that doesn't reduce noise).

### Test 2: HT-SIG viterbi with null injection (NEW)

**File**: `examples/test_htsig_mmse_synthetic.py` (new)

**Approach**: Mirror Phase 37's `test_htsig_viterbi_synthetic.py` but inject channel
nulls before equalization, and compare ZF vs MMSE.

**Test matrix**:
| Variant | K nulls | N0 (dB) | Equalizer | Expected result |
|---------|--------:|--------:|-----------|-----------------|
| No nulls, ZF | 0 | 30 | ZF | PASS (Phase 37 Layer 1) |
| No nulls, MMSE | 0 | 30 | MMSE | PASS (MMSE ≈ ZF at strong SCs) |
| 5 nulls, ZF | 5 | 30 | ZF | FAIL (50× noise at nulls) |
| 5 nulls, MMSE | 5 | 30 | MMSE | PASS expected (capped gain) |
| 5 nulls, MMSE, low SNR | 5 | 6 | MMSE | PASS expected |
| 5 nulls, MMSE, all SCs nulls | 52 | 30 | MMSE | PASS (graceful — N0=0 → MMSE=ZF) |
| 5 nulls, MMSE + L-LTF average | 5 | 30 | MMSE+Avg | PASS expected (compounding) |

**Acceptance**: MMSE PASSes all "with nulls" cases that ZF fails. Loopback (no nulls)
is unaffected.

### Test 3: Loopback regression

**File**: `examples/test_direct_loopback.py`

**Test matrix**:
| Env config | Expected |
|------------|----------|
| Default (all OFF) | 3/3 PASS (unchanged) |
| `IEEE80211_LLTF_AVERAGE_H52=1` only | 3/3 PASS (loopback H is clean) |
| `IEEE80211_HTSIG_PILOT_REFINE_H52=1` only | 3/3 PASS (loopback pilots match L-LTF) |
| `IEEE80211_MMSE_EQUALIZE=1` only | 3/3 PASS (MMSE≈ZF on clean channel) |
| All 3 ON | 3/3 PASS |

### Test 4: USRP validation

**Command** (progressive env var enabling):
```bash
# Baseline (current state)
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --duration 60

# + AR6a (L-LTF average) only
... + IEEE80211_LLTF_AVERAGE_H52=1

# + AR6b (pilot refine) only
... + IEEE80211_LLTF_AVERAGE_H52=1 IEEE80211_HTSIG_PILOT_REFINE_H52=1

# + AR5 (MMSE) only
... + IEEE80211_MMSE_EQUALIZE=1

# All 3 ON
... + IEEE80211_LLTF_AVERAGE_H52=1 IEEE80211_HTSIG_PILOT_REFINE_H52=1 IEEE80211_MMSE_EQUALIZE=1
```

**Comparison vs Phase 41 baseline** (8 events / 1 frame / 30s, FCS_OK=0):

| Metric | Phase 41 baseline | AR6a only | +AR6b | +AR5 | All 3 |
|--------|------------------:|----------:|------:|-----:|------:|
| `HT_SIG_PARSE_FAIL` | 8 | _meas_ | _meas_ | _meas_ | _meas_ |
| `FCS_OK` | 0 | _meas_ | _meas_ | _meas_ | **>0** |
| `LSIG_DECODE OK` | 104 | _meas_ | _meas_ | _meas_ | _meas_ |
| `avg_snr_htsig` | 10.99 | _meas_ | _meas_ | _meas_ | _meas_ |

**Acceptance criteria**:
- ✅ `FCS_OK > 0` on USRP (primary success — at least one of the three layers must
  achieve this, with all three expected to be better)
- ✅ `LSIG_DECODE OK` unchanged or improved (no regression — L-SIG path is untouched)
- ✅ `HT_SIG_PARSE_FAIL` reduced (signal quality is improving)
- ❌ If `FCS_OK = 0` AND any layer causes regression: REFUTED, revert + verdict

## Failure Modes and Rollback

| Outcome | Action |
|---------|--------|
| AR6a alone: `FCS_OK > 0` | Commit, write verdict "Phase 45: L-LTF averaging unblocks HT-SIG" |
| AR6a+AR6b: `FCS_OK > 0` | Better — commit, write verdict |
| AR6a+AR6b+AR5: `FCS_OK > 0` | Best case — commit, write verdict |
| AR5 alone: `FCS_OK > 0` | Different — MMSE unblocks HT-SIG, write verdict |
| None of 3: `FCS_OK > 0` | Revert all 3 env vars, accept USRP HT-SIG as not solvable, document |
| Any layer: regression on L-SIG | Bug — that layer is affecting more than HT-SIG. Revert. |
| Any layer: regression on loopback | Bug — that layer is broken. Revert + verdict. |

**Rollback safety**:
- All 3 env vars default OFF → no behavior change for existing USRP / loopback runs
- All changes in `lib/frame_equalizer_impl.{h,cc}` only → localized revert
- No viterbi or H estimation algorithm changes → no risk of breaking decoder correctness
- L-LTF averaging has been "almost enabled" since the function signature was designed
  for it (line 916 comment confirms intent)

## Files Affected

| File | Change | Lines |
|------|--------|------:|
| `lib/frame_equalizer_impl.h` | 3 new private fields | ~+4 |
| `lib/frame_equalizer_impl.cc` | 4 new static helpers + 3 integration points | ~+120 |
| `examples/test_lltf_average_synthetic.py` | NEW test file | ~+150 |
| `examples/test_htsig_mmse_synthetic.py` | NEW test file | ~+250 |

**No changes to**:
- `lib/viterbi_decoder*.cc` — viterbi algorithm unchanged
- `lib/ht_symbol_splitter_impl.cc` — splitter unchanged (Phase 40 verified)
- `lib/sync_long.cc` — sync_long unchanged (Phase 33 verified)
- `lib/sync_short*.cc` — sync_short unchanged
- L-SIG equalization path — already working, don't touch

## Open Questions

1. **AR5 N0 percentile choice (25th)**: Could try 10th, 50th, etc. Initial 25th
   chosen because Phase 38 evidence shows nulls are <25% of SCs. If MMSE underperforms,
   sweep percentile.

2. **AR6b outlier threshold (5×)**: Currently set at 5× mismatch between L-LTF H and
   pilot H before ignoring pilot. Conservative. If pilot refinement is too conservative,
   lower to 2-3×.

3. **Should AR5 also apply to L-SIG?**: L-SIG already works with ZF (BPSK has 90°
   margin). MMSE on L-SIG should also work but isn't needed. Keeping it HT-SIG-only
   limits blast radius. Open question: maybe MMSE on L-SIG improves robustness to
   edge cases. Defer to follow-up.

4. **AR6a H52 noise reduction vs 50× null amplification**: L-LTF averaging gives
   sqrt(2) ≈ 3 dB SNR improvement at strong SCs but does nothing at null SCs (noise
   and signal both reduced). MMSE (AR5) is what actually caps the null-SC damage.
   The two compose: AR6a gives a clean baseline, AR5 caps the damage at residual nulls.

## Why This Is Better Than Phase 42

Phase 42's Layer 1 (null detect + interpolate) and Layer 2 (LLR weight) are workarounds
that don't change the equalization algorithm. They mask the issue by either replacing
|H| (Layer 1) or down-weighting |H| (Layer 2) — but the underlying 50× noise amplification
is still happening for all the SCs that aren't detected as nulls (false negatives).

This redesign attacks the root cause:
- **AR5 (MMSE)**: replaces the equalizer that is mathematically null-intolerant with one
  that has bounded noise gain. The problem can't happen by construction at the
  equalizer level.
- **AR6a (L-LTF averaging)**: addresses the quality of H52 itself. 3 dB free
  improvement. Bug fix — the L-LTF1 was always supposed to be used.
- **AR6b (pilot refinement)**: targeted at the specific 4 pilot SCs where the worst
  noise amplification happens. Different from Phase 39 (replacement) — this is a
  confidence-weighted blend.

Together: AR6a gives a better H52, AR6b refines it where the pilots have high SNR,
AR5 equalizes it without null amplification. Layered defense.

## References

- `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` — CLOSED verdict, 12 REFUTED
- `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md` — Phase 38 H52 null quantification
- `docs/superpowers/notes/2026-06-28-phase41-verdict.md` — Phase 41 USRP baseline (FCS_OK=0, 8 parse fails)
- `docs/superpowers/notes/2026-06-28-phase44-verdict.md` — Phase 44 soft-LLR (decoder-side, REFUTED)
- `docs/superpowers/specs/2026-06-28-usrp-htsig-per-sc-null-detection.md` — Phase 42 (superseded)
- `examples/test_htsig_viterbi_synthetic.py` — Phase 37 synthetic test (basis for Test 2)
- `examples/test_h_estimation_synthetic.py` — existing H estimation test (basis for Test 1)
- `examples/test_direct_loopback.py` — loopback regression test
- `lib/frame_equalizer_impl.cc:96` — `safe_div` (ZF equalizer)
- `lib/frame_equalizer_impl.cc:916` — `estimate_header_channel_from_lltf52` (unused lltf1_52 param)
- `lib/frame_equalizer_impl.cc:3827-3834` — HT-SIG0 equalization loop
- `lib/frame_equalizer_impl.cc:2258` — `decode_htsig_from_rotated` (HT-SIG1 equalization)
