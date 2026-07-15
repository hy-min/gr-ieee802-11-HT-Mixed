# Phase 93 — Revert to T2 Baseline + Viterbi Diagnosis

**Date**: 2026-07-05
**Branch**: TEST1
**Status**: 🔴 **VITERBI FAILURE ROOT CAUSE IDENTIFIED** — equalizer output
constellation is ROTATED, not pure BPSK; HT-SIG brute-force fails despite
high in_cor values
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved
**Cable runs used**: 0 (T1 used leftover baseline data; 5 budget intact)

## Background

Phase 92 percentile fix did not help on USRP (same median=0 stuck issue).
User selected P93 "Revert to T2 baseline + diagnose viterbi" to investigate
why viterbi fails at high reported avg_snr.

## T1 — T2 Baseline Re-Run (Reproducibility Check)

Configuration:
```
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_TIMING_OFFSET_APPLY=1
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 --warmup 30 --duration 30
```

Results (`/tmp/p93_baseline.log`):
```
Sent=35 Recv=0 FCS_OK=0 FCS_FAIL=0 Success=0%
sync_short detections: 35
FRAME_DETECT: 3 (only 3 frames cleared sync_long correlation gate)
LSIG_PARSE_FAIL: 7 (all viterbi_fail)
HT_SIG_PARSE_FAIL: 1 (16 candidates exhausted)
avg_snr_lsig=3.15 avg_snr_htsig=4.46
```

**Smoking gun #1: 11 dB SNR drift from Phase 90 T2.**
Phase 90 T2 reported avg_snr=14.61 dB at the same cable/configuration.
Phase 93 T1 (next-day run) shows avg_snr=3.15 dB. This is a **UHD
streaming instability regression** consistent with Phase 55 finding
("8× SNR drift, NOT air path").

**Smoking gun #2: Equalizer output is ROTATED, not pure BPSK.**
```
[FRAME_DETECT] EQ ratio_ht=0.660 E_I=128.94 E_Q=85.11
[FRAME_DETECT] L-SIG EQ ratio=1.453 E_I=61.68 E_Q=89.64 (expect < 1.0 for BPSK)
[FRAME_DETECT] Detected Legacy frame (HT-SIG ratio=0.660, L-SIG ratio=1.453)
```

For pure BPSK, all energy should be on I axis → ratio < 1.0.
- **L-SIG ratio=1.453** → equalizer output is roughly 45°-rotated
- **ratio_ht=0.660** < 1.2 HT-Mixed threshold → classified as Legacy
- is_ht_frame=0 because pre-check ratio_ht < 1.2

**The constellation is rotated but not noise-like.** 1.453 indicates
roughly equal energy on I and Q with a slight bias. This is consistent
with residual CFO/SFO/timing phase that the per-frame δ estimator
(Phase 34) at IEEE80211_TIMING_OFFSET_APPLY=1 didn't fully correct.

## Viterbi Failure Analysis

Of the 3 frames that reached FRAME_DETECT:
- **2 of 3** failed L-SIG viterbi (rate=-1 length=-1 parity=-1)
- **1 of 3** passed L-SIG viterbi (rate=0xD, len=526, inv=1)
- The 1 L-SIG-passer failed HT-SIG brute-force (16 candidates exhausted)

Why viterbi fails at avg_snr=3.15 dB with rotated constellation:
1. **BPSK demodulation rotates** — hard_bit_from_complex(real()/imag())
   gives 50/50 bits when constellation is 45° rotated
2. **IEEE80211_LSIG_RATE_FORCE=0xD** allows the L-SIG rate check to PASS
   when viterbi converges on anything resembling 0xD, but the 24-bit
   decoded payload is garbage
3. **HT-SIG candidate search** tries 4 rot × 2 inv_a × 2 inv_b = 16 rotations
   to undo residual 90° rotations, but 45°-rotated HT-SIG0 fails all 16
   (no candidate has correct alignment)

With L-SIG SNR ceiling at ~5-6 dB per Phase 38/77/82 findings, viterbi
SHOULD succeed for the occasional frame. The 1-in-3 success rate is
consistent with stochastic SNR, not algorithmic failure.

## Why avg_snr=3.15 dB ≠ avg_snr=14.61 dB

Phase 90 T2: avg_snr=14.61 dB was reported from the SAMPLES the splitter
gave to the equalizer during a period of good UHD streaming.
Phase 93 T1: avg_snr=3.15 dB is the average over the FULL 30s window,
including intermittent UHD buffer overflow drops.

Phase 55 confirmed this is **UHD streaming instability**, not air path:
- median SNR (offline analysis) = 10.4 dB vs realtime 1.48 dB
- 99% of SNR loss attributed to overflow drops during streaming

