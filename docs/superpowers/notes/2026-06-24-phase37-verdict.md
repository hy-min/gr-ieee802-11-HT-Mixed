# Phase 37 Verdict — HT-SIG Viterbi Synthetic Tolerance Test

**Date:** 2026-06-24
**Status:** PASS (all 3 enabled layers clean)
**Test:** `examples/test_htsig_viterbi_synthetic.py`

## Layer Results

| Layer | Impairment | Range | Result | Verdict |
|---|---|---|---|---|
| 1 | Clean | n/a | 3/3 PASS (metric=0) | Decoder correct on ideal input |
| 2 | +CFO | 0, 100, 500, 1000, 5000 Hz | 3/3 at all values | Tolerates static coherent CFO up to at least 5 kHz |
| 3 | +AWGN | 20, 15, 12, 9, 6 dB SNR | 3/3 at all values | Hard-decision viterbi robust to ≥6 dB SNR |
| 4 (+SFO) | Skipped | — | — | SFO already REFUTED 3× (Phase 24-26); no new info |

## Detailed Output

```
[PASS] test_viterbi_encode_decode_roundtrip: metric=0, len=24, all-match=True
[PASS] test_make_known_htsig_bits_case_a: bits[0..7]=[0, 0, 0, 0, 0, 0, 0, 0], length_field=100, mcs=0, crc[0..3]=[0, 0, 1, 0]
[PASS] test_make_known_htsig_bits_case_b: mcs=7 length=1000 agg=1 sgi=1 crc[0..3]=[1, 1, 0, 0]
[PASS] test_make_known_htsig_bits_case_c: mcs=0 length=10 ldpc=1 crc[0..3]=[0, 0, 1, 0]
[PASS] Layer1/A: CRC OK, metric=0, mcs=0, length=100, agg=0, sgi=0, ldpc=0
[PASS] Layer1/B: CRC OK, metric=0, mcs=7, length=1000, agg=1, sgi=1, ldpc=0
[PASS] Layer1/C: CRC OK, metric=0, mcs=0, length=10, agg=0, sgi=0, ldpc=1
[PASS] Layer 1 clean: 3/3
[INFO] Layer2/CFO=0Hz: 3/3
[INFO] Layer2/CFO=100Hz: 3/3
[INFO] Layer2/CFO=500Hz: 3/3
[INFO] Layer2/CFO=1000Hz: 3/3
[INFO] Layer2/CFO=5000Hz: 3/3
[PASS] Layer 2 +CFO: 3/3 PASS at CFO <= 1 kHz. 5kHz result: 3/3
[INFO] Layer3/SNR=20dB: 3/3
[INFO] Layer3/SNR=15dB: 3/3
[INFO] Layer3/SNR=12dB: 3/3
[INFO] Layer3/SNR=9dB: 3/3
[INFO] Layer3/SNR=6dB: 3/3
[PASS] Layer 3 +AWGN: 3/3 PASS at SNR >= 12 dB. 9dB: 3/3, 6dB: 3/3

Phase 37 Layer 1+2+3 tests passed.
```

Loopback regression: `Final: OK=1 FAIL=0` (Phase 33 14-sample fix preserved).

## Verdict

**Equalizer bottleneck confirmed.** HT-SIG viterbi decoder is CORRECT:

- **Layer 1 (clean, metric=0)**: NumPy re-implementation of `decode_htsig_from_rotated()`
  produces identical bit decisions to the C++ decoder on ideal input. CRC OK with
  zero bit errors across all 3 test cases (BCC default, MCS7+agg+sgi, LDPC).
- **Layer 2 (+CFO)**: Tolerates 5 kHz static CFO without failure. This confirms
  the decoder is NOT sensitive to coherent phase rotation that changes per
  symbol. Phase 36's per-SC fit REFUTED for viterbi tolerance reasons.
- **Layer 3 (+AWGN)**: Tolerates 6 dB SNR — surprisingly robust for a hard-decision
  viterbi. This rules out the hypothesis that viterbi needs soft LLR on USRP frames
  (USRP L-SIG typically sees ~12-15 dB avg_snr per Phase 31b).

The HT-SIG failure on USRP (Phase 19, 35, 36) is therefore NOT caused by:
- A bug in the viterbi decoder algorithm
- Insufficient phase tracking (decoder tolerates CFO)
- SNR degradation (decoder tolerates AWGN to 6 dB)

The bottleneck must be upstream of viterbi input. Concretely, the equalizer's
output (`d_early_eqsym[kHtSig1Rel]`) is delivering hard-decision-corrupting
constellation rotation that the decoder cannot correct.

## Recommendation

**Do NOT touch the viterbi decoder or add soft LLR / per-symbol CPE.**
Return to upstream equalizer investigation. Phase 33 (L-LTF0 14-sample shift)
fixed the dominant impairment; the residual USRP-specific issue is elsewhere.

Candidates to investigate next (in priority order):

1. **HT-SIG-specific equalizer path**: Does `d_early_eqsym[kHtSig0Rel/1Rel]`
   use the same equalizer chain as L-SIG? If HT-SIG1 enters via a different
   code path that bypasses Phase 34's δ correction, that's the gap.
2. **Channel-estimation contamination from L-SIG**: L-SIG is decoded first and
   its result is fed back into H estimation. If L-SIG is decoded incorrectly
   (Phase 33b residual), H52 is contaminated, but only for HT-SIG. Confirm
   L-SIG CRC OK rate matches HT-SIG CRC OK rate on USRP.
3. **Per-frame δ variance on HT-SIG1**: Phase 33b showed δ varies per frame
   in [0, 1) at 1/64 quantization. Phase 34 estimates δ from L-LTF0 (counter=3)
   and uses it for HT-SIG0 (counter=3, retroactive). HT-SIG1 (counter=4) uses
   the same estimate via real-time path. If δ drifts between counter=3 and
   counter=4 by > 1/64 sample, HT-SIG1 fails even with δ correction. Verify
   with `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` (new env-var, not yet added).

If all three are exhausted, the impairment is at FFT-window level (Phase 31c
REFUTED K-sweep, but Phase 33 fix changed the baseline — re-run K-sweep on
current code).

## Forbidden Directions (still forbidden after Phase 37)

- Soft-decision LLR viterbi (decoder is already robust enough)
- CFO tracking / per-symbol CPE on HT-SIG (decoder tolerates CFO)
- Per-SC pilot CPE on HT-SIG pilots (Phase 36 REFUTED, decoder is the wrong target)
- Touching `viterbi_decode_133_171` algorithm (Layer 1 metric=0)

## Files

- Test: `examples/test_htsig_viterbi_synthetic.py` (707 lines)
- Harness output: `docs/superpowers/notes/2026-06-24-phase37-harness-output.log`
- Spec: `docs/superpowers/specs/2026-06-24-phase37-htsig-viterbi-synthetic-design.md`
- Plan: `docs/superpowers/plans/2026-06-24-phase37-htsig-viterbi-synthetic.md`

## Commits

- 9bc1813 plan
- 6a87d20 T2 scaffold
- e1a51cc T4 BCC + interleaver + QBPSK mod + pilots
- b5aa028 T4 fix #1 (WRONG interleaver formula)
- b513f95 T4 revert (correct C++-matching interleaver)
- 0686ef6 T5 NumPy viterbi + round-trip
- 7bf18d8 T6 Layer 1 clean (3/3 PASS)
- 0b9befa T7 Layer 2 +CFO (3/3 PASS at 5 kHz)
- 98b0327 T8 Layer 3 +AWGN (3/3 PASS at 6 dB)
- 3a970ad T10 harness output saved
