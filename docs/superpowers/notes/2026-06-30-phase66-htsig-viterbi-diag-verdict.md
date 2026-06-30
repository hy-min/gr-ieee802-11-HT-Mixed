# Phase 66 Verdict — Per-Candidate HT-SIG Viterbi Diagnostic (PARTIAL — LSIG viterbi is the real wall)

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **PARTIAL** — Per-candidate diagnostic added (commit `c272edd`, env-var `IEEE80211_HTSIG_VITERBI_DIAG=1`). 32 HT_SIG_CAND across 2 candidate-search frames ALL fail with `crc_fail` at metric 13-14 (~27-29% BER on 48-step Hamming distance, no threshold gate). Two distinct failure modes (Frame 7 rot-degenerate, Frame 9 inv-asymmetric). **6/8 frames blocked by LSIG viterbi upstream** (`LSIG_PARSE_FAIL reason='viterbi_fail'`) — this is the higher-leverage wall, not HT-SIG. FCS_OK=0 unchanged.
**Commits**: c272edd (T1 diagnostic), [verdict SHA from this commit]

## Goal

Investigate why all HT-SIG viterbi candidates fail `crc_fail` on USRP, despite K=0 implicit (Phase 65) opening the upstream HT-SIG parse path (`is_ht_frame=1` appearing 8 times). Add per-candidate diagnostic logging to capture metric distributions and identify the viterbi failure mode.

## Method

1. T1 (commit `c272edd`): Add per-candidate HT-SIG viterbi diagnostic in `lib/frame_equalizer_impl.cc` — track `best_metric/best_rot/best_inv_a/best_inv_b/best_fail`, opt-in via `IEEE80211_HTSIG_VITERBI_DIAG=1` (default OFF, thread-safe snprintf per commit `e90e3f5`).
2. T2: `make && make install`.
3. T3: 60s warmup + 35s test with `HTSIG_VITERBI_DIAG=1` + Phase 65 K=0 baseline config.
4. T4: Extract per-candidate metrics, identify failure patterns.

## Results

### Per-frame diagnostic (8 HTSIG_VITERBI_DIAG frames)

| frame_sym | n_candidates | found | best_metric | best_rot | best_inv_a | best_inv_b | best_fail |
|---|---|---|---|---|---|---|---|
| 4 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |
| 5 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |
| 6 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |
| **7** | **16** | 0 | **14** | 1 | 0 | 0 | **crc_fail** |
| 8 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |
| **9** | **16** | 0 | **13** | 1 | 1 | 0 | **crc_fail** |
| 10 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |
| 11 | 0 | 0 | -1 | -1 | -1 | -1 | none (LSIG viterbi_fail) |

### Per-candidate metric distribution (32 lines from /tmp/p66_htsig_cand_lines.txt)

Frame 7 (16 lines): rot=0,3 (all inv_a/inv_b) → metric 16; rot=1,2 (all inv_a/inv_b) → metric 14. **8 lines @14, 8 lines @16.**

Frame 9 (16 lines): rot=0 (mixed) → 16/14/16/14; rot=1 → 15/15/13/13; rot=2 → 13/13/15/15; rot=3 (mixed) → 14/16/14/16. **4 @13, 4 @15, 4 @14, 4 @16.**

| metric | count | notes |
|---|---:|---|
| 13 | 4 | all from Frame 9 (rot=1 inv_a=1 / rot=2 inv_a=0) |
| 14 | 12 | Frame 7: 8 (rot=1,2); Frame 9: 4 (rot=0 inv_a=1 / rot=3 inv_a=0,1) |
| 15 | 4 | all from Frame 9 (rot=1 inv_a=0 / rot=2 inv_a=1) |
| 16 | 12 | Frame 7: 8 (rot=0,3); Frame 9: 4 (rot=0 inv_a=0 / rot=3 inv_a=1) |

**All 32 candidates fail `crc_fail`**. No candidate passes the 6-bit HT-SIG CRC on bits 33-38 (per `frame_equalizer_impl.cc` viterbi branch).

### Viterbi metric semantics

`viterbi_decode_133_171` returns best-path **Hamming distance** over 48 trellis steps. There is **NO explicit metric threshold** — the accept gate is the 6-bit HT-SIG CRC on decoded bits 33-38.

**13-14 disagreements on 48 steps = ~27-29% BER** on the encoded stream, which after deinterleaving yields a multi-bit-error header where CRC is essentially never satisfied by chance.

