# Phase 62 Verdict — `--rate 10` + Phase 60/61 Combo Sweep

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **BLOCKED** — None of 5 conditions produced USRP realtime `FCS_OK ≥ 1`. RX chain stalls at L-SIG viterbi across all conditions. Test-script mismatch + LLTF_OFFSET_CORRECT silently clamped block any equalizer-side validation.
**Commits**: 8bd0fc2, 34c44d9 (Task 1), verdict(this commit)

## Goal

Determine whether running USRP at `--rate 10` together with Phase 60/61
equalizer-side env vars (`IEEE80211_H52_NULL_INTERP=1` and/or
`IEEE80211_H52_NULL_COMBO=1`) achieves `FCS_OK ≥ 1` on a single 35s run,
closing the Phase 61 PARTIAL verdict (L-SIG viterbi gate at avg_snr 3-8 dB).

## Method

5-condition USRP sweep on `test_usrp_tdd_ratematch.py` (CLI args added
in Phase 62 Task 1 to support rate/duration sweep). All runs 35s,
same-board A:0/A:0 RX2, freq 5890, tx-gain 20, standard env vars
`IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14
IEEE80211_TIMING_OFFSET_APPLY=1`.

| # | --rate | env var | Hypothesis |
|---|---|---|---|
| 1 | 20e6 | none | Confirm Phase 55-61 closure state |
| 2 | 10e6 | none | Re-test Phase 56 PARTIAL on current code |
| 3 | 10e6 | H52_NULL_INTERP=1 | Joint SNR + EQ-gate unblock (Phase 60) |
| 4A | 10e6 | H52_NULL_COMBO=1 | Strongest equalizer-side + SNR (Phase 61) |
| 4B | 10e6 | H52_NULL_COMBO=1 | Replicate for CV (Phase 58 method) |

## Results

### Comparison Table (verbatim from /tmp/p62_metrics_summary.txt)

| Condition | --rate | env var | Sent | OK | FAIL | H60 banner | avg_snr N |
|---|---|---|---:|---:|---:|---:|---:|
| 1: rate20 baseline | 20e6 | none | 70 | 0 | 0 | 0 | 0 |
| 2: rate10 baseline | 10e6 | none | 70 | 0 | 0 | 0 | 0 |
| 3: rate10 + INTERP | 10e6 | H52_NULL_INTERP=1 | 70 | 0 | 0 | 2 | 0 |
| 4A: rate10 + COMBO | 10e6 | H52_NULL_COMBO=1 | 70 | 0 | 0 | 2 | 0 |
| 4B: rate10 + COMBO rep | 10e6 | H52_NULL_COMBO=1 | 70 | 0 | 0 | 2 | 0 |

*Clarification: The `H60 banner` column counts `[FRAME_EQ] IEEE80211_H52_NULL_INTERP=1` / `H52_NULL_COMBO=1` startup banner occurrences only, NOT actual `[H60_NULL]` event lines. Phase 62 ran with the default `dump=OFF` for H52 null diagnostics, so no `[H60_NULL]` event lines were ever logged in any of the 5 conditions — the value=2 in conditions 3-5 reflects two startup banners (one per `frame_equalizer` instantiation in the flowgraph).*

**Key finding**: All 5 conditions produce identical Sent=70 / OK=0 /
FAIL=0 trajectories across all 35 seconds. The `H60 banner`=2 in
conditions 3-5 are startup banner occurrences (do NOT indicate the
pre-clean produced an FCS_OK result). They appear because two
`frame_equalizer` blocks are instantiated in the flowgraph when the env
var is ON, and each instantiation logs the `[FRAME_EQ]
IEEE80211_H52_NULL_INTERP=1` (or `H52_NULL_COMBO=1`) banner. Conditions
1-2 (no env var) produce `H60 banner`=0 because the env-var-gated
banner is not printed. The Phase 62 runs used default `dump=OFF` for
the H52 null diagnostic, so no `[H60_NULL]` event lines were ever
emitted — this column cannot be compared directly to Phase 60's
H60_NULL=8 (which used `dump=ON` and counted actual call-site fires).

CV analysis (Phase 58 method): N_avg=0 across all 5 conditions → CV
**undefined**. The avg_snr dump never fires because the RX chain stalls
at L-SIG viterbi upstream of HT_SIG_EQ, where the avg_snr dump lives.

