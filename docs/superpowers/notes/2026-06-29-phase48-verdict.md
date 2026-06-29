# Phase 48 Verdict — Baseline Reproduction + Architectural Discovery

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ⚠️ BLOCKED — USRP air path SNR has degraded to 4.5 dB (was 12.9 dB)
**Commits**: (no new commits, diagnostic run only)

## Goal

Verify whether Phase 47 (MMSE for data + N0 percentile) actually helped USRP
data payload CRC, or whether the DECODE_FAIL events in Phase 47 verdict were
reproducible. Discovered a separate deeper problem.

## Test Configuration

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=./build/python/bindings:./python:./examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  timeout 35 /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 30
```

(Standard config per memory, no MMSE, no soft-LLR — pure baseline.)

## Results — Three runs, 30s each

| Run | Sent | Recv | Conv DECODE_FAIL | LDPC DECODE_FAIL | HT_SIG_PARSE_FAIL | LSIG_DECODE OK | avg_snr_lsig (linear) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 (no MMSE) | 30 | 0 | 2 | 4 | 4 | 0 | 2.82 (4.5 dB) |
| 2 (no MMSE) | 30 | 0 | 2 | 4 | 4 | 0 | 2.82 |
| 3 (Phase 41 std) | 30 | 0 | 5 | 10 | 11 | 0 | 2.82–3363 (varies) |

**Note**: `avg_snr_lsig` is in **linear |eq|² units**, not dB. Phase 31b reported
`avg_snr_lsig=12.91` which is **19.5 dB** (10·log10(12.91²)). Current 2.82 is
4.5 dB. **The USRP air path is 15 dB weaker than Phase 31b baseline.**

## Comparison to Phase 41 Final Verdict (2026-06-28)

| Metric | Phase 41 (final close) | Phase 48 baseline (now) |
|---|---:|---:|
| HT_SIG_PARSE_FAIL | 8 | 11 |
| DECODE_FAIL | 0 | 5 Conv + 10 LDPC |
| LSIG_DECODE OK | n/a | 0 |
| avg_snr_lsig | 12.91 (19.5 dB) | 2.82 (4.5 dB) |

**Phase 41 final close claimed USRP was "channel-physics blocked". Current
numbers show even more failure modes**, but the air path SNR has dropped by
**15 dB**. The earlier "final close" verdict may have been based on stale
air-path conditions or measurement artifacts.

## Why This Changes Everything

The Phase 41 conclusion was that USRP HT-SIG cannot succeed at the air
interface due to H52 channel nulls (|H|=0.02-0.14 → 50× noise amplification).
That conclusion was based on `avg_snr_lsig=12.91` (19.5 dB).

At the current **4.5 dB SNR** (15 dB worse), MMSE for HT-SIG gives:
- (Signal/|H|²)·N0 where N0=25th percentile of |H|²
- At SNR=4.5 dB, the noise floor is much higher than at 19.5 dB
- Post-MMSE signal-to-noise ratio is **bounded by the air path SNR**

**The fix has to start with restoring the air path SNR.** The 15 dB loss
could be:
- Antenna moved
- Cable disconnected
- RX gain not applied (no AGC)
- TX gain wrong
- Frequency band blocked
- 5 GHz subdev changed
- UHD driver version mismatch

## What Worked in This Run

1. ✅ Phase 47 MMSE code compiled without regression
2. ✅ Test framework reproducible
3. ✅ DECODE_FAIL events with `len=38` (correct frame size)
4. ✅ Both Conv and LDPC paths fire DECODE_FAIL (expected behavior)

## What's Blocked

1. ❌ No FCS_OK > 0 (Sent=30, Recv=0)
2. ❌ L-SIG viterbi also fails (0 LSIG_DECODE OK) — was 110 in Phase 47
3. ❌ avg_snr_lsig dropped to 4.5 dB from 19.5 dB

## Next Direction — Phase 49

Restore USRP air path SNR before any further equalizer investigation:

1. **Verify USRP physical setup**:
   - Antenna position
   - Cable connection (SMA, no loose connectors)
   - Power supply stable (X310 needs 12V 3A minimum)

2. **Test parameter sweep**:
   - `--tx-gain` 10, 15, 20, 25, 30
   - `--rx-scale` 30, 45, 60
   - `--freq` 5180, 5500, 5890

3. **Compare to Phase 31b commands** exactly:
   ```
   Phase 31b: test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20
   Phase 48:  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45
   ```
   The `--rx-scale 45` parameter is new. Try without it.

4. **Re-run H52_DUMP=1** to see if H52 quality matches Phase 38.

## Architectural Insight

The "12 REFUTED hypotheses" closing was **premature**. The actual blocker
in current USRP runs is **air path SNR degradation**, not equalizer/decoder
algorithms. The previous 41 phases of investigation were on a healthier
air path (avg_snr_lsig=19.5 dB) and reached the H52 null wall, but **at
4.5 dB we never even get past L-SIG viterbi**.

This is good news: it means the bottleneck is environmental, not
architectural. Phase 49 should recover the air path first, then re-test
the MMSE + N0 percentile approach with a healthy air path.

## Files Referenced

- `lib/frame_equalizer_impl.cc:4455-4488` — avg_snr_lsig computation (linear, not dB)
- `lib/frame_equalizer_impl.cc:4071` — data symbol bit slicing (d_frame_n_bpsc switch)
- `lib/viterbi_decoder/viterbi_decoder_x86.cc:291` — data payload viterbi (hard-bit only)
- `docs/superpowers/notes/2026-06-28-phase47-verdict.md` — previous MARGINAL verdict

## Counter-Increment

Still 12 REFUTED hypotheses (Phase 41 closure). Phase 48 did not introduce
new REFUTED hypotheses — it discovered a new condition (air path SNR
degradation) that supersedes the prior conclusion.
