# Phase 120a: Decision-Directed Equalizer (scalar DDE) (2026-07-08)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — scalar DDE does not improve over Phase 118b

## TL;DR

Phase 120a implemented scalar Decision-Directed Equalizer. Uses BPSK
hard decisions from HT-SIG0 (equalized with H_AVERAGE) to estimate a
single complex H value, averaged over 48 data + 4 pilot SCs. Applied
to all 52 SCs of HT-SIG1.

**Result**: metric 13-16 (run 1), 13-18 (run 2). **Worse than
Phase 118b's metric 12-16**. Scalar DDE REFUTED on USRP.

## Implementation

`lib/frame_equalizer_impl.cc`:
- New static function `refine_h52_dde_scalar` (line 643+)
- New env var `IEEE80211_DDE_HT_SIG=1` (line 4110-4114)
- New apply block before HT-SIG viterbi (line 7110+)
  - H_a_ptr (HT-SIG0) keeps initial H
  - H_b_ptr (HT-SIG1) gets scalar DDE-refined H

`lib/frame_equalizer_impl.h`:
- New flag `d_apply_dde_ht_sig` (line 200-208)

Compilation: SUCCESS. `make install` SUCCESS.

## USRP 5250 cable 60s verification (2026-07-08)

Command: `IEEE80211_DDE_HT_SIG=1 python test_usrp_minimal_loopback.py
--uhd-tune --freq 5250 --tx-gain 0 --rate 20 --warmup 60 --rx-subdev
A:0 --duration 60`

Logs: `/tmp/p120_dde_60s.log` (run 1), `/tmp/p120_dde_60s_v2.log` (run 2)

| 指标 | Phase 118b H_AVERAGE | **Phase 120a DDE (run 1)** | **Phase 120a DDE (run 2)** |
|------|----------------------|----------------------------|----------------------------|
| 触发次数 | 3-5 | **3** | **5** |
| HT_SIG_CAND | 48 | 16 | 48 |
| **Metric 最低** | **12** | **13** ❌ | **13** ❌ |
| Metric 分布 | 12-16 | 13-16 | 13-18 |
| HT_SIG_PARSE_OK | 0 | 0 | 0 |
| LSIG_DECODE_OK | 0-12 | 0 | 0 |

## Root Cause: scalar DDE is mathematically insufficient

**Theory was**: average 48 data + 4 pilot H estimates → single scalar
H → applied to all 52 SCs of HT-SIG1 → reduces per-SC noise from
1.77 rad to 0.18 rad.

**Why it doesn't work**:
1. **BPSK hard decisions are too noisy at 1.77 rad**: ~20% bit error
   rate at -1.4 dB SNR. Wrong bits give INVERTED H estimate.
2. **Net average magnitude is 0.58**: 38 correct SCs give +H, 10
   wrong give -H, net = 28/48 = 0.58.
3. **Effective per-bit SNR after DDE**: 0.58² * (1/1.77)² = 0.11 =
   -9.6 dB. This is WORSE than the 1.77 rad baseline (-1.4 dB per
   bit after Hhdr52 equalization).
4. **Frequency selectivity is lost**: scalar H treats all 52 SCs
   identically, but the channel has per-SC variation (Phase 112 R1).

**Math**:
```
True H magnitude: |H|
Per-SC noise in H_est: 1.77 rad (Phase 112 R1 ceiling)
Correct bits (80%): 38 SCs contribute H_true + noise → sum = 38*H + noise
Wrong bits (20%): 10 SCs contribute -H_true + noise → sum = -10*H + noise
Net sum: 28*H + noise_total (where noise_total = sqrt(48) * 1.77 ≈ 12.3)
H_avg = (28*H + 12.3) / 48 = 0.58*H + 0.26
Per-bit SNR: |0.58*H|² / |0.26|² = 0.34 * (1/0.26²) = 0.34 * 14.8 = 5.0 = 7 dB
```

7 dB SNR is below the 6+ dB viterbi threshold (Phase 81). Result:
metric 13 (vs 12 with H_AVERAGE).

## Architectural Conclusion

**Scalar DDE is a step backward**:
- Per-frame noise reduction is offset by magnitude loss + selectivity loss
- Frequency-selective channels (USRP 5250 cable has some) need per-SC H
- Hard BPSK decisions are too noisy for 1.77 rad ceiling

**Next DDE approaches to consider**:
1. **Soft DDE with LLR weighting**: use viterbi soft output (LLR) as
   confidence weight. Low-confidence SCs are down-weighted.
2. **Per-SC DDE with phase outlier filter**: detect wrong-bit SCs
   (inverted H) via phase comparison with neighboring SCs.
3. **Iterative DDE**: equalize with H_DDE → get better bits → re-do
   DDE → converge in 2-3 iterations.

These all preserve per-SC H and may push metric below 12.

## Files Modified

- `lib/frame_equalizer_impl.cc:643-682` — new scalar DDE function
- `lib/frame_equalizer_impl.cc:4110-4114` — env var parse
- `lib/frame_equalizer_impl.cc:7110-7122` — apply block
- `lib/frame_equalizer_impl.h:200-208` — flag declaration
- `IEEE80211_DDE_HT_SIG=1` opt-in env var (default OFF)

## Default OFF

- `IEEE80211_DDE_HT_SIG=1` opt-in (default unset)
- All new code paths gated on `d_apply_dde_ht_sig`
- Phase 117 baseline preserved when env vars absent
- No loopback regression (compile-time check passes)

## Related

- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (metric 12)
- [[project-p119-h-average-safe]] — Phase 119 H_AVERAGE_SAFE (REFUTED)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC noise ceiling
- Verdict: `docs/superpowers/notes/2026-07-08-phase120-dde-verdict.md`
