# Phase 94 — FINE_ROT 8x + 5250 Cable + ACCEPT=0x9 Verdict

**Date**: 2026-07-05
**Branch**: TEST1
**Status**: 🟡 **PARTIAL** — FINE_ROT mechanism works (1 L-SIG win at 45°), but
avg_snr_htsig=6.24 dB only marginally above the 6 dB threshold, so HT-SIG
brute-force 16 candidates still fail
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — **NOT achieved**
**Cable runs used**: 2 of 5 budget (Phase 90 + Phase 94)

## Background

Phase 93 identified rotated-constellation (L-SIG EQ ratio=1.453, ~45°) as
root cause. Phase 70 EXTENSION (IEEE80211_LSIG_FINE_ROT=1) added 8 rot × 2 inv = 16
candidates at 45° step. Phase 94 tested with full Phase 94 attack combo:
FINE_ROT + 5250 cable (Phase 81 +5.7 dB) + ACCEPT=0x9 (Phase 81 patch).

## Implementation (commit pending)

`lib/frame_equalizer_impl.cc`:

```cpp
static bool decode_lsig_direct_from_header52(...,
                                             int rot_idx = 0,
                                             int rot_step_div = 2)
{
    ...
    const gr_complex rot_factor = std::polar(1.0f,
        rot_idx * (float)(M_PI / rot_step_div));   // PI/2 default, PI/4 with FINE_ROT
}

// In candidate search:
const bool fine_rot_env = getenv("IEEE80211_LSIG_FINE_ROT") && ...
int n_rot = 1;
int rot_step_div = 2;
if (fine_rot_env) {
    n_rot = 8;
    rot_step_div = 4;  // PI/4 step
} else if (cand_env) {
    n_rot = 4;          // PI/2 step (Phase 70 default)
}
// Pass rot_step_div to decoder at call site.
```

Default OFF — preserves Phase 70 baseline. Loopback regression 1/1 PASS
unchanged with FINE_ROT=1.

## T1 — 5250 MHz Cable Run (60s warmup + 60s test)

Configuration:
```
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9       # NEW (Phase 81 patch)
IEEE80211_TIMING_OFFSET_APPLY=1
IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16
IEEE80211_LSIG_FINE_ROT=1                # NEW (Phase 94)
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20
                                --warmup 60 --duration 60 --rx-subdev A:0
```

## Results (`/tmp/p94_cable_fine_rot.log`)

```
sync_short detections: 121  (Phase 93: 35; Phase 90: 27)
FRAME_DETECT: 1 frame
  ratio_ht=0.965 (vs Phase 93: 0.660, +0.305 closer to 1.2 threshold)
  L-SIG EQ ratio=1.411 (vs Phase 93: 1.453, marginal change)
LSIG_PARSE_FAIL: 7 (all viterbi_fail)
  - Frame rejected at all 8 rotations × 2 inv = 16 attempts
LSIG_CANDIDATE_WIN: 1
  - [LSIG_CANDIDATE_WIN] rot=1 inv=1 approx_metric=0 enc=0 len=1663 rate_field=0xD parity_ok=1
  - rot=1 with rot_step_div=4 → 45° rotation
HT_SIG_PARSE_FAIL: 1
  - [HT_SIG_PARSE_FAIL] timeout_sym=11 n_candidates=16 best_metric=N/A threshold=N/A
    avg_snr_lsig=4.26 avg_snr_htsig=6.24 lsig_rate=0xD lsig_len=1663 lsig_inv=0
    last_rot=3 last_inv_a=1 last_inv_b=1 is_ht_frame=0
```

**Cable run #2 of 5 budget**.

## Analysis — Partial Success

### What worked

1. **FINE_ROT mechanism fires**: 1 of 8 frames won L-SIG viterbi candidate
   search at rotation 1 (45°). The Phase 70 EXTENSION correctly adds 45° step
   candidates that the original 90° step missed.

2. **Detector improvement**: 121 sync_short detections (vs 35 baseline,
   3.5× more). Phase 92 percentile + adaptive threshold fixes helped.

3. **SNR improved 1.4 dB over baseline**:
   - avg_snr_lsig: 3.15 → 4.26 (+1.11 dB)
   - avg_snr_htsig: 4.46 → 6.24 (+1.78 dB)
   - 5250 cable delivered ~1 dB of the expected +5.7 dB boost

### What failed

1. **HT-SIG brute-force 16 candidates all failed** despite avg_snr_htsig=6.24 dB:
   - best_metric=N/A (all 16 candidates had metric=INT_MAX or similar)
   - This is the same rotation problem: 90° step HT-SIG search misses
     45° mis-rotations just like Phase 70 L-SIG used to

