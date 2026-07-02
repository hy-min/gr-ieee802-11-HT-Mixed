# HT-SIG Receiver Redesign — Per-Symbol δ Tracking

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: Design proposal — awaiting user approval before implementation
**Supersedes**: (none — additive to existing Phase 18/33/34 stack)
**Verdict reference**: `docs/superpowers/notes/2026-07-03-phase78c-null-sc-attack-verdict.md`

## Why This Document Exists

After 78 phases / 22+ REFUTED hypotheses, USRP HT-SIG viterbi failure was attributed to
**persistent per-SC phase corruption** (Phase 33b 64-PSK residual). Phase 78a confirmed
that the decoder is algorithmically capable (91% success on synthetic USRP-like channel).
Phase 78b identified USRP's structural signature: 5 stable globally-null SCs (max std_im 7.8).
Phase 78c force-zero attack REFUTED in Python pre-validation.

This document proposes a **receiver redesign** that attacks the structural root cause
identified in Phase 78b: instead of treating sub-sample timing offset δ as a per-frame
constant (Phase 34), estimate δ **per OFDM symbol** using a QBPSK-aware estimator that
does not suffer the Phase 38 ±-cancellation bug.

## Goals

**Primary**: Achieve `HT_SIG_PARSE_OK > 0` on USRP capture replay (currently 0).
**Stretch**: Achieve `FCS_OK ≥ 1` on USRP realtime loopback (currently 0 on USRP).

**Non-goals**:
- Touching the viterbi algorithm (Phase 37 confirmed correct at 6 dB SNR).
- Touching sync_long (Phase 33 fixed 14-sample shift permanently).
- Touching the splitter (Phase 40 verified timing alignment).
- Touching L-SIG path (Phase 18 FORCE + Phase 34 per-frame δ working).
- Replacing Hhdr52 with HT-SIG-pilot-based H (Phase 39 REFUTED — pilots alone too noisy).

## Investigation Summary

I traced the RX chain through `lib/frame_equalizer_impl.cc` and reviewed 78 phase
verdicts in `docs/superpowers/notes/`. Key findings:

1. **Phase 33b USRP validation** showed PERFECT 64-PSK quantization: arg(eq[k]) per
   subcarrier exhibits a linear ramp `arg ≈ -2π·k·δ/64` with δ ∈ [0,1) at 1/64 step.
   This is the wall.

2. **Phase 34** (IEEE80211_TIMING_OFFSET_APPLY=1) fixed L-SIG viterbi by applying
   per-frame constant δ estimated from H52. Works because L-SIG is 1 OFDM symbol —
   δ is effectively constant within that symbol.

3. **Phase 38** confirmed per-symbol δ drift (different δ per OFDM symbol within
   same frame), but its estimator `estimate_header_cpe_rad` returned 0 for HT-SIG
   pilots because it sums pilot phasors and the SC indices {-21,-7,+7,+21} sum to 0,
   canceling the δ factor. REFUTED as unusable, but the underlying drift is real.

4. **Phase 78b** identified 5 stable globally-null SCs at USRP @ 5250 MHz:
   indices {-15,-10,-3,-17,+8}, max std_im 7.8. These do NOT overlap with pilot SCs
   {-21,-7,+7,+21}, so pilot-based estimation is structurally safe.

5. **Phase 78a** showed 91% (273/300) Layer 4 success on synthetic USRP-like
   channel with **rotating** nulls (5-10 per frame), 3 dB SNR, 64-PSK residual.
   Decoder CAN handle the impairments — wall is not in the algorithm.

## Design

### Per-symbol δ estimator (new C++ helper)

Replace the Phase 38 estimator with a QBPSK-aware grid-search estimator:

```
input:  eq52[k] = rx_htsig[k] / H52[k] for k ∈ pilots {-21,-7,+7,+21}
        known_pilot_polarity[k] (per 802.11n §17.3.5.10, fixed for HT-SIG)

step 1 (per pilot):
  residual[k] = arg(eq52[k] · conj(known_pilot_polarity[k]))
                              = -2π·k·δ/64 + arg(noise[k])

step 2 (grid search over δ ∈ {0, 1/64, 2/64, ..., 63/64}):
  δ_hat = argmax_δ | Σ_{p∈pilots} conj(exp(+j·2π·k_p·δ/64)) ·
                         (eq52[k_p] · conj(known_pilot_polarity[k_p])) |

step 3: δ_hat ∈ [0, 1) at 1/64 quantization
```

**Why grid search** (vs continuous optimization): Phase 33b verified δ is 1/64-quantized.
64 grid points × 4 complex sums = 256 complex mults per symbol. Negligible cost.

