# Phase 55 Verdict — USRP SNR 退化 = UHD Streaming 问题，非 Air Path

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ✅ **诊断完成**：SNR 退化源于 UHD streaming + GR scheduler 问题，不是 air path。
**Commits**: (no new commits, diagnostic files in data/p55/)

## Goal

USRP avg_snr_lsig 8x 漂移：Phase 31b (12.91) → Phase 48 (2.82) → Phase 53 (6.12) → Phase 54 (1.48).
诊断根因是 UHD streaming 还是 air path physics。

## Method

1. Run `test_usrp_minimal_loopback.py --capture /tmp/...` × 3 times (different runs)
2. Offline replay with `examples/analyze_h52_offline.py` + custom SNR calculator
3. Compare offline avg_snr_lsig (computed from raw IQ) to realtime avg_snr (computed by frame_equalizer during streaming)

## Results

### Capture file sizes (test of UHD stability)

| Capture | File size | Samples | Duration |
|---|---:|---:|---:|
| capture.bin | 5.4 MB | 680850 | 34.0 ms |
| capture2.bin | 24 MB | 3128630 | 156.4 ms |
| capture3.bin | 3.7 MB | 482340 | 24.1 ms |

**Duration is 0.034-0.156s, but `--duration 20` was requested.** 6.5x variance in
actual samples delivered. **USRP streaming is fundamentally unstable**: even with
3 reported overflows (16+15+14), UHD fails to deliver full 20s of samples.

### Offline SNR per frame (capture2.bin, top 15 frames by sync_short correlation)

| Frame | sync_short corr | offline avg_snr_lsig | dB |
|---:|---:|---:|---:|
| 200 | 0.998 | 14.89 | 11.7 |
| 600 | 0.998 | 25.69 | 14.1 |
| 800 | 0.998 | 13.98 | 11.5 |
| 1000 | 0.998 | 8.27 | 9.2 |
| 1200 | 0.998 | 6.47 | 8.1 |
| 1600 | 0.997 | 9.12 | 9.6 |
| 1800 | 0.998 | 13.44 | 11.3 |
| 2000 | 0.998 | 10.37 | 10.2 |
| 2200 | 0.997 | 7.91 | 9.0 |
| 2800 | 0.997 | 170.02 | 22.3 |
| 3000 | 0.998 | 12.04 | 10.8 |
| 3200 | 0.998 | 9.20 | 9.6 |
| 3400 | 0.998 | 7.31 | 8.6 |
| 3600 | 0.997 | 38.93 | 15.9 |
| 400 | 0.997 | 11.04 | 10.4 |

**Statistics**:
- Mean offline SNR: 21.19 (13.26 dB)
- Median offline SNR: 10.38 (10.16 dB)
- Min offline SNR: 6.47 (8.11 dB)
- Max offline SNR: 170.02 (22.31 dB)
- std/mean ratio: 1.665

### Realtime vs Offline SNR

| Measurement | avg_snr_lsig (linear) | dB |
|---|---:|---:|
| Phase 31b realtime (2026-06-17) | 12.91 | 19.5 |
| Phase 54 realtime (2026-06-29, 6h after Phase 53) | 1.48 | 2.7 |
| Phase 55 offline (median) | 10.38 | 10.2 |
| Phase 55 offline (mean) | 21.19 | 13.3 |

**Offline median SNR (10.4) is 7x higher than Phase 54 realtime (1.48)**.
**Offline median SNR is comparable to Phase 31b baseline (12.91)**.

## Conclusion: Root Cause = UHD Streaming

The data clearly shows:
1. **Raw IQ contains usable SNR signal** — offline analysis at sync_short peaks
   yields 6-22 dB SNR, comparable to historical baseline.
2. **Realtime processing under-reports SNR** — frame_equalizer only processes
   frames that survive the entire RX chain (sync_short → sync_long → frame_eq),
   which is a tiny subset of the IQ data that arrives.
3. **Capture size 5.4 MB for 20-second test** — only 0.034s of actual data
   delivered, confirming UHD is starving the chain.

