# Phase 57 Verdict — --rate 10 30-min Soak Test

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ⚠️ **MARGINAL** — `--rate 10` provides partial SNR recovery but is NOT stable enough for standard config promotion.
**Commits**: (this commit)

## Goal

Verify `--rate 10` (halve UHD bandwidth) doesn't drift back down like Phase 53-54's
6.12 → 1.48 in 6h. Phase 56 single-test confirmed 1.48 → 6.35 (+5.3 dB).
Phase 57 tests stability across 3 runs with 5-min idle periods.

## Test Setup

- Standard env vars: IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=4 IEEE80211_TIMING_OFFSET_APPLY=1 IEEE80211_MMSE_EQUALIZE=1 IEEE80211_MMSE_N0_PERCENTILE=25
- --freq 5890 --tx-gain 20 --rx-scale 45 --duration 35 --rate 10
- Same-board A:0/A:0 (RX2) per Phase 53
- 3 runs × 35s + 2 × 5min idle = ~20 min total wall-clock
- Summary log: /tmp/p57_soak_summary.txt

## Results

### Per-Run Metrics

| Run | Time | avg_snr | HT_SIG_CAND | LSIG_OK | overflows |
|---|---|---:|---:|---:|---:|
| 1 | 16:00:02 | 3.65 | 0 | 2 | 39 |
| 2 | 16:07:12 | 2.00 | 16 | 6 | 27 |
| 3 | 16:15:03 | 3.96 | 0 | 1 | 47 |

### Stability Analysis

- avg_snr mean: 3.20 (std=1.05, range=2.00-3.96)
- Coefficient of variation: **CV = 0.329**
- Verdict per Phase 57 plan thresholds:
  - CV < 0.20 → STABLE
  - 0.20 ≤ CV < 0.50 → **MARGINAL** ← actual
  - CV ≥ 0.50 → UNSTABLE

### Comparison to Phase 56 Baseline

| Metric | Phase 56 (single) | Phase 57 (soak mean) | Soak max | Soak min |
|---|---:|---:|---:|---:|
| avg_snr (linear) | 6.35 | 3.20 (50.4%) | 3.96 (62.4%) | 2.00 (31.5%) |
| HT_SIG_CAND | 0 | 5.33 (mean) | 16 | 0 |
| LSIG_OK | 3 | 3.00 (mean) | 6 | 1 |
| overflows/run | — | 37.67 | 47 | 27 |

**Soak mean is 50% of Phase 56 baseline. Best run is only 62% of baseline.**

## Conclusion: MARGINAL — Do NOT Promote to Standard Config

`--rate 10` is a real but partial SNR recovery. Compared to the worst baseline
(Phase 54's 1.48), every run is 1.4-2.7x better. But compared to the Phase 56
single-test 6.35, the 3-run soak mean is half.

### Key Findings

1. **No monotonic drift** — 3.65 → 2.00 → 3.96 is non-monotonic, not a "running down" pattern
2. **HT_SIG_CAND bimodal** — 0/16/0 distribution suggests bursty/lucky frame behavior
3. **Overflows are chronic** — 27-47 per 35s run = ~0.8-1.3/sec
4. **Coefficient of variation 0.33** sits in the middle of the MARGINAL band

### Why Not Promote

- A 50% run-to-run variance is too high to call this "stable"
- 2/3 runs have HT_SIG_CAND=0 — most runs are still upstream-blocked
- Standard config promotion should require CV < 0.20 (STABLE band)

## Recommendation: KEEP --rate 10 as Opt-In Partial Recovery

`--rate 10` is strictly better than the no-fix state in all 3 runs (1.4-2.7x above
Phase 54 baseline). It does not solve the underlying UHD streaming instability —
it merely changes the timing/buffering dynamics in a way that sometimes helps.

### When to use --rate 10

- Trying to capture occasional HT_SIG_CAND events on USRP
- Offline replay experiments where data volume matters more than realtime
- Diagnostic testing where bursty SNR is acceptable

### When NOT to use --rate 10

- When seeking consistent FCS_OK > 0
- For overnight soak tests (may drift further)
- As a "production" USRP config

## Next Step: Phase 58 Investigation Pivots

To achieve truly stable USRP streaming, consider:
1. **CPU isolation** — pin UHD callback thread to a dedicated core
2. **USRP warmup protocol** — 60s post-boot idle before tests
3. **UHD socket buffer tuning** — increase recv buffer beyond defaults
4. **--rate 5** — even more conservative bandwidth (1/4 of standard)
5. **GR scheduler tuning** — investigate block rates/buffer sizes in `wifi_phy_hier.py`

## Files

- /tmp/p57_soak_summary.txt — 3-run summary log
- docs/superpowers/notes/2026-06-29-phase56-rate10-verdict.md — single-test baseline
- docs/superpowers/notes/2026-06-29-phase55-verdict.md — UHD streaming root cause
- docs/superpowers/plans/2026-06-29-rate10-soak-test.md — this test plan

## Counter-Increment

No new REFUTED hypothesis. Phase 57 is a **stability characterization**, not a refutation.
Phase 55 root cause (UHD streaming) stands. Phase 56 single-test result stands.
Phase 57 adds: "single test is not enough — stability across runs reveals the
50% variance not visible in a single 35s run."

## Implications

- **--rate 10 is documented as a partial recovery option** but NOT a standard config
- **Software loopback 3/3 PASS** remains decoder validation path
- **Pause RX chain redesign** until streaming stability is solved
- **HT-SIG bottleneck remains channel-physics** (Phase 28/38/41) — even when UHD
  streaming is healthier, HT-SIG parse still fails intermittently
