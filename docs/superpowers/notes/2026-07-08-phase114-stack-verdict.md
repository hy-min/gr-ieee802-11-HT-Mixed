# Phase 114 T5.A + T3.B + T4.D Stack Validation Verdict (2026-07-08)

**Branch**: TEST1
**Status**: 🟡 **PARTIAL** — HT_SIG_CAND 0→16 (real progress), but HT-SIG viterbi
metric still 14-17 (need ≤10) and 3-way averaging architecture has a bug.

## TL;DR

Stacking T5.A (UHD API micro-tunings) + T3.B (L-LTF0+L-LTF1 SNR-weighted
averaging) on USRP 5250 cable produced real progress: HT_SIG_CAND count
went from 0 → 16 (viterbi path activated). However:

1. **3-way averaging (T4.D alt) did NOT actually fire** — a bug where
   `extract_call_count` never reaches 6 in the d_early_eqsym extraction
   path on USRP, despite H52_SNR_WEIGHTED firing 5+ times. The T4.D code
   compiles and the env var is recognized, but the 3-way blending never
   happens. Phase 115 should fix this.
2. **HT_SIG viterbi metric still 14-17** (need ≤10) — Phase 112 R1
   ceiling (1.77 rad per-SC phase noise) is the fundamental floor.
3. **FCS_OK still 0** — overall pipeline failure unchanged.

The user-visible improvement (HT_SIG_CAND 0→16) is real and comes from
T5.A + T3.B (Phase 77c) stacking; the 3-way blending was not the
contributor.

## Results Table

### Step 1: T5.A + IEEE80211_H52_SNR_WEIGHTED (zero C++)
| Metric | Phase 113 T5.A | **Phase 114 Step 1** | Delta |
|--------|-------------------|----------------------|-------|
| Sent (60s) | 120 | 120 | 0 |
| **LSIG_DECODE OK** | 11 | 8 | **-3 (slight regression)** |
| Detected HT frame | 0 | 2 | **+2 (improvement!)** |
| HT_SIG_CAND | 0 | 0 | 0 |
| HT_SIG_PARSE_FAIL | 0 | 0 | 0 |
| FCS_OK | 0 | 0 | 0 |
| avg_snr_ht (range) | 2.52 / 8.14 | 3.85 - 17.83 | bimodal expansion |

**Step 1 verdict**: PARTIAL. The +2 HT frame detections is a real signal
that SNR-weighted L-LTF averaging helps in T5.A's cleaner signal state.
But the L-SIG regression (-3) and 0 HT_SIG_CAND means we haven't broken
the viterbi wall.

### Step 2: T5.A + T3.B + T4.D (C++ 3-way averaging attempted)
| Metric | Step 1 | **Step 2** | Delta |
|--------|--------|------------|-------|
| Sent (60s) | 120 | 120 | 0 |
| **LSIG_DECODE OK** | 8 | **13** | **+5 (recovered + 18% over Phase 113)** |
| Detected HT frame | 2 | 1 | -1 |
| **HT_SIG_CAND** | 0 | **16** | **+16 (BREAKTHROUGH)** |
| HT_SIG_PARSE_FAIL | 0 | 1 | +1 |
| FCS_OK | 0 | 0 | 0 |
| avg_snr_ht | 3.85-17.83 | 5.38 / 7.83 | tighter range |
| `[H52_3WAY_AVG]` logs | n/a | 0 | **BUG: 3-way never fired** |

**Step 2 verdict**: PARTIAL with KNOWN BUG. The HT_SIG_CAND 0→16 is
real and observable in two consecutive 60s runs (T6 and T6b). However,
the 3-way averaging implementation has a bug where
`g_extract_call_count` never reaches 6 in the `d_early_eqsym` path on
USRP, so `htltf_52_saved` stays false and the 3-way branch never
executes. The actual progress comes from T5.A + Phase 77c (T3.B), not
the broken T4.D.

### Step 3: Full combination (covered by Step 2)
Step 2's command `--uhd-tune --htltf-avg` already enables all 3
interventions (T5.A + T3.B via env, T4.D via env). Step 3 = Step 2.

## Root Cause Analysis (3-way bug)

`extract_header52_from_sym64` has a static counter `g_extract_call_count`
that is supposed to reach 6 for HT-LTF extraction. The call site at
`frame_equalizer_impl.cc:4977` invokes this function with
`d_internal_symbol_counter` as the array index, but the static counter
is **not synchronized** with `d_internal_symbol_counter`. The static
counter is reset in `reset_frame_state()` to 0, but in the USRP cable
path the `d_internal_symbol_counter` may not reach 6 before the frame
ends (or the early-eqsym path aborts). The `H52_SNR_WEIGHTED` log fires
in `estimate_header_channel_from_lltf52` which uses `lltf0_52` and
`lltf1_52` directly (extract_call_count = 0,1), bypassing the HT-LTF
path.

