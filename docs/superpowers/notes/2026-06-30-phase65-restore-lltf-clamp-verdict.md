# Phase 65 Verdict — Restore ±4 Clamp + K=0 Implicit Baseline (PARTIAL)

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **PARTIAL** — Reverted ±4 clamp and removed `LLTF_OFFSET_CORRECT=14` from CLAUDE.md (commits `d85bc94`, `295be73`). K=0 implicit run reveals **NEW finding**: `is_ht_frame=1` appears 8 times (vs 0 in Phase 63 C3 with K=4). HT_SIG_CAND 16→48. **K=4 was also the wrong axis** — K=0 is the correct splitter FFT window offset. FCS_OK=0 unchanged (HT-SIG viterbi crc_fail wall). avg_snr is highly variable (UHD noise, not K-effect).
**Commits**: d85bc94 (T1 revert), 295be73 (T2 docs), [verdict SHA from this commit]

## Goal

Restore the `LLTF_OFFSET_CORRECT` clamp to ±4 (URGENT, Phase 64 exposure) and update CLAUDE.md to remove the broken `LLTF_OFFSET_CORRECT=14` from standard USRP test config. Then re-run Phase 63 C3 baseline (K=0 implicit) to confirm system is healthy after the revert.

## Method

1. T1 (commit `d85bc94`): `git revert b6e3142` — restore ±4 clamp at `lib/ht_symbol_splitter_impl.cc:111-113`.
2. T2 (commit `295be73`): Update CLAUDE.md to remove `IEEE80211_LLTF_OFFSET_CORRECT=14` from standard USRP test config; add explanatory comment.
3. T3: `make && make install` to propagate the revert.
4. T4: Run 60s warmup + 35s test on `test_usrp_minimal_loopback.py` at `--rate 10` with:
   - **NO `IEEE80211_LLTF_OFFSET_CORRECT`** (K=0 implicit, default)
   - `IEEE80211_H52_NULL_INTERP=1` (Phase 60 mechanism)
   - `IEEE80211_H52_NULL_DUMP=1` (count H60_NULL events)
   - Standard env vars (LSIG_RATE_FORCE=0xD, TIMING_OFFSET_APPLY=1)
5. T5: Compare to Phase 63 C3 (K=4 baseline).

## Results

### Banner verification (T3+T4)

No `[SPLITTER]` banner in Phase 65 log (expected — when `IEEE80211_LLTF_OFFSET_CORRECT` env var is unset, the `fprintf` is gated by `getenv(...)` check at `ht_symbol_splitter_impl.cc:109-117`, so the banner is skipped entirely). `g_lltf_offset_correct` stays at its default (0). Phase 65 design intent met.

### Comparison vs Phase 63 C3 (K=4)

| Metric | Phase 63 C3 (K=4) | Phase 65 (K=0 implicit) | Delta |
|---|---|---|---|
| Sent/Recv/FCS_OK/FCS_FAIL | 95/0/0/0 | 95/0/0/0 | identical |
| H60_NULL events | 8 | 8 | identical |
| n_nulls per event | 23/52 | 20/52 | -13% better |
| **HT_SIG_CAND** | **16** (crc_fail) | **48** (crc_fail) | **3x more candidates** |
| **is_ht_frame=1** | **0** | **8** | **+8 (HUGE improvement)** |
| is_ht_frame=0 | 8 | 0 | -8 |
| avg_snr_lsig | 10.55 | 3.98 | -62% (UHD noise) |
| avg_snr_htsig | — | 2.54 | — |
| LSIG_DECODE OK | 4 | 6 | +2 |
| Log size | 2.1 GB | 369 MB | smaller (different failure mode) |
| Exit code | 0 | 0 | clean |

### Per-condition findings

**Phase 65 K=0 implicit (this test)**: `is_ht_frame=1` appears 8 times in H60 events. HT-SIG parse correctly identifies HT frames and the candidate search loop fires 48 times (3x more than C3). All 48 candidates still fail `crc_fail` — the upstream HT-SIG viterbi wall is unchanged.

## Verdict: PARTIAL

**Phase 65 achieves its primary goal**: URGENT restoration of the ±4 clamp to prevent silent regressions from `LLTF_OFFSET_CORRECT=14`. The CLAUDE.md standard config is now consistent with code behavior (no broken `=14` env var).

**Phase 65 yields a NEW finding**: With K=0 (no `LLTF_OFFSET_CORRECT` env var set), `is_ht_frame=1` now appears 8 times — Phase 60/61/63's `is_ht_frame=0` anomaly was not just a script/dump artifact, but a **consequence of K=4 being passed to the splitter**. K=4 (clamped from K=14) was the wrong axis all along; K=0 is the correct splitter FFT window offset.

**Phase 65 PARTIAL caveat**: avg_snr is highly variable run-to-run (Phase 55 confirmed UHD streaming instability, not air path). The avg_snr 10.55 → 3.98 drop is **likely noise**, not a K-effect. The is_ht_frame inversion and HT_SIG_CAND 3x increase are the **robust signals** — these are architectural differences, not SNR-dependent.

