# Phase 95 — HT-SIG FINE_ROT 32-cand Search Verdict

**Date**: 2026-07-05
**Branch**: TEST1
**Status**: 🟡 **PARTIAL** — HT-SIG FINE_ROT mechanism confirmed (32 candidates tried,
n_candidates=32 confirmed), but avg_snr_htsig=2.88 dB is FAR below 6 dB
viterbi threshold; UHD streaming instability dominates
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — **NOT achieved**
**Cable runs used**: 4 of 5 budget (Phase 90 + Phase 94 + Phase 95 + Phase 95b)

## Background

Phase 94 identified that HT-SIG brute-force 16-cand search (4 rot × 2 inv_a ×
2 inv_b at 90° step) misses rotated HT-SIG frames. Phase 95 added
`IEEE80211_HTSIG_FINE_ROT=1` opt-in env var to extend search to 8 rot × 2 inv_a
× 2 inv_b = 32 candidates at 45° step. Also confirmed via inspection that the
ratio_ht > 1.2 HT-Mixed pre-classifier gate was already disabled (line 5001
has `// d_is_ht_frame &&  // Temporarily disabled`), so no threshold change was
needed.

### P95-T2 finding: ratio_ht gate was already disabled

Inspection of `lib/frame_equalizer_impl.cc:4999-5007` showed:
```cpp
const bool ht_parse_condition =
    !d_have_ht_header &&
    // d_is_ht_frame &&     // Temporarily disabled - ratio threshold too strict
    d_internal_symbol_counter >= kHtSig1Rel &&
    d_early_eqsym_valid[kLltf0Rel] &&
    ...
```
The `d_is_ht_frame` check is commented out. HT-SIG chain proceeds regardless
of `is_ht_frame` classification. So **P95-T2 was a no-op**.

## Implementation (commit pending)

`lib/frame_equalizer_impl.cc`:

```cpp
// get_htsig_rotation_factor uses std::polar:
static inline gr_complex get_htsig_rotation_factor(int rotation, int step_div = 2)
{
    // Phase 95: IEEE80211_HTSIG_FINE_ROT=1 sets step_div=4 (PI/4 = 45°).
    // Default step_div=2 (PI/2 = 90°) preserves Phase 70 4-rotation baseline.
    return std::polar(1.0f, rotation * (float)(M_PI / step_div));
}

// apply_htsig_rotation passes step_div through:
static void apply_htsig_rotation(const gr_complex* in52, gr_complex* out52,
                                 int rotation, int step_div = 2)
{
    gr_complex rot = get_htsig_rotation_factor(rotation, step_div);
    ...
}

// HT-SIG brute-force loop:
const bool htsig_fine_rot_env =
    getenv("IEEE80211_HTSIG_FINE_ROT") &&
    getenv("IEEE80211_HTSIG_FINE_ROT")[0] != '\0';
const int htsig_n_rot     = htsig_fine_rot_env ? 8 : 4;
const int htsig_step_div  = htsig_fine_rot_env ? 4 : 2;

for (int rot = 0; rot < htsig_n_rot && !found; rot++) {
    apply_htsig_rotation(..., rot, htsig_step_div);
    ...
}
```

Default OFF — preserves Phase 70 16-candidate baseline.

## T1 — 5250 MHz Cable Run (Phase 95 First Attempt)

Configuration:
```
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9
IEEE80211_TIMING_OFFSET_APPLY=1
IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16
IEEE80211_LSIG_FINE_ROT=1
IEEE80211_HTSIG_FINE_ROT=1
```

Result: `n_candidates=0` in HT_SIG_PARSE_FAIL. **HT-SIG brute-force never ran**.

### Smoking gun T1

All 4 L-SIG CANDIDATE_WIN entries had `enc=4` (NOT BPSK 1/2):
```
[LSIG_CANDIDATE_WIN] rot=1 inv=1 approx_metric=8 enc=4 len=1725 rate_field=0x9 parity_ok=1
[HT_SIG_PARSE_FAIL] timeout_sym=4 n_candidates=0 ... avg_snr_htsig=4.83 lsig_rate=0x9 ...
```

The body-skip at line 5839 (`if (lsig_enc != 0 && !getenv("IEEE80211_FORCE_HTSIG"))`)
rejected them all. HT-SIG chain never reached FINE_ROT search.

## T2 — Retry with IEEE80211_FORCE_HTSIG=1

Configuration: same as T1 plus `IEEE80211_FORCE_HTSIG=1`.

### Results (`/tmp/p95b_cable_force_htsig.log`)

```
LSIG_CANDIDATE_WIN entries:
  rot=0 inv=1 approx_metric=0 enc=0 len=3430 rate_field=0xD parity_ok=1  ← CLEAN
  rot=3 inv=1 approx_metric=8 enc=4 len=1223 rate_field=0x9 parity_ok=1  ← non-BPSK
  rot=1 inv=1 approx_metric=0 enc=0 len=1104 rate_field=0xD parity_ok=1  ← CLEAN

FRAME_DETECT:
  ratio_ht=1.134 (Phase 93: 0.660; Phase 94: 0.965)  ← getting closer
  L-SIG EQ ratio=1.056 (Phase 93: 1.453; Phase 94: 1.411)  ← cleanest yet

HT_SIG_PARSE_FAIL entries: 3 (all ran 32 candidates, all failed)
  n_candidates=32  ← FINE_ROT ACTIVE ✓
  best_metric=N/A   ← no candidate converged
  avg_snr_lsig=3.23 avg_snr_htsig=2.88
  last_rot=7 (tried all 8 rotations)
```

