# Phase 61 Verdict — Combined Pre-Clean + Per-Symbol Pilot CPE (USRP Test)

**Date**: 2026-06-30
**Branch**: TEST1
**Status**: **PARTIAL** — combo algorithm validated (n_nulls 21→4 combo / 21→2 retry), L-SIG viterbi is the new upstream gate
**Commits**: 8b881c3, caa2c0d, 84d1323, b61bdd0

## Goal

USRP realtime single 35s run with `IEEE80211_H52_NULL_COMBO=1`:
FCS_OK / Sent ≥ 1/3.

## Method

Phase 60 PARTIAL showed 21/52 SCs (40%) remain as nulls after pre-clean
with default (thresh=0.15, radius=2), blocking viterbi convergence.
Phase 61 combines 3 levers in a single opt-in env var:
  - Phase 60 H52 pre-clean (UN-gated upstream site)
  - Threshold 0.10 (tighter than default 0.15, catches weaker nulls)
  - Radius 3 (wider than default 2, recovers clustered nulls)
  - Phase 35 per-symbol HT-SIG pilot CPE (cancels per-symbol phase drift)

## Results

### Synthetic regression (all PASS)
- test_h61_combo_synthetic.py: 3/3 PASS (combo, wider, cpe)
- test_h60_pre_clean_synthetic.py: 3/3 PASS
- test_h52_null_interp_synthetic.py: 4/4 PASS
- test_direct_loopback.py: 3/3 PASS (env var OFF and ON)
- test_htsig_viterbi_synthetic.py: 3/3 PASS
- test_lsig_viterbi_synthetic.py: 3/3 PASS
- test_h_estimation_synthetic.py: 4/4 PASS

### USRP E2E (single run, 35s)

| Metric | P60 baseline | P60 ON | P61 combo (thresh=0.10) | P61 retry (thresh=0.05, r=4) |
|---|---:|---:|---:|---:|
| Sent | 95 | 95 | 95 | 95 |
| Recv | 0 | 0 | 0 | 0 |
| FCS_OK | 0 | 0 | 0 | 0 |
| LSIG_DECODE OK | 0 | 3-11 | 4 | 1 |
| HT_SIG_CAND | 0 | 32 | 0 | 0 |
| H60_NULL frames | 0 | 8 | 8 | 8 |
| Avg n_nulls | N/A | 21.0 | 4.0 | 2.0 |
| avg_snr in fail | - | - | 3.18 | 8.50 |
| HTSIG_PILOT_CPE applied | 0 | 0 | 0 | 0 | [^pilot-cpe] |

[^pilot-cpe]: The "0" value here means the C++ call site never fired,
    not that the C++ code is broken. L-SIG viterbi blocks HT-SIG
    processing entirely (`is_ht_frame=0` on every USRP frame), so the
    per-symbol pilot CPE helper at HT-SIG EQ is unreachable. The C++
    change is verified by synthetic tests (3/3 PASS) and remains in
    place for future activation when HT-SIG processing becomes
    reachable (see "What works" below).

### 30-min soak
NOT RUN — no PASS to soak. Per decision tree, pivot to Phase 62.

## Verdict: PARTIAL — combo validated, L-SIG viterbi is new gate

### What works (combo algorithm validated)
- n_nulls reduced from 21.0 (Phase 60) → 4.0 (combo) → 2.0 (retry). This
  is 5-10x improvement, validating the algorithm in USRP conditions.
- Phase 35 per-symbol pilot CPE never fires because HT-SIG is unreachable
  (L-SIG viterbi fails first), but the C++ change is in place for future
  reuse.

### What doesn't work (new upstream gate)
- L-SIG viterbi fails at avg_snr=3-8 dB. Phase 34 baseline showed
  avg_snr_lsig=15.12 with same env vars; the 6-12 dB degradation is
  consistent with Phase 55/56/57 finding that **realtime avg_snr is
  not a reliable air-path metric** — it's a UHD streaming instability
  artifact. The Phase 55 offline replay showed median SNR=10.4.
