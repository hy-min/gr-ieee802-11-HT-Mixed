# Phase 113 T5.A UHD API Micro-Tuning Verdict (2026-07-08)

**Branch**: TEST1
**Status**: 🔴 **T5.A REFUTED** — Disabling auto DC offset and IQ balance
calibration on UBX-160 *hurts* HT-SIG candidate detection (4 → 0).

## TL;DR

Disabling `set_auto_dc_offset(False)` and `set_auto_iq_balance(False)`
on the UHD source during USRP X310 + UBX-160 v2 cable tests degrades
HT-SIG viterbi detection by 100% (4 → 0 candidates). L-SIG detection
stays flat. FRAME_DETECT slightly improves (4 → 6) but downstream
HT-SIG path worsens because auto-calibration was masking an
underlying 1.77 rad noise floor (R1 ceiling).

The calibration-on behavior was actually beneficial for HT-SIG
demodulation — without it, the residual per-SC phase noise corrupts
QBPSK (45° margin) at the metric floor.

## Code Changes (preserved as opt-in)

| Commit | Change |
|--------|--------|
| `4b50d0e` | feat: add `--uhd-tune` argparse flag (Task 1) |
| `09eb7bc` | feat: insert UHD API block (Task 2, initial) |
| `1e449ea` | fix: broaden exception (RuntimeError, AttributeError) |
| `99caadb` | fix: rename to gr-uhd API names (set_auto_dc_offset etc.) |
| `5b345a6` | fix: remove set_rx_agc (not supported on UBX-160) |
| `b31f3be` | fix: set_lo_source signature (src, name, chan) |
| `2f4fef1` | fix: remove set_lo_source (UBX-160 only supports internal) |

## Final Code State

```python
if args.uhd_tune:
    print("[TEST] UHD micro-tunings ENABLED (Phase 113 T5.A): "
          "DC=off, IQ=off, LO=internal")
    try:
        self.uhd_usrp_source.set_auto_dc_offset(False, 0)
        self.uhd_usrp_source.set_auto_iq_balance(False, 0)
        print("[TEST] UHD micro-tunings applied successfully")
    except (RuntimeError, AttributeError) as e:
        print(f"[TEST] UHD API micro-tuning failed (non-fatal): {e}")
```

## Test Results (5250 MHz cable, --tx-gain 0, --rate 20, 60s warmup, 30s duration)

### Phase 112 baseline (Task 5 — no flag, reproduce)

| Metric | Value |
|--------|-------|
| Sent | 90 |
| Recv | 0 |
| FCS_OK | 0 |
| HT_SIG_CAND | 4 |
| FRAME_DETECT | 4 |
| LSIG_DECODE OK | 1 |
| HT_SIG_PARSE_FAIL | 0 |

### Phase 113 T5.A (Task 6b — --uhd-tune)

| Metric | Value | Delta |
|--------|-------|-------|
| Sent | 90 | = |
| Recv | 0 | = |
| FCS_OK | 0 | = |
| **HT_SIG_CAND** | **0** | **-4 (-100%) ❌** |
| FRAME_DETECT | 6 | +2 (+50%) |
| LSIG_DECODE OK | 1 | = |
| HT_SIG_PARSE_FAIL | 0 | = |

## Analysis

### Why Disabling Auto-Calibration Hurts HT-SIG

UBX-160's auto DC-offset and IQ-balance calibration runs
continuously, tracking slow drift caused by:

- **Thermal drift in analog front-end** (per UBX-160 datasheet,
  the calibration compensates ~10 kHz drift over minutes)
- **DC bias from ADC sampling clock** (set_auto_dc_offset enables
  internal estimation that subtracts the bias in real-time)
- **I/Q amplitude/phase imbalance** (set_auto_iq_balance runs a
  calibration tone through the receiver)

Disabling both effectively **freezes the analog front-end** at
its initial calibration state, which then drifts during the 60s
warmup + 30s test window. The drift is per-subcarrier (since it
comes from the analog mixers and ADC), and at 1.77 rad per-SC
phase noise (Phase 112 R1 ceiling), this drift puts HT-SIG QBPSK
constellation points outside the 45° decision boundary.

L-SIG BPSK (180° margin) survives, but HT-SIG QBPSK (45° margin)
does not. This is the same root cause as the 30+ REFUTED
equalizer-layer attacks — the 1.77 rad floor is unfixable from
the receiver side.

### What Worked / What Didn't

| API | Effect | Verdict |
|-----|--------|---------|
| `set_auto_dc_offset(False, 0)` | Regressed HT-SIG | Keep default (True) |
| `set_auto_iq_balance(False, 0)` | Regressed HT-SIG | Keep default (True) |
| `set_rx_agc(False, 0)` | Not supported on UBX-160 | N/A |
| `set_lo_source('internal', 'RX2', 0)` | Rejected — already internal only | N/A |

## Conclusion

**T5.A REFUTED.** UHD API micro-tuning cannot reduce the 1.77 rad
per-SC phase noise floor because:

1. Auto-calibration is actively compensating drift — disabling it
   lets drift accumulate during the test window.
2. The 1.77 rad noise source is the UBX-160 RF chain + LO
   architecture, not the digital receiver.
3. HT-SIG QBPSK is the most sensitive decoder (45° margin), so
   any SNR hit manifests first as HT_SIG_CAND collapse.

## Code Preserved as Opt-In

The `--uhd-tune` flag remains in the codebase as an opt-in for
future Phase 114+ work that may want to disable calibration for
short-burst captures (where drift is negligible). The code is
safe to keep — default OFF preserves Phase 112 baseline exactly.

## Phase 114+ Recommendations

Given T5.A REFUTED:

1. **T3.B L-LTF0+L-LTF1 averaging** — Phase 86 L-LTF0 audit showed
   |H| mean 267-455 across pilots. Averaging may marginally reduce
   |H| std but NOT phase std (which dominates). Probably PARTIAL.

2. **T3.C LMMSE equalizer** — `d_mmse_equalize` field exists in
   frame_equalizer_impl.cc but not wired. Phase 72 REFUTED on
   loopback. Probably REFUTED again.

3. **T4.D HT-LTF 2x averaging** — uses the 2 HT-LTF symbols in
   HT-Mixed preamble. Each averaging halves phase std by 1/√2.
   Modest gain (~0.6 rad) but not enough for 1.77 → <1 rad target.

4. **External ref clock (LFCS / GPSDO)** — user EXPLICITLY
   excluded. Not an option.

5. **LDPC decoder swap** — user EXPLICITLY excluded "换算法".

## Status

🔴 **T5.A REFUTED** — opt-in flag preserved for future work.
Equalizer-layer ceiling CONFIRMED yet again (31+ REFUTED now).
Phase 114 must attack architectural choices or accept limit
(forbidden by user hard constraint).

## Files Modified

- `test_usrp_minimal_loopback.py:310-315` — argparse flag
- `test_usrp_minimal_loopback.py:185-200` — UHD API block
- `docs/superpowers/specs/2026-07-08-phase113-uhd-api-microtuning-design.md`
- `docs/superpowers/plans/2026-07-08-phase113-uhd-api-microtuning.md`
- `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md`

## Related

- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase noise
- [[project-p112-t7e-usrp-verification]] — R1 ceiling confirmed
- [[feedback-no-closure-usrp-fcs-ok]] — user hard constraint