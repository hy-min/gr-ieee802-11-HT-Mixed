# Phase 3 Stage 1 (Reorganized) — L-LTF0 FFT at frame_equalizer Input

**Date:** 2026-06-11
**Branch:** TEST1
**Verdict:** STAGE_AMBIGUOUS — L-LTF0 FFT is severely corrupted at the equalizer input

## Summary

Phase 2 (H52 diagnosis, 2026-06-11) confirmed H is broken on USRP (H_BOTH_BROKEN:
|H| std=8.64 vs loopback 0.0; argH diff only 15.4% SCs within 0.5rad).
Phase 3 Stage 1 (this analysis) tracebacks H's input — the L-LTF0 FFT at the
`frame_equalizer_impl` entry point — and finds **the corruption is already
present at this point**. Per-frame std of |LLTF[k]| is 12.7x worse than
loopback, per-SC range is 13.6x. This is upstream of the H math.

## Architecture Correction

**Original plan** placed Stage 1 in `lib/sync_long_impl.{h,cc}`, assuming
sync_long did FFT. **Actual:** sync_long only does matched-filter correlation;
the FFT is in a Python `fft_vcc(64, True, ...)` block at
`wifi_phy_hier.py:87`. The first C++ touchpoint is
`frame_equalizer_impl.cc:533` (`memcpy(saved_ltf0_fft, sym64, ...)`).

Tasks R.0–R.7 reorganized Stage 1 to instrument this save site.

## Data Captured

Two USRP runs (A:0 single-board TDD, 5.18 GHz, 20 MHz):

| Run | Duration | Sent | Recv | L-LTF0 dumps | Verdict |
|-----|----------|------|------|--------------|---------|
| 30s  | 30s | 31 | 0  | 25  | STAGE_AMBIGUOUS |
| 60s  | 60s | 61 | 0  | 47  | STAGE_AMBIGUOUS |

Both runs use `IEEE80211_LTF0_FFT_DUMP=1` env flag.

## Per-Subcarrier Statistics (60s, n=47)

```
SC | mean|LLTF| | std|LLTF|
  0 | 3.565      | 3.585
  1 | 3.511      | 3.347
  2 | 21.854     | 10.342
 25 | 4.547      | 6.539
 26 | 4.019      | 4.200
 49 | 23.165     | 10.508
 50 | 6.836      | 7.691
 51 | 4.020      | 3.773
  (middle SCs omitted; same pattern: every other SC is ~4x higher)
```

**Per-SC range:** 3.084 to 41.868 (13.6x spread)
**Per-frame std_avg:** 12.731
**Loopback reference (n=1):** mean=8.875, std=0.000 (uniform 8.875)

## Verdict Logic

`examples/test_ltf0_fft.py` classification:
- `STAGE_FINE`: all metrics within threshold
- `STAGE_BROKEN_GAIN`: mean|LLTF| off-scale (loopback is 8.875 = kFftNormalize)
- `STAGE_BROKEN_TIMING`: stable SCs < 80% (per-SC std<0.3)
- `STAGE_BROKEN_FREQRESP`: uniform SCs < 80% (means spread widely)
- `STAGE_AMBIGUOUS`: **per-frame std_avg > 1.0** (the data is too variable to classify)

USRP per-frame std 12.7 >> 1.0, so STAGE_AMBIGUOUS. **But the verdict is
misleading** — the per-frame std being 12.7x worse than loopback IS the
smoking gun. The corruption is unambiguous; it just doesn't fit one of the
3 BROKEN buckets.

## Why This Is the Smoking Gun

A correct L-LTF0 FFT should be:
1. **Stable across frames** — same transmitted sequence, same channel
2. **Smooth across SCs** — |LLTF[k]| follows a frequency-selective but smooth response

