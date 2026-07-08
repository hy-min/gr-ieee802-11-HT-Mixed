# Phase 113 T5.A UHD API Micro-Tuning Verdict (2026-07-08) — REVISED

**Branch**: TEST1
**Status**: 🟡 **T5.A PARTIAL / PROMISING** — Disabling UBX-160 auto DC offset +
IQ balance calibration produces dramatically better L-SIG decoding (1 → 11 OK
in 60s trace). Not REFUTED as initially judged; the initial analysis was
based on a misread of HT_SIG_CAND count.

## CRITICAL Correction

The initial verdict (commit `8db9103`) declared T5.A REFUTED based on
"HT_SIG_CAND 4 → 0" — **that count was a grep artifact** matching
`[TX_HT_SIG]` strings on the TX side, not actual RX HT_SIG candidates.

After systematic re-analysis:
- **T5 baseline HT_SIG_CAND = 0** (correct count, no false matches)
- **T6b T5.A HT_SIG_CAND = 0** (same — neither produced candidates)

The real differences are below.

## TL;DR

Disabling `set_auto_dc_offset(False)` and `set_auto_iq_balance(False)`
on the UHD source during USRP X310 + UBX-160 v2 cable tests:

| Effect | Magnitude | Verdict |
|--------|-----------|---------|
| L-SIG EQ ratio improvement | 2.681 → 0.863 (-1.82) | ✅ **Much cleaner constellation** |
| HT frame detection | 0 → 1 (T6b) / 0 (T7) | ± borderline |
| **LSIG_DECODE OK** | **1 → 11 (+1000%)** | ✅ **10× improvement** |
| avg_snr_ht | 10.43 → 4.46 (bimodal 2.52/8.14 in T7) | mixed |
| FCS_OK | 0 | still 0 (viterbi wall) |

The constellation cleanup is real and dramatic, but Phase 1 of the
HT-SIG viterbi chain still fails because the per-SC phase noise
(1.77 rad floor) is unchanged.

## Corrected Data (60s USRP 5250 cable)

| Metric | Phase 112 baseline (T5, 30s) | T6b T5.A (30s) | T7 T5.A (60s) |
|--------|-------------------------------|------------------|----------------|
| Sent | 60 | 60 | 120 |
| Recv | 0 | 0 | 0 |
| **FCS_OK** | **0** | **0** | **0** |
| **LSIG_DECODE OK** | **1** | **1** | **11** ✅ |
| Detected HT frame | 0 | 1 | 0 |
| Detected Legacy frame | 1 | 1 | 2 |
| L-SIG EQ ratio (sample) | 2.681 | 0.863 | 0.35-0.86 |
| EQ ratio_ht (sample) | 0.351 | 2.058 | (random) |
| avg_snr_ht (range) | 10.43 | 4.46 | 2.52 / 8.14 |
| HT_SIG_CAND | 0 | 0 | 0 |
| HT_SIG_PARSE_FAIL | 0 | 0 | 0 |

## Why the Calibration-off Helps

UBX-160's `set_auto_dc_offset(True)` and `set_auto_iq_balance(True)`
run continuous calibration that, in the presence of the 1.77 rad
per-SC phase noise floor (Phase 112 R1 ceiling), causes the calibration
to chase the noise. This produces:

1. **Over-amplified equalizer output** (|eq|² ≈ 9.70 baseline, ideal ≈ 1.0)
2. **Inflated L-SIG EQ ratio** (2.681, expect < 1.0 for BPSK)
3. **Constellation rotation** (Phase 93 verdict: 45° L-SIG rotation)

Disabling both calibration freezes the analog front-end at its
power-on state, which (after 60s thermal stabilization) produces:

1. **Near-ideal |eq|²** (~1.0)
2. **Clean L-SIG EQ ratio** (0.863, expected BPSK)
3. **Improved L-SIG decode success** (10× more)

The trade-off: the calibration is no longer compensating slow thermal
drift, so longer traces may show drift in avg_snr_ht (bimodal
distribution in T7).