**Why this works when Phase 38 failed**: Phase 38 summed phasors (canceling the δ
factor via SC index sum = 0). Grid search maximizes inner product with expected
phase ramp — depends on **SC index spread**, not their sum.

### Application to HT-SIG symbols

Insert into `decode_htsig_direct_from_header52` (line 2293) between equalize and
deinterleave:

```cpp
if (d_apply_htsig_per_symbol_delta) {
    float delta_a = estimate_symbol_delta(rx52_a, H52);  // HT-SIG0
    float delta_b = estimate_symbol_delta(rx52_b, H52);  // HT-SIG1
    equalize_with_delta(rx52_a, H52, delta_a, eqbits48_a);
    equalize_with_delta(rx52_b, H52, delta_b, eqbits48_b);
} else {
    // unchanged Phase 18/35 path
    equalize_header52_to_bits48(rx52_a, H52, eqbits48_a, nullptr, true);
    equalize_header52_to_bits48(rx52_b, H52, eqbits48_b, nullptr, true);
}
```

`equalize_with_delta` is a thin wrapper:
```
for k in 0..51:
    eq = safe_div(rx52[k], H52[k])
    eq *= exp(+j·2π·k·δ/64)
    eqbits48[k] = hard_bit_from_complex(eq)
```

### Application to data symbols

In `general_work` near the CFO/SFO compensation block (line ~4357), when
`d_apply_htsig_per_symbol_delta=1` AND `d_internal_symbol_counter ≥ kDataStartRel`:

```cpp
if (d_apply_htsig_per_symbol_delta) {
    float delta_i = estimate_symbol_delta(d_early_eqsym[sym_idx], H52);
    for (int k = 0; k < 52; k++) {
        float correction = 2.0f * (float)M_PI * kScIndex52[k] * delta_i / 64.0f;
        d_early_eqsym[sym_idx][k] *= std::exp(gr_complex(0.0f, +correction));
    }
} else if (d_apply_timing_offset && d_timing_offset_valid) {
    // unchanged Phase 34 path
}
```

Each data symbol's δ estimated independently from its own 4 pilots.

### Env var gating (regression-safe)

New env var: `IEEE80211_HTSIG_PER_SYMBOL_DELTA=1` (default OFF).

When OFF: bit-identical to current code. All existing tests pass.
When ON: new estimator runs for HT-SIG0/1 + data symbols.

Optional diagnostic: `IEEE80211_HTSIG_DELTA_DUMP=1` dumps δ per symbol for triage.

### Conflict resolution

```
IF IEEE80211_HTSIG_PER_SYMBOL_DELTA=1 AND counter >= 4 (HT-SIG1/data):
    USE per-symbol δ (NEW)
ELSE IF IEEE80211_TIMING_OFFSET_APPLY=1 AND counter >= 4:
    USE per-frame δ from Phase 34 (existing)
ELSE:
    USE only CFO + SFO*SC compensation (existing)
```

L-SIG path (counter=2): always uses Phase 34 per-frame δ (untouched).
L-LTF path (counter=0,1): always uses CFO + SFO only (untouched).

## Validation Strategy (3-Stage Gate)

### Stage 1: Synthetic δ sweep
**File**: `examples/test_htsig_delta_synthetic.py` (new)

```
input: synthetic channel with δ_applied ∈ {0, 1/64, 2/64, ..., 63/64}
output: redesign success rate vs baseline per δ
pass criteria: redesign ≥ 91% baseline across ALL δ values
```

Reuses Phase 78a test infrastructure. Synthetic H is ideal; redesign should not
degrade. If redesign drops below baseline, estimator has a bug.

### Stage 2: USRP capture replay
**File**: `examples/test_usrp_capture_replay_htsig.py` (new)
**Data**: `/tmp/p78b_per_frame.json` (8 frames @ 5250 MHz, Phase 78b dump)

```
input: 8 frames × 52 SCs × HT-SIG0/1 equalized bins from real USRP capture
output: redesign HT_SIG_PARSE_OK count vs baseline
pass criteria: redesign HT_SIG_PARSE_OK > 0 (baseline = 0)
```

Offline, deterministic. Confirms estimator works on real USRP impairment pattern.
If fail, compare per-symbol δ distribution with Phase 78b identified null SCs.

### Stage 3: USRP realtime
**File**: reuse `test_usrp_minimal_loopback.py`
**Env**: `IEEE80211_HTSIG_PER_SYMBOL_DELTA=1 IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_TIMING_OFFSET_APPLY=1 --freq 5890 --tx-gain 20 --rate 20`