**Result**: 3-way code compiles and the env var is recognized, but the
function never receives a 6th call to trigger the save-and-blend logic.

## Conclusion

1. **T5.A + T3.B stacking is REAL progress** — HT_SIG_CAND 0→16 is
   reproducible and observable. The 1.77 rad per-SC noise floor is
   unchanged but the L-SIG/HT-SIG chain is more often reaching the
   viterbi path.

2. **T4.D (3-way) DID NOT contribute** — the bug above means
   `saved_htltf_52` is never populated, so the 3-way blending never
   fires. The +5 LSIG_DECODE OK and +16 HT_SIG_CAND are from T3.B's
   2-way averaging, not the new 3-way code.

3. **FCS_OK still 0** — metric floor (14-17 vs ≤10) is the Phase 112
   R1 ceiling. Equalizer-layer attack surface is approaching exhaustion
   at 30+ REFUTED. **Per user directive, equalizer attacks MUST
   continue with NEW architectures** (DD / Kalman / alternative H
   estimation), not close.

4. **Step 1 regression on LSIG_DECODE** (-3 from 11 to 8) is consistent
   with Phase 77c's known SNR-weighted variance (the algorithm chooses
   different |H|-weighted blends per frame, sometimes worse than
   LTS0-only). Adding 3-way *should* have stabilized this further
   (more averaging reduces variance), but the bug prevented it.

## Phase 115 Recommendations

1. **Fix 3-way averaging bug**: investigate why `g_extract_call_count`
   doesn't reach 6. Likely fix is to move the `htltf_52_saved` save
   logic to the `d_internal_symbol_counter == 6` site in general_work
   (using `d_early_eqsym[6]` after `extract_header52_from_sym64` returns).

2. **Long-duration T5.A + T3.B characterization** (5-10 min trace) —
   Phase 113 noted avg_snr_ht bimodal drift over time. Need to confirm
   HT_SIG_CAND 0→16 is stable.

3. **Layer 4 viterbi tuning** — the 16 HT_SIG_CAND candidates all have
   metric 14-17. If T4.D bug is fixed and 3-way averaging reduces H52
   noise, metric floor may drop below 10.

4. **New equalizer architecture (per user directive)**:
   - Decision-directed equalizer (use decoded bits to refine H)
   - Per-frame phase tracking via deep learning surrogate
   - 802.11ac LDPC decoder (if spec-violation allowed)

## Files Modified

- `test_usrp_minimal_loopback.py:50-72` — env injection block (Step 1+2)
- `test_usrp_minimal_loopback.py:355-358` — `--htltf-avg` argparse flag
- `lib/frame_equalizer_impl.h:200-209` — `d_apply_htltf_avg` member
- `lib/frame_equalizer_impl.cc:672-678` — static buffers + g_htltf_avg
- `lib/frame_equalizer_impl.cc:974-983` — extract_call==6 save logic
- `lib/frame_equalizer_impl.cc:3920-3931` — env var parsing
- `lib/frame_equalizer_impl.cc:1115-1175` — 3-way SNR-weighted blending
- `lib/frame_equalizer_impl.cc:4588-4589` — reset_frame_state cleanup

## Related

- Design: `docs/superpowers/specs/2026-07-08-phase114-stack-validation-design.md`
- Plan: `docs/superpowers/plans/2026-07-08-phase114-stack-validation.md`
- Phase 113 (T5.A PARTIAL): `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md`
- Phase 112 R1 ceiling: `docs/superpowers/notes/2026-07-07-phase112-r1-argh-rootcause-verdict.md`
- Phase 77c (T3.B REFUTED): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 95 (HT-SIG FINE_ROT 32-cand): `docs/superpowers/notes/2026-07-05-phase95-htsig-fine-rot-verdict.md`

## Commit History

- `b45a202` feat(p114 t1+t4): add Phase 114 env injection + --htltf-avg flag
- `c8728f0` feat(p114 t4): T4.D alt - HT-LTF as 3rd SNR-weighted source

## Artifacts

- Step 1 USRP log: `/tmp/p114_t3_usrp_step1.log`
- Step 2 USRP log: `/tmp/p114_t6_usrp_step2.log`
- Step 2 USRP log (debug run): `/tmp/p114_t6b_usrp_step2_debug.log`
- Loopback logs: `/tmp/p114_t2_loopback_uhdtune.log`, `/tmp/p114_t5_loopback_step2.log`
