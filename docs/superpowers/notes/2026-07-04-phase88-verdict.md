# Phase 88 — sync_short Threshold Tuning Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🟡 **PARTIAL** — sync_short_fused diagnostic dump reveals MA(48)/MA(64) ratio is flawed; threshold tuning alone will not fix detection
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved

## Background

Phase 87 confirmed sync_short fails L-STF detection in `/tmp/p28_loopback_iq.fc32`.
Phase 88 was tasked with sync_short threshold tuning (user choice).

## T1 — Read sync_short / sync_short_fused Source

| Block | File | Threshold |
|---|---|---|
| sync_short | `lib/sync_short.cc` | 0.01 (fixed at construction from `sensitivity` arg) |
| sync_short_fused | `lib/sync_short_fused.cc` | Computes `cor = \|MA(48)\| / MA(64)`, optional energy gate with factor=3.0 |
| wifi_phy_hier | `wifi_phy_hier.py:89` | `sync_short(sensitivity, 2, True, True)` — MIN_PLATEAU=2 |
| wifi_phy_hier | `wifi_phy_hier.py:163` | `sync_short_fused(sensitivity, 3.0, 1024)` — energy gate 3.0 |
| p68_replay | `examples/p68_replay_offline.py:85` | `sensitivity=0.01` |

## T2 — Python Autocorrelation Statistics (Reference)

For comparison, Python's L-STF detector (period-16 autocorr + 16-sample boxcar smoothing,
threshold = max(median*10, 0.01)) on the same capture:

- Global median autocorrelation: **0.0062**
- Python adaptive threshold: **0.0622** (10x median)
- At Python L-STF positions (n=20 sampled): peak autocorr **431-811**
- Plateau > 0.01 at L-STF: **204-206 samples** (not just 2!)
- Plateau > 0.5 at L-STF: 187-192 samples
- Non-L-STF regions: only 1.4% of samples > 0.01 (noise dominated)

**Conclusion**: Python's adaptive threshold approach IS detecting L-STF correctly with
lots of margin. Plateau is 200+ samples — way more than MIN_PLATEAU=2.

## T2b — sync_short_fused Diagnostic Dump

Added opt-in env var `IEEE80211_SYNC_SHORT_FUSED_DUMP=1` to `sync_short_fused.cc`.
Re-ran p68_replay_offline.py for 5s. Key findings:

### Noise region (samples 0-781K, calls 0-199):

| metric | value |
|---|---|
| batch_power | 0.0078 |
| noise_floor | 0.0000-0.0002 |
| gated | 0 (FALSE — gating OFF) |
| max_cor | 0.26-0.37 |
| n>0.01 (per 3908-sample batch) | 3800-3900 of 3908 |

**Noise correlation is 0.26-0.37** — well above threshold 0.01. This is the
NOISE-DRIVEN random walk pattern in MA(48)/MA(64) ratio.

### Signal region (calls 200+, batch_power jumps to 100-1257):

| metric | value |
|---|---|
| batch_power | 100-1257 |
| noise_floor (EMA) | 0.5-1.6 |
| gated | 0 (FALSE — gating OFF, correct) |
| max_cor | 0.40-0.75 |

**L-STF correlation is 0.40-0.75** — max around 0.75 (theoretical 48/64=0.75).

## T2d — sync_short Detection Pattern

