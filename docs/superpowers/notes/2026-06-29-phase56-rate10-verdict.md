# Phase 56 Verdict — --rate 10 SNR Recovery (Phase 55 Hypothesis PARTIAL VALIDATION)

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: PARTIAL CONFIRM — avg_snr 4.3x recovery, HT-SIG bottleneck unchanged
**Commits**: 007c8e0 (Phase 36-55 history), Phase 56 verdict (this commit)

## Goal
Validate Phase 55 hypothesis: USRP SNR 8x drift is UHD streaming overflow, not air path.
Halve UHD bandwidth via `--rate 10` (20→10 MHz) and observe if realtime avg_snr_lsig recovers.

## Test Setup
- Standard env vars: IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=4 IEEE80211_TIMING_OFFSET_APPLY=1 IEEE80211_MMSE_EQUALIZE=1 IEEE80211_MMSE_N0_PERCENTILE=25
- Args: --freq 5890 --tx-gain 20 --rx-scale 45 --duration 35 --rate 10
- Same-board A:0/A:0 (RX2) per Phase 53
- Test log: /tmp/p56_rate10.log

## Results

| Metric | Phase 54 (rate=20) | Phase 56 (rate=10) | Δ |
|---|---:|---:|---:|
| avg_snr (linear) | 1.48 | 6.35 | **4.3x ↑** |
| avg_snr (dB) | 2.7 dB | 8.03 dB | **+5.3 dB ↑** |
| HT_SIG_CAND | 0 | 0 | unchanged |
| LSIG_DECODE OK | 0 | 3 | **+3 ↑** |
| overflows/sec | ~20.3 | ~16.7 | 18% drop |
| Sent / Recv | 36 / 0 | 36 / 0 | unchanged |

8 consistent `avg_snr=6.35` measurements (no spread). LSIG viterbi fires on 3 frames (vs 0 in Phase 54).

## Conclusion: Phase 55 Hypothesis PARTIAL VALIDATION

**Confirmed**:
- UHD bandwidth reduction **substantially recovers realtime avg_snr**
- 4.3x SNR rise for ~18% fewer overflows suggests **overflow-induced buffer underruns corrupt frames more than proportionally**
- Phase 55's offline median (10.4 dB) is the ceiling; we achieved 8.03 dB (77% of ceiling)
- L-SIG viterbi path now works (3 OK events vs 0 in Phase 54)

**Not confirmed / unchanged**:
- HT_SIG_CAND remains 0 → HT-SIG decode bottleneck is **NOT in UHD streaming**
- Consistent with Phase 28/38/41 closure: HT-SIG bottleneck is equalizer/H52 channel-physics
- avg_snr 6.35 is below Phase 31b baseline (12.91 = 19.5 dB), but the gap is now **smaller**

## Counter-Increment

No new REFUTED hypotheses. Phase 56 validates Phase 55's diagnosis.

**Refined understanding**:
- 12 REFUTED HT-SIG hypotheses (Phase 25-44) are still correct
- The SNR drift they were chasing was partially UHD streaming (now mitigated)
- Remaining HT-SIG bottleneck is genuine channel-physics (Hhdr52 nulls)

## Recommended Next Steps

1. **Stabilize `--rate 10`**: Run 30+ min soak test to verify avg_snr doesn't drift back down (Phase 53-54 showed 6.12 → 1.48 in 6h)
2. **Add `--rate 10` to standard USRP test config** if soak test passes
3. **HT-SIG investigation**: Now that SNR is healthier, retry a subset of the 12 REFUTED hypotheses at 8 dB SNR (e.g., Phase 41 metric=0 events)
4. **CPU isolation / USRP warmup**: May further reduce overflow frequency below the 16.7/sec baseline

## Implications

- **Pause RX chain redesign** (`docs/superpowers/specs/2026-06-28-rx-chain-redesign.md`): Now that UHD streaming is the dominant issue, equalizer-side changes won't help until streaming is fully stable
- **Software loopback 3/3 PASS** remains decoder validation path — decoder is correct
- **Soft-decision LDPC** (Phase 54) remains a verified capability, currently unreachable on USRP because HT-SIG bottleneck is upstream

## Files
- /tmp/p56_rate10.log — Phase 56 test output (233 MB)
- docs/superpowers/notes/2026-06-29-phase55-verdict.md — referenced Phase 55 hypothesis
- docs/superpowers/specs/2026-06-29-phase55-iq-capture-offline-replay.md
- docs/superpowers/plans/2026-06-29-rate10-usrp-snr-recovery.md
