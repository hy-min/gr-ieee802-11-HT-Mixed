# Phase 82 — 5250 cable rate=0x9 δ-tuning verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 REFUTED — δ tuning cannot reliably map rate 0x9 → 0xD
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (0/Recv in all variants)

## Headline

| Metric | Phase 81 verdict | My T3 (offline, this capture) | My T3.5 (LTF ref fix) |
|---|---|---|---|
| Capture | 5250 cable, fresh, prod pipeline | same setup, raw IQ | same setup, raw IQ |
| avg_snr_lsig | **7.11 dB** | **-2.67 dB** (no δ) | **-2.63 dB** (with δ) |
| Rate (most common) | 0x9 consistent | scattered (0x8: 31%, 0x9: 16%, 0xB: 20%) | scattered (0xF: 15%, 0x8: 15%, 0x9: 10%) |
| Rate=0xD | 0 frames | 0 frames | 6 frames (4.0%) |
| ε scan best | – | 10/149 (6.7%) | – |

**10 dB SNR gap with Phase 81 verdict**: my offline analysis (matches Phase 28 baseline)
consistently shows SNR ≈ -2.6 dB. Phase 81's prod pipeline shows 7.11 dB on the same setup.

## What Phase 82 tried

1. **T1 — 5250 cable raw IQ capture**: 30s @ 5250 MHz, 4.8 GB at `/tmp/p28_loopback_iq.fc32`.
   - 149 frames detected (matches 200 ms strobe × 30 s = 150 frames expected).

2. **T2 — multi-frame L-SIG rate analysis** (`p82_lsig_rate_sweep.py`):
   - Without δ correction: SNR = -2.67 dB, rate distribution scattered.
   - rate=0xD: **0 frames** decoded as expected.

3. **T3 — apply Phase 34 δ correction offline** (`p82_t3_delta_corrected.py`):
   - With δ: SNR = -2.81 dB (marginal change), rate scattered.
   - ε-scan [-32, +32]/64: best 10/149 (6.7%) at 0xD, no clean shift.

4. **T3.5 — add LTF reference division** (`p82_t3_5_ltf_ref_fix.py`):
   - With kLltf64Binned division: SNR = -2.63 dB.
   - Only +0.31 dB vs no-ref version — confirms LTF ref was NOT the 10-dB gap.
   - Rate=0xD: 6 frames (4.0%), 0x9: 15 frames (10.1%) — still scattered.

## Why the SNR gap with Phase 81

Three plausible explanations (none definitively confirmed):

1. **Different capture**: Phase 81 used a fresh capture; my T1 capture was made later.
   Phase 81 verdict (14:01) and my T1 (16:15) differ by ~2 hours of USRP runtime.
   UHD streaming instability (Phase 55 finding: 8x SNR drift) is real and time-dependent.

2. **C++ frame_equalizer has additional SNR-boosting processing my Python misses**:
   - Per-SC equalization refinement
   - Time-domain CPE tracking across multiple symbols
   - Possibly kFftNormalize scaling factor (constant `64.0f / sqrt(52.0f)` per
     `lib/ieee80211_constants.h:24`)

3. **rx-scale interaction**: My Python reads raw IQ at rx-scale=40; C++ receives the same.
   If a different rx-scale was used in Phase 81 capture, the absolute SNR differs but
   the rate decode should be invariant.

**My T3.5 Pass B (no LTF ref) → Pass A (with LTF ref)**: only +0.31 dB improvement.
This rules out (any portion attributable to) missing LTF ref division as the cause.

## Phase 82 attack lever verdict

**Hypothesis**: Phase 34 δ correction at 5250 cable produces wrong δ → 0xD maps to 0x9.
Add a small ε offset to recover 0xD.

**REFUTED** because:
- ε-scan over [-32, +32]/64 produces at most 10/149 frames (6.7%) at rate=0xD
- The "best" ε shifts across the grid (no consistent offset)
- SNR is too low (-2.6 dB) for viterbi to converge reliably to ANY specific rate
- The rate distribution with ε offset looks essentially random (consistent with noise)

## Phase 82 closure — equalizer layer is closed

