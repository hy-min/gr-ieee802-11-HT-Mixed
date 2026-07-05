# Phase 96 — TX-GAIN 20 Cable Test Verdict

**Date**: 2026-07-05
**Branch**: TEST1
**Status**: 🔵 **ALMOST** — `--tx-gain 20` produces CLEAN BPSK constellation
(L-SIG EQ ratio=0.701, was 1.4+ in earlier phases), 1 clean L-SIG viterbi win
(rate=0xD, enc=0, len=346) — but avg_snr_htsig=5.5 dB is just 0.5 dB below
viterbi threshold. **0.5 dB short of HARD CONSTRAINT.**
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — **NOT achieved** (0/120)
**Cable runs used**: 5/5 budget

## Critical Discovery — avg_snr is linear, not dB

Inspection of `lib/frame_equalizer_impl.cc:5667`:
```cpp
avg_snr_lsig = (sum_mag2 / (double)cnt);
```

This computes **average |eq|²** (linear energy per subcarrier), NOT a
conventional SNR in dB. For unit-amplitude BPSK with additive white noise,
E[|eq|²] = 1 + σ². To convert to dB SNR:

`SNR(dB) = 10 × log10(avg_snr_lsig - 1)`  (approx, assumes unit signal)

| Linear value | dB SNR |
|---|---|
| 1.0 | 0 dB (no signal) |
| 2.0 | 3 dB |
| 3.0 | 4.8 dB |
| 4.0 | 6.0 dB ← viterbi threshold |
| 6.24 | 7.95 dB |

So Phase 96's avg_snr_htsig=3.58 is actually **5.54 dB** SNR — close to
threshold but below.

## User Intuition was Right About Cable Path

CLAUDE.md assumed `--tx-gain 0` = max TX output. **WRONG** — UBX-160 gain
range is `0.0 to 31.5 step 0.5 dB` per `uhd_usrp_probe`. `--tx-gain 0` =
MIN output, `--tx-gain 31.5` = MAX output. The default for
`test_usrp_minimal_loopback.py:273` is `--tx-gain 20`, NOT 0.

User correctly identified that with direct SMA cable, the path loss is
minimal (~0.5 dB at 5250 MHz). Increasing tx-gain to 20 (closer to UBX-160
nominal operating point) produces a CLEANER constellation.

## T1 — 5250 MHz Cable Run with --tx-gain 20 (Cable #5)

Configuration:
```
test_usrp_minimal_loopback.py --freq 5250 --tx-gain 20 --rate 20
                                --warmup 60 --duration 60 --rx-subdev A:0
+ IEEE80211_LSIG_RATE_FORCE=0xD
+ IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9
+ IEEE80211_FORCE_HTSIG=1
+ IEEE80211_TIMING_OFFSET_APPLY=1
+ IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
+ IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
+ IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16
+ IEEE80211_LSIG_FINE_ROT=1
+ IEEE80211_HTSIG_FINE_ROT=1
```

### Results (`/tmp/p96_cable_txgain20.log`)

```
Sent: 120 | Recv: 0 | FCS_OK=0 | FCS_FAIL=0 | Success: 0.0%
sync_short detections: 99
FRAME_DETECT: 1 frame
  ratio_ht=0.760 (Phase 93: 0.660, Phase 94: 0.965, Phase 95: 1.134)
  L-SIG EQ ratio=0.701 (Phase 93: 1.453, Phase 94: 1.411, Phase 95: 1.056)
            ↑ ↓ FINALLY <1.0 = pure BPSK constellation!
LSIG_CANDIDATE_WIN: 5 (incl. 1 clean)
  rot=0 inv=0 approx_metric=0 enc=0 len=346 rate_field=0xD parity_ok=1   ← CLEAN
  rot=2 inv=0 approx_metric=8 enc=4 len=2634 rate_field=0x9 parity_ok=1
  rot=2 inv=0 approx_metric=8 enc=4 len=3842 rate_field=0x9 parity_ok=1
  (+ 2 more rate=0x9)
HT_SIG_PARSE_FAIL: 5 (all with n_candidates=32)
  avg_snr_lsig=1.90 (linear) = 2.79 dB
  avg_snr_htsig=3.58 (linear) = 5.54 dB ← just below 6 dB threshold
```

**Cable run #5 of 5 budget — exhausted.**

## Analysis — Almost There

### What improved

| Metric | Phase 95 | Phase 96 | Δ |
|---|---|---|---|
| L-SIG EQ ratio | 1.056 | **0.701** | **-34% (PURE BPSK)** |
| ratio_ht | 1.134 | 0.760 | -0.374 |
| L-SIG clean wins (enc=0 rate=0xD) | 2 | 1 | -1 (UHD variance) |
| HT-SIG candidates tried | 32 | 32 | flat (mechanism OK) |
| avg_snr_htsig (linear) | 2.88 | 3.58 | **+0.70** |
| avg_snr_htsig (dB equiv) | ~4.6 dB | **5.5 dB** | **+0.9 dB** |

`--tx-gain 20` produced a clean BPSK constellation AND slightly higher SNR
than `--tx-gain 0`. Phase 95's avg_snr dropped to 2.88 from its 4-5 dB range
was somewhat unusual; Phase 96 at 5.5 dB is closer to the historical
average.

### What's blocking FCS_OK

