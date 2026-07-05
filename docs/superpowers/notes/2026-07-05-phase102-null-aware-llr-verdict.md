# Phase 102 — Null-Aware Soft-LLR Viterbi Verdict

**Date**: 2026-07-05
**Branch**: TEST1
**Status**: 🟡 IMPLEMENTED, AWAITING USRP CABLE VERIFICATION
**Cable runs used**: 0 (Phase 102 is implementation only)

## Implementation

- `lib/frame_equalizer_impl.cc`: env var `IEEE80211_HTSIG_NULL_SCS=<csv>`
  parsed in constructor; `d_htsig_null_sc_mask[52]` populated.
- HT-SIG soft-LLR path (HT-SIG0 + HT-SIG1): masked SCs → `llr = 0.0f`.
- Default OFF (env var unset = mask all zeros = current behavior).

## Verification

- Software loopback 1/1 PASS (env unset, baseline preserved)
- Software loopback 1/1 PASS (env set, soft-LLR null-aware path active)
- USRP cable run: PENDING (consume 1 cable budget after Phase 101 SC identification)

## Test command

```bash
export IEEE80211_SOFT_LLR_VITERBI=1
export IEEE80211_HTSIG_NULL_SCS="<5 comma-separated kScIndex52 indices from Phase 101>"
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 20 --rate 20 --warmup 60 --rx-subdev A:0
```

Expected: HT-SIG viterbi metric 13-15 → 5-9 (recoverable below free-distance=10).

## Risk

REFUTED territory (Phase 78c similar approach). Soft-LLR was REFUTED in
Phase 77b at 5250 clean (n_nulls=0). Different from Phase 78c: this
ONLY sets conf=0 for the 5 globally-null SCs, leaves the rest at full weight.
If USRP fails: equalizer-layer confirmed CLOSED, redirect to upstream
sync_short re-architecture (Schmidl-Cox / Park-Gezici / FFT-based L-STF)
or stop.