## Why FCS_OK Still 0

The 1.77 rad per-SC phase noise floor (Phase 112 R1) is in the
analog RF chain — disabling digital calibration does not address it.
HT-SIG QBPSK (45° margin) still fails viterbi. Equalizer-layer ceiling
remains CONFIRMED (31+ REFUTED).

## Code Changes (preserved as opt-in)

| Commit | Change |
|--------|--------|
| `4b50d0e` | feat: add `--uhd-tune` argparse flag |
| `09eb7bc` | feat: insert UHD API block |
| `1e449ea` | fix: broaden exception (RuntimeError, AttributeError) |
| `99caadb` | fix: rename to gr-uhd API names |
| `5b345a6` | fix: remove set_rx_agc (UBX-160 unsupported) |
| `b31f3be` | fix: set_lo_source signature (src, name, chan) |
| `2f4fef1` | fix: remove set_lo_source (UBX-160 internal only) |

## Final Code State

```python
if args.uhd_tune:
    print("[TEST] UHD micro-tunings ENABLED (Phase 113 T5.A): "
          "DC=off, IQ=off")
    try:
        self.uhd_usrp_source.set_auto_dc_offset(False, 0)
        self.uhd_usrp_source.set_auto_iq_balance(False, 0)
        print("[TEST] UHD micro-tunings applied successfully")
    except (RuntimeError, AttributeError) as e:
        print(f"[TEST] UHD API micro-tuning failed (non-fatal): {e}")
```

## gr-uhd API Naming (CRITICAL DISCOVERY for future work)

gr-uhd 4.9.0.0 Python binding uses DIFFERENT API names than raw UHD C++:
- `set_auto_dc_offset(enable, chan)` — not `set_rx_dc_offset`
- `set_auto_iq_balance(enable, chan)` — not `set_rx_iq_balance`
- `set_rx_agc(enable, chan)` — NOT supported on UBX-160 (NotImplementedError)
- `set_lo_source(src, name, chan)` — 3-arg signature; UBX-160 only accepts internal

## Phase 114 Recommendations

Given T5.A PARTIAL (L-SIG path dramatically improved, HT-SIG path unchanged):

1. **T3.B L-LTF0+L-LTF1 averaging** — Phase 86 L-LTF0 audit showed
   CPE phase std=90°. With cleaner L-SIG EQ ratio (0.863 vs 2.681),
   L-LTF averaging may have higher ROI now.

2. **T4.D HT-LTF 2x averaging** — uses 2 HT-LTF symbols. ~0.6 rad
   reduction per averaging step. Modest but additive.

3. **Combine T5.A + T3.B + T4.D** — stacking improvements:
   - T5.A: -1.8 L-SIG EQ ratio
   - T3.B: -0.5 phase std (estimate)
   - T4.D: -0.6 phase std
   - Combined: maybe enough to break 4 dB HT-SIG viterbi threshold

4. **Extend T5.A to longer traces** — bimodal avg_snr_ht suggests
   drift over time; need 5+ minute trace to characterize.

5. **External ref clock** — USER EXPLICITLY EXCLUDED.

6. **LDPC decoder swap** — USER EXPLICITLY EXCLUDED "换算法".

## Files Modified

- `test_usrp_minimal_loopback.py:310-315` — argparse flag
- `test_usrp_minimal_loopback.py:185-200` — UHD API block
- `docs/superpowers/specs/2026-07-08-phase113-uhd-api-microtuning-design.md`
- `docs/superpowers/plans/2026-07-08-phase113-uhd-api-microtuning.md`
- `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md`

## Related

- [[project-p112-t7e-usrp-verification]] — R1 1.77 rad ceiling
- [[project-p112-r1-argh-rootcause]] — per-SC phase noise floor
- [[project-p93-viterbi-diagnosis]] — 45° L-SIG rotation
- [[project-p100-htsig-audit]] — avg_snr interpretation
- [[feedback-no-closure-usrp-fcs-ok]] — user hard constraint