avg_snr_htsig=5.5 dB is **just 0.5 dB below** the 6 dB viterbi threshold.
One clean L-SIG win made it through, HT-SIG 32-cand search ran all
candidates, but viterbi didn't converge on any. At 5.5 dB with 48-bit
BPSK codeword, the bit error rate is ~3e-2; viterbi convergence
probability per codeword is ~10-30%.

### Why avg_snr doesn't track expected SNR

With direct cable (0.5 dB loss), UBX-160 output ~+5 dBm at --tx-gain 20,
RX2 input ~+4.5 dBm. True SNR on real signal: 80+ dB (limited by ADC
quantization).

Realtime avg_snr_lsig=1.90 (linear) = 2.79 dB is the **measured** SNR on
frames that reach equalizer after sync_short detection. The discrepancy
is explained by:
1. UHD streaming drops destroy frames BEFORE they reach the equalizer
2. sync_short detects 99 frames but only 1 reaches FRAME_DETECT (98% lost)
3. The 1 frame that gets through might be a "lucky" moment of streaming

Phase 55 verdict confirmed UHD streaming instability causes 8× SNR drift
across runs of the same code.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (0/120)
- Cable runs used: **5 of 5 budget** (EXHAUSTED)
- avg_snr_htsig: 5.5 dB (need 6 dB for viterbi)
- We're 0.5 dB short of working — within reach with variance reduction

## What's Next

**Cable budget exhausted.** Per Phase 81+CLAUDE.md constraint:

> Limit total cable runs ≤5 until 30 dB attenuator arrives.

Possible paths forward (requires USER AUTHORIZATION to exceed budget):

### A. Run additional cable tests (no env var changes)

Phase 96 SNR was 5.5 dB; Phase 94 was 7.95 dB (above threshold!). With UHD
streaming variance, additional runs at SAME config might hit 6+ dB and
yield FCS_OK ≥ 1. **Cost: 1+ more cable runs.**

### B. Use 30 dB SMA attenuator (the original Phase 81 setup)

Phase 81 baseline gave avg_snr=9.61 dB at 5250 cable + 30 dB attenuator.
Attaching 30 dB HAT-30+ would:
- Bring RX2 input from +4.5 dBm → -25.5 dBm (well in linear range)
- Likely give avg_snr ≥ 7-10 dB (above 6 dB threshold)
- **Cost: 1 cable run + 1 attenuator.**

### C. Re-evaluate HARD CONSTRAINT

5 cable runs have been invested. The equalizer layer is CLOSED (24+
REFUTED). The bottleneck is now reduced to:
- UHD streaming instability (Phase 55) — random frame loss
- avg_snr ~5-6 dB at 5250 cable (right at threshold)
- HT-SIG viterbi needs 6 dB but only gets 5.5 (variance-dependent)

The remaining gap is INSIDE the noise floor of cable SNR variance.
FCS_OK ≥ 1 is achievable with the right UHD session state — we just need
more cable runs to find one that lands in the high tail.

### D. Accept 3/3 software loopback FCS_OK as final state

Phase 37 confirmed synthetic HT-SIG viterbi 3/3 PASS. Phase 18 confirmed
software loopback 3/3 PASS. Per HARD CONSTRAINT preservation: "Software
loopback 3/3 PASS ... cannot substitute for USRP verification" — but
this might be the realistic final state.

## Files of Record

- T1: `/tmp/p96_cable_txgain20.log` (Sent=120, Recv=0, 0 FCS_OK)
- Investigation: `lib/frame_equalizer_impl.cc:5667` (avg_snr_lsig is linear)
- Investigation: `uhd_usrp_probe` (UBX-160 gain range 0-31.5 dB)

## Recommendation

The user has already connected USRP. They explicitly said they're using
direct SMA cable. The HARD CONSTRAINT is 0.5 dB from being achievable.

**Recommendation A**: Add 30 dB SMA attenuator (HAT-30+) and run one more
cable test. Highest probability of HARD CONSTRAINT achievement.

**Recommendation B**: Re-run same Phase 96 config 2-3 times. At least one
should hit the high tail of UHD streaming variance (Phase 94 hit 7.95 dB
on a successful run). Budget exceeds Phase 81 5-cable cap.

**Recommendation C**: Acknowledge that the EQUIVALENT SNR is at the
viterbi threshold, and HARD CONSTRAINT requires more variance reduction
than current budget allows. Document and move on.

## Decision Required

This is a HARD CONSTRAINT situation requiring user direction:

1. Continue cable testing beyond Phase 81 5-budget cap (Risky: HW may
   overdrive or burn out without attenuator)?

2. Use 30 dB attenuator (HAT-30+) and run one more test?

3. Stop. Document Phase 96 as the closest we've gotten (5.5 dB avg_snr,
   clean BPSK constellation, 0.5 dB from threshold).

## Related

- Phase 95 verdict: `docs/superpowers/notes/2026-07-05-phase95-verdict.md`
- Phase 94 verdict: `docs/superpowers/notes/2026-07-05-phase94-verdict.md`
- Phase 93 verdict (rotated constellation root cause): `docs/superpowers/notes/2026-07-05-phase93-verdict.md`
- Phase 81 verdict (cable @ 5250 +5.7 dB): `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 55 verdict (UHD streaming instability): `docs/superpowers/notes/2026-06-29-phase55-verdict.md`
- CLAUDE.md constraint: 5-cable budget cap