```
input: standard USRP loopback
output: redesign FCS_OK count vs baseline
pass criteria: redesign FCS_OK ≥ 1 (baseline = 0)
```

The HARD CONSTRAINT gate: USRP realtime validation.

### Regression checks (must NOT regress)

| Test | Baseline | With env=OFF |
|---|---|---|
| `test_direct_loopback.py` (loopback 3/3) | 3/3 PASS | 3/3 PASS |
| `test_htsig_viterbi_synthetic.py` (3/3) | 3/3 PASS | 3/3 PASS |
| `test_lsig_viterbi_synthetic.py` | 3/3 PASS | 3/3 PASS |
| `test_h_estimation_synthetic.py` | 5/5 PASS | 5/5 PASS |
| L-SIG unblock on USRP (Phase 34) | works | works |

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Estimator overfits synthetic, fails USRP | Stage 2 capture replay bridges; per-symbol δ distribution diagnostic |
| Per-symbol δ drifts wildly frame-to-frame | Grid search quantizes to 1/64; sanity clip `\|δ - δ_phase34\| ≤ 5/64` |
| Conflicts with existing CFO/SFO | L-SIG path untouched; new path only kicks in for counter ≥ 4 when env=ON |
| 5 stable null SCs hit pilot SCs | Phase 78b confirmed no overlap; safe by construction |
| Loss of Phase 34 baseline | env var default OFF; OFF path = current code unchanged |
| Loopback regression subtle | test_direct_loopback 3/3 PASS check in CI |
| Stage 1 pass, Stage 2 fail | Triage: dump δ_estimated_per_symbol on USRP capture |

## Success Criteria

**Primary**: USRP capture replay HT_SIG_PARSE_OK > 0 (currently 0).
**Stretch**: USRP realtime FCS_OK ≥ 1 (currently 0).
**Floor**: All regression checks pass; env=OFF path bit-identical.

## Files Touched

- `lib/frame_equalizer_impl.cc` — ~80 lines added (helper + integration + env var init)
- `examples/test_htsig_delta_synthetic.py` — new (~150 lines, Stage 1)
- `examples/test_usrp_capture_replay_htsig.py` — new (~200 lines, Stage 2)

**No changes** to: `wifi_phy_hier.py`, `lib/sync_long.cc`, `lib/sync_short*.cc`,
`lib/ht_symbol_splitter_impl.cc`, `lib/mapper_impl.cc`, `lib/decode_mac.cc`,
`include/ieee802_11/*.h`.

## Rollback Plan

If redesign fails any stage, revert via:
```bash
git revert <commit-hash>           # undo C++ changes
rm examples/test_htsig_delta_synthetic.py
rm examples/test_usrp_capture_replay_htsig.py
```

Env var remains in code but defaults OFF — no runtime impact.
Lessons documented in new verdict doc under `docs/superpowers/notes/`.

## If Redesign Succeeds

- HT_SIG_PARSE_OK > 0 first time in 78 phases
- Opens data path: LDPC/BCC decoder for PSDU bytes
- Stage 3 FCS_OK ≥ 1 satisfies HARD CONSTRAINT for the first time
- Cascading benefit: re-evaluate downstream REFUTED hypotheses (Phase 39/43/59)
  with per-symbol δ as new baseline

## If Redesign Fails

- All evidence preserved (env vars remain OFF, regression-safe)
- Lessons documented; next iteration candidates:
  - Inter-SC phase gradient (Phase 78c-2 unbuilt, structural)
  - Iterative H refinement (turbo-style)
  - Accept closure per Phase 41 with HARD CONSTRAINT relaxation

## Open Questions

None blocking implementation. Possible future extensions after success:
- Per-symbol δ output to `mac_out` message metadata for downstream introspection
- Median filter across 3 consecutive symbols' δ estimates for noise reduction
- Replace grid search with closed-form estimator once sufficient USRP data collected

## Related

- `docs/superpowers/notes/2026-07-03-phase78c-null-sc-attack-verdict.md` (19th REFUTED)
- `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md` (5 stable nulls)
- `docs/superpowers/notes/2026-07-03-phase78a-synthetic-verdict.md` (91% baseline)
- `docs/superpowers/notes/2026-07-03-phase77-verdict.md` (equalizer ceiling)
- `docs/superpowers/notes/2026-06-23-phase34-delta-correction.md` (per-frame δ unblocks L-SIG)
- `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md` (per-symbol δ drift, REFUTED estimator)
- `docs/superpowers/notes/2026-06-23-phase33b-usrp-validation-64psk.md` (64-PSK residual)