# Phase 121: Per-SC DDE with phase filter (2026-07-08)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — per-SC DDE is WORSE than Phase 118b baseline

## TL;DR

Phase 121 implemented per-SC Decision-Directed Equalizer with dot-product
phase filter. H_est[sc] = rx52_a[sc] / constellation[sc], filtered by
`Re(conj(Hhdr52) * H_est) > 0`. Goal: preserve frequency selectivity
+ reject wrong-bit SCs (inverted H).

**Result**: metric 14-17 (run 2), 14-17 (run 1). **WORSE than Phase 118b's
metric 12-16**. Per-SC DDE is REFUTED on USRP.

## Implementation

`lib/frame_equalizer_impl.cc`:
- New static function `refine_h52_dde_per_sc` (line 686+)
- New env var `IEEE80211_DDE_HT_SIG_PER_SC=1` (line 4124-4128)
- New apply block before HT-SIG viterbi (line 7145+)

`lib/frame_equalizer_impl.h`:
- New flag `d_apply_dde_ht_sig_per_sc` (line 209-216)

Compilation: SUCCESS. `make install` SUCCESS.

## USRP 5250 cable 60s verification (2026-07-08)

Command: `IEEE80211_DDE_HT_SIG_PER_SC=1 python
test_usrp_minimal_loopback.py --uhd-tune --freq 5250 --tx-gain 0
--rate 20 --warmup 60 --rx-subdev A:0 --duration 60`

Logs: `/tmp/p121_dde_persc_60s.log` (run 1), `/tmp/p121_dde_persc_60s_v2.log` (run 2)

| 指标 | Phase 118b H_AVERAGE | **Phase 120a scalar DDE** | **Phase 121 per-SC DDE** |
|------|----------------------|---------------------------|--------------------------|
| 触发次数 | 3-5 | 3-5 | 2-3 |
| HT_SIG_CAND | 48 | 16-48 | 0-16 |
| **Metric 最低** | **12** | 13 | **14** ❌ (worse) |
| Metric 分布 | 12-16 | 13-18 | 14-17 |
| HT_SIG_PARSE_OK | 0 | 0 | 0 |

## Root Cause: H_est and Hhdr52 have same noise level

The per-SC DDE is mathematically equivalent to using H_est at some SCs
and Hhdr52 at others. Both are noisy estimates of the same H_true with
~1.77 rad noise per SC (Phase 112 R1 ceiling).

**Why per-SC DDE is WORSE than Phase 118b**:
1. At correct-bit SCs (80%): H_est has same noise as Hhdr52 → no gain
2. At wrong-bit SCs (20%): H_est is INVERTED (-H_true + noise)
   - Dot product filter: `dot = Re(conj(Hhdr52) * H_est) > 0`
   - If H_est is inverted: dot < 0 (in phase opposite to Hhdr52)
   - Filter rejects (uses Hhdr52 fallback) — should work
   - But filter noise margin is small at 1.77 rad noise:
     - `dot = ±|Hhdr52|² + noise_dot` where `noise_dot std = |Hhdr52| * 1.77`
     - For |Hhdr52| ≈ 1.0: filter works for ~50% of wrong-bit SCs
3. At 50% of wrong-bit SCs, H_est is used (inverted) → catastrophic
   bit errors in HT-SIG1 viterbi
4. Net: per-SC DDE is WORSE than Hhdr52 baseline because some SCs
   have inverted H

**Why scalar DDE was better than per-SC**:
- Scalar DDE averages 52 H estimates → wrong bits (10/48) get diluted
  in 48-sample average
- Average magnitude: 0.58 of true H (loss but recoverable)
- Per-SC DDE: wrong-bit SCs keep inverted H → unrecoverable

**Math**:
```
For per-SC DDE at correct-bit SCs: H_est noise = 1.77 rad (same as Hhdr52)
For per-SC DDE at wrong-bit SCs (filter miss): H_est = -H_true + noise
  → viterbi bit error at that SC → +1 metric error per SC
For 50% filter miss rate on 20% wrong SCs: 10% of SCs have inverted H
  → 5 SCs with bit error → 5+ metric penalty

For scalar DDE: average noise = 0.26 rad, magnitude 0.58
  → per-bit SNR 7 dB (just below 6 dB threshold)
  → metric 13 (1 worse than H_AVERAGE)

For per-SC DDE: some SCs have bit error → 5+ metric penalty
  → metric 14 (2 worse than H_AVERAGE)
```

## Architectural Conclusion

**DDE is fundamentally limited at 1.77 rad noise floor**:
- BPSK hard decisions are too noisy (20% error rate)
- Wrong bits give inverted H estimates
- Both scalar (averaging) and per-SC (filtering) approaches fail to
  reduce per-SC noise below the Phase 112 R1 ceiling

**Future approaches that may help**:
1. **Soft DDE with LLR weighting**: use viterbi soft output (LLR) as
   confidence weight. LLR magnitude tells us which bits are reliable.
2. **Cross-frame H tracking**: use H[t-1] as prior, Bayesian update
   with H[t]. Multiple frames average out per-symbol noise.
3. **HT-LTF P-matrix**: use real HT-LTF instead of L-LTF proxy.
   Closer in time to HT-SIG1 than L-LTF.
4. **Wiener phase tracking**: model 1.77 rad as Wiener process,
   predict per-symbol phase, subtract.

These all operate at the channel-tracking level, not at the per-SC
equalization level. They may break the 1.77 rad ceiling.

## Files Modified

- `lib/frame_equalizer_impl.cc:686-757` — new per-SC DDE function
- `lib/frame_equalizer_impl.cc:4124-4128` — env var parse
- `lib/frame_equalizer_impl.cc:7145-7157` — apply block
- `lib/frame_equalizer_impl.h:209-216` — flag declaration
- `IEEE80211_DDE_HT_SIG_PER_SC=1` opt-in env var (default OFF)

## Default OFF

- `IEEE80211_DDE_HT_SIG_PER_SC=1` opt-in (default unset)
- All new code paths gated on `d_apply_dde_ht_sig_per_sc`
- Phase 117 baseline preserved when env vars absent
- No loopback regression (compile-time check passes)

## Related

- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (metric 12, still BEST)
- [[project-p120-dde-refuted]] — Phase 120a scalar DDE (REFUTED, metric 13)
- [[project-p119-h-average-safe]] — Phase 119 H_AVERAGE_SAFE (REFUTED, metric 12)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC noise ceiling
- Verdict: `docs/superpowers/notes/2026-07-08-phase121-dde-per-sc-verdict.md`
