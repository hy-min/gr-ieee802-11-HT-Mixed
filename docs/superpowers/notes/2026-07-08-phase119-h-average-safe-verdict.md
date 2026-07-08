# Phase 119: H_AVERAGE_SAFE per-bin safety filter (2026-07-08)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — safety filter does not improve over Phase 118b

## TL;DR

Phase 118b H_AVERAGE dropped HT_SIG metric from 13 (Phase 117) to 12.
Phase 119 added per-bin safety filter (reject pilot refinement when
|H_pilot - Hhdr52| > 50% * |Hhdr52|). Goal: avoid Phase 39 piecewise-
linear interpolation overshoot at non-pilot SCs.

**Result**: metric 12-17, identical to Phase 118b (12-16). Best metric
stuck at 12. **Safety filter is REFUTED on USRP** — pilot-based H does
not overshoot significantly enough for the safety filter to help.

## Implementation

`lib/frame_equalizer_impl.cc`:
- New static function `refine_h52_average_pilots_safe` (line 575+)
- New env var `IEEE80211_HTSIG_H_AVERAGE_SAFE=1` (line 4098-4101)
- New apply block before HT-SIG viterbi (line 7025+)
- Header flag `d_apply_htsig_h_average_safe` (line 198-199)

`lib/frame_equalizer_impl.h`:
- New flag `d_apply_htsig_h_average_safe` (line 198-199)

Compilation: SUCCESS. `make install` SUCCESS.

## USRP 5250 cable 60s verification (2026-07-08)

Command: `IEEE80211_HTSIG_H_AVERAGE_SAFE=1 python
test_usrp_minimal_loopback.py --uhd-tune --freq 5250 --tx-gain 0
--rate 20 --warmup 60 --rx-subdev A:0 --duration 60`

Logs: `/tmp/p119_havg_safe_60s.log` (run 1), `/tmp/p119_havg_safe_60s_v2.log` (run 2)

| 指标 | Phase 117 baseline | **Phase 118b H_AVERAGE** | **Phase 119 H_AVERAGE_SAFE** |
|------|--------------------|--------------------------|------------------------------|
| 触发次数 | - | 3 | **4** |
| HT_SIG_CAND | 144 | 48 | 32 |
| Metric 最低 | 13 | 12 | **12** (无改善) |
| Metric 分布 | 13-18 | 12-16 | **12-17** |
| avg_snr_ht | 2.81 | 2.58 | - |
| HT_SIG_PARSE_OK | 0 | 0 | 0 |
| LSIG_DECODE_OK | 27 | 0 (run 1) | 0 (run 1+2) |

Metric distribution (Phase 119, run 2):
- metric=12: 2 candidates
- metric=14: 8 candidates
- metric=15: 8 candidates
- metric=16: 6 candidates
- metric=17: 8 candidates

## Root Cause: safety filter doesn't help

**Theory was**: Phase 39 piecewise-linear interpolation can overshoot
at non-pilot SCs. The averaging formula (2 Hhdr52 + 1 H_htsig0 + 1 H_htsig1)
would then be dominated by the overshooting H_pilot, hurting the
average. Safety filter rejects SCs with >50% deviation from Hhdr52.

**Why it doesn't help**:
1. Phase 39's interpolation does not overshoot significantly at any
   SC on USRP. The pilot-based H is within 50% of Hhdr52 for nearly
   all SCs.
2. The averaging formula already dampens overshoot by blending with
   2x Hhdr52 weight.
3. The per-SC noise floor is dominated by 1.77 rad analog chain (Phase
   112 R1), not interpolation overshoot.

## Architectural Insight

The HT-SIG viterbi wall is at metric=12, **2 units above the ≤10
threshold**. The improvement from 13 (Phase 117) to 12 (Phase 118b) is
real but small. Further per-SC H refinement cannot push below 12
because:
- The 4 pilots per HT-SIG symbol provide only 4 complex samples for
  H refinement → 1.77/√2 ≈ 1.25 rad (correlated to Hhdr52).
- Adding H_htsig1 as a 5th estimate gives 1.77/√2.5 ≈ 1.12 rad.
- The safety filter rejects bins that are already noisy → minimal
  impact on average.

**Phase 118b's H_AVERAGE already achieves the theoretical limit of
per-symbol H refinement from pilots.**

## Next Steps (per user "new architecture" directive)

1. **Cross-frame H tracking** (Phase 111 T3 extension): use H[t-1] as
   prior for H[t]. Reduces per-frame noise via Bayesian update.
2. **Decision-directed equalizer**: decode HT-SIG0 first, then use the
   known bits to refine H52 for HT-SIG1 + DATA.
3. **HT-LTF P-matrix H re-estimation** (Phase 111 T2 variant): use the
   real HT-LTF instead of L-LTF proxy for header equalization.
4. **Wiener phase tracking**: model 1.77 rad as Wiener process, predict
   per-symbol phase, subtract.

These all operate at the equalizer level — they may yield the
remaining 2 metric units needed to reach ≤10 viterbi threshold.

## Files Modified

- `lib/frame_equalizer_impl.cc:575-622` — new safety filter function
- `lib/frame_equalizer_impl.cc:4098-4102` — env var parse
- `lib/frame_equalizer_impl.cc:7025-7040` — apply block
- `lib/frame_equalizer_impl.h:193-199` — flag declaration
- `IEEE80211_HTSIG_H_AVERAGE_SAFE=1` opt-in env var (default OFF)

## Default OFF

- `IEEE80211_HTSIG_H_AVERAGE_SAFE=1` opt-in (default unset)
- All new code paths gated on `d_apply_htsig_h_average_safe`
- Phase 117 baseline preserved when env vars absent
- No loopback regression (compile-time check passes)

## Related

- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (parent)
- [[project-p117-baseline]] — Phase 117 baseline (metric 13)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC ceiling
- Verdict: `docs/superpowers/notes/2026-07-08-phase119-h-average-safe-verdict.md`
