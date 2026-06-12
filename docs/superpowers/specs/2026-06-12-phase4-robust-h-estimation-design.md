# Phase 4: Single-Frame Robust H Estimation via Median Filter

**Date:** 2026-06-12
**Branch:** TEST1
**Status:** DESIGN — awaiting user review
**Supersedes:** N/A
**Follows:** Phase 3 Stage 1 verdict (STAGE_AMBIGUOUS, L-LTF0 FFT corruption at frame_eq input)

## 1. Purpose

Unblock the USRP RX pipeline's end-to-end success criterion (B criterion:
Recv≥1) by making the H52 channel estimate tolerant of the per-frame, per-SC
magnitude corruption observed in the L-LTF0 FFT at the `frame_equalizer_impl`
entry point.

**Scope:** Single-frame, zero added latency, opt-in via env flag. Not a
generalizable fix; not a fix for the underlying RF-chain corruption.

**Out of scope:**
- Multi-frame H averaging (would add ~30-50 ms latency; explicitly excluded
  by user).
- Robust estimation of the underlying RF-chain corruption source.
- Modifications to CFO/SFO compensation, kFftNormalize handling, or the
  `compute_H52_tx_order` (data path) function.

## 2. Background

### 2.1 Corruption signature (Phase 3 Stage 1)

USRP A:0 single-board TDD, 5.18 GHz, 20 MHz, 60s run (n=47):

| Metric | USRP | Loopback |
|--------|------|----------|
| per-frame std_avg of `|LLTF|` (52 SCs) | 12.7 | 0.0 |
| per-SC range (max - min mean) | 13.6× | 1.0× |
| per-SC std (avg) | 5.0 | 0.0 |
| Adjacent-SC swing | up to 5× | smooth |

Pattern: "every other SC is high" (e.g., SC 0=3.5, SC 2=21.9, SC 5=20+) —
consistent with frequency-selective multipath null.

Phase 3 fix-experiments confirmed:
- FFT window timing (offset ±1..3 samples): NOT the cause
- Hardware gain (rx 5-25, tx 15-30): NOT the cause
- Corruption is **structural** (std/mean ratio constant)

### 2.2 Why median filter

- L-LTF0 corruption is per-frame but the channel itself is slowly varying
  (frequency-selective, smooth across SCs).
- A 3-tap median filter across SCs:
  - Removes single-SC spikes (typical "stuck at high value" corruption)
  - Smooths 2-SC periodicities (the "every other SC is high" pattern)
  - Preserves the underlying channel shape (nonlinear, edge-preserving)
- Compared to mean filter: median is robust to outliers up to floor(N/2)
  corrupted neighbors.
- Compared to multi-frame averaging: zero added latency, no frame buffer.

## 3. Architecture

### 3.1 Modified function

`lib/frame_equalizer_impl.cc:632`
`estimate_header_channel_from_lltf52(const gr_complex* lltf0_52,
                                    const gr_complex* lltf1_52,
                                    gr_complex* H52)`

Insert median filter as a final step before returning. The function is called
twice in `general_work` (lines 2421, 2728); both call sites benefit
automatically.

### 3.2 Data flow (after change)

```
saved_ltf0_fft[64]  (already corrupted at frame_eq entry)
       │
       ▼
extract_header52_from_sym64(sym64, lltf0_52[52])   // existing
       │
       ▼
estimate_header_channel_from_lltf52(lltf0_52, ..., H52_raw[52])   // existing
       │  // for i in 0..47: H52_raw[i] = lltf0[i] / kLltf48TX[i]
       │  // for i in 0..3:  H52_raw[48+i] = lltf0[48+i] / kLltfPilotTX[i]
       ▼
apply_h_median_filter(H52_raw, H52_out[52], window=3)   // NEW, opt-in
       │  // sort key: |H[i]|
       │  // boundary handling: window=2 at i=0,51
       ▼
H52_out (returned)  ──► L-SIG/HT-SIG equalization (existing)
```

### 3.3 Out-of-band