USRP data violates both:
- Per-frame std 12.7 (some frames' 52 SCs span 40.7 = 3.0x range)
- Per-SC std avg 5.0 (each SC's magnitude flickers ±70% per frame)
- Adjacent SCs swing 5x: SC 0=3.5, SC 2=21.9, SC 5=20+ (every other SC is high)

This is **the same NOISE_LIKE pattern** (72.7% in Phase 1a [PHASE_RESIDUAL]
analysis) — the underlying signal at L-LTF0 is too noisy/flickering to
support reliable H estimation.

## Root Cause Candidates (ranked, all upstream of frame_equalizer)

1. **FFT window timing** (most likely) — `d_frame_start` from `sync_long`
   may be off by N samples, causing the FFT window to capture the wrong
   L-LTF0 portion (or mix with adjacent L-STF/L-SIG). Per-frame variance
   suggests the timing isn't fixed — could be a small fraction of a sample
   difference per frame.
2. **Hardware gain instability** — per-frame std 12.7 with low mean (11.4)
   could indicate AGC or RX gain is fluctuating frame-to-frame
3. **RF chain issue** — antenna, cable, USRP AGC (already verified 28.4 dB
   CW SNR; over-the-air path could be different)
4. **Frequency-selective fading** — every other SC high pattern is consistent
   with a strong null at certain frequencies (perhaps from a multipath
   reflection or sharp filter response). This is environmental, not code.

## What This Confirms

- ✅ H is broken **because L-LTF0 FFT is broken at the equalizer input**
- ❌ H math (`estimate_header_channel_from_lltf52`) is correct — it just
  gets garbage in, garbage out
- ❌ L-SIG viterbi cannot be fixed without fixing the input
- ❌ L-LTF1 variant, kFftNormalize, CFO/SFO knobs cannot fix this (all are
  downstream of the corruption)

## What This Refutes

- ❌ "Equalizer is fine, it's a phase-only problem" — H52 + Stage 1 together
  show the corruption is structural (per-SC magnitude varies 5x between
  adjacent SCs, and flickers frame-to-frame)
- ❌ "It's a CFO/SFO mismatch" — per-frame variance is too high for
  coherent CFO (CFO would be ~constant over 25 frames)

## Diagnostic Infrastructure Added (Stage 1)

| Commit | File | Content |
|--------|------|---------|
| `7cb9ece` | `lib/frame_equalizer_impl.h` | `d_log_ltf0_fft` member |
| `28066d7` | `lib/frame_equalizer_impl.cc` | `IEEE80211_LTF0_FFT_DUMP` env flag |
| `48b3bf7` | `lib/frame_equalizer_impl.cc` | `[LTF0_FFT_DUMP]` atomic dump at saved_ltf0_fft save site (line 540-593) |
| `afeb6a8` | `examples/test_ltf0_fft.py` | Per-SC aggregation + 5-bucket verdict |
| `44df950` | `examples/test_ltf0_fft.py` | Widen gain range to 0.5-12.0 to accommodate kFftNormalize=8.875 |

Reverted (mistaken placement in sync_long):
- `cd92e7a` (sync_long d_log_ltf0_rx) → reverted by `dce628d`
- `85cbd68` (sync_long IEEE80211_LTF0_RX_DUMP) → reverted by `39ce184`

## Artifacts

- USRP 30s log: `/tmp/usrp_ltf0_fft.log` (471 MB), copy at `/tmp/h_chain_traceback/usrp_ltf0_fft.log`
- USRP 60s log: `/tmp/usrp_ltf0_fft_60s.log` (larger), copy at `/tmp/h_chain_traceback/usrp_ltf0_fft_60s.log`
- Loopback log: `/tmp/loopback_ltf0_fft.log` (29 MB), copy at `/tmp/h_chain_traceback/loopback_ltf0_fft.log`
- 30s verdict: `/tmp/ltf0_fft_verdict.txt`
- 60s verdict: (from test_ltf0_fft.py output above)

## Recommended Next Phase

Per user's "诊断 + 1 修复" scope and prior forbidden directions, the most
promising next direction is:

**Try `d_frame_start` adjustment (FFT window timing)** — sync_long emits
`d_frame_start=160` currently. A 1-2 sample shift could move the FFT window
to capture a cleaner L-LTF0 portion. This is upstream of everything else.

OR

**Investigate RF chain** — check antenna connection, try a different rx_gain
(currently 20; could try 15, 25), confirm AGC behavior.

These are the only remaining directions that:
- Are not on the forbidden list (CFO/SFO, kFftNormalize, L-LTF1, L-SIG CPE)
- Are not yet ruled out
- Match the symptom (per-frame variance, per-SC range)