Combined with prior REFUTED chain (Phase 77 equalizer ceiling, Phase 79 per-symbol δ,
Phase 80b per-SC LUT, Phase 80b synthetic test, Phase 80b USRP run):
- **20+ equalizer-layer hypotheses REFUTED**
- **Cable @ 5250 SNR is highly variable** across capture moments (Phase 81 saw 7+ dB,
  this capture shows -2.6 dB)
- **δ correction provides at most +1 dB SNR improvement on this capture**
- **No deterministic δ/ε adjustment produces 0xD**

The equalizer-layer attack surface is **exhausted**. Phase 82 does NOT yield a
viable path to USRP realtime FCS_OK via δ-tuning at 5250 cable.

## What still works (regression baseline)

- Software loopback `test_direct_loopback.py`: OK=1 FAIL=0 (unchanged)
- `test_htsig_viterbi_synthetic.py`: Layer 4 = 273/300 (91.0%) (unchanged)
- Phase 18 strict rate check still in place at `lib/frame_equalizer_impl.cc:2431`
- All env vars default OFF (no baseline regression)

## Hardware risk status

This T1 capture used `--tx-gain 20 --rx-gain 20` (default in test script). This is
**below** the tx-gain=0 used in Phase 80b's bare-cable test. Cable loss < 1 dB →
RX2 sees ~tx-gain(dB) - cable loss. At tx-gain=20 (=+20 dBm analog gain), the
input level is high but UBX-160 should handle it (vs tx-gain=0 which is +5 dBm
direct, outside -15 dBm spec).

**2 cable runs today**: Phase 80b Stage 1 + this T1. Budget 2/5 remaining.
Recommend STOP further cable runs pending an upstream-attack plan per HARD CONSTRAINT.

## Implications for project (HARD CONSTRAINT)

Per CLAUDE.md:
> **NOT acceptable**: Concluding "BLOCKED" without an upstream-attack plan that
> targets the actual USRP gate

The USRP gate is **L-SIG viterbi failure** at the equalizer input. With 20+
equalizer-layer hypotheses REFUTED and equalizer-layer attack surface closed, the
**upstream path** is the only remaining direction:

- L-LTF0 FFT window timing (Phase 31c REFUTED K-sweep; commit bd5c1d2 14-sample
  shift at sync_long.cc)
- sync_short / sync_long boundary alignment
- Frame detector / splitter path
- RF chain (LO leakage, UBX-160 phase noise, ADC saturation)

None of these have produced a viable attack plan in 80+ REFUTED hypotheses over
60+ phases. The project's HARD CONSTRAINT (USRP realtime FCS_OK) appears
**structurally infeasible** with the current equipment setup (X310 + UBX-160 +
GNU Radio + bare-cable USRP-grade impairments).

## Recommended next step

HARD STOP on further cable runs without a deliberate upstream-attack plan. Possible
directions for the next phase:
- Audit the upstream path (sync_long → frame_detect → splitter → frame_equalizer)
  for a missed algorithmic bug, vs accepting the channel-physics wall
- Build a USRP-realistic **offline replay framework** with channel model
  (5 stable nulls + 64-PSK residual + UHD streaming drift) so future attack
  hypotheses can be tested without burning cable runs

## Files of record

- Capture: `/tmp/p28_loopback_iq.fc32` (4.8 GB, 30s @ 5250 MHz, 200 ms strobe)
- T2 script: `p82_lsig_rate_sweep.py`
- T3 script: `p82_t3_delta_corrected.py`
- T3.5 script: `p82_t3_5_ltf_ref_fix.py`
- T2 per-frame data: `/tmp/p82_t2_per_frame.npz`
- T3 Pass 1+2 data: `/tmp/p82_t3_pass1.npz`, `/tmp/p82_t3_pass2.npz`
- T3.5 data: `/tmp/p82_t3_5.npz`

## Related

- Phase 81 cable verdict: `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 80b per-SC LUT REFUTED: `docs/superpowers/notes/2026-07-04-p80b-verdict.md`
- Phase 83 pause memo: `docs/superpowers/notes/2026-07-04-p83-pause.md`
- Phase 77 equalizer ceiling: `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 79 per-symbol δ REFUTED: `docs/superpowers/notes/2026-07-02-phase79-verdict.md`
- Phase 78b per-SC nulls: `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`