The 4 edge subcarriers (SC -28, -27, +27, +28) are filled by
`compute_H52_tx_order` from `saved_htltf_edge` (HT-LTF, not L-LTF0) for the
data path. They are NOT passed through `estimate_header_channel_from_lltf52`
and are therefore NOT affected by this filter. This is correct — those SCs
are not the corrupted source.

## 4. Algorithm

### 4.1 Median filter over complex values

**Sort key:** `|H[i]|` (real-valued magnitude).
**Selection rule:** Return the complex value whose magnitude is the median.

This is the standard 1-D complex median definition used in image processing
for complex-valued signals. Phase is preserved (not lost) — we pick the
phase of the median-magnitude sample, which is the least corrupted one by
construction (neighbors are presumably more corrupted if their magnitudes
are outliers).

### 4.2 Window rules (length=52)

For each i ∈ {0, 1, ..., 51}:

| i | Window | Output |
|---|--------|--------|
| 0 | {H[0], H[1]} | complex value at median of {|H[0]|, |H[1]|} |
| 1..50 | {H[i-1], H[i], H[i+1]} | complex value at median |
| 51 | {H[50], H[51]} | complex value at median of {|H[50]|, |H[51]|} |

### 4.3 No pilot/data SC separation

All 52 SCs (indices 0..47 data, 48..51 pilots) are filtered uniformly. The
median filter does not require same-magnitude prior; it only requires that
outliers are sparser than window/2. Pilots in L-LTF are also ±1 (real),
so they have similar magnitude profile to data SCs in clean conditions.

### 4.4 Complexity

- O(N) per frame, N=52, with 3 comparisons per SC.
- 3-tap median requires at most 3 `std::abs` + 2-3 comparisons per SC.
- Negligible CPU cost. Zero added latency (in-place filter, single pass).

## 5. Opt-in Control

### 5.1 Env var

`IEEE80211_H_MEDIAN_FILTER=1` — apply the median filter.

**Default:** OFF. Behavior identical to current commit (byte-level) when unset.
This preserves the software loopback 9/9 baseline as the regression ground
truth.

### 5.2 Plumbing

- `lib/frame_equalizer_impl.h`: add `bool d_h_median_filter;` member
- `lib/frame_equalizer_impl.cc`:
  - Constructor: `d_h_median_filter = std::getenv("IEEE80211_H_MEDIAN_FILTER") && ...`
  - Helper function `apply_h_median_filter(const gr_complex* in, gr_complex* out, int n)`
    implementing the algorithm in §4.
  - In `estimate_header_channel_from_lltf52`, after the existing H52 fill loops,
    if `d_h_median_filter` is true, call `apply_h_median_filter(H52, H52, 52)`
    (in-place is fine; output array is the same as input array).

### 5.3 Style consistency

The env-var flag pattern matches existing infrastructure:
- `IEEE80211_H52_DUMP=1` (commit 33df3f9)
- `IEEE80211_LTF0_FFT_DUMP=1` (commit 28066d7)
- `IEEE80211_PHASE_RESIDUAL=1` (commit dea4805)
- `IEEE80211_FRAME_START_OFFSET` (commit b8e0e34, uncommitted)

## 6. Testing

### 6.1 Test matrix

| # | Test | Purpose | Pass criterion |
|---|------|---------|----------------|
| 1 | Synthetic: noisy LTF0 + Gaussian | Verify algorithm reduces noise | per-frame std reduced ≥3× |
| 2 | Loopback 9/9 (filter OFF) | No-regression baseline | 9/9 (existing) |
| 3 | Loopback 9/9 (filter ON) | No-regression with filter | 9/9 |
| 4 | USRP 30s (filter OFF) | Reference baseline | Recv=0 (existing) |
| 5 | USRP 30s (filter ON) | **B criterion** | **Recv≥1** |
| 6 | USRP 60s (filter ON) | Stability | ≥1 / 60s |

### 6.2 New diagnostic dump

Add `[H52_DUMP_FILTERED]` log at the median-filter output (when filter is ON),
mirroring the existing `[H52_DUMP]` format. Use atomic `snprintf+USRP_LOG`
pattern (per `e90e3f5`). This enables pre/post comparison in
`test_h52_compare.py`.