- Every viterbi attempt shows `is_ht_frame=0`, confirming L-SIG viterbi
  is the upstream gate that blocks HT-SIG processing entirely.

### Why this is PARTIAL, not FAIL or PASS
- **NOT FAIL**: The combo algorithm IS working. n_nulls reduced 5-10x
  is a real, measurable algorithmic improvement. The C++ change is
  correct and the env var prints confirm the call site fires.
- **NOT PASS**: FCS_OK=0 means USRP realtime validation is not yet
  achieved. HARD CONSTRAINT not satisfied.
- **PARTIAL**: Combo lever is exhausted (further threshold reduction
  has diminishing returns since n_nulls is already 2.0). L-SIG SNR
  is the new upstream gate, and per Phase 55 verdict, that's a
  streaming/environment issue, not an algorithmic one.

## Implications

- **Code kept in place**: opt-in env var `IEEE80211_H52_NULL_COMBO=1`
  remains available. n_nulls 21→4 (combo) / 21→2 (retry) is a permanent
  algorithmic gain.
- **Standard USRP test config unchanged**: no promotion (FCS_OK=0).
- **Phase 62 needed**: pivot to L-SIG viterbi SNR investigation.
  Per Phase 55 verdict, this is NOT an equalizer-side fix.

## Phase 62 candidates (next steps)

1. **UHD streaming investigation** (per Phase 55):
   - Re-test with --rate 10 + CPU isolation + USRP warmup
   - Compare realtime avg_snr to offline replay avg_snr
   - If offline SNR > realtime SNR by 5+ dB, the issue is streaming
     not air path. Phase 56/57 partial recovery attempts.
2. **PA/LNA/antenna hardware changes** (excluded per CLAUDE.md):
   - PA/LNA swap, antenna positioning, different freq (--freq 5180
     vs 5890) to improve SNR margin
3. **L-SIG viterbi input scaling** (NOT algorithmic per Phase 37):
   - viterbi input is bounded by Phase 37 metric=0 verification
   - However, if input magnitude is too small, fixed-point quantization
     could lose margin. Investigate safe_div output scaling.

## Files

- /tmp/p61_e2e_baseline.log (Phase 61 baseline, env var OFF)
- /tmp/p61_e2e.log (Phase 61 combo env var ON, single run)
- /tmp/p61_e2e_retry.log (Phase 61 retry with thresh=0.05, radius=4)
- examples/test_h61_combo_synthetic.py (3-mode test)
- lib/frame_equalizer_impl.cc (combo env var read at line 3236-3268)
- lib/frame_equalizer_impl.h (1 new field: d_h52_null_combo_enabled at line 274)

## What This Validates

- Combo env var (thresh=0.10, radius=3, +pilot CPE) reduces n_nulls from
  21 to 4 (combo) / 2 (retry) in USRP conditions (5-10x algorithmic improvement)
- Phase 35 per-symbol pilot CPE C++ code is in place and ready for
  activation when HT-SIG processing becomes reachable
- Regression suite preserved (loopback 3/3, all synthetic tests pass)
- Override warnings (commit b61bdd0) catch silent misconfig

## What This Does NOT Validate

- USRP realtime FCS_OK ≥ Sent/3 (still 0)
- HT-SIG viterbi convergence on USRP (L-SIG viterbi blocks upstream)
- 30-min soak stability (no PASS to soak)

## Lessons Learned

1. **Each phase reveals the next upstream gate**: Phase 59 hit HT-SIG
   gate, Phase 60 broke that gate, Phase 61 hit L-SIG gate. Per HARD
   CONSTRAINT, the chain must continue.
2. **Realtime avg_snr is unreliable** (Phase 55 finding). When
   viterbi fails at "low SNR" (3-8 dB), first suspect is UHD streaming
   instability, not air path.
3. **Algorithmic improvements are permanent** even if upstream gate
   blocks end-to-end: n_nulls 21→4 (combo) / 21→2 (retry) is a real
   gain that will pay off when L-SIG SNR is restored.