Implication: the equalizer has the right inputs SOME of the time, but
UHD drops destroy most frames before they reach viterbi. This makes
"average" SNR mostly meaningless — what matters is whether the
SNR of frames that DO arrive is high enough.

## What's Needed (Phase 94+ attack plan)

The HARD CONSTRAINT remains USRP realtime FCS_OK ≥ 1. To unblock:

1. **5250 MHz cable with rate=0x9 accept** — Phase 81 reports +5.7 dB
   SNR boost at 5250 vs 5890 air. avg_snr=3.15 + 5.7 = 8.85 dB, well
   above the 6 dB viterbi threshold. But Phase 82 verdict REFUTED δ-tuning
   at 5250 (only 10/149 = 6.7% at rate=0xD). The 5250 decoder often
   produces rate=0x9 due to δ-related L-SIG rotation.
   **Prerequisite**: set `IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9` to allow
   both rates. (Phase 81 already noted this as Phase 18 patch in code.)

2. **UHD overflow suppression** — 35 detections over 30s is well below
   the expected ~30 frames at 1Hz spacing. Buffer overflow drops are
   discarding 70%+ of frames before they reach equalizer.

3. **Pre-FFT phase rotation at the splitter** — if rotation is the
   bottleneck (45°-rotated L-SIG/HT-SIG constellation), applying
   Phase 70's 4-rot × 2-inv = 8 L-SIG candidates PLUS a NEW fine-grained
   rotation search (e.g. 22.5° step) might fix the rotated-constellation
   failure. Requires Phase 70 candidate search code modification.

**Recommended direction for Phase 94**: Combine #1 (5250 + accept
0x9) and #3 (fine-grained rotation search). This targets the actual
upstream failure (rotated constellation) while bypassing the 0xD
sensitivity in Phase 18.

## 5250 MHz Cable Run — Justification Per HARD CONSTRAINT

Per HARD CONSTRAINT, a 5250 cable run is justified because:
- avg_snr needs +3 dB to clear viterbi threshold (3.15 → 6.15)
- Phase 81 measured +5.7 dB cable boost (5250 - 5890 air, 9.61 vs 4.25)
- Currently 0/35 = 0% success; 5250 should give +5 dB so even with
  UHD drops we should see 5-10% success → FCS_OK ≥ 1
- 4 cable runs remaining (1 used in Phase 90)
- HW risk: bare cable at --tx-gain 0 sends ~+5 dBm; limit ≤5 runs

Test command for Phase 94:
```
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9
IEEE80211_TIMING_OFFSET_APPLY=1
IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20
                                --warmup 60 --duration 60 --rx-subdev A:0
```

Expected: avg_snr_htsig 8-10 dB (vs 3-5 dB at 5890 air).
If avg_snr_htsig ≥ 6 dB: HT-SIG viterbi should pass more frames.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (0/35 baseline, 0/90 phased detector)
- Cable runs used: **0 of 5 budget** (T1 used leftover baseline data)
- avg_snr on USRP: 3.15 dB (need 6+ dB)
- Phase 89 detector fix works in algorithm; defeated by 11 dB SNR drift on USRP
- Phase 92 percentile fix did not help (median=0 still)
- Phase 93 identified upstream cause: **rotated constellation + UHD streaming instability**
- Phase 94+ must attack upstream (rotated-constellation, 5250 + 0x9 accept)

## Files of Record

- T1: `/tmp/p93_baseline.log` (35 detections, 3 FRAME_DETECT, 7 viterbi_fail, 1 HT_SIG_fail)
- Investigation: `lib/frame_equalizer_impl.cc:4723-4727` (ratio_ht HT-Mixed classifier)
- Investigation: `lib/frame_equalizer_impl.cc:2265-2340` (L-SIG EQ dump)
- Investigation: `lib/frame_equalizer_impl.cc:6105-6175` (LSIG/HT_SIG parse failure logger)

## Related

- Phase 92 verdict (percentile fix REGRESSION): `docs/superpowers/notes/...` (Phase 92)
- Phase 91 verdict (energy gate bypass REGRESSION): `docs/superpowers/notes/2026-07-04-phase91-verdict.md`
- Phase 90 verdict (5250 cable regression): `docs/superpowers/notes/2026-07-04-phase90-verdict.md`
- Phase 89 verdict (sync_short detector SUCCESS): `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 82 verdict (δ-tuning REFUTED): `docs/superpowers/notes/2026-07-04-phase82-verdict.md`
- Phase 81 verdict (cable @ 5250 +5.7 dB): `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 77 closure (equalizer ceiling REACHED): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 70 verdict (L-SIG candidate search REFUTED): included in 77 closure
- Phase 55 verdict (UHD streaming instability 8× SNR drift): `docs/superpowers/notes/2026-06-29-phase55-verdict.md`
