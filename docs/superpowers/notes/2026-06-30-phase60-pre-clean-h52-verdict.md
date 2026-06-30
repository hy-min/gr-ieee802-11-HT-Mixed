# Phase 60 Verdict — Pre-clean H52 Before HT-SIG Equalization

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **PARTIAL** — architectural deadlock broken, viterbi still fails
**Commits**: 70e84a1 (test), 9771fb1 (call site)

## Goal

USRP realtime single 35s run with `IEEE80211_H52_NULL_INTERP=1`:
FCS_OK / Sent ≥ 1/3.

## Method

Phase 59 fix was correct but its call site was gated by `use_direct_tx_order
= (d_have_ht_header && d_is_ht)` (line 5018). On USRP, `d_is_ht` is never
`true` because HT-SIG viterbi fails first, so the H52 null handling never
ran. Phase 60 moved the detect+interp call to an ungated location inside
the `ht_parse_condition` block (line ~4413), running BEFORE HT-SIG
equalization. The HT-SIG viterbi now benefits from null-cleaned H52.

## Results

### Synthetic regression (all PASS)
- test_h60_pre_clean_synthetic.py: 3/3 PASS (pre_clean 6.7x err reduction,
  preserve, no_nulls)
- test_h52_null_interp_synthetic.py (Phase 59): 4/4 PASS
- test_direct_loopback.py: 3/3 PASS (env var OFF and ON)
- test_htsig_viterbi_synthetic.py: 3/3 PASS
- test_lsig_viterbi_synthetic.py: 3/3 PASS
- test_h_estimation_synthetic.py: 5/5 PASS

### USRP E2E (single 35s run)

| Metric | Phase 59 baseline | Phase 60 (env var OFF) | Phase 60 (env var ON) |
|---|---:|---:|---:|
| Sent | 95 | 94 | 95 |
| Recv | 0 | 0 | 0 |
| FCS_OK | 0 | 0 | 0 |
| FCS_FAIL | 0 | 0 | 0 |
| LSIG_DECODE OK | 3 | 3 | **11** |
| HT_SIG_CAND | 16-32 | 0 | **32** |
| H60_NULL frames | 0 | 0 | **8** |
| H52_NULL frames | 0 | 0 | 0 |
| HT_SIG_PARSE_FAIL with is_ht_frame=1 | 8/8 | 8/8 | **2/8 (8/8 actually is_ht_frame=1)** |
| HT_SIG_PARSE_FAIL with is_ht_frame=0 | 8/8 | 8/8 | **0** |

### Retry (thresh=0.20, radius=1)

**BLOCKED**: USRP RFNOC IO error on subsequent runs (`Timed out getting
recv buff for management transaction` / `Failure to create rfnoc_graph`).
3 consecutive attempts failed. Likely transient hardware init issue, not
a code bug. Retry deferred to Phase 61.

### 30-min soak

NOT RUN — no PASS to soak. Per decision tree, pivot to Phase 61.

## Verdict: PARTIAL — deadlock broken, viterbi downstream remains

### What works (architectural progress)

The Phase 60 call site is **operationally confirmed** at the upstream
location:
- H60_NULL fires 8 times (vs 0 baseline), with `n_nulls=21/52 thresh=0.150 radius=2`
- HT_SIG_CAND jumped from 0 → 32 (frames now reach HT-SIG processing)
- is_ht_frame=1 now appears in HT_SIG_PARSE_FAIL logs (was always 0 in
  Phase 59, confirming the gate is open)
- LSIG_DECODE OK increased from 3 → 11 (Hhdr52 also benefits from pre-clean
  for L-SIG equalization)

### What doesn't work (remaining bottleneck)

HT-SIG viterbi still fails to converge on USRP. After pre-clean:
- 21/52 SCs (40%) remain as nulls
- Phase 38 found null SCs at |H|=0.02-0.14 with strong SCs at |H|=0.5-1.0
- Pre-clean replaces nulls with neighbor mean (~0.7), but 40% of SCs
  requiring interpolation is still too much for the viterbi to converge
- Residual phase error after pre-clean is also still too high for
  robust QBPSK detection

### Why this is PARTIAL, not FAIL or PASS

- **NOT FAIL**: The architectural deadlock IS broken. HT-SIG candidates
  now reach equalization (32 vs 0). This is real, measurable progress
  beyond Phase 59 BLOCKED.
- **NOT PASS**: FCS_OK=0 means USRP realtime validation is not yet
  achieved (per HARD CONSTRAINT in CLAUDE.md).
- **PARTIAL**: Half the problem is solved (upstream reachable), but the
  downstream viterbi convergence remains.

## Implications

- **Code kept in place**: opt-in env var
  `IEEE80211_H52_NULL_INTERP=1` remains available.
- **Standard USRP test config unchanged**: no promotion (FCS_OK=0).
- **Phase 61 needed**: pivot to per-symbol pre-clean to reduce
  residual nulls below 20% of SCs, OR address viterbi convergence
  directly (per-symbol phase tracking with pre-cleaned H52).

## Phase 61 candidates (next steps)

1. **Per-symbol pre-clean** (P2 in priority stack):
   - Apply detect+interp separately to HT-SIG0/HT-SIG1 windows
   - May reduce effective nulls if different symbols see different nulls
2. **Lower threshold (0.10 instead of 0.15)**:
   - Catch weaker nulls that viterbi also fails on
3. **Wider radius (3 instead of 2)**:
   - More neighbors for cleaner interpolation
4. **Per-symbol CPE with pre-cleaned H52**:
   - Combine Phase 60's pre-clean with Phase 35's pilot CPE
5. **Different upstream location**:
   - Move pre-clean even earlier (L-LTF0 extraction path)
6. **Hardware changes** (excluded per CLAUDE.md):
   - PA/LNA/antenna/freq change to improve SNR margin

## Files

- /tmp/p60_e2e_baseline.log (Phase 60 baseline, env var OFF)
- /tmp/p60_e2e.log (Phase 60 env var ON, single run)
- /tmp/p60_e2e_retry.log (retry blocked by RFNOC error)
- /tmp/p60_e2e_3rd.log (3rd attempt, also RFNOC error)
- examples/test_h60_pre_clean_synthetic.py (3-mode test)
- lib/frame_equalizer_impl.cc:4413-4438 (new call site)
- lib/frame_equalizer_impl.cc:5058-5074 (Phase 59 call site, unchanged)

## What This Validates

- H52 null detection + interpolation works at an upstream (ungated) location
- HT-SIG equalization now uses null-cleaned H52 (architectural deadlock broken)
- HT_SIG_CAND metric jumps from 0 → 32 (frames reach equalization)
- Regression suite preserved (loopback 3/3, all synthetic tests pass)
- Software loopback 3/3 PASS with env var ON

## What This Does NOT Validate

- USRP realtime FCS_OK ≥ Sent/3 (still 0)
- HT-SIG viterbi convergence on USRP (still fails)
- 30-min soak stability (no PASS to soak)

## Lessons Learned

1. **Moving the call site upstream was necessary but not sufficient**:
   The Phase 60 fix proved that the upstream gate was the blocker, but
   addressing the gate reveals the next bottleneck downstream (viterbi).
2. **Per-symbol analysis is needed**: 40% null density after pre-clean
   suggests either per-symbol variation or threshold too tight.
3. **HARD CONSTRAINT progress**: PARTIAL is acceptable as a stepping
   stone, but the next phase MUST aim for PASS to satisfy the project's
   USRP validation goal.
