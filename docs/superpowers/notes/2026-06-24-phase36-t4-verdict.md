# Phase 36 Task 4 Verdict — Per-SC Pilot CPE on USRP (2026-06-24)

## Summary

**Status:** BLOCKED (std = 1.367 rad — per-SC fit did NOT attack the right layer)

Per-SC linear fit on HT-SIG pilots does NOT reduce pilot phase diff between HT-SIG0 and HT-SIG1. The frequency-selective phase profile the fit was designed to address is NOT dominant in the impairment.

## Test Conditions

- Build: clean (T1-T3 commits 659d584, 30dbb60, 9725b66, dd7362f)
- USRP: X310 + A:0+A:0 @ 5 GHz, --freq 5890, --tx-gain 20, --rx-gain 20, --duration 60s
- Env-vars enabled: TIMING_OFFSET_APPLY=1, HTSIG_PILOT_CPE=1, HTSIG_PILOT_PERSC=1, HTSIG_BIN_DUMP=1, HTSIG_PILOT_DUMP=1, LSIG_RATE_FORCE=0xD
- Frames Sent: 61 | Recv: 0 | FCS_OK=0 | HT_SIG_PARSE_FAIL=28 (viterbi crc_fail)
- LSIG_DECODE OK: 206 (Phase 34 δ correction still working)

## Pilot Diff std (HT-SIG1 - HT-SIG0)

| Run | std (rad) | max|diff| (rad) | Notes |
|---|---|---|---|---|
| T6 (no fix) | 1.654 | 3.10+ | Phase 35 baseline |
| T8b (per-symbol MEAN) | 1.390 | — | Phase 35 T7c fix |
| **T4 (per-symbol + per-SC)** | **1.367** | **3.100** | this run |

**Delta vs T8b: -0.023 rad (-1.7%)** — within noise of the 3-frame sample.

## Per-Pilot Breakdown

```
pilot@-21: mean=+0.049rad  std=1.215rad  max|diff|=1.662rad
pilot@-7:  mean=-0.503rad  std=1.197rad  max|diff|=2.196rad
pilot@+7:  mean=-1.233rad  std=1.342rad  max|diff|=3.100rad
pilot@+21: mean=-0.407rad  std=1.384rad  max|diff|=2.227rad
ALL pooled: mean=-0.524rad  std=1.367rad
```

The per-pilot means are NOT zero (mean ranges from -1.23 to +0.05 rad) and NOT
symmetric. The +7 pilot has the largest mean offset (-1.23 rad) and the
largest max|diff| (3.10 rad). This is **NOT** a linear-in-SC phase ramp.

## Per-SC Fit Coefficients (samples seen)

```
sym=0 a=-0.1603 b=0.103184
sym=0 a= 0.1617 b=-0.086053
sym=0 a=-0.0580 b=0.139458
sym=0 a= 1.1075 b=-0.125293
sym=0 a= 0.1777 b=0.000783
sym=0 a= 0.0364 b=0.008223
sym=0 a=-0.4307 b=0.029245
sym=0 a= 0.5215 b=0.087283
sym=0 a=-0.3869 b=0.013651
sym=0 a=-1.5192 b=-0.105462
```

`a` (intercept, constant offset) ranges from -1.52 to +1.11 rad with std
roughly ~0.7 rad. `b` (slope, per-SC drift) is small (|b| < 0.14 rad/SC for
all but one outlier) and **does not consistently improve phase residual**.

The "linear" assumption is wrong: a single symbol sees the channel look
flat-ish in expectation, but the per-pilot phase residual is roughly
constant std across the 4 SCs (1.2-1.4 rad), which means the residual
phase is NOT linearly varying with SC index. The frequency-selective
hypothesis from Phase 35 (within-symbol pilot std = 1.3 rad) was correct
but the **structure of that frequency-selectivity is not a linear ramp**.

## Pilot Coherence

```
frame  htsig0_std  htsig1_std  mean_|h0-h1|
   0      1.585      1.359       1.729
   1      1.501      1.300       0.185   <-- coherent this frame
   2      1.196      1.405       1.395
```

Within-symbol pilot std ~1.2-1.6 rad on BOTH HT-SIG0 and HT-SIG1
symmetrically. The "frequency-selective phase profile" is per-symbol-noise,
not a coherent channel frequency response.

## Decision

**Per-SC fit is NOT effective at this layer.** The improvement (-1.7%) is
within sample noise. The 3-frame sample is small but consistent with T6/T8b
baselines (1.654 / 1.390 / 1.367 all within 0.3 rad).

**Recommendation: Do NOT proceed to T5 (USRP e2e) with this fix.**

## Why the Linear-Fit Hypothesis Failed

Phase 35 verdict (within-symbol pilot std = 1.3 rad) suggested the
channel was frequency-selective, so a per-SC linear fit on 4 pilot SCs
would recover the underlying channel phase profile. But the fit's `b`
(slope) coefficient is ~0.1 rad/SC typical, which projects to ~1 rad at
the edge of the 52-SC HT-SIG bandwidth — comparable to the residual noise
floor. The frequency-selectivity is either:
1. Not a linear function of SC index (higher-order polynomial, or non-monotonic)
2. Not present at all — the 1.3 rad within-symbol spread is per-pilot noise
3. Time-varying faster than the 4-μs HT-SIG symbol

The 4-point fit cannot distinguish these from a noisy/random phase pattern.

## What's NEXT (architectural question)

After **8 equalizer-level investigations** REFUTED + 2 frequency-domain
corrections (per-symbol MEAN, per-SC fit) also REFUTED, the per-symbol
phase drift between HT-SIG0 and HT-SIG1 is a deeper impairment than the
H52 channel / pilot-aided CPE can fix.

Possibilities to investigate:
1. **BCC decoder bug** — viterbi is still crc_fail, not a CPE problem at all
2. **HT-SIG-specific frequency offset** — distinct from L-SIG/HT-DATA
3. **Phase noise during HT-SIG only** (LO re-lock, AGC settling)
4. **The "pilots" in HT-SIG are not channel-probing** — they are just BPSK data with a known polarity sequence, and 1.3 rad noise is the actual channel at HT-SIG time

## Files

- Log: /tmp/p36a_usrp.log.failed (saved for diagnosis)
- Bin dumps: 3 frames captured, 5 PILOT_DUMP entries (counter=4 limit)
- Analyzer output: pilot diff std = 1.367 rad, max|d|=3.100 rad

## Conclusion

**Phase 36 T4 REFUTES per-SC fit hypothesis.** Investigation at wall
for the 9th time. Need to question whether the impairment is in the
equalizer at all, or in downstream viterbi/decoder stages.