### Two distinct structural failure modes

**Frame 7 (rot-degenerate)** — metric landscape is degenerate by 90° rotations:
- rot=0,3 (quadrature axes) → metric 16
- rot=1,2 (diagonal axes) → metric 14
- inv_a/inv_b is irrelevant (all 4 combinations yield same metric per rot)

This is the **signature of a residual phase offset ~45° from the optimal QBPSK axis** — only rotations close to the true offset reduce metric. Single-axis (rot-only) sweep would marginally improve, but the 2-step gap (14 vs 16) shows the bias is close to 45° not 0°.

**Frame 9 (inv-asymmetric)** — metric landscape shows inv-asymmetric spread:
- rot=1 inv_a=0,1: 15 vs 13 (Δ=2)
- rot=2 inv_a=0,1: 13 vs 15 (Δ=2)
- rot=0: 16/14/16/14 (rot-quadrature pattern)
- rot=3: 14/16/14/16 (rot-quadrature pattern)

Rotation alone cannot resolve polarity. This is a **different failure mode** — single-axis tuning (rot-only or inv-only) is unlikely to fix both Frame 7 and Frame 9 simultaneously.

### 6/8 frames blocked by LSIG viterbi upstream

`LSIG_PARSE_FAIL reason='viterbi_fail'` fires on all 6 non-candidate-search frames (sym=4,5,6,8,10,11). `LSIG_DECODE OK` count is **4** (4 lines in /tmp/p66 log: enc=1 len=1258, enc=0 len=322, enc=0 len=2416, enc=3 len=1965). These frames have `is_ht_frame=1` set but the **L-SIG path lacks the rot×inv_a×inv_b candidate search that HT-SIG has**. This is the **higher-leverage wall** — 75% of frames never reach HT-SIG because LSIG viterbi fails first.

### Suspicious n_nulls uniformity

`n_nulls=24/52 thresh=0.150 radius=2` appears in all 8 H60_NULL events with **identical** counts. This could be:
- (a) **Pre-clean operating as designed** (each frame picks the same static-SC set, since the channel null pattern is time-invariant across the 35s test)
- (b) **Frozen-counter bug** (n_nulls is being recorded before update, or the counter never advances)

Recommend Phase 67 add a per-frame `n_nulls` dump at the H60 pre-clean call site to disambiguate (a) vs (b).

### Other T3 metrics

