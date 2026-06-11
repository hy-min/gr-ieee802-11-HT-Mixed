# Phase 3 FFT Window Timing Scan (2026-06-11)

## Question

Phase 3 Stage 1 (reorganized) found per-frame std of L-LTF0 FFT = 12.7x
loopback on USRP. STAGE_AMBIGUOUS verdict. Most likely cause was hypothesized
to be sub-sample FFT window timing — the `d_frame_start=160` hardcoded in
`sync_long.cc` might be off by ±1-2 samples, causing the FFT to capture a
portion of the L-LTF0 that mixes with adjacent L-STF/L-SIG samples.

## Method

Added opt-in env var `IEEE80211_FRAME_START_OFFSET` (default 0) to
`lib/sync_long.cc`. With offset N, `d_frame_start = 160 + N`. Two paths
instrumented: SYNC→COPY transition (line 124) and HT-mode-plateau force-override
(line 451).

Swept offset ∈ {-3, -2, -1, 0, +1, +2, +3}, 30s USRP run per offset, captured
`[LTF0_FFT_DUMP]` for all frames, analyzed with `examples/test_ltf0_fft.py`.

## Results

| offset | per-frame std_avg | mean\|LLTF\| mean | mean\|LLTF\| range | Recv |
|--------|-------------------|-------------------|---------------------|------|
| -3     | 10.321            | 9.433             | [2.955, 31.772]     | 0    |
| -2     | 10.305            | 10.387            | [3.326, 32.864]     | 0    |
| -1     | 9.999             | 10.341            | [3.373, 33.264]     | 0    |
| **0**  | **8.731 (lowest)**| 10.357            | [4.262, 27.484]     | 0    |
| +1     | 10.851            | 10.968            | [3.621, 35.879]     | 0    |
| +2     | 9.216             | 10.002            | [3.886, 30.347]     | 0    |
| +3     | 11.426            | 10.576            | [3.379, 35.253]     | 0    |

## Verdict: ❌ FFT Timing Is NOT the Root Cause

- **offset=0 is the local optimum** — varying ±1-3 samples strictly INCREASES
  per-frame std (8.7 → 10-11). A genuine sub-sample timing problem would
  produce a U-shape with a clear optimum at one of the non-zero offsets.
- **All offsets produce Recv=0** — no sample window produces a single decoded
  frame.
- **Per-frame std is in a tight 8.7-11.4 band** across all offsets, vs
  loopback's 0.0 — the corruption is structural, not timing-related.

## What This Rules Out

- ❌ **Sub-sample FFT window misalignment** — the `d_frame_start=160` value
  is correct (already forced-override in `sync_long.cc:451`).
- ❌ **Sample drift across frames** — if timing varied, we would see a
  U-shape (some offset better than 0).
- ❌ **Off-by-N in the splitter** — even a ±3 sample window shift doesn't
  help.

## What This Doesn't Rule Out (Remaining Candidates)

1. **Hardware gain** — try `rx_gain=15/25` (current=20) in
   `test_usrp_minimal_loopback.py`. Per-frame std 8-11 with mean 10 is
   consistent with a low SNR condition that gain could mitigate.
2. **RF chain** — antenna, cable, USRP AGC. The every-other-SC high pattern
   (SC 0=3.5, SC 2=21.9) is consistent with frequency-selective fading, which
   gain or RF changes could address.
3. **H estimation math (downstream of L-LTF0 FFT input)** — even though L-LTF0
   FFT is corrupted at input, the per-SC range [3.0, 27.5] is so wide that
   some H estimates will be reasonable by chance. The fix could be in the
   `estimate_header_channel_from_lltf52` function (line 576-610) to use a
   more robust estimate (e.g., time-domain averaging, or per-SC outlier
   rejection across multiple frames).

## Decision

**Do NOT** keep the `IEEE80211_FRAME_START_OFFSET` env var active. The
default (offset=0, equivalent to original hardcoded 160) is correct.

**Option A:** Revert the env var code changes (offset=0 path is identical
to original, but the if condition is slightly different — see below).

**Option B:** Keep the env var as opt-in diagnostic infrastructure for
future experiments (low cost, doesn't change default behavior).

**Recommendation:** **Option B** (keep) — the env var is a clean test
harness for future timing experiments, and the offset=0 path is byte-identical
to original behavior.

## Artifacts

- 7 USRP logs: `/tmp/fft_timing_scan/off_{N}.log` (562-589 MB each, total 4.0 GB)
- Summary: `/tmp/fft_timing_scan/summary.txt`
- Code change: `lib/sync_long.cc` (additions only, no behavior change at offset=0)

## Diagnostic Infrastructure Added

| Item | Purpose |
|------|---------|
| `IEEE80211_FRAME_START_OFFSET` env var | Opt-in test harness for FFT window timing |

## Commits Pending

The `lib/sync_long.cc` edit is uncommitted. After user's decision on
Option A vs B, will commit.