### 6.3 Acceptance criteria

- **Success (B criterion met):** USRP 30s run, filter ON, Recv ≥ 1.
- **Acceptable side effect:** H52 per-SC std reduced by ≥ 2× vs pre-filter,
  even if Recv doesn't reach 1 (indicates algorithm is working; the
  remaining blocker is elsewhere).
- **Failure:** USRP 30s run, filter ON, per-frame std change < 30%.
  → Re-design (consider Stage 2: cross-LTF averaging).

## 7. Failure Modes & Exit

### 7.1 Why it might not unblock

- **LTF0 corruption exceeds algorithm capacity.** If >50% of the 52 SCs are
  corrupted in a single frame, even median cannot rescue it. (Phase 3 data:
  per-frame std 12.7 with mean ~10 — likely 30-50% of SCs are corrupted
  badly, which is borderline for 3-tap median.)
- **Phase corruption is the dominant failure mode.** Median filter preserves
  the phase of the median-magnitude sample. If phase corruption is
  independent of magnitude, this does not help. (Phase 1a showed phase
  random on unit circle, so this is possible.)
- **LTF0 corruption is mostly additive (Gaussian) noise.** Median filter
  reduces spike noise but is not optimal for Gaussian. In that case, the
  per-frame std will be reduced but the equalization may still fail.

### 7.2 Stop conditions

Stop the Phase 4 effort if any of:
1. **B criterion met (Recv≥1):** record success, write memory, done.
2. **No measurable improvement:** per-frame std change < 30% after filter
   on USRP data. Algorithm ineffective; need Stage 2.
3. **Software loopback regression:** 9/9 → <9/9. Immediate revert.

### 7.3 Stage 2 candidates (only if Stage 1 fails)

- Per-frame outlier rejection (Winsorize) + median
- Frequency-domain H interpolation (replace outlier SCs with linear interp
  from neighbors)
- Cross-LTF0/LTF1 H consistency check (require |H_LTF0[k] - H_LTF1[k]| < ε
  for all k; else fall back to a smoothed value)

These are not in Phase 4 Stage 1 scope; they would be a new spec.

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Median filter changes the math for valid (clean) USRP frames | low | Opt-in env var; loopback regression test |
| Edge SC handling wrong (window=2 vs window=3) | low | Test synthetic + loopback |
| Pilot SCs (48-51) confuse the filter | low | Pilots ±1 in L-LTF, similar magnitude to data |
| Filter is effectively a no-op on USRP corruption | medium | Per-frame std metric; stop if <30% change |
| Software loopback regression (subtle H change in 1 frame) | low | Run 9/9 with filter ON before USRP |

## 9. Out-of-Scope Reminders

Per project memory, the following are **NOT** to be re-attempted:
- ❌ CFO/SFO residual phase rotation compensation
- ❌ L-SIG CPE compensation
- ❌ L-LTF1 swap for H estimation (existing flag kept OFF by default)
- ❌ kFftNormalize / eq_lsig division
- ❌ FFT window timing shifts (offset=0 confirmed optimal)
- ❌ Hardware gain tuning (std/mean constant)

## 10. Artifacts

- Spec: this file (`docs/superpowers/specs/2026-06-12-phase4-robust-h-estimation-design.md`)
- Plan: `docs/superpowers/plans/2026-06-12-phase4-robust-h-estimation.md` (TBD)
- Code: `lib/frame_equalizer_impl.{h,cc}` (modified)
- Test: `examples/test_h_median_filter_synthetic.py` (new, for test 1)
- USRP logs: `/tmp/usrp_run_phase4_*.log`
- Decision note: `docs/superpowers/notes/2026-06-12-phase4-verdict.md` (TBD)

## 11. Commits (planned)

| Commit | Content |
|--------|---------|
| (TBD) | feat(frame_eq): add IEEE80211_H_MEDIAN_FILTER opt-in median filter |
| (TBD) | test: add synthetic noisy LTF0 test for median filter |
| (TBD) | feat(frame_eq): add [H52_DUMP_FILTERED] diagnostic |
| (TBD) | notes: Phase 4 verdict — B criterion check |