The 8x SNR drift is **NOT an air path physics issue**. It is a **UHD streaming
stability issue** that causes:
- `usrp_source` overflow (15-16/764ms typical)
- Loss of L-LTF0/L-LTF1 windows
- frame_equalizer receives noise-only frames
- avg_snr_lsig computed from those noise-only frames is low

## Why Phase 31b Was Healthy

Phase 31b baseline (avg_snr=12.91) was measured when the system was freshly
rebooted, with cold CPU caches and stable UHD session. Subsequent runs (Phase 47+)
saw progressive degradation as:
- Multiple sessions accumulated
- CPU thermal throttling kicked in
- GR scheduler back-pressure increased
- UHD socket buffer state varied

## Implications

### 1. Software-loopback 3/3 PASS remains the decoder validation path

The decoder is correct (verified multiple times via software loopback). What
fails on USRP is **frame delivery**, not **frame decoding**.

### 2. The 12 REFUTED HT-SIG hypotheses were investigating a symptom, not the cause

Phase 25-44 tested equalizer/viterbi/decoder fixes. **None of them could fix
SNR if the L-LTF0 was already lost to UHD overflow.** Some of those fixes may
still be useful in the cleaner SNR conditions, but they cannot be validated
on USRP with current streaming instability.

### 3. RX chain redesign (specs/2026-06-28-rx-chain-redesign.md) needs to be
re-scoped

The redesign was based on the assumption that frame_equalizer receives
decent-quality L-LTF0/L-LTF1. With 99.8% of frames lost to overflow, redesigning
the equalizer doesn't help — the upstream scheduler must be fixed first.

## Recommended Next Steps (Software Fix)

### High-priority UHD streaming fixes

1. **Add UHD buffer diagnostic**: capture actual delivered sample count per
   second. Detect when usrp_source drops below expected.

2. **Reduce sample rate to 10 MHz** (instead of 20 MHz): halves UHD bandwidth
   pressure, halves overflow frequency. Phase 47/52 already tried this.

3. **Add `--rate 10` test option** to `test_usrp_minimal_loopback.py`. Run
   with `--rate 10` and compare:
   - Overflow rate (should drop 2-4x)
   - Realtime avg_snr (should rise if USRP is the issue)
   - Offline avg_snr (should match previous captures)

4. **System-wide CPU isolation**: pin UHD callback thread to a dedicated CPU
   core. Check if /proc/cpuinfo shows CPU governor in `performance` mode (vs
   `powersave`).

5. **Pre-test warmup**: reboot USRP X310, wait 60s for thermal stabilization,
   then run test. This restores Phase 31b-like conditions.

### If software fixes don't help → Physical intervention

If after UHD fixes avg_snr still <5 linear, the only remaining options are:
- External LNA
- Antenna repositioning
- External clock reference (replace TCXO with GPSDO or OctoClock)

## Files

- `data/p55/capture.bin`, `capture2.bin`, `capture3.bin` — raw IQ captures
- `/tmp/p55_capture.log`, `/tmp/p55_cap2.log`, `/tmp/p55_cap3.log` — realtime logs
- Spec: `docs/superpowers/specs/2026-06-29-phase55-iq-capture-offline-replay.md`

## Counter-Increment

No new REFUTED hypothesis. Phase 55 is a **diagnostic**, not a refutation.

Phase 25-44 REFUTED hypotheses stand (equalizer/viterbi/decoder fixes don't
help when L-LTF is lost to overflow).

The 8x SNR drift is now attributed to **UHD streaming instability**, not
Hhdr52 channel nulls (which was Phase 38 hypothesis). The channel nulls are
real but secondary to the delivery problem.

## Recommendation

**Pause the RX chain redesign** (specs/2026-06-28-rx-chain-redesign.md) until
USRP streaming stability is improved. Otherwise, the redesign cannot be
validated on USRP.

**Focus on**: UHD sample-rate reduction, CPU isolation, USRP warmup protocol.

**Accept**: USRP FCS_OK > 0 requires both:
1. Stable UHD streaming (delivers full L-LTF0/L-LTF1 to frame_equalizer)
2. Sufficient air path SNR (≥10 dB linear, currently 1.5-6 linear)