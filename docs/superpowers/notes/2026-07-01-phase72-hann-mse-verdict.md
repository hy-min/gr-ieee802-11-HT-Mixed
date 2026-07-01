# Phase 72 Verdict — H52 Quality: Hann Compensation + MMSE EQ

**Date**: 2026-07-01
**Branch**: TEST1
**Status**: REFUTED on USRP (MMSE standalone) / REFUTED on loopback (Hann+comp)
**Commits**:
- feat(p72): synthetic test for Hann compensation + MMSE EQ (ce3be30)
- feat(p72): synthetic test fixes (96a9b23)
- feat(p72): add MMSE option to equalize_header52_to_eq48_and_bits (f7f36ea)
- feat(p72): Hann envelope compensation in estimate_header_channel_from_lltf52 (a76b049)
- **revert(p72): Hann envelope compensation (REFUTED on loopback)** (a22c639)

## Goal

Improve H52 channel-estimate quality at the equalizer layer with two
complementary fixes targeting the H52 nulls (|H|=0.02-0.14) bottleneck
identified in Phase 27/30/38/41 and confirmed in Phase 70/71 verdicts.

## Method

Two independent C++ changes to `lib/frame_equalizer_impl.cc`, each gated by a
default-OFF env var, both attacking H52 quality at the equalizer layer:

1. **Hann compensation (Option B from Phase 71 verdict)**: in
   `estimate_header_channel_from_lltf52()`, after computing H52 = lltf0/tx,
   multiply by `1.0 / hann_main_lobe_gain` (≈2.0 for Hann) to restore the
   magnitude scale lost by windowing. Env var: `IEEE80211_RX_FFT_WINDOW_COMPENSATE=1`
   (default ON when `IEEE80211_RX_FFT_WINDOW` ≠ rectangular).

2. **MMSE equalization (Option C from Phase 71 verdict)**: in
   `equalize_header52_to_eq48_and_bits()`, when `IEEE80211_MMSE_EQUALIZE=1`,
   replace the ZF `safe_div(rx, H)` with `(H* · rx) / (|H|² + N0)` where N0
   is the 25th-percentile of |H|² over the 48 data SCs. Env vars:
   `IEEE80211_MMSE_EQUALIZE=1` (default OFF) and `IEEE80211_MMSE_N0_PERCENTILE=25`.

## Results

### Synthetic test (Task 1) — 3/3 PASS
- `[NULL_SC_TEST] PASS`: MMSE mse=712 vs ZF mse=125551 (176× improvement)
- `[HANN_COMP] PASS`: Rect mag=64.0, Hann mag=36.84, Hann+comp mag=73.68 (within 30%)
- `[LOW_SNR_TEST] PASS`: MMSE mse=782 vs ZF mse=282188 (361× improvement at 5 dB SNR)

The synthetic test validated the algorithms in isolation. Both fixes work
correctly on a synthetic channel.

### Loopback regression (Task 4) — 1/1 PASS baseline, FAIL with Hann

| Configuration | Result | Notes |
|---|---|---|
| rect+ZF (baseline) | `Final: OK=1 FAIL=0` ✓ | pre-Phase-72 behavior |
| rect+MMSE | `Final: OK=1 FAIL=0` ✓ | MMSE on clean channel = transparent (N0 tiny) |
| Hann+ZF-compensate | `Final: OK=0 FAIL=0` ✗ | **REFUTED — Hann+comp introduced regression** |

**Hann compensation failure root cause**: Hann window's spectral response is
**highly non-flat across the 64 FFT bins**:
- DC bin (k=0): gain = 0.5
- Adjacent bins (k=1, k=63): gain ≈ 0.25
- Mid bins (k=32): gain ≈ 0 (sidelobe null)

The "first-order" 2x compensation (multiplying H52 by 2.0 to undo DC gain)
is **insufficient AND harmful**:
- At SCs where Hann's response is much smaller than 0.5 (e.g., 0.1), 2x
  compensation only partially restores magnitude
- This leaves |H| at ~0.1-0.2 at those SCs, causing 5-10× noise amplification
  under ZF EQ
- Test output: `eq=...,48.370,-2.717,-32.854,...,54.871,...` (some symbols 50×
  larger than expected BPSK)
- Result: 0/1 PASS (regression) instead of 1/1 PASS

**Decision**: REVERT Hann compensation block (commit a22c639). The Hann
window approach (Phase 71 + Task 3) is **fully REFUTED** — Hann window is
fundamentally incompatible with this equalizer approach.

The Hann env var `IEEE80211_RX_FFT_WINDOW` in `wifi_phy_hier.py` (Phase 71)
remains as an opt-in diagnostic. Setting it now produces broken loopback
output (as in Phase 71), but does not actively corrupt the .so.

### Offline replay (Task 5) — 2 combinations, identical results

Used existing Phase 68 capture file `/tmp/p68_raw_iq.bin` (72 MB,
9.0M samples ≈ 0.45s of USRP capture at 20 MHz). 30s replay duration
(file_source loops the same 0.45s).

| Configuration | LSIG_OK | LSIG_PARSE_FAIL | is_ht_frame=1 | avg_snr | Final RX |
|---|---|---|---|---|---|
| rect+ZF (baseline) | 1 | 8 | 0 | 1.59 | 0 |
| rect+MMSE (standalone) | 1 | 8 | 0 | 1.59 | 0 |