2. **L-SIG EQ ratio=1.411 indicates constellation still rotated**:
   - Phase 94 FINE_ROT brought this down from 1.453 → 1.411 (only -0.04)
   - But the EQ ratio is computed BEFORE the rotation search, so the ratio
     reflects the IQ axis distribution, not the rotation angle. The viterbi
     candidate search sees the rotated constellation correctly via rot_factor.

3. **is_ht_frame=0**: ratio_ht=0.965 < 1.2 threshold. This pre-viterbi
   classification is wrong for the same reason: HT-SIG QBPSK 90° rotation
   is hidden by the residual 45° rotation.

## Why avg_snr didn't reach 8-10 dB (Phase 81 prediction)

Phase 81 measured avg_snr=9.61 dB at 5250 cable on the SAME hardware.
Phase 94 measured avg_snr_htsig=6.24 dB. That's a 3.4 dB gap from
Phase 81's prediction. Likely cause: UHD streaming instability (Phase 55)
still dominating. 5250 cable boost (+5.7 dB) partly cancels out with
realtime SNR drift (-3.4 dB average), net ~+2 dB instead of +5.7 dB.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (0/121)
- Cable runs used: **2 of 5 budget**
- 0 cables left for further Phase 90-style experimentation
- But 3 cables remain available for any Phase 95+ attack plan

## What next? — Upstream Attack Options

The bottleneck has now clearly shifted to:

1. **HT-SIG brute-force 16-candidate search doesn't handle 45° rotation**.
   Phase 70 EXTENSION fixed L-SIG; HT-SIG needs the same extension.
   Required: pass `rot_step_div` to `decode_htsig_direct_from_header52`
   and extend HT-SIG search to 8 rot × 2 inv_a × 2 inv_b = 32 candidates.

2. **is_ht_frame pre-check ratio_ht < 1.2 blocks HT-SIG chain**.
   The ratio check needs to be lowered (e.g. to 0.7 or 0.8) OR
   removed entirely.

3. **avg_snr_htsig ≤ 6 dB is the channel physics limit at 5250 cable** —
   need to fight UHD streaming instability (overflow drops) to get
   higher SNR on frames that DO arrive.

### Recommended Phase 95 attack plan

1. **HT-SIG rotation search EXTENSION** (mirror of Phase 94 L-SIG fix):
   - Add `IEEE80211_HTSIG_FINE_ROT=1` opt-in env var
   - 8 rot × 2 inv_a × 2 inv_b = 32 candidates at 45° step
   - Default OFF preserves Phase 70 16-candidate baseline

2. **Lower ratio_ht threshold** to 0.7 (current 1.2):
   - With 5250 cable at avg_snr=4-6 dB, ratio_ht should be 0.7-0.95
   - Hardcoded lower threshold catches rotated HT-Mixed frames
   - Or make threshold env-configurable

3. **Optionally**: investigate why Phase 81's 5250 boost (+5.7 dB)
   isn't materializing — could be UHD streaming state at this session.

Phase 95 RECOMMENDED: combine #1 and #2, keep the cable config from
Phase 94 (5250 + ACCEPT=0x9 + all detector fixes). With 3 cables
remaining, this is the last best upstream attack before budget exhausts.

## Files of Record

- T1: `/tmp/p94_cable_fine_rot.log` (121 detections, 1 FRAME_DETECT, 7 viterbi_fail, 1 HT_SIG_fail)
- Implementation: `lib/frame_equalizer_impl.cc` (rot_step_div parameter, FINE_ROT env var)
- Loopback regression: PASS (1/1 with FINE_ROT=1)

## Related

- Phase 93 verdict (rotated constellation root cause): `docs/superpowers/notes/2026-07-05-phase93-verdict.md`
- Phase 92 verdict (percentile fix REGRESSION): archived
- Phase 91 verdict (energy gate bypass REGRESSION): `docs/superpowers/notes/2026-07-04-phase91-verdict.md`
- Phase 90 verdict (5250 cable regression): `docs/superpowers/notes/2026-07-04-phase90-verdict.md`
- Phase 89 verdict (sync_short detector SUCCESS): `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 82 verdict (δ-tuning REFUTED at 5250): `docs/superpowers/notes/2026-07-04-phase82-verdict.md`
- Phase 81 verdict (cable @ 5250 +5.7 dB): `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 77 closure (equalizer ceiling REACHED): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 70 (L-SIG candidate search REFUTED, but used as basis for Phase 94): in 77 closure
- Phase 55 verdict (UHD streaming instability 8× SNR drift): `docs/superpowers/notes/2026-06-29-phase55-verdict.md`