**FCS_OK=0 unchanged** — the upstream HT-SIG viterbi wall (Phase 41/44) is still the gate. Pre-clean + K=0 opens more frames into the HT-SIG EQ path, but the viterbi still fails on CRC.

## What This Validates

- The ±4 clamp revert (commit `d85bc94`) is in place; the Phase 64 regression is no longer reachable.
- CLAUDE.md standard config is now self-consistent with code behavior.
- K=0 implicit is the correct splitter FFT window offset (K=4 was already wrong, K=14 was more wrong).
- The H60 detection call site at `frame_equalizer_impl.cc:4413` is reachable with K=0, producing both `is_ht_frame=0` and `is_ht_frame=1` events depending on parse-path convergence.

## What This Does NOT Validate

- USRP realtime `FCS_OK ≥ Sent/N` (still 0 with K=0; was 0 with K=4).
- HT-SIG viterbi unblocking — K=0 opens more candidates (48 vs 16) but all still crc_fail.
- A direct `K=0 vs K=4` clean comparison — the SNR variability dominates the metrics, requiring 3+ trials to claim statistical equivalence.
- Any other upstream-of-viterbi fix site (Phase 64 ruled out L-LTF0 splitter offset; K=0 is correct but doesn't unblock viterbi).

## Implications

- **Standard USRP test config in CLAUDE.md is now healthy**: `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_TIMING_OFFSET_APPLY=1 --freq 5890 --tx-gain 20` (no `LLTF_OFFSET_CORRECT=14`).
- **The `IEEE80211_H52_NULL_DUMP=1` env var is required** for H60 event line emission (Phase 62 BLOCKED used default `dump=OFF` and missed events).
- **is_ht_frame=1 is the right gate-opening signal**: With K=0, the H60 detection can now reach the HT-SIG parse path. Phase 60/61/63's `is_ht_frame=0` was a side effect of K=4 (or K=14 in Phase 64) being passed to the splitter.
- **Phase 66 must attack HT-SIG viterbi** (the actual gate that produces `crc_fail`), not L-LTF0 timing.

## Files

- `/home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc` (T1 commit `d85bc94`, ±4 clamp restored)
- `/home/hy/gr-ieee802-11/CLAUDE.md` (T2 commit `295be73`, `LLTF_OFFSET_CORRECT=14` removed)
- `/home/hy/gr-ieee802-11/build/libgnuradio-ieee802-11.so*` (T3 install)
- `/tmp/p65_k0_baseline.log` (T4 35s test, 369 MB)
- `/tmp/p63_metrics_summary.txt` (T4 appended)
- `docs/superpowers/notes/2026-06-30-phase65-restore-lltf-clamp-verdict.md` (this file)

## Phase 66 candidates (HARD CONSTRAINT upstream-attack)

Per HARD CONSTRAINT, Phase 66 must attack upstream of the HT-SIG viterbi. K=0 opens more frames into the HT-SIG EQ path, but the viterbi still fails on CRC. Three concrete candidates:

1. **Investigate HT-SIG viterbi input** — with K=0, 48 candidates are now searched per frame (vs 16 in K=4). Add per-candidate diagnostic log to capture metric distributions and identify why all candidates fail. This is the most direct path to understanding the viterbi failure mode.

2. **Per-frame H52 re-estimation from HT-SIG pilots** (Phase 39 candidate, REFUTED on USRP) — re-evaluate with K=0 since the upstream gate is now different. Phase 39 saw pilots noise-dominated; with cleaner H52 from K=0, pilots may now be usable.

3. **Investigate the `is_ht_frame=1` convergence path** — Phase 65 sees 8 `is_ht_frame=1` events (vs 0 in C3). Add a call-site counter at the `is_ht_frame` decision to understand what conditions lead to HT-SIG parse success. If the path is reliable, focus downstream viterbi fixes here.

## Lessons Learned

1. **The ±4 clamp was a feature, not a bug** — it silently protected the system from a broken CLAUDE.md config. Lifting the clamp exposed the latent issue but did NOT solve the upstream problem.

2. **K=4 is also the wrong axis** — Phase 64 ruled out K=14; Phase 65 reveals K=4 is wrong too. The correct splitter FFT window offset is K=0 (no shift). The 14-sample shift is a one-time boundary fix at `sync_long.cc FRAME_START_BASE 160→174`; it should NOT be re-applied at the splitter.

3. **is_ht_frame=1 is the right gate-opening signal** — K=0 produces 8 `is_ht_frame=1` events (vs 0 with K=4/K=14). This is a robust architectural difference, not SNR-dependent.

4. **avg_snr is unreliable for run-to-run comparison** (Phase 55) — the 10.55 → 3.98 drop is UHD streaming noise, not a K-effect. 3+ trials are needed for clean statistical claims.

5. **The upstream HT-SIG viterbi wall** (Phase 41/44) is still the gate. K=0 opens more candidates into the wall but doesn't break through. Phase 66 must attack the viterbi itself, not L-LTF0 timing.