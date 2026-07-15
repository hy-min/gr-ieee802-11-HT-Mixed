# Phase 148 Verdict: Trustworthy FCS Funnel — Non-Determinism ROOT-CAUSED (fix decision pending)

**Date:** 2026-07-15
**Branch:** TEST1
**Status:** Measurement tooling built + reviewed; non-determinism **root-caused to a
chunk-partition-dependent `sync_long` detection bug** (fixable, non-trivial). The
"fix-vs-statistics" decision is deferred to the user (see Decision Point below).

---

## What was built (committed)

| File | Commit | Purpose |
|---|---|---|
| `p148_parse.py` | `c9bb10c` | stderr → per-stage funnel counts (7 stages), **correct distinct-terminal-frame counting** |
| `p148_stats.py` | `ec79aaf` + `8777b7a` | N-run aggregator + determinism test (exit 0=PASS/1=FAIL/2=harness-crash) |
| `p148_funnel.py` | **untracked** (deliberate) | `p147_replay_funnel.py` + drain phase |

Reviews: spec-compliance ✅; code-quality ✅ (T1+T2, fixes folded in at `8777b7a`).

## Counting-semantics fix (real)

Old "per-frame FCS = SUCCESS/(SUCCESS+FAIL)" over-counted the denominator: each failed
frame prints BOTH a `Conv FCS error` and an `LDPC FCS error` line. Counting only terminal
`LDPC FCS error` gives the real per-frame rate ≈ **13/(13+9) = 59%**, not the 42% previously quoted.

## The non-determinism — root cause (this is the important part)

**Claim (evidence-backed): identical replay runs give different decode counts because of a
chunk-partition-dependent bug in `sync_long` — NOT irreducible signal noise, and NOT only
a shutdown/drain race.**

Evidence chain:
1. Input `/tmp/p146_rxonly_cap.fc32` is byte-identical across runs (static file) — noise is frozen.
2. Decoder (`lib/decode_mac.cc`) has **no RNG** — deterministic given identical samples + chunk boundaries.
3. Yet `sync_long` wifi_start **offsets move run-to-run** (md5 differs), even under:
   - single-threaded scheduler `GR_SCHEDULER=STS` (498 vs 493 offsets), and
   - `IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=0`.
   → the source is NOT only the scheduler, and NOT the adaptive-threshold window.
4. **Mechanism (confirmed in source):** `lib/sync_long.cc:192` sets `set_output_multiple(80)`
   (a deadlock fix, required). GNU Radio delivers **variable-sized** chunks that are multiples
   of 80 → `ninput` varies per `general_work` → `filter_len = min(SYNC_LENGTH, max(ninput-63,0))`
   (line 337) varies → the correlation `d_cor` accumulates over a **variable** window →
   different argmax peak → different `d_frame_start` → different L-LTF window → different
   H52 → HT-SIG viterbi swings → `decoded` swings (cv ≈ 0.10–0.19).

**Two earlier hypotheses REFUTED:**
- *Drain/shutdown race* (the plan's original premise): drain phase added; helps marginally
  (no truncated frames) but did NOT remove the variance → not the root cause.
- *Irreducible signal noise* (Task 4's first conclusion): REFUTED by an independent
  adversarial review — identical input + no RNG + moving detection offsets = deterministic-but-chunk-dependent.

## Determinism-test results (N=5 unless noted)

| Config | `decoded` cv | note |
|---|---|---|
| `p147_replay_funnel.py` (no drain) | 0.100 | RED baseline |
| `p148_funnel.py` (drain) | 0.101 | drain alone insufficient |
| `GR_SCHEDULER=STS` | 0.192 | single-thread does NOT fix (different partition) |
| `ADAPTIVE_THRESH=0` | 0.134 | threshold window NOT the source |
| baseline re-run | 0.151 | operating point itself drifts |

Also: `fcs_fail` "constant at 4" (Task 3) did NOT reproduce — small-N coincidence.

## Decision Point (USER INPUT REQUIRED)

Two ways to get a trustworthy ruler for measuring arrival-rate improvements:

**Option A — fix the chunking bug (deterministic ruler).** Make `sync_long` detection
chunk-invariant (e.g. accumulate the correlation over a FIXED 64-sample-aligned window
independent of `ninput`, or buffer to a fixed processing quantum). Pros: cv→~0, N≈3–5 runs
suffice, and it's a genuine decoder-robustness improvement that likely transfers to USRP
realtime. Cons: C++ change + `make && make install` + regression, and MUST preserve the
`set_output_multiple(80)` deadlock fix (lib/sync_long.cc:185-191) and keep loopback 3/3 PASS.
Estimated: medium effort, some deadlock-regression risk.

**Option B — statistical ruler (no C++ change).** Accept cv≈0.10–0.15, report mean±std over
N runs. Sizing (unpaired, power 0.8, α=0.05): to detect a +10% arrival change needs
**N≈16–27 per config** (cv 0.10→0.13), up to ~57 at cv 0.19. Pros: zero code risk, works now.
Cons: each measurement = 16–27 runs × ~12 s ≈ 3–6 min; marginal frames stay noisy.

## Recommendation

**Option A** if we intend to iterate on arrival-rate improvements (a deterministic ruler pays
for itself after ~2–3 measurements and doubles as a decoder fix). **Option B** to defer the C++
work and proceed statistically now. Both preserve the project goal; neither touches the 1.77 rad
per-frame ceiling (that's the *next* bottleneck after arrival is measurable).

## Harnesses

- `p148_parse.py`, `p148_stats.py`, `p148_funnel.py` — offline funnel measurement.
- Run convention: `unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/bindings:python:examples /home/hy/conda/envs/gnuradio/bin/python <harness>`
- Example: `python3 p148_stats.py --harness p148_funnel.py --runs 10`

## Related

- Plan: `docs/superpowers/plans/2026-07-15-phase148-trustworthy-funnel.md`
- Phase 147 (static-buffer race — a *different*, now-fixed, non-determinism source): `docs/superpowers/notes/2026-07-15-phase147-sync-short-race-fix-verdict.md`
- Root-cause method note: independent adversarial review was required to overturn an initial
  "irreducible noise" mis-conclusion — consistent with retrospective lesson "root cause before fix".