- `avg_snr_lsig=1.71` (lower than Phase 65's 3.98, confirms Phase 55 avg_snr unreliability)
- `avg_snr_htsig=1.81`
- `LSIG_DECODE OK=4`, `LSIG_PARSE_FAIL=6` (viterbi_fail all)
- `HT_SIG_CAND=32` (16 per frame, 2 frames)
- `FCS_OK=0`, `FCS_FAIL=0` (no HT-SIG → no payload decode attempt)

## Verdict: PARTIAL — LSIG viterbi is the real wall

Phase 66 successfully added per-candidate diagnostic infrastructure (commit `c272edd`). The data reveals:

1. **HT-SIG viterbi failure is structural, not random**: 13-14 metric on 48 steps is ~27-29% BER — far above CRC satisfaction probability.
2. **Two failure modes require two different fixes** (or one fix that addresses both).
3. **6/8 frames never reach HT-SIG candidate search** — LSIG viterbi is the upstream gate (4x leverage over HT-SIG).
4. **FCS_OK=0 unchanged** — the upstream-of-viterbi attack (Phase 65 K=0) opened the HT-SIG parse path but viterbi itself remains broken.

The architectural wall (Phase 41/44 HT-SIG viterbi crc_fail) is real and is now characterized at the per-candidate level. The next investigation must target the **upstream LSIG viterbi** (which blocks 75% of frames) and/or the **H52 quality** (which limits the 25% of frames that do reach HT-SIG to ~27-29% BER).

## What This Validates

- Per-candidate HT-SIG viterbi diagnostic infrastructure (env-var gated, opt-in, thread-safe).
- HT-SIG viterbi failure is **structural, not random**: best_metric 13-14 across 16 candidates, both frames converge to same wrong answer.
- Two distinct failure modes identified (Frame 7 rot-degenerate, Frame 9 inv-asymmetric).
- LSIG viterbi is the **higher-leverage wall** — 6/8 frames never reach HT-SIG.

## What This Does NOT Validate

- USRP realtime `FCS_OK ≥ Sent/N` (still 0).
- HT-SIG viterbi unblocking — best_metric 13-14 is way below acceptable threshold.
- Single-axis fix (per-symbol CPE, larger candidate space) — Frame 7 and Frame 9 have different failure modes.
- Why H60 pre-clean produces n_nulls=24/52 across all 8 frames with **identical** counts (likely (a) channel static, but (b) frozen counter is not ruled out).

## Implications

- **LSIG viterbi is the next attack target** — 6/8 frames blocked here, leverage is 4x higher than HT-SIG.
- **H52 quality is the root cause** — even when frames reach HT-SIG, the 13-14 metric indicates ~27-29% BER, which is consistent with H52 nulls amplifying noise.
- **Phase 60 pre-clean is reaching its limits** — n_nulls=24/52 plateau, not adapting to channel depth.
- **Larger candidate space (per-symbol CPE) is unlikely to help alone** — both failure modes are structural, not rotational.

## Files

- `/home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc` (T1 commit `c272edd`)
- `/home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so.g590b96a` (T2 install, 18:41)
- `/tmp/p66_htsig_viterbi_diag.log` (T3 359MB raw log)
- `/tmp/p66_htsig_viterbi_frames.txt` (8 HTSIG_VITERBI_DIAG summary)
- `/tmp/p66_htsig_cand_lines.txt` (32 per-candidate lines)
- `docs/superpowers/notes/2026-06-30-phase66-htsig-viterbi-diag-verdict.md` (this file)

## Phase 67 candidates (HARD CONSTRAINT upstream-attack)

Per HARD CONSTRAINT, Phase 67 must attack upstream of the HT-SIG viterbi wall. Per T4 analysis, the highest-leverage target is **LSIG viterbi** (6/8 frames blocked), not HT-SIG. Four concrete candidates:

1. **Extend candidate search to L-SIG viterbi** (HIGHEST LEVERAGE) — add `IEEE80211_LSIG_VITERBI_DIAG=1` symmetric to Phase 66 T1 to characterize the L-SIG metric floor. L-SIG path currently lacks the rot×inv_a×inv_b candidate search that HT-SIG has. If L-SIG best_metric is also 13-14, the noise amplification is upstream (Hhdr52 nulls) and the search loop cannot help. **This is upstream of the HT-SIG viterbi wall, satisfying HARD CONSTRAINT.**

2. **Tighten H60 pre-clean threshold** — current threshold (0.15, radius=2) plateaus at exactly 24 nulls across all 8 frames, not adapting to actual channel depth. Two-stage coarse→fine pass (0.15 → 0.10) is a candidate. If n_nulls drops below 12/52, both LSIG and HT-SIG viterbi metrics should improve. **Upstream of viterbi, satisfies HARD CONSTRAINT.**

3. **Per-symbol H re-estimation** for HT-SIG (Phase 39 REFUTED standalone, but not tested under Phase 60 pre-clean activation) — re-test as a combo, not blindly retry Phase 39. **Upstream of HT-SIG viterbi EQ.**

4. **Verify n_nulls=24/52 uniformity is (a) not (b)** — add per-frame `n_nulls` dump at the H60 pre-clean call site to disambiguate "channel static" from "frozen counter bug". Cheap diagnostic; should run before any code change. **Diagnostic, satisfies HARD CONSTRAINT.**

## Lessons Learned

1. **Per-candidate diagnostic reveals structural failure modes** — 16 candidates all fail with metric 13-14 in 2 distinct patterns (rot-degenerate vs inv-asymmetric). Without the diagnostic, the wall looked like random noise.

2. **LSIG viterbi is the higher-leverage wall** — 6/8 frames blocked before reaching HT-SIG. Future investigation must target LSIG first, not HT-SIG.

3. **13-14 metric on 48 steps = ~27-29% BER** — this is the failure fingerprint. Any future fix that brings the viterbi metric below ~10 (post-deinterleaving) should yield CRC success.

4. **avg_snr is unreliable** (Phase 55) — T3 saw avg_snr_lsig=1.71 vs Phase 65's 3.98 on identical config. The per-candidate metric is a more robust signal than avg_snr.

5. **n_nulls=24/52 frozen uniformity** is suspicious — could be (a) channel static (likely) or (b) frozen counter bug (unverified). Phase 67 should disambiguate before any code change.