LSIG_DECODE OK events: **0 across all 5 conditions**.
HT_SIG_CAND events: **0 across all 5 conditions**.
Final FCS line in all 5 logs: `Final: FCS OK=0 FAIL=0`.

### Per-second Sent Trajectory

All 5 conditions produce identical Sent progression: starts at t=10s
(S=20), increments by +2 each second until t=35s (S=70). This is the
2 frames/sec cadence of `test_usrp_tdd_ratematch.py`. The TX path is
healthy across all conditions — the failure is downstream of TX.

### Phase 60 verdict NOT REPRODUCED on test_usrp_tdd_ratematch.py

Phase 60 verdict (commits `70e84a1`, `9771fb1`) claimed
`IEEE80211_H52_NULL_INTERP=1` produced H60_NULL=8 events and HT_SIG_CAND
jumped 0→32 on the same env vars at `--rate 20`. That Phase 60 run used
the project-root script `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py`
(NOT `examples/test_usrp_tdd_ratematch.py`) and ran with
`IEEE80211_H52_NULL_INTERP=1 ... dump=ON`, so the H60_NULL=8 figure
counts actual `[H60_NULL]` event lines.

Phase 62 Task 4 ran the same env vars (`H52_NULL_INTERP=1` at `--rate 10`)
on `examples/test_usrp_tdd_ratematch.py` and observed HT_SIG_CAND=0
and FCS_OK=0 — crucially zero across all 5 conditions. The Phase 62
runs used default `dump=OFF` for H52 null diagnostics, so no
`[H60_NULL]` event lines were emitted at all in any condition.

This implies:
- (a) Phase 60 used `test_usrp_minimal_loopback.py` (project root), NOT
      `examples/test_usrp_tdd_ratematch.py`. The two scripts route
      through different RX paths. Per the project's HARD CONSTRAINT,
      Phase 63 must re-run Phase 62 conditions on the project-root
      script Phase 60 actually used.
- (b) Phase 62 ran with `dump=OFF` (default), so its banner-only
      column cannot be compared numerically to Phase 60's H60_NULL=8.
- (c) test_usrp_tdd_ratematch.py is the wrong test script for Phase 60
      metrics and should not be used for HARD CONSTRAINT validation of
      the pre-clean claim.

### LLTF_OFFSET_CORRECT=14 silently clamped to 4

The project's standard USRP test config (CLAUDE.md) specifies
`IEEE80211_LLTF_OFFSET_CORRECT=14`. But `lib/ht_symbol_splitter_impl.cc:111-113`
clamps K to `[-4, +4]`. Both Phase 62 Task 2 and Task 3 logs show
`[SPLITTER] IEEE80211_LLTF_OFFSET_CORRECT=4 (L-LTF0 offset shifted by 4 samples)`.
The "=14" in the standard config is a no-op.

Either the code's K clamp should be lifted to ≥14, OR CLAUDE.md should
be updated to `=4`. Without resolving this, all "standard config" runs
are silently using 4 not 14. Per Phase 33, 14-sample shift is the true
optimum — so the recommended fix is to lift the clamp (not to remove
the env var from CLAUDE.md).

## Verdict: BLOCKED — upstream test-script gate + LLTF clamp bug

Phase 62 was a measurement pass on `test_usrp_tdd_ratematch.py`. The 5
condition matrices (--rate/--env_var) all produced the same metrics:
Sent=70, OK=0, FAIL=0, LSIG_DECODE=0, HT_SIG_CAND=0. NO equalizer-side
fix or UHD rate change can be validated when the RX chain stalls at
L-SIG viterbi before any HT_SIG_EQ event fires.

**Three concrete upstream issues block Phase 62 conditions:**

1. **Test script mismatch**: Phase 60 verdict validated `IEEE80211_H52_NULL_INTERP=1`
   on `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` (project
   root), reaching H60_NULL=8 / HT_SIG_CAND=32 with `dump=ON`. Phase 62
   used `examples/test_usrp_tdd_ratematch.py` and saw H60_NULL banner=2
   (no event lines, `dump=OFF`) / HT_SIG_CAND=0 / FCS_OK=0. The two test
   scripts route through different RX paths. The HARD CONSTRAINT requires
   real-time USRP validation, so Phase 63 must use the project-root
   `test_usrp_minimal_loopback.py` script Phase 60 actually used, with
   `IEEE80211_H52_NULL_DUMP=1` to capture actual `[H60_NULL]` event
   lines.

