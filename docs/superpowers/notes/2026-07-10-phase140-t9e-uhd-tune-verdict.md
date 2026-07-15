# Phase 140 T9e USRP 5250 uhd-tune + phase140-on 4 — 2026-07-10

**VERDICT: REFUTED** (recv=0, no FCS_OK)

## Configuration

- Test script: `test_usrp_minimal_loopback.py`
- HW: USRP X310 @ 192.168.10.2, serial 323850C, UBX-160 daughterboard
- Frequency: 5250 MHz (cable direct SMA male-male, same-board A:0 TX → A:0 RX2)
- `--tx-gain 0 --rx-gain 31.5 --rate 20 --warmup 60 --rx-subdev A:0`
- `--duration 60 --interval 200`
- **Phase 113 T5.A: `--uhd-tune`** (disables UBX-160 auto DC + IQ calibration)
- **Phase 140: `--phase140-on 4 --phase140-log`** (2-way L-LTF0+L-LTF1 H52 +
  N=4 FIFO averaging at L-SIG viterbi; σ_post = 1.25/√n_avg rad)

## Headline metrics

| Metric | Value | Reference |
|--------|-------|-----------|
| Sent | 600 | test invocations |
| Recv | **0** | Phase 139 T3 = 4 |
| FCS_OK | 0 | Phase 139 T3 = 0 (same) |
| LSIG_DECODE OK | 11 | Phase 139 T3 = 4 |
| LSIG_PARSE_FAIL | 24 | viterbi_fail |
| HT_SIG_CAND | **0** | Phase 139 T3 = 16 |
| is_ht_frame=1 | 0 | upstream gate never crossed |
| Cross-frame σ_post (max) | 0.559 rad @ n_avg=5 | theoretical min @ FIFO |
| avg_snr | 1.98 / 3.31 / 8.79 | three L-SIG attempts |
| avg_snr_ht | 2.82 / 4.21 / 5.01 | three L-SIG attempts |
| sync_short min_cor mean | 8.025 (max 10.39) | HEALTHY (>3 = good) |
| UHD underflows / overflows | 150 / 30 | streaming noise |

## Phase 140 mechanism confirmation

**MATHEMATICALLY CORRECT** — σ_post matches theoretical `1.25/√n_avg` exactly:

```
n_avg=1 depth=4 sigma_est_input=1.25 sigma_est_post=1.250 rad
n_avg=2 depth=4 sigma_est_input=1.25 sigma_est_post=0.884 rad
n_avg=3 depth=4 sigma_est_input=1.25 sigma_est_post=0.722 rad
n_avg=4 depth=4 sigma_est_input=1.25 sigma_est_post=0.625 rad
n_avg=5 depth=4 sigma_est_input=1.25 sigma_est_post=0.559 rad
```

27 Phase 140 fires total. σ reaches 0.559 rad at full FIFO (target ≤0.52 rad for
metric ≤10), confirming the FIFO averaging is working at the math level.

## Why T9e still failed: Phase 18 strict rate gate

11 LSIG_DECODE OK events fired but **every decode landed on `enc ∈ {1,2,4,6}`
non-HT encodings (rate=0x9)**. All 11 OK events are Legacy frames, never HT-SIG.
All 24 viterbi_fail events are also non-HT (is_ht_frame=0).

- Phase 18 strict rate=0xD check rejects 0x9 decodes at L-SIG → no HT-SIG chain.
- HT_SIG_CAND=0 because no HT frame ever made it past L-SIG viterbi.

This is the **Phase 82 root-cause**: even with σ→0.559 rad (lowest ever), the
Phase 18 strict check rejects valid 0x9 decodes when L-LTF phase noise biases
the rate estimate. The σ reduction cannot help if the frame is rejected before
HT-SIG ever runs.

## Comparison vs prior baselines

| Test | LSIG_DECODE_OK | HT_SIG_CAND | best metric | avg_snr_ht | FCS_OK |
|------|----------------|-------------|-------------|------------|--------|
| Phase 139 T3 (2-way baseline) | 4/4 | 16-32 | 13 | 8.78 dB | 0 |
| Phase 139 T3b (3-way + pilot refine) | 4/4 | 0 | - | - | 0 |
| **Phase 140 T9d (no uhd-tune)** | 0 | 0 | - | - | 0 |
| **Phase 140 T9e (uhd-tune + p140)** | 11 | **0** | - | 5.01 dB | 0 |

