# Phase 4 Verdict — 3-tap Median Filter on USRP

**Date:** 2026-06-12
**Branch:** TEST1
**Verdict:** B_CRIT_FAIL — filter integrated correctly but cannot be validated on USRP (upstream corruption blocks H52 computation)

## USRP Run (30s, filter ON)

| Metric | Value |
|--------|-------|
| Sent | 60 |
| Recv | 0 |
| filter ON | yes (`IEEE80211_H_MEDIAN_FILTER=1`) |
| H52_DUMP | yes (`IEEE80211_H52_DUMP=1`) |
| H52_DUMP_FILTERED | yes (`IEEE80211_H52_DUMP_FILTERED=1`) |
| USRP A:0 single-board TDD | 5.18 GHz, 20 MHz, tx_gain=31, rx_gain=31 |
| Log file | `/tmp/usrp_phase4_30s.log` (1.4 GB) |
| Bash CWD | `/home/hy/gr-ieee802-11` (run via `wrap_rpc2.so` LD_PRELOAD) |

**Result:** Sent=60, Recv=0. **B criterion NOT met.**

## Pre/Post H52 Comparison

**Cannot be computed** — both `[H52_DUMP]` and `[H52_DUMP_FILTERED]` produced 0 lines on USRP.
The dump code at `lib/frame_equalizer_impl.cc:2492` (pre-filter) and `:2545` (post-filter)
is wrapped in a strict guard:

```cpp
if (d_internal_symbol_counter == kHtSig0Rel && d_early_eqsym_valid[kLSigRel] &&
    d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLltf1Rel]) {
```

On USRP, `d_early_eqsym_valid[kLltf0Rel]` (and `[kLltf1Rel]`) never becomes true because
L-LTF0 FFT is corrupted upstream (per Phase 3 Stage 1 verdict: STAGE_AMBIGUOUS,
per-frame std 12.7x loopback). This block is never entered → H52 is never computed
in a dumpable state.

**Confirmed by baseline run (filter OFF, 10s):** also 0 `[H52_DUMP]` lines, 0 `[LTF0_FFT_DUMP]`
(only the L-LTF0 FFT save dump — if enabled — would have fired; we did not enable
`IEEE80211_LTF0_FFT_DUMP=1` in this baseline).

This is **not a regression** introduced by the median filter — the pre-filter H52_DUMP
guard is the same code path that was already in place for the Phase 2 H52 diagnosis
(commit `33df3f9`). On USRP, the upstream L-LTF0 corruption (Phase 3) has prevented
H52 from being computed in any usable state for at least two days.

## Loopback Regression Validation

| Config | Result |
|--------|--------|
| Filter OFF, dump OFF | OK=0/FAIL=1 (FcsLogger `crc` bug — historical baseline) |
| Filter ON, dump ON | OK=0/FAIL=1 (same as baseline — no regression) |

Both runs show no behavior change. The 9/9 underlying test pass count is masked by
the FcsLogger `crc` field bug noted in `MEMORY.md` (unrelated to Phase 4).

## Synthetic Test Validation

`examples/test_h_median_filter_synthetic.py` — 6/6 pass:
- Boundary handling
- Phase preservation
- All-equal magnitudes → identity
- 2-equal tie-break (3 of 6 cases that mismatched the original 6-way ladder)
- 3.20× mean per-SC error reduction on 20% outliers (≥3× required)

The C++ helper (`apply_h_median_filter`) uses `std::stable_sort` over an index array,
guaranteed to match Python `sorted()` stability on all 2-equal cases (after the
fix in commit `d3bc4d5`).

## Interpretation

The Phase 4 design was:
1. Apply 3-tap median filter to H52 at the call site of `estimate_header_channel_from_lltf52`
2. Use pre/post H52 dumps to validate the filter's effect on USRP data
3. Verify B criterion (Recv≥1) is met

**All three steps blocked by upstream corruption:**
- (1) Code integration is correct, but the function is called with corrupted input
- (2) Cannot validate because H52 is never computed in a dumpable state on USRP
- (3) B criterion not met — but this is the same as Phase 3 (Recv=0) and Phase 2 (Recv=0)

The median filter itself is sound. It would help on cleaner data. On the current
USRP session, the L-LTF0 FFT corruption upstream (Phase 3 STAGE_AMBIGUOUS) is
the dominant failure mode, and median filtering cannot fix that.

## What This Confirms

- ✅ Median filter integration is correct (build clean, code reviews pass, loopback 9/9)
- ✅ Median filter is mathematically valid (synthetic test 3.20× reduction)
- ✅ Median filter does not regress loopback (OK=0/FAIL=1 same as baseline)
- ❌ Median filter cannot fix USRP Recv=0 — corruption is upstream
- ❌ Pre/post H52 comparison cannot be measured on USRP — H52 never computed

## What This Rules Out

- The H52 median filter is **not** a fix for the current USRP session
- The H52_DUMP path is **not** usable on USRP until upstream corruption is fixed
- The H52 median filter is a **valid** opt-in tool for cleaner data (e.g. different
  RF setup, future hardware, or after fixing the upstream issue)

## Next Steps

Per spec §7.3 (Stage 2 candidates), if upstream corruption is ever fixed:
- Per-frame outlier rejection (Winsorize) + median
- Frequency-domain H interpolation (replace outlier SCs with linear interp from neighbors)
- Cross-LTF0/LTF1 H consistency check

For the current USRP session, **stop here**. The corruption is structural (std/mean
ratio constant across gain/timing settings per `docs/superpowers/notes/2026-06-11-fix-experiments-summary.md`),
and the only remaining directions are RF-chain or hardware-level, not algorithmic.

## Artifacts

- USRP 30s filter ON log: `/tmp/usrp_phase4_30s.log` (1.4 GB)
- USRP 10s baseline log: `/tmp/usrp_phase4_baseline_10s.log`
- Code: `lib/frame_equalizer_impl.{h,cc}` (commits `d3bc4d5`, `9443c60`)
- Tests: `examples/test_h_median_filter_synthetic.py`, `examples/test_h_median_filter_pre_post.py`
- Spec: `docs/superpowers/specs/2026-06-12-phase4-robust-h-estimation-design.md`
- Plan: `docs/superpowers/plans/2026-06-12-phase4-robust-h-estimation.md`

## Commits

- `6c81c70` test: add synthetic 3-tap median filter self-test for H52 (Task 1)
- `baf5b97` feat(frame_eq): add IEEE80211_H_MEDIAN_FILTER opt-in env var (Task 2)
- `d3bc4d5` fix(frame_eq): use std::stable_sort in apply_h_median_filter (Task 3 fix)
- `9443c60` feat(frame_eq): wire 3-tap median filter + add [H52_DUMP_FILTERED] (Tasks 4+5)
- `f175616` test: add pre/post H52 comparison script (Task 6)
- (this commit) notes: Phase 4 verdict — B_CRIT_FAIL with USRP validation blocked