2. **LLTF_OFFSET_CORRECT silently clamped**: The standard config's `=14`
   never takes effect due to code clamp at K ∈ [-4, +4]. Either lift the
   code clamp or update CLAUDE.md to `=4`. Until this is resolved,
   standard USRP runs are not what they appear to be.

3. **Phase 60's call site may not exist in test_usrp_tdd_ratematch.py's RX path**:
   The H60_NULL log fires from a specific call site in
   `frame_equalizer_impl.cc:4413`. Phase 60 used
   `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` (project
   root), which has a different RX path than
   `examples/test_usrp_tdd_ratematch.py`. If the call site is not
   reached on `test_usrp_tdd_ratematch.py`'s RX path, no env var
   toggle will produce HT_SIG_CAND events. Phase 63 must validate the
   call site is reached on the project-root script AND that downstream
   viterbi fires, with `IEEE80211_H52_NULL_DUMP=1` set so `[H60_NULL]`
   event lines actually log.

## What This Validates

- The CLI args `--rate`, `--duration`, `--freq`, `--tx-gain` added to
  `test_usrp_tdd_ratematch.py` are functional and produce the expected
  per-second Sent cadence (70 frames over 35s = 2/s).
- All 5 USRP conditions (no env, INTERP, COMBO ×2) produce **identical
  zero metrics** on this test script — there is no detectable SNR/coupling
  effect on `test_usrp_tdd_ratematch.py` at the L-SIG viterbi gate.
- The LLTF_OFFSET_CORRECT clamp at lines 111-113 of
  `ht_symbol_splitter_impl.cc` silently caps all standard config runs.

## What This Does NOT Validate

- USRP realtime `FCS_OK ≥ Sent/N` (still 0 across all 5 conditions)
- avg_snr recovery at `--rate 10` (Phase 56 hypothesis not testable because
  RX stalls before HT_SIG_EQ)
- Phase 60 H60_NULL=8 / HT_SIG_CAND=32 behavior (NOT REPRODUCED on
  `test_usrp_tdd_ratematch.py`; behavior was observed on the
  project-root `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py`
  with `IEEE80211_H52_NULL_DUMP=1`)
- Phase 35 per-symbol pilot CPE (combo env var confirmed ON but call
  site downstream of L-SIG viterbi unreachable)

## Implications

- **Standard USRP test config in CLAUDE.md is partially broken**:
  `IEEE80211_LLTF_OFFSET_CORRECT=14` should be `=4` (matches actual
  code behavior) OR the code clamp should be lifted to ≥14.
- **Phase 60/61 env vars remain opt-in** — no promotion yet (no observable
  FCS_OK on this test script at any --rate/env combination).
- **`test_usrp_tdd_ratematch.py` is the wrong test script for HARD
  CONSTRAINT validation of the Phase 60/61 pre-clean claim** — Phase 60
  used `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` (project
  root) with `IEEE80211_H52_NULL_DUMP=1`. Phase 63 must re-run Phase 62
  conditions on that project-root script (with `dump=ON`) to validate
  the upstream-attack.
- **Phase 55 SNR instability hypothesis remains in force** —
  realtime avg_snr cannot be trusted. The off-line median from
  Phase 55 (10.4 dB) remains the ceiling until UHD streaming is fixed.

## Files

- /tmp/p62_rate20_baseline.log (255 MB)
- /tmp/p62_rate10_baseline.log (652 MB)
- /tmp/p62_rate10_interp.log (618 MB)
- /tmp/p62_rate10_combo.log (652 MB)
- /tmp/p62_rate10_combo_2nd.log (623 MB)
- /tmp/p62_metrics_summary.txt (~8 KB, contains all 3 sections)
- /tmp/p62_avg_snr_extraction.txt
- /tmp/p62_cv_calc.py
- /tmp/p62_cv_output.txt
- /tmp/p62_sent_trajectory.py
- /tmp/p62_sent_trajectory.txt
- examples/test_usrp_tdd_ratematch.py (CLI args added in Task 1)
- docs/superpowers/notes/2026-06-30-phase62-rate10-combo-verdict.md (this file)

## Phase 63 candidates (HARD CONSTRAINT upstream-attack)

Per CLAUDE.md HARD CONSTRAINT, the next phase MUST attack upstream of
the L-SIG viterbi block. Three concrete candidates (must investigate
ALL, not pick one):

