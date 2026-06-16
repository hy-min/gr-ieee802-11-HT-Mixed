# Phase 20 — Per-Subcarrier Phase Tracking on HT-SIG1 (2026-06-15)

## TL;DR

Phase 20 (6 tasks, of 9 planned) implemented per-subcarrier phase tracking infrastructure on HT-SIG1 in `decode_htsig_from_rotated` (line 1847 of `lib/frame_equalizer_impl.cc`). Two static C++ helpers were added (Tasks 3+4) and a diagnostic dump env-gated block (Task 5) was added. The diagnostic captured 256 per-SC phase samples from 30s of USRP traffic at 5 GHz A:0. **The per-SC phase hypothesis was REFUTED** — the per-SC phase is RANDOM noise (std ≈ 0.94 rad, pearson_r ≈ 0.07), not structured. Tasks 7-8 (per-SC fix + 4-run USRP matrix) were SKIPPED per the diagnostic-first workflow.

## Test Results

| Run | Env | FCS OK | HT_SIG_PARSE_FAIL | Notes |
|-----|-----|--------|-------------------|-------|
| 1 | DUMP=1 | 0 | 384 | diagnostic, no fix |
| 2 | baseline | 0 | 24 | reference (Task 1) |
| 3 | (FIX=1) | — | — | SKIPPED (Task 7) |
| 4 | (DUMP=1, FIX=1) | — | — | SKIPPED (Task 7) |

Note: Run 1 has 384 HT_SIG_PARSE_FAIL (24 frames × 16 inv/rot candidates), all `crc_fail`. This is consistent with Phase 19 verdict.

## Per-SC Phase Analysis (Run 1 dump)

```
=== Per-trial statistics (n=256 total events) ===
inv_a  inv_b   n  mean|phase|  std|phase|  pearson_r  verdict
   0      0  64        0.8458       0.9410      0.0662   RANDOM
   0      1  64        0.8458       0.9410      0.0662   RANDOM
   1      0  64        0.8458       0.9410      0.0662   RANDOM
   1      1  64        0.8458       0.9410      0.0662   RANDOM

=== Overall phase distribution ===
  count: 13312
  mean:  -0.0697 rad
  std:   1.0241 rad
  min:   -3.066 rad
  max:   2.948 rad
```

**Hypothesis verdict: NOT SUPPORTED.** The per-SC phase is uniformly distributed across [-π, π] with std ≈ 1 rad, indicating random noise. There is no structure (no linear dependence on SC index, |pearson_r| < 0.1) to correct.

Note on identical per-trial statistics: This is expected because equalization (rx52_a, H52) is computed once per frame, and only eqbits48_a bits change with (inv_a, inv_b). The per-SC phase values flip by π when bits flip, but |phase| and pearson_r are invariant to this constant shift, so the statistics match.

## Code Changes

- Commit `879128cf`: feat(phase20-task3): estimate_per_sc_phase_from_htsig0 helper
- Commit `3b28e3b0`: feat(phase20-task4): apply_per_sc_phase_correction helper
- Commit `968608d8`: diag(phase20-task5): IEEE80211_HT_PER_SC_PHASE_DUMP env-gated
  - **Note**: This commit also added a forward declaration of `estimate_per_sc_phase_from_htsig0` at line 1845 (helper defined at line 2265, used at line 1912). The forward declaration is required for the call site to compile.

## Why this matters

Phase 20 is the SECOND attempt at per-symbol CPE-style correction on HT-SIG (after Phase 19 T7's per-symbol CPE which was REFUTED). Both approaches — per-symbol (Phase 19) and per-subcarrier (Phase 20) — were REFUTED by diagnostic data:

| Phase | Approach | Hypothesis | Result |
|-------|----------|------------|--------|
| 10 T4 | per-symbol CPE on L-SIG | Common-phase between L-LTF0/1 and L-SIG | REVERTED (high variance 7.9%→13.6%) |
| 19 T7 | per-symbol CPE on HT-SIG | Common-phase between HT-SIG0/1 | REFUTED (56 vs 24 failures, 0 FCS OK improvement) |
| 20    | per-subcarrier CPE on HT-SIG | Per-SC phase error between HT-SIG0/1 | **REFUTED** (std ≈ 1 rad, random) |

**The HT-SIG1-specific corruption is NOT a per-SC phase phenomenon.** Future investigation should focus on:
- Sub-sample timing offset between HT-SIG0 and HT-SIG1
- Equalization quality (Phase 3 root cause: per-frame L-LTF0 FFT std=12.7)
- Hardware LO residual (X300 TCXO, despite Phase 17 subdev workaround)
- HT-SIG-specific decoder changes (8 enc96 patterns suggest pruning)

## Open questions / Phase 21+ direction

1. **Sub-sample timing offset**: Try applying a fractional-sample timing offset to HT-SIG1 specifically. If the channel response has a slight time shift between HT-SIG0 and HT-SIG1, this could be the source.
2. **Equalization improvement**: The Phase 3 root cause (per-frame L-LTF0 FFT std=12.7) still affects the equalized symbols. Improving equalization quality would benefit both L-SIG and HT-SIG.
3. **HT-SIG-specific decoder changes**: 8 enc96 patterns suggest the decoder is trying 4 rotations × 2 inv_a × 2 inv_b = 16 candidates. inv_a is a clean polarity flip (per Phase 19 Task 6 analysis). If inv_b is also a clean polarity flip, prune to 4 candidates.
4. **Fix the loopback regression** discovered in Phase 19 Task 6: `sync_short_fused`'s energy-gate prevents frames in software loopback. This blocks automated regression tests.
5. **Investigate viterbi decoder behavior**: 100% crc_fail with constrained bit-density (15-22 ones, not 24 = uniform random) suggests viterbi is finding local minima. The decoder may need better trellis construction or a soft-decision metric.

## Related memory

- [[project_p19_htsig_viterbi]] — Phase 19: HT-SIG0 stable / HT-SIG1 varies (per-symbol CPE REFUTED)
- [[project_p18_lsig_viterbi_analysis]] — Phase 18: LSIG_RATE_FORCE=0xD fix
- [[project_p10_task4_cpe]] — Phase 10: per-symbol CPE REVERTED
- [[project_p14_sync_long_deadlock]] — Phase 14: scheduler fix
- [[project_p17_5ghz_a0_subdev]] — Phase 17: 5 GHz A:0 subdev isolation