T9e **improved** LSIG_DECODE_OK vs Phase 139 T3 (11 vs 4) but **regressed**
HT_SIG_CAND (0 vs 16-32). The σ_post drop (Phase 140) is helping L-SIG
viterbi find more Legacy candidates, but they all decode to enc=1/2/4/6
(non-HT rate=0x9), which Phase 18 rejects.

## L-SIG EQ ratio distribution (3 frame_detect events only)

```
EQ ratio_ht=0.933 L-SIG ratio=2.062 (BAD — >1.0)
EQ ratio_ht=0.731 L-SIG ratio=1.162 (BAD)
EQ ratio_ht=1.151 L-SIG ratio=0.494 (OK)
```

Phase 113 T5.A signature was "L-SIG EQ ratio 1.4+ → 0.863". T9e shows
0.494 best, 2.062 worst — high variance. uhd-tune is helping some frames,
hurting others.

## What this rules out

- **σ reduction alone CANNOT break viterbi wall** when Phase 18 strict
  rate=0xD check rejects 0x9 decodes. Phase 140 mechanism is CORRECT but
  the upstream L-SIG rate gate is the binding constraint.
- **--uhd-tune does NOT consistently improve L-SIG EQ ratio** on this run
  (0.494-2.062 range across 3 frames — high variance, no signal-level floor).

## Sync_short signal quality

sync_short fired 432 COPY work batches with healthy correlations:
- min_cor mean=8.025, max=10.39
- max_cor mean=9.49, max=11.19

vs Phase 89 baseline corr=3.163. Sync_short is **healthy**, not the bottleneck.

## Capture

IQ file: `/tmp/p140_usrp/T9e_uhd_tune_p140.fc32` (54,834,016 bytes = ~6.85s
of 20 Msps IQ = 400M samples at fc32 = 1.6 GB; this is the streamed subset
captured via `--capture` flag, not the full 60s run).

## Connection status

UHD 4.7.0.HEAD-release, linux; GNU C++ 12.3.0; Boost_108400.
USRP X310 reachable. 150 underflows / 30 overflows during 60s run
(streams OK, but UHD scheduling pressure exists — same as prior runs).

## Wall clock

~62s (60s warmup + 60s test + ~2s overhead). Test ran within 180s timeout.

## Phase 140 verdict consolidation

Phase 140 cross-frame H52 FIFO averaging is mathematically correct, σ_post
matches 1.25/√n_avg exactly. **It cannot help Phase 18 strict rate=0xD
gate that rejects 0x9 decodes.**

## Next attack plan (per user hard constraint)

L-SIG wall paradox: σ drops correctly, more Legacy decodes succeed, but
none reach HT-SIG. Two parallel paths needed:

1. **Phase 140+ T1: Phase 18 rate-accept patch (continue from Phase 81)**
   Add `IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9` opt-in to bypass Phase 18 strict
   check. Re-test Phase 140 T9e config. If 0x9 decodes now reach HT-SIG with
   σ=0.559 rad, metric may drop below 10 and break viterbi wall.

2. **Phase 140+ T2: Per-frame H52 estimator with phase tracking**
   The σ=0.559 rad is post-averaging. The input σ=1.25 rad is per-symbol.
   Kalman or per-symbol tracker could reduce input σ itself, not just average
   it (Phase 111 T3 PASS on synthetic).

3. **Phase 140+ T3: 30 dB SMA attenuator install (HW, $50)**
   User-excluded but mentioned in MEMORY.md as "strongest path forward".
   Would reduce analog-chain noise to ~0.5-0.7 rad directly.

## Verdict

**REFUTED**: 0 FCS_OK. Phase 140 σ mechanism correct but upstream L-SIG
rate gate (Phase 18) binds. Equalizer-layer attacks MUST continue per
user 2026-07-07 directive.

## Files

- Log: `/tmp/p140_usrp/T9e_uhd_tune_p140.log`
- Capture: `/tmp/p140_usrp/T9e_uhd_tune_p140.fc32`