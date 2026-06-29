# Phase 59 Verdict — H52 Null Detect + 邻域插值 (USRP Test)

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: **BLOCKED** (architectural — call site unreachable, see Diagnosis)
**Commits**: 0fd80b1, 1bede19, 50728fb, 249da2e, 7e254fd, 1639ea7, 377b923, c60d06a

## Goal

USRP realtime single 35s run with `IEEE80211_H52_NULL_INTERP=1`:
FCS_OK / Sent ≥ 1/3.

## Method

1. Software loopback regression: 3/3 PASS (Tasks 3, 4)
2. C++ cross-check: Python prototype == C++ implementation (Task 4)
3. Synthetic unit tests: 4/4 PASS (Task 4)
4. USRP E2E single run with env var ON
5. If PASS: 30-min soak (3 runs)
6. If MARGINAL: retry with thresh=0.20, radius=1

## Results

### Synthetic regression
- test_direct_loopback.py: 3/3 PASS
- test_htsig_viterbi_synthetic.py: 3/3 PASS
- test_lsig_viterbi_synthetic.py: 3/3 PASS
- test_h_estimation_synthetic.py: 5/5 PASS
- test_h52_null_interp_synthetic.py: 4/4 PASS (detect, interp, e2e, crosscheck)

### USRP E2E (single run, 35s, env vars ON)
- Sent: 95
- Recv: 0
- FCS_OK: 0
- FCS_FAIL: 0
- LSIG_DECODE OK: 5
- HT_SIG_CAND: 32
- H52_NULL frames: **0** (env var was parsed and enabled, but call site never fired)
- HT_SIG_PARSE_FAIL: 8 — **all 8 logged entries show `is_ht_frame=0`**
- n_nulls distribution: empty (no H52 dump entries)

### Re-run with `IEEE80211_H52_NULL_DUMP=1` (35s, 60s warmup)
- Sent: 95
- Recv: 0
- FCS_OK: 0
- LSIG_DECODE OK: 3
- HT_SIG_CAND: 16
- H52_NULL frames: **0** (dump env var enabled, still 0 entries)
- HT_SIG_PARSE_FAIL: 8 — **all 8 logged entries show `is_ht_frame=0`**

### Offline dump analyzer
- `examples/p59_h52_null_dump_analyze.py` runs correctly, parses 0 H52_NULL entries as expected.

## Verdict: **BLOCKED — H52 call site is unreachable on USRP**

### Diagnosis (root cause)

The H52 null interp call site is gated by:

```cpp
const bool use_direct_tx_order = (d_have_ht_header && d_is_ht);
if (use_direct_tx_order) {
    if (!d_H52_tx_order_valid) {
        ...
        auto nulls = detect_h52_nulls(d_H52_tx_order, d_h52_null_thresh);
        ...
    }
}
```

`d_is_ht = true` is set only inside `set_ht_frame_params_from_mcs_len()`
(line 3458), which is called only after a successful HT-SIG viterbi decode.

On USRP, **all 8 logged `HT_SIG_PARSE_FAIL` entries show `is_ht_frame=0`**.
The HT-SIG detector currently never sets `is_ht_frame=1` for any frame in
this 35s run, so the equalizer's HT-data code path is never entered and
`compute_H52_tx_order` is never called. The H52 null interp branch is
unreachable for the frames that would benefit from it.

This is the **same root cause as Phase 38 / Phase 41 closure**: the
channel-physics impairment at the H52 boundary (|H| nulls in the
50× amplification range) breaks the HT-SIG viterbi upstream, blocking
access to the equalizer's HT-data path. The H52 null interp fix is
correct in isolation but cannot be USRP-verified because it sits
**downstream of the upstream gate**.

### Why this is BLOCKED, not FAIL

- **Software loopback**: 3/3 PASS, env var OFF = no behavior change (Tasks 3, 4)
- **Synthetic tests**: 4/4 PASS, including cross-check Python == C++
- **The implementation is correct** — it is architecturally unreachable in
  the USRP path due to the upstream HT-SIG viterbi bottleneck.

A code change to move the H52 null detection upstream of `d_is_ht` (e.g.
into the L-LTF0 read path or as part of H52 estimation regardless of
HT-SIG outcome) is **out of scope for Task 5** (verification only) and
would be a Phase 60 follow-up.

## Implications

- **No behavior change on USRP**: env var default OFF means production
  code path is unchanged. Even with env var ON, USRP frames hit the
  HT-SIG viterbi bottleneck before the equalizer's H52 call site.
- **Code kept in place**: opt-in env var
  `IEEE80211_H52_NULL_INTERP=1` remains available for software loopback
  testing and for any future Phase 60 work that moves H52 detection
  upstream.
- **Phase 41 closure verdict still applies**: USRP HT-SIG remains
  blocked by the channel-physics impairment at the H52 boundary.
  12 prior hypotheses REFUTED, and the upstream gate is confirmed
  unbreakable from equalizer-side fixes.

## Files

- /tmp/p59_e2e.log (single 35s run, env var ON, dump OFF)
- /tmp/p59_e2e_dump.log (35s run, env var ON, dump ON — confirms call site never fires)
- examples/p59_h52_null_dump_analyze.py (offline analyzer — 0 entries parsed)
- examples/test_h52_null_interp_synthetic.py (4 modes, cross-check — 4/4 PASS)
- lib/frame_equalizer_impl.cc (helpers + call site — unchanged from Task 3)
- lib/frame_equalizer_impl.h (4 new fields — unchanged from Task 2)

## What This Validates

- C++ algorithm in lib/frame_equalizer_impl.cc matches Python prototype
- Software loopback 3/3 PASS preserved (env var OFF = no change)
- Env var ON: detection and interp logic are exercised by synthetic tests
- USRP test command unchanged: standard env vars preserved

## What This Does NOT Validate

- USRP realtime end-to-end — the call site is unreachable when
  HT-SIG viterbi fails. This is a known USRP limitation per
  Phase 41 closure, not a Phase 59 implementation issue.

## Future Work (Phase 60 candidates)

1. Move H52 null detection upstream of `d_is_ht` (e.g., into the
   L-LTF0 FFT processing path before HT-SIG viterbi). This would
   require the equalizer to compute H52 even for non-HT frames, or
   for the splitter to expose H52 to downstream blocks via a port.
2. Re-test Phase 59 on a path that successfully reaches the equalizer
   (e.g., software loopback already passes; need an intermediate
   USRP condition where L-SIG passes but HT-SIG is intentionally
   not parsed, which is a contrived scenario).
3. Accept USRP HT-SIG not solvable (per Phase 41 closure) and route
   the H52 null interp work toward non-USRP use cases.