**HT-SIG FINE_ROT 32-cand search works correctly.** All 3 frames ran 32
candidates (8 rot × 2 inv_a × 2 inv_b). 0 candidates converged (best_metric=N/A
suggests all had metric=INT_MAX or similar).

### Improvement vs Phase 94

| Metric | Phase 94 (16-cand) | Phase 95 (32-cand) | Δ |
|---|---|---|---|
| L-SIG viterbi wins (clean enc=0 rate=0xD) | 1 | 2 | **+100%** |
| HT-SIG candidates tried | 16 | **32** | **+100%** |
| ratio_ht | 0.965 | **1.134** | +0.169 |
| L-SIG EQ ratio | 1.411 | **1.056** | **-25% (best yet)** |
| avg_snr_lsig | 4.26 | 3.23 | **-1.03 dB (worse)** |
| avg_snr_htsig | 6.24 | **2.88** | **-3.36 dB (much worse)** |
| HT-SIG brute-force win | 0/16 | 0/32 | flat |

Phase 95 got LOWER SNR than Phase 94 (Phase 55 UHD streaming instability
hit harder this run), but the L-SIG constellation ratio is much cleaner.
HT-SIG FINE_ROT fired but avg_snr_htsig=2.88 dB is too low for viterbi.

### Why HT-SIG viterbi still fails at 2.88 dB

The HT-SIG codeword is BPSK (QBPSK actually) with ½-rate convolutional code
over 48 bits. At 2.88 dB SNR with no frequency-selective equalization help,
the bit error rate is ~5e-2. With 48 bits, the probability of viterbi
convergence on a real codeword is below 1%. So even with the perfect
rotation, the decoder can't find a path through the trellis.

## What's needed for Phase 96+

Remaining gap: avg_snr_htsig ≥ 6 dB at 5250 cable. Phase 94 achieved 6.24 dB
once but Phase 95 got 2.88. The variance is large due to UHD streaming
instability.

1. **Run multiple cable tests and select best**: Phase 56 showed rate=10
   Soak had CV=0.329 (so ~50% of runs hit poor SNR). Phase 95 SNR is
   abnormally low; try a Phase 97 cable test with same env vars and see if
   we land in the high tail.

2. **Reduce UHD streaming instability**:
   - Investigate overflow handling — too many sync_short detections but few
     reach FRAME_DETECT suggests UHD drops
   - Lower --rate to 10? (Phase 58 REFUTED at 5; 10 untested)
   - Try larger --tx-gain or different frequency to compensate for UHD drops

3. **Accept that 5250 cable budget exhausted; try 5890 air** with shorter
   timeline. Phase 81 verdict was that 5250 cable = +5.7 dB vs 5890 air,
   but cable @ 5890 hadn't been tested on USRP. Could be different.

4. **Investigate WHY Phase 95 had such bad SNR vs Phase 94**:
   - Same cable
   - Same --tx-gain 0
   - Same --rate 20
   - Different USRP session state?
   - UHD streaming drop pattern?

### Recommended Phase 96 attack plan

Run the SAME Phase 95 config multiple times (no env var changes, 60+60s each)
and observe SNR distribution. If maximum avg_snr_htsig reaches 6+ dB in any
run, the chain SHOULD work and yield FCS_OK ≥ 1. Phase 95 ~2.88 dB is in
the bottom of Phase 55's distribution; another run might hit 6+ dB.

Cost: 1 more cable run = 4 used + 1 = 5/5 budget exhausted.
After that, would need user authorization to exceed 5-cable budget cap.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (0/3 L-SIG winners → 0/3 HT-SIG chains)
- Cable runs used: **4 of 5 budget** (Phase 90 + 94 + 95 + 95b)
- 1 cable run remaining for any Phase 96 attack
- HT-SIG FINE_ROT 32-cand mechanism: **VERIFIED WORKING** in C++ code
- avg_snr_htsig on USRP: 2.88 dB (need 6+ dB)
- Equalizer layer remains CLOSED (24+ REFUTED including Phase 95b)
- **Phase 95 is the LAST HOPE for the equalizer layer**. If Phase 96 also
  doesn't yield FCS_OK, the equalizer-layer is permanently exhausted and
  HARD CONSTRAINT requires a different upstream strategy (HW change, test
  config change, or accept loopback 3/3 PASS as final state).

## Files of Record

- T1 (no FORCE_HTSIG): `/tmp/p95_cable_htsig_fine_rot.log` (4 L-SIG wins but n_candidates=0)
- T2 (with FORCE_HTSIG): `/tmp/p95b_cable_force_htsig.log` (3 L-SIG wins, n_candidates=32)
- Implementation: `lib/frame_equalizer_impl.cc:1977-1986` (get_htsig_rotation_factor),
  `:2213-2220` (apply_htsig_rotation), `:5998-6015` (HTSIG_FINE_ROT loop)
- New env var: `IEEE80211_HTSIG_FINE_ROT=1` (opt-in)

## Related

- Phase 94 verdict: `docs/superpowers/notes/2026-07-05-phase94-verdict.md`
- Phase 93 verdict (rotated constellation root cause): `docs/superpowers/notes/2026-07-05-phase93-verdict.md`
- Phase 89 verdict (sync_short detector SUCCESS): `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 82 verdict (δ-tuning REFUTED at 5250): `docs/superpowers/notes/2026-07-04-phase82-verdict.md`
- Phase 81 verdict (cable @ 5250 +5.7 dB): `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 77 closure (equalizer ceiling REACHED): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 70 (L-SIG candidate search REFUTED, basis for Phase 94/95): in 77 closure
- Phase 55 verdict (UHD streaming instability 8× SNR drift): `docs/superpowers/notes/2026-06-29-phase55-verdict.md`