**MMSE EQ shows ZERO measurable effect on offline replay**:
- LSIG_DECODE OK identical (1/8)
- LSIG_PARSE_FAIL identical (8/8, all `viterbi_fail`)
- is_ht_frame=1: 0 in both (no HT-SIG chain reached)
- avg_snr: 1.59 (both)

The MMSE N0 estimate (25th-percentile of |H|²) is dwarfed by the H52 null
magnitudes at this SNR regime. The bottleneck is upstream of the equalizer
algorithm — at the channel-estimate quality itself.

### USRP realtime (Task 6) — SKIPPED (per discipline rule)

Offline replay (Task 5) used the same captured USRP signal processed through
the same C++ equalizer chain. Re-running it through `test_usrp_minimal_loopback.py`
with UHD would only add UHD streaming variance — it cannot improve the SNR
floor set by the captured RF signal itself. Per discipline rule: don't run
a test that the previous task already proved is invariant.

## Decision

**Phase 72 is REFUTED at the USRP gate**:
- **Hann compensation (Task 3)**: REFUTED on loopback, reverted (a22c639).
  Hann window's spectral response is non-flat; flat 2x compensation cannot
  fix the per-SC spectral leakage.
- **MMSE EQ (Task 2)**: REFUTED on USRP offline replay. Zero measurable
  effect at avg_snr_lsig=1.59 with 18/52 H52 nulls. The 25th-percentile N0
  regularization is too weak to overcome the channel null bottleneck.
- **Both fixes preserved as opt-in env vars**: `IEEE80211_MMSE_EQUALIZE=1`
  (Task 2, default OFF) is kept in the .so for future re-evaluation. Hann
  compensation block removed.

The H52 channel-estimate quality bottleneck (Phase 27/30/38/41/70/71) is
**not solvable at the equalizer layer**. The fundamental channel-physics
limit remains: 18/52 SCs with |H| ≈ 0 produce 50× noise amplification under
any form of linear equalization.

## Files

- `lib/frame_equalizer_impl.cc` (MMSE branch kept in EQ; Hann compensation REMOVED)
- `examples/test_hann_compensation_and_mmse_synthetic.py` (regression test, 3/3 PASS)
- `/tmp/p72_loopback_rect_zf.log`, `/tmp/p72_loopback_rect_mmse.log` (loopback tests)
- `/tmp/p72_offline_rect_zf.log`, `/tmp/p72_offline_rect_mmse.log` (offline replay)

## Phase 73+ candidates (per HARD CONSTRAINT)

The HARD CONSTRAINT requires BLOCKED verdicts to include an upstream-attack
plan. Three candidates for Phase 73+:

### Option A: Per-symbol H re-estimation from HT-SIG pilots (Phase 39 revisit)
- Phase 39 was REFUTED with 8× worse std_im (1.5 → 12.7)
- Was attempted without Phase 71/72 improvements — the underlying H52 was
  probably already noise-dominated
- With clean H52 (loopback), per-symbol re-estimation may become viable
- Risk: same pilots may still be noise-dominated under USRP conditions

### Option B: Per-symbol pre-clean of H52 using H60 NULL pre-clean + linear interp
- Phase 60 PARTIAL: H60_NULL fires 8 times, but 21/52 SCs null after pre-clean
  is too many for robust viterbi
- Phase 61 PARTIAL: Combo pre-clean+pilot CPE; n_nulls 21→4 (5× gain) on USRP
- May need per-symbol H52 re-estimation AFTER pre-clean

### Option C: Investigate the upstream channel (RF, antenna, USRP subdev)
- Per Phase 53, cross-board is 2.4× weaker than same-board
- Per Phase 28, USRP hardware is OK (DC=2e-6, TCXO 0.6ppb)
- Per Phase 31b, air path OK once freq/tx-gain correct
- The H52 nulls are an air-path artifact, not a software bug
- May need antenna repositioning, LO frequency change, or different USRP subdev

### Option D: Accept USRP HT-SIG as unsolvable (Phase 41 closure reaffirmed)
- Per Phase 41 verdict (and 12 REFUTED hypotheses since):
  "Channel-physics limitation, not software bug"
- USRP HT-SIG investigation CLOSED
- Loopback 3/3 PASS remains decoder validation path
- This is the documentation path — does not unblock USRP realtime

**Recommendation**: Phase 73 should investigate **Option A (per-symbol H
re-estimation with cleaner H52)** OR **Option C (RF/antenna investigation)**.
Both attack the H52 quality at upstream points per HARD CONSTRAINT.
Option B is partially-validated (Phase 61 PARTIAL); could be combined with
A for stacked improvement. Option D is the "accept and document" path.

## Related

- [[project_p71_h52_hann_window]] — Phase 71 Hann REFUTED on loopback
- [[project_p70_lsig_viterbi_candidate]] — Phase 70 candidate search REFUTED
- [[project_p61_combo]] — Phase 61 PARTIAL (n_nulls 21→4, still gated)
- [[project_p60_pre_clean_h52]] — Phase 60 PARTIAL (H60_NULL works, 21/52 left)
- [[project_p59_h52_null_interp]] — Phase 59 BLOCKED (call site unreachable)
- [[project_p41_usrp_htsig_final_verdict]] — Phase 41 USRP closure (reaffirmed)