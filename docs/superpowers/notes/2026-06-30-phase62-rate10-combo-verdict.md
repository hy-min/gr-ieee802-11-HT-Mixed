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

| Condition | --rate | env var | Sent | OK | FAIL | H60_NULL | avg_snr N |
|---|---|---|---:|---:|---:|---:|---:|
| 1: rate20 baseline | 20e6 | none | 70 | 0 | 0 | 0 | 0 |
| 2: rate10 baseline | 10e6 | none | 70 | 0 | 0 | 0 | 0 |
| 3: rate10 + INTERP | 10e6 | H52_NULL_INTERP=1 | 70 | 0 | 0 | 2 | 0 |
| 4A: rate10 + COMBO | 10e6 | H52_NULL_COMBO=1 | 70 | 0 | 0 | 2 | 0 |
| 4B: rate10 + COMBO rep | 10e6 | H52_NULL_COMBO=1 | 70 | 0 | 0 | 2 | 0 |

**Key finding**: All 5 conditions produce identical Sent=70 / OK=0 /
FAIL=0 trajectories across all 35 seconds. The H60_NULL=2 fires in
conditions 3-5 are diagnostic-call-site probes (do NOT indicate the
pre-clean produced an FCS_OK result). They are an internal Phase 60
debug-counter that fires per L-LTF observe, regardless of whether
viterbi succeeded downstream. Conditions 1-2 (no env var) produce
H60_NULL=0 because the call site is gated on the env var being ON.

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

### Phase 60 verdict REFUTED on test_usrp_tdd_ratematch.py

Phase 60 verdict claimed `IEEE80211_H52_NULL_INTERP=1` produced
H60_NULL=8 events and HT_SIG_CAND jumped 0→32 on the same env vars at
`--rate 20`. Phase 62 Task 4 ran the same env vars (`H52_NULL_INTERP=1`
at `--rate 10`) and observed H60_NULL=2 and HT_SIG_CAND=0 — and crucially
zero FCS_OK across all 5 conditions.

Even if Phase 60's H60_NULL=8 number were accurate (vs Phase 62's 2),
HT_SIG_CAND=0 and FCS_OK=0 across all Phase 62 conditions still shows
the call site does not advance the RX chain to FCS. The Phase 60 claim
that the call site unblocks L-SIG viterbi downstream is **not
reproduced on test_usrp_tdd_ratematch.py**.

This implies either:
- (a) Phase 60 used a different test script than test_usrp_tdd_ratematch.py, OR
- (b) Phase 60's RX chain reached a path that no longer exists in the
      current code (Phase 61 commits `84d1323`, `b61bdd0` modified
      `frame_equalizer_impl.cc` and may have changed reachability), OR
- (c) test_usrp_tdd_ratematch.py is the wrong test script for Phase 60
      metrics and should not be used for HARD CONSTRAINT validation.

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
   on some test script that reached H60_NULL=8 / HT_SIG_CAND=32. Phase 62
   used `test_usrp_tdd_ratematch.py` and saw H60_NULL=2 / HT_SIG_CAND=0 /
   FCS_OK=0. The two test scripts probably route through different RX
   paths. The HARD CONSTRAINT requires real-time USRP validation, so
   Phase 63 must identify and use the same test script Phase 60 used.

2. **LLTF_OFFSET_CORRECT silently clamped**: The standard config's `=14`
   never takes effect due to code clamp at K ∈ [-4, +4]. Either lift the
   code clamp or update CLAUDE.md to `=4`. Until this is resolved,
   standard USRP runs are not what they appear to be.

3. **Phase 60's call site may not exist in test_usrp_tdd_ratematch.py's RX path**:
   The H60_NULL log fires from a specific call site in
   `frame_equalizer_impl.cc:4413`. If this call site is not reached on
   test_usrp_tdd_ratematch.py's RX path, no env var toggle will produce
   HT_SIG_CAND events. Phase 63 must validate the call site is reached
   AND that downstream viterbi fires.

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
- Phase 60 H60_NULL=8 / HT_SIG_CAND=32 behavior (refuted on
  test_usrp_tdd_ratematch.py, may still hold on the script Phase 60 used)
- Phase 35 per-symbol pilot CPE (combo env var confirmed ON but call
  site downstream of L-SIG viterbi unreachable)

## Implications

- **Standard USRP test config in CLAUDE.md is partially broken**:
  `IEEE80211_LLTF_OFFSET_CORRECT=14` should be `=4` (matches actual
  code behavior) OR the code clamp should be lifted to ≥14.
- **Phase 60/61 env vars remain opt-in** — no promotion yet (no observable
  FCS_OK on this test script at any --rate/env combination).
- **`test_usrp_tdd_ratematch.py` may be the wrong test script for HARD
  CONSTRAINT** — Phase 63 must identify what test script Phase 60 used
  and re-run Phase 62 conditions on it.
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

1. **Identify Phase 60's test script** (PRIORITY — do FIRST).
   Read Phase 60 verdict
   `docs/superpowers/notes/2026-06-30-phase60-pre-clean-h52-verdict.md`
   end-to-end. Determine which test script Phase 60 used to observe
   H60_NULL=8 / HT_SIG_CAND=32. If it was NOT `test_usrp_tdd_ratematch.py`,
   redo Phase 62 conditions on the Phase 60 script BEFORE any other
   work. Per Phase 59 prior history, Phase 60 likely used
   `test_usrp_minimal_loopback.py` which has a different RX path.

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
   frame, not gated on `d_is_ht` or `use_direct_tx_order`. Run a single
   35s test, count "calls per frame". If count >> Sent N (e.g. 4x to 8x),
   the call site is reachable AND downstream viterbi should be able to
   use pre-cleaned H. If count ≈ Sent N (1x), the call site fires
   exactly once per frame and any downstream progress is independent
   of pre-clean. If count = 0, there is a deeper upstream gate.

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

4. **H60_NULL is a per-L-LTF diagnostic, not a per-FCS counter**.
   Phase 62 saw H60_NULL=2 in three INTERP/COMBO conditions while
   FCS_OK=0 in all three. So H60_NULL=2 means "the pre-clean call site
   fired 2 times" — not "the pre-clean unblocked 2 frames". The naming
   was misleading; future phases must distinguish.
