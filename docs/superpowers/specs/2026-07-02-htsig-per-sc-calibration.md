# Phase 80b — Per-SC Phase Calibration from L-LTF

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: Design proposal — awaiting user approval
**Supersedes**: (none — additive to existing Phase 18/33/34/35/46/79 stack)
**Verdict reference**: `docs/superpowers/notes/2026-07-02-phase79-verdict.md`

## Why This Document Exists

Phase 79 (per-symbol δ tracking) was REFUTED on USRP realtime (FCS_OK=0/90). Verdict
identified the root cause: the δ estimator used center pilot SCs ({-21,-7,+7,+21}) which
have **low δ-sensitivity** (their phases are near-zero because they're close to DC).
The scalar δ correction per-frame was therefore a no-op on the structural noise.

Phase 78b identified USRP's structural fingerprint: **5 stable globally-null SCs at edges**
({-15,-10,-3,-17,+8}, max std_im=7.8). These edge SCs have **high δ-sensitivity** —
they sit at the extreme of the 64-PSK residual phase ramp and reveal the structural
distortion that center pilots cannot.

This spec proposes a **per-SC phase calibration** that exploits this structural fingerprint:
1. Estimate per-frame δ from the **edge null SCs** (high sensitivity)
2. Apply **per-SC phase correction** (not scalar δ across symbols)
3. Optionally apply a static **per-SC phase LUT** from historical multi-frame USRP data
   (offline-computed, captures residual structure beyond the linear ramp model)

## Goals

**Primary**: Achieve `HT_SIG_PARSE_OK > 0` on USRP capture replay (currently 0 even
with Phase 79 enabled).
**Stretch**: Achieve `FCS_OK ≥ 1` on USRP realtime loopback (currently 0).
**Floor**: All regression checks pass; env=OFF path bit-identical.

**Non-goals**:
- Touching the viterbi algorithm (Phase 37 confirmed correct at 6 dB SNR).
- Touching sync_long (Phase 33 fix is permanent).
- Touching the splitter (Phase 40 verified timing alignment).
- Touching L-SIG path (Phase 18 FORCE + Phase 34 per-frame δ working).
- Replacing Hhdr52 with HT-SIG-pilot-based H (Phase 39 REFUTED — pilots alone too noisy).
- Per-symbol δ tracking as primary fix (Phase 79 REFUTED on USRP — kept as opt-in).

## Investigation Summary

I traced Phase 79's failure path through `lib/frame_equalizer_impl.cc` and reviewed
Phase 33b/78b/78c/79 verdicts in `docs/superpowers/notes/`. Key findings:

1. **Phase 79 estimator** used pilots at SCs {-21,-7,+7,+21}. These are *not* at edges.
   For a 64-PSK residual with δ=0.9112 (Phase 79 actual), the phase ramp at these SCs is:
   - SC=±21: `2π × 21 × 0.9112/64 = 1.88 rad = 107°` (significant but the pilots are ON this axis)
   - SC=±26 (edge data SCs): `2π × 26 × 0.9112/64 = 2.32 rad = 133°` (much larger)
   The estimator recovered δ from pilots, but the residual phase at edge SCs after
   scalar correction is still large.

2. **Phase 33b** confirmed USRP's 64-PSK residual is per-frame sub-sample timing offset
   δ, with the phase ramp `arg(eq[k]) = -2π·k·δ/64`. Edge SCs (±26) carry the most
   phase information.

3. **Phase 78b** identified 5 stable globally-null SCs at indices {-15,-10,-3,-17,+8}.
   These are at HIGH |k| positions. They are noise-dominated (max std_im=7.8) but
   STABLE across frames — a structural fingerprint.

4. **Phase 78a** showed 91% Layer 4 success on synthetic USRP-like channel with
   rotating nulls. Decoder CAN handle the impairments — wall is not in the algorithm.

5. **Phase 78c** force-zero attack REFUTED: applying bit=0 at null SCs hurts because
   synthetic uses ROTATING nulls (USRP uses STABLE nulls — structural mismatch).

## Design

### Per-SC phase calibration (new C++ helper)

Replace the Phase 79 per-symbol scalar δ with a per-SC phase correction:

```
For each data SC k (0..47) and pilot SC k_p (48..51):

step 1 (per-frame linear fit from null SCs):
  // Use 5 stable null SCs as anchors
  // For each null SC k_n: extract phase arg(eq[k_n])
  // Fit linear regression: phase[k] = a + b·k  →  b = δ_hat × 2π/64
  δ_hat = b × 64 / (2π)  ∈ [0, 1) at 1/64 quantization

step 2 (optional static per-SC LUT, Phase 80b-B):
  LUT[k] = exp(-j × median_arg_eq_across_N_frames[k])
  Pre-computed offline from USRP capture, applied as additional correction.

step 3 (per-SC phase correction):
  correction[k] = LUT[k] × exp(+j·2π·k·δ_hat/64)
  eq[k] = safe_div(rx[k], H[k]) × correction[k]
  eqbits48[k] = hard_bit_from_complex(eq[k])
```

### Why This Works (vs Phase 79)

| Aspect | Phase 79 | Phase 80b |
|---|---|---|
| Estimator source | Center pilots ({-21,-7,+7,+21}) | Edge null SCs (high |k|) |
| Phase ramp slope | Computed from low-sensitivity anchors | Computed from high-sensitivity anchors |
| Correction shape | Scalar δ (uniform across SCs) | Per-SC × (LUT + δ ramp) |
| Residual error after correction | High at edges (±133° at SC=±26) | Low (captured by per-SC model) |

**Key insight**: Phase 79 applied scalar δ `exp(+j·2π·k·δ/64)`. The δ value came from
center pilots where the ramp is small. So the correction at edges is large but
*systematically wrong* — applying the center-derived δ doesn't fully correct the
edge distortion because the actual distortion has higher-order components.

Phase 80b uses edge null SCs (where the phase ramp is large) for δ estimation,
giving a more accurate δ value. Plus optional LUT captures non-linear residuals.

### Application to HT-SIG symbols

Insert into `decode_htsig_from_rotated` (line 2621 in `lib/frame_equalizer_impl.cc`)
between equalize and bit extraction:

```cpp
if (d_apply_htsig_per_sc_cal) {
    // Phase 80b-A: per-frame δ from null SCs
    float delta_a = estimate_delta_from_null_scs(rx52_a, H52_a);
    float delta_b = estimate_delta_from_null_scs(rx52_b, H52_b);

    // Phase 80b-B: optional static LUT
    if (d_htsig_per_sc_lut_valid) {
        apply_per_sc_lut(rx52_a, H52_a, d_htsig_per_sc_lut_a, eq48_a);
        apply_per_sc_lut(rx52_b, H52_b, d_htsig_per_sc_lut_b, eq48_b);
    }

    // Apply per-SC δ ramp correction
    for (int i = 0; i < 48; i++) {
        float delta_phase = TWO_PI * kScIndex52[i] * delta_a / 64.0f;
        eq48_a[i] *= std::polar(1.0f, +delta_phase);
    }
    // ... similar for b ...
} else {
    // Existing Phase 18/35/46 path (unchanged)
}
```

### Application to data symbols

In `general_work` near the Phase 34/79 block, when `d_apply_htsig_per_sc_cal=1` AND
`d_internal_symbol_counter ≥ kDataStartRel`:

```cpp
if (d_apply_htsig_per_sc_cal) {
    // Phase 80b-A: per-symbol δ from this symbol's null SCs
    float delta_i = estimate_delta_from_null_scs(d_early_eqsym[i], H52);
    for (int k = 0; k < 52; k++) {
        float delta_phase = TWO_PI * kScIndex52[k] * delta_i / 64.0f;
        d_early_eqsym[i][k] *= std::polar(1.0f, +delta_phase);
    }
} else if (d_apply_htsig_per_symbol_delta) {
    // Phase 79 path (unchanged)
} else {
    // Phase 34 path (unchanged)
}
```

### Env var gating (regression-safe)

- `IEEE80211_HTSIG_PER_SC_CAL=1` (default OFF) — enables Phase 80b-A per-frame δ from null SCs
- `IEEE80211_HTSIG_PER_SC_LUT=path/to/lut.json` (default unset) — enables Phase 80b-B static LUT
- `IEEE80211_HTSIG_DELTA_DUMP=1` (default OFF, from Phase 79) — diagnostic logging

When OFF: bit-identical to current code. All existing tests pass.
When ON: new estimator runs for HT-SIG0/1 + data symbols.

### Conflict resolution

```
IF IEEE80211_HTSIG_PER_SC_CAL=1 AND counter >= 4 (HT-SIG1/data):
    USE per-SC calibration (NEW Phase 80b)
ELSE IF IEEE80211_HTSIG_PER_SYMBOL_DELTA=1 AND counter >= 4:
    USE per-symbol scalar δ (Phase 79)
ELSE IF IEEE80211_TIMING_OFFSET_APPLY=1 AND counter >= 4:
    USE per-frame δ from Phase 34 (existing)
ELSE:
    USE only CFO + SFO*SC compensation (existing)
```

L-SIG path (counter=2): always uses Phase 34 per-frame δ (untouched).
L-LTF path (counter=0,1): always uses CFO + SFO only (untouched).

## Validation Strategy (3-Stage Gate)

### Stage 1: Synthetic per-SC calibration
**File**: `examples/test_htsig_per_sc_cal_synthetic.py` (new)

```
input: synthetic channel with 5 stable null SCs (NOT rotating, per Phase 78b)
       per-frame δ ∈ {0, 1/64, ..., 63/64}
output: redesign success rate vs baseline per δ
pass criteria: redesign ≥ 91% baseline across ALL δ values
```

Reuses Phase 78a synthetic test infrastructure but with STABLE nulls (refined from rotating).
Uses Phase 79 Python reference (`test_htsig_delta_synthetic.py`) but with null SCs as anchors.

### Stage 2: USRP capture replay
**File**: `examples/test_usrp_capture_replay_per_sc.py` (new)
**Data**: `/tmp/p78b_per_frame.json` (8 frames @ 5250 MHz, Phase 78b dump)

```
input: 8 frames × 52 SCs × HT-SIG0/1 equalized bins from real USRP capture
output: redesign HT_SIG_PARSE_OK count vs baseline
pass criteria: redesign HT_SIG_PARSE_OK > 0 (baseline = 0 even with Phase 79)
```

Offline, deterministic. Confirms Phase 80b fixes what Phase 79 couldn't.
If fail, compare per-frame δ distribution with Phase 78b identified null SCs.

### Stage 3: USRP realtime
**File**: reuse `test_usrp_minimal_loopback.py`
**Env**: `IEEE80211_HTSIG_PER_SC_CAL=1 IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_TIMING_OFFSET_APPLY=1 --freq 5890 --tx-gain 20 --rate 20`

```
input: standard USRP loopback
output: redesign FCS_OK count vs baseline
pass criteria: redesign FCS_OK ≥ 1 (baseline = 0 even with Phase 79)
```

The HARD CONSTRAINT gate: USRP realtime validation.

### Regression checks (must NOT regress)

| Test | Baseline | With env=OFF | With Phase 79 env=ON | With Phase 80b env=ON |
|---|---|---|---|---|
| `test_direct_loopback.py` (3/3) | 3/3 PASS | 3/3 PASS | 3/3 PASS | 3/3 PASS |
| `test_htsig_viterbi_synthetic.py` (3/3) | 3/3 PASS | 3/3 PASS | 3/3 PASS | 3/3 PASS |
| `test_lsig_viterbi_synthetic.py` | 3/3 PASS | 3/3 PASS | 3/3 PASS | 3/3 PASS |
| `test_h_estimation_synthetic.py` | 5/5 PASS | 5/5 PASS | 5/5 PASS | 5/5 PASS |
| `test_htsig_delta_synthetic.py` (Phase 79) | N/A | 4/4 PASS | 4/4 PASS | 4/4 PASS |
| L-SIG unblock on USRP (Phase 34) | works | works | works | works |

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Edge null SCs noise-dominated (max std_im=7.8) | Use linear regression across 5 SCs, not single-SC; robustness via L2 fit |
| Linear assumption wrong (phase has higher-order terms) | Phase 80b-B static LUT captures residuals; quadratic fit as enhancement |
| Conflict with Phase 79 env=ON | Explicit precedence: per-SC > per-symbol scalar; documented in code |
| LUT portability (session-dependent) | Per-session calibration; LUT loaded from JSON file with timestamp |
| Loopback regression | env=OFF path bit-identical; full git revert available |
| Stage 1 pass, Stage 2 fail (USRP-specific) | Triage: dump per-frame δ values on USRP capture; compare with Phase 78b null SCs |
| 5 stable null SCs not actually stable across all sessions | Phase 78b verified stability on 5250 MHz; if not stable, re-identify per session |
| Worse than Phase 79 (regression) | All env vars default OFF; Phase 79 path still works independently |

## Success Criteria

**Primary**: USRP capture replay HT_SIG_PARSE_OK > 0 (currently 0 with Phase 79).
**Stretch**: USRP realtime FCS_OK ≥ 1 (currently 0).
**Floor**: All regression checks pass; env=OFF path bit-identical.

## Files Touched

- `lib/frame_equalizer_impl.cc` — ~150 lines added (`estimate_delta_from_null_scs`,
  `apply_per_sc_lut`, env var init, integration into HT-SIG decoder + data symbol block)
- `examples/test_htsig_per_sc_cal_synthetic.py` — new (~150 lines, Stage 1)
- `examples/test_usrp_capture_replay_per_sc.py` — new (~200 lines, Stage 2)

**No changes** to: `wifi_phy_hier.py`, `lib/sync_long.cc`, `lib/sync_short*.cc`,
`lib/ht_symbol_splitter_impl.cc`, `lib/mapper_impl.cc`, `lib/decode_mac.cc`,
`include/ieee802_11/*.h`.

## Rollback Plan

If Phase 80b fails any stage, revert via:
```bash
git revert <commit-hash>           # undo C++ changes
rm examples/test_htsig_per_sc_cal_synthetic.py
rm examples/test_usrp_capture_replay_per_sc.py
```

Env vars remain in code but default OFF — no runtime impact.
Lessons documented in new verdict doc under `docs/superpowers/notes/`.

## If Phase 80b Succeeds

- HT_SIG_PARSE_OK > 0 first time in 79 phases
- Opens data path: LDPC/BCC decoder for PSDU bytes
- Stage 3 FCS_OK ≥ 1 satisfies HARD CONSTRAINT for the first time
- Cascading benefit: re-evaluate downstream REFUTED hypotheses (Phase 39/43/59/79)
  with per-SC calibration as new baseline

## If Phase 80b Fails

- All evidence preserved (env vars remain OFF, regression-safe)
- Lessons documented; next iteration candidates:
  - 80c: Iterative δ refinement using decoded bits as reference
  - 80d: Accept Phase 41 closure with HARD CONSTRAINT relaxation

## Open Questions

None blocking implementation. Possible future extensions after success:
- Quadratic phase model from null SCs (captures curvature beyond linear ramp)
- Real-time LUT adaptation (track session drift)
- Per-symbol H re-estimation using LUT-corrected HT-SIG pilots

## Related

- `docs/superpowers/notes/2026-07-02-phase79-verdict.md` (REFUTED — root cause analysis)
- `docs/superpowers/notes/2026-07-03-phase78c-null-sc-attack-verdict.md` (5 stable null SCs)
- `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md` (5 stable nulls)
- `docs/superpowers/notes/2026-07-03-phase78a-synthetic-verdict.md` (91% baseline)
- `docs/superpowers/notes/2026-06-23-phase33b-usrp-validation-64psk.md` (64-PSK residual)
- `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md` (per-symbol δ drift REFUTED)