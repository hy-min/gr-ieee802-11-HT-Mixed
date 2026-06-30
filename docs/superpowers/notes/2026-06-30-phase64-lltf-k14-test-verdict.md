# Phase 64 Verdict — LLTF_OFFSET_CORRECT=14 Lifted Clamp Test (REFUTED)

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **REFUTED** — K=14 splitter re-shift introduces cyclic alias that BREAKS L-SIG rate field detection. HT_SIG_CAND regressed 16→0, avg_snr_lsig -5 dB. The 14-sample fix from Phase 33 was at `sync_long.cc FRAME_START_BASE 160→174`, not at the splitter FFT window. **Re-shifting inside the splitter is the wrong axis.**
**Commits**: b6e3142 (T1 lift clamp), [verdict SHA from this commit]

## Goal

Test whether lifting the `IEEE80211_LLTF_OFFSET_CORRECT` clamp from ±4 to ±16 (so the standard config's `=14` actually takes effect) unblocks USRP HT-SIG viterbi.

## Method

1. Lift clamp: `lib/ht_symbol_splitter_impl.cc:111-113` from `K ∈ [-4, +4]` to `K ∈ [-16, +16]` (commit `b6e3142`).
2. `make && make install` to propagate.
3. Verify `[SPLITTER] IEEE80211_LLTF_OFFSET_CORRECT=14` banner appears (T3).
4. Run 60s warmup + 35s test on `test_usrp_minimal_loopback.py` at `--rate 10` with:
   - `IEEE80211_LLTF_OFFSET_CORRECT=14` (was silently =4)
   - `IEEE80211_H52_NULL_INTERP=1` (Phase 60 mechanism)
   - `IEEE80211_H52_NULL_DUMP=1` (count H60_NULL events)
   - Standard env vars (LSIG_RATE_FORCE=0xD, TIMING_OFFSET_APPLY=1)

## Results

### Banner verification (T3)

`[SPLITTER] IEEE80211_LLTF_OFFSET_CORRECT=14 (L-LTF0 offset shifted by 14 samples)` confirmed at runtime. The clamp lift propagated correctly.

### Comparison vs Phase 63 C3 (K=4)

| Metric | Phase 63 C3 (K=4) | Phase 64 (K=14) | Delta |
|---|---|---|---|
| SPLITTER banner | `=4` (silently clamped) | `=14` (active) | K=14 confirmed |
| Sent / Recv / FCS_OK | 95 / 0 / 0 | 95 / 0 / 0 | identical |
| H60_NULL events | 8 | 8 | identical |
| n_nulls per event | 23/52 | 22/52 | -1 SC |
| **HT_SIG_CAND** | **16** (crc_fail) | **0** | **-100% regression** |
| is_ht_frame=1 | 0 | 0 | both 0 |
| **avg_snr_lsig** | **10.55** | **3.27** | **-5 dB regression** |
| avg_snr_htsig | — | 6.93 | — |
| LSIG_DECODE OK | 4 | 4 | identical |
| rate_field detection | working | `rate=-1, length=-1, parity_ok=-1` (broken) | **BROKEN** |
| Log size | 2.1 GB | 390 MB | smaller (less frame processing) |
| Exit code | 0 | 0 | clean |

### Per-condition findings

**Phase 64 K=14 (this test)**: HT-SIG candidates drop to **zero**. All 8 H60 events show `is_ht_frame=0` — frames never enter the HT-SIG EQ branch. The 8 L-SIG parse-fail events all show `rate=-1, length=-1, parity_ok=-1` — L-SIG rate field detection is **broken** at the splitter level.

## Verdict: REFUTED

**K=14 is the wrong direction.** Phase 33's 14-sample fix was at `sync_long.cc` (`FRAME_START_BASE 160→174`), which sets the boundary between sync_short preamble and the L-LTF0 FFT window. After that fix, the L-LTF0 FFT window is correctly placed. Re-shifting the L-LTF0 FFT by another 14 samples INSIDE the splitter introduces a different cyclic alias that breaks the L-SIG rate field detection entirely.

**Important corollary**: The CLAUDE.md standard config's `IEEE80211_LLTF_OFFSET_CORRECT=14` was **silently broken** by the ±4 clamp. The fact that the clamp was at ±4 actually **protected the system** from this regression for many phases. Lifting the clamp (T1 commit `b6e3142`) **exposed** the latent bug — but the fix is NOT to keep the clamp at ±4. The fix is to **remove `LLTF_OFFSET_CORRECT=14` from CLAUDE.md standard config** since the 14-sample shift is already correctly applied at `sync_long.cc`.

**Phase 64 rule established (REFUTED for further direct L-LTF shifting at splitter level)**: applying `LLTF_OFFSET_CORRECT` beyond the originally-corrected `sync_long` boundary DEGRADES downstream parsing. The correct value is **0** (no further shift) or the env var should be **omitted** from standard config.

## What This Validates

- The `LLTF_OFFSET_CORRECT` env var works as designed — when K=14 is allowed, the splitter applies the 14-sample shift (banner confirms).
- The ±4 clamp in `ht_symbol_splitter_impl.cc:111-113` was the **only thing** preventing the broken `=14` config from running.
- The K=14 splitter re-shift is the **wrong axis** for H52 quality improvement; it breaks L-SIG rate detection.
- Phase 41/44 architectural wall (HT-SIG viterbi crc_fail) is **NOT** upstream of L-LTF0 splitter offset.

## What This Does NOT Validate

- USRP realtime `FCS_OK ≥ Sent/N` (still 0 with K=14; was 0 with K=4 baseline).
- HT-SIG viterbi unblocking — K=14 made it **worse** (HT_SIG_CAND 16→0).
- Any other upstream-of-viterbi fix site (Phase 64 ruled out L-LTF0 splitter offset).

## Implications

- **CRITICAL CLAUDE.md update required**: Remove `IEEE80211_LLTF_OFFSET_CORRECT=14` from standard USRP test config. Use `=0` or omit. The `sync_long.cc FRAME_START_BASE 160→174` is the only correct 14-sample shift; it does not need a re-application at the splitter.
- **Decision needed on clamp range**: Should we (a) restore the ±4 clamp, (b) keep ±16 clamp (allows future tests of K=8, K=10, etc. but won't take effect from CLAUDE.md), or (c) add a build-time warning when LLTF_OFFSET_CORRECT is set to a non-zero value? Recommend (a) — restore ±4 clamp to protect against accidental `=14` use; Phase 65+ should test K=0 explicitly.
- **Phase 65 must attack a DIFFERENT axis**, not L-LTF0 splitter offset.

## Files

- `/home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc` (T1 commit `b6e3142`)
- `/home/hy/gr-ieee802-11/build/libgnuradio-ieee802-11.so*` (T2 install)
- `/tmp/p64_banner_check.log` (T3 banner verification, 5s test)
- `/tmp/p64_k14_interp.log` (T4 35s test, 390 MB)
- `/tmp/p63_metrics_summary.txt` (T4 appended)
- `docs/superpowers/notes/2026-06-30-phase64-lltf-k14-test-verdict.md` (this file)

## Phase 65 candidates (HARD CONSTRAINT upstream-attack)

Per HARD CONSTRAINT, Phase 65 must attack upstream of the viterbi. The K=14 splitter offset is **ruled out**. Three concrete candidates:

1. **Restore ±4 clamp + update CLAUDE.md to remove `LLTF_OFFSET_CORRECT=14` from standard config**. The 14-sample shift is correctly applied at `sync_long.cc`; the env var should be 0 or omitted. Then re-run Phase 63 C3 (K=0) and confirm baseline reproduces. Code change: revert `ht_symbol_splitter_impl.cc:111-113` to ±4. CLAUDE.md change: remove the env var from standard config.

2. **Investigate COMBO HT_SIG_CAND=0** (from Phase 63): pilot CPE may consume frames before HT-SIG parse. Add a per-symbol counter at the pilot CPE call site (`frame_equalizer_impl.cc`) and confirm pilot CPE is not gating downstream progress. If pilot CPE is the bottleneck, consider disabling it in COMBO env var and re-test.

3. **Try `--rate 5`** (HIGH RISK, Phase 58 REFUTED, do not attempt unless #1 and #2 are exhausted). Phase 58 verdict was on a different code path; minimal_loopback may behave differently. ONLY attempt if all other upstream fixes fail.

## Lessons Learned

1. **The ±4 clamp was a feature, not a bug**: It silently protected the system from a broken CLAUDE.md config (`LLTF_OFFSET_CORRECT=14`). Lifting the clamp exposed the latent issue but did NOT solve the upstream problem.

2. **The 14-sample shift is a one-time fix at the boundary**: `sync_long.cc FRAME_START_BASE 160→174` already applies it. Re-applying at the splitter (FFT window offset) is the wrong axis. The two sites should not both be active.

3. **Per-frame H52 estimation may need a different L-LTF0 sample alignment than the boundary**: K=14's HT_SIG_CAND 16→0 regression shows that the L-LTF0 sample alignment for the **boundary** is correct (Phase 33 fix), but the L-LTF0 sample alignment for **per-frame H52 estimation** may want K=0. This is a fine-grained architectural question Phase 65+ must investigate.

4. **Reverting the clamp change is urgent**: The lifted clamp (commit `b6e3142`) currently in master could cause silent regressions if any code or operator sets `LLTF_OFFSET_CORRECT=14` in production. Recommend immediate revert.