1. **Identify and use Phase 60's test script** (PRIORITY — do FIRST).
   Phase 60 used the project-root script
   `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` (NOT
   `examples/test_usrp_tdd_ratematch.py`), as evidenced by Phase 60's
   `/tmp/p60_e2e.log` startup banner showing
   `[FRAME_EQ] IEEE80211_H52_NULL_INTERP=1 (..., dump=ON)`. Phase 63
   must:
   - Re-run all 5 Phase 62 conditions on
     `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` (the
     project-root script).
   - Set `IEEE80211_H52_NULL_DUMP=1` env var to enable `[H60_NULL]`
     event logging. Phase 60 used `dump=ON`; Phase 62 used default
     `dump=OFF` and captured no event lines, which is why its
     `H60 banner` column shows only startup banners, not actual
     call-site fires.
   - Compare the resulting H60_NULL event counts and HT_SIG_CAND
     counts to Phase 60's H60_NULL=8 / HT_SIG_CAND=32 baseline.

2. **Fix LLTF_OFFSET_CORRECT clamp**.
   `lib/ht_symbol_splitter_impl.cc:111-113` clamps K to ±4. Either:
   - (a) Lift the clamp to ±16 (covers Phase 33's 14-sample shift + some margin)
   - (b) Update CLAUDE.md and standard config to `=4` (matches actual code)
   Recommend (a) — Phase 33 found 14-sample shift is the true optimum.
   This requires code change, C++ rebuild, `make install`. Verify
   after rebuild by re-running Phase 62 Condition 1 and confirming
   `[SPLITTER] IEEE80211_LLTF_OFFSET_CORRECT=14` actually appears in
   the log (not `=4`).

3. **Validate H60_NULL call site reachability**.
   Add a debug log line at the ungated call site
   `frame_equalizer_impl.cc:4413` (Phase 60 fix) that fires on EVERY
   frame, not gated on `d_is_ht` or `use_direct_tx_order`. Set
   `IEEE80211_H52_NULL_DUMP=1` env var so `[H60_NULL]` event lines
   actually log (Phase 60 used `dump=ON`; default is `dump=OFF`).
   Run a single 35s test, count "calls per frame". If count >> Sent N
   (e.g. 4x to 8x), the call site is reachable AND downstream viterbi
   should be able to use pre-cleaned H. If count ≈ Sent N (1x), the
   call site fires exactly once per frame and any downstream progress
   is independent of pre-clean. If count = 0, there is a deeper
   upstream gate.

**Critical Phase 63 prioritization**: Do (1) FIRST (read Phase 60
verdict end-to-end to find the test script). If that script is NOT
`test_usrp_tdd_ratematch.py`, redo Phase 62 conditions on the Phase 60
script before any other work. Only after (1) is resolved should (2)
be attempted, then (3).

## Lessons Learned

1. **The test script matters**: Phase 60 worked on one test script,
   Phase 62 with a different script sees no FCS_OK events. Before
   assuming an algorithm works, identify which test script reaches the
   metric. The HARD CONSTRAINT says "USRP realtime end-to-end" — that
   means a specific test script must be used, not "any" test script.

2. **Standard config can be silently no-op**: LLTF_OFFSET_CORRECT=14
   was silently clamped to 4 by the code. CLAUDE.md standard config
   should be self-consistent with code (or the code should be brought
   in line). Always spot-check the `[SPLITTER]` log line at run start.

3. **Per Phase 55 closure**, realtime avg_snr is unreliable. Phase 62
   even at `--rate 10` cannot extract avg_snr because the RX chain stalls
   upstream of HT_SIG_EQ. The off-line median from Phase 55 (10.4 dB)
   remains the ceiling until upstream gates are resolved.

4. **The `H60_NULL` metric has TWO distinct meanings depending on `dump`
   state**, and the column header in the comparison table confused them:
   - With `IEEE80211_H52_NULL_DUMP=1` (Phase 60 default): `[H60_NULL]`
     event lines are emitted per L-LTF observe, counting actual call-site
     fires. Phase 60 saw H60_NULL=8 on
     `/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py` with
     `dump=ON`.
   - With default `dump=OFF` (Phase 62 default): NO `[H60_NULL]` event
     lines are emitted. The "H60_NULL=2" in Phase 62's table counts only
     startup banner occurrences (`[FRAME_EQ]
     IEEE80211_H52_NULL_INTERP=1`), not call-site fires. The two
     columns are NOT directly comparable.
   - Future phases must report both the dump state and the event count
     explicitly. The column header was misleading; fixed to `H60 banner`
     in this verdict to clarify it counts banner occurrences only.