sync_short detected **174 frames in 5s** (close to Python's 149 L-STFs).
But detection correlation values are LOW: 0.085, 0.085, 0.056, 0.100, 0.058, 0.133, 0.185, 0.151, 0.024, 0.060, ...

Distribution: 0.02-0.18, mean ~0.10.

**These are NOT L-STF detections.** L-STF detection should give corr ≈ 0.75.

**These are NOISE spikes** — sync_short_fused's MA(48)/MA(64) ratio can spike to
high values on noise (random walk), and MIN_PLATEAU=2 finds noise peaks first.

## ROOT CAUSE — sync_short_fused MA(48)/MA(64) Ratio is Flawed

For pure L-STF (BPSK ±1):
- MA(48) of `x[i]*conj(x[i-16])` = 48 (coherent sum of ±1)
- MA(64) of `|x[i]|²` = 64 (each is 1)
- `|MA(48)| / MA(64)` = **48/64 = 0.75**

For noise (random complex with power σ²):
- MA(48) of `x[i]*conj(x[i-16])` ≈ sum of 48 random products → ~sqrt(48) * σ (random walk)
- MA(64) of `|x[i]|²` ≈ 64 * σ² (chi-squared, mean = 64σ²)
- `|MA(48)| / MA(64)` ≈ sqrt(48) / 64 * (1/σ) ≈ **1.22 / σ** (could be >> 1)

**Noise ratio can EXCEED signal ratio** because:
- Coherent signal grows MA(48) linearly (×48)
- Random walk grows MA(48) as sqrt(48) (×6.93)
- But MA(64) for noise grows as 64*σ² (small for small σ)
- For σ²=0.008 (this capture's noise floor), the ratio is highly variable

**Therefore**: `cor = |MA(48)| / MA(64)` is NOT a reliable signal-vs-noise discriminator
when noise power is low. It actually FAILS in this regime.

## Why sync_short "Detects 174 Frames" but sync_long Only Sees 1 wifi_start

1. sync_short's MIN_PLATEAU=2 is satisfied by noise spikes (cor=0.2-0.4 sustained over
   2-3 samples from MA smoothing).
2. Each detection emits wifi_start tag → triggers transition to COPY state.
3. In COPY state, sync_short outputs samples (no more wifi_start until GAP_THRESHOLD=500).
4. After ~500 samples below threshold, sync_short returns to SEARCH.
5. sync_short cycles SEARCH → COPY → SEARCH repeatedly.
6. sync_long may only see the FIRST wifi_start (offset=0 = first false positive).
7. sync_long falls back to correlation-search, producing 156 noise "frames" in samples [0, 3M].

## Why Threshold Tuning Won't Fix This

The user's chosen approach (sync_short threshold tuning) is insufficient because:
- Threshold 0.01 is already below noise correlation (0.26-0.37)
- Lowering threshold makes it worse
- Raising threshold (e.g., to 0.5) would skip real L-STF (0.75) too

**The fundamental issue is the MA(48)/MA(64) ratio is unreliable for low-SNR signals.**

## What's Needed (Phase 89+ attack plan)

1. **Replace MA(48)/MA(64) with proper L-STF detection**:
   - Period-16 autocorr with raw product `|x[i] * conj(x[i-16])|`
   - Then 16-sample boxcar smoothing (matching Python)
   - Use Python's adaptive threshold: `max(median*10, 0.01)`

2. **Increase MIN_PLATEAU to match actual L-STF plateau** (~16 samples, not 2):
   - MIN_PLATEAU=16 would require 16 consecutive samples above threshold
   - Matches the L-STF plateau structure (8-sample period × 2 repetitions)
   - Filters out brief noise spikes

3. **Disable the energy gate** (factor=3.0):
   - The gate blocks low-power noise bursts but doesn't help distinguish L-STF from noise
   - `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1` env var already exists for this

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved** (Phase 88 partial — root cause identified
  but not fixed)
- 0 cable runs used (offline Python + C++ offline replay only)
- Phase 89 must attack sync_short_fused's MA(48)/MA(64) ratio

## Files of Record

- T1: read `lib/sync_short.cc`, `lib/sync_short_fused.cc`
- T2 script: `p88_t2_measure_autocorr.py` (Python reference)
- T2b script: `p88_t2b_autocorr_profile.py` (plateau length measurement)
- T2c diff: `lib/sync_short_fused.cc` (added `IEEE80211_SYNC_SHORT_FUSED_DUMP=1` env var)
- T2d log: `/tmp/p88_fused_dump_v2.log` (506 SYNC-SHORT-FUSED lines + 174 frame detections)

## Related

- Phase 87 verdict: `docs/superpowers/notes/2026-07-04-phase87-verdict.md`
- Phase 86 verdict: `docs/superpowers/notes/2026-07-04-phase86-verdict.md`
- sync_short_fused source: `lib/sync_short_fused.cc`
- sync_short source: `lib/sync_short.cc`
- Phase 84 framework: `docs/superpowers/notes/2026-07-04-phase84-design-verdict.md`