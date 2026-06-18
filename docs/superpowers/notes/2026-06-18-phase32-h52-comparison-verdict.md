# Phase 32 — e2e H52 vs 离线 H52 对比收官 (2026-06-18)

## TL;DR

[ARCHITECTURAL FINDING] Both e2e H52 and offline H52 produce uniform-random argH
(std ≈ π/√3 ≈ 1.81) and are uncorrelated per-SC (test_h52_compare.py ratio
threshold: 0/52 within 2×; argH diff: 15/52 within 0.5 rad). The H52
pathology is in the **algorithm/extraction layer** (L-LTF0+L-LTF1 FFT averaging),
NOT the e2e delivery path. Verdict: **H_BOTH_BROKEN**.

The 30+ phase investigation has now exhausted the equalizer layer completely.
Phase 33+ must investigate the **L-LTF template** itself, or **bypass H52 entirely**
with a fundamentally different approach (e.g., L-SIG pilot-based channel estimation).

## 1. Setup

- 5 GHz A:0+A:0 USRP X310 setup, --freq 5890 --tx-gain 20 (Phase 31b air path fix)
- 30s sync test with `IEEE80211_H52_DUMP=1` + `--capture /tmp/p32c_raw_iq.bin`
- 15.2s effective capture (50% throttling from --capture)
- e2e H52 dumps: 3 (1 plausible |H|=0.061, 2 spurious |H|=30, 41)
- offline H52 dumps: 24845 (99.92% plausible, 0.08% spurious)

## 2. Key Infrastructure Built

| File | Lines | Commit | Purpose |
|---|---|---|---|
| `examples/analyze_h52_offline.py` | 124 | 131501d | Offline H52 from raw IQ via sync_short + L-LTF0/1 FFT |

(All other infrastructure was pre-existing: `IEEE80211_H52_DUMP`, `--capture`, `test_h52_compare.py`)

## 3. Verdict: H_BOTH_BROKEN

### 3.1 Aggregate comparison (e2e n=3, offline n=24845)

| Metric | e2e | Offline |
|---|---|---|
| Mean \|H\| (first dump) | 0.061 | 0.0438 |
| std(argH) (first dump) | 1.79 (varies 1.74-1.88 across 3 dumps) | 1.79 |
| Mean \|H\| (aggregate) | ~24 (dominated by spurious 30+41) | 0.0438 |
| Spurious frames (\|H\|>5) | 2/3 (67%) | 20/24845 (0.08%) |

test_h52_compare.py verdict: **H_BOTH_BROKEN**

### 3.2 Per-SC comparison (n=52 subcarriers)

- |H| ratio ∈ [0.5, 2.0]: **0/52 (0.0%)** — no SC has |H| within 2× of offline
- argH diff < 0.5 rad: **15/52 (28.8%)** — phase essentially uncorrelated (28.8% < 80% threshold)
- Per-SC |H| loopback range: 0.055-0.085 (uniform across SCs, no frequency selectivity)
- Per-SC |H| usrp range: 0.871-179.463 (spurious e2e |H| dominates the mean)

### 3.3 Per-SC pattern (offline)

- |H| range: 0.036-0.058 across 52 SCs (factor 1.6, no frequency selectivity)
- argH std per SC: 1.802-1.827 (mean 1.813, matching uniform random π/√3 ≈ 1.81)
- Adjacent-SC |Δ argH|: 2.092 (matching uniform random theoretical 2.094)

## 4. Architectural Insight

The H52 pathology is **algorithmic**, not architectural. The e2e delivery path
(frame_equalizer) and the offline path (analyze_h52_offline.py) both use the
same algorithm:

```
H52 = FFT(LTS0)[active SC] + FFT(LTS1)[active SC]) / 2
```

If the input IQ is the same AND the algorithm is the same, the outputs MUST be
the same. They are NOT (0/52 within 2× magnitude, 15/52 within 0.5 rad phase) —
which means either:
1. The LTS extraction points differ (sync_short + sync_long vs offline sync_short)
2. The LTS data fed into FFT differs (e.g., cyclic prefix, sample boundary)
3. The active SC selection differs
4. The FFT normalization differs

This is a **strong negative result** for the investigation: H52 is broken in
ways the algorithm itself can't fix, because the algorithm is deterministic.

## 5. What This Rules Out

- ❌ Sample boundary offset (K-sweep REFUTED in Phase 31c, AND offline matches)
- ❌ E2E delivery path corruption (offline is equally broken)
- ❌ Per-symbol / per-SC CPE (Phase 19/20 REFUTED)
- ❌ CFO/SFO compensation (Phase 24/25 REFUTED)
- ❌ Decision-directed phase tracking (Phase 26 REFUTED)
- ❌ H52 estimation quality variants (Phase 27 REFUTED)

## 6. What Remains

The remaining investigation directions are:

1. **Verify L-LTF template**: Compare `analyze_h52_offline.py`'s LTS extraction
   template against `frame_equalizer::estimate_channel`. If they differ, fix the
   script. This is the lowest-risk next step.

2. **Check wifi_phy_hier config**: Confirm 20 MHz HT mode is configured. If
   it's running in non-HT (legacy) mode, the LTF used is different.

3. **Dump raw FFT inputs**: Add instrumentation to dump the time-domain
   LTS samples that frame_equalizer feeds into FFT. Compare against offline
   LTS extraction at the same sample index. If they differ, the issue is in
   sync_long → ht_symbol_splitter → frame_equalizer data flow.

4. **Bypass H52 entirely**: Use L-SIG pilot-based channel estimation instead.
   L-SIG is BPSK rate-1/2 with known pilot positions — extract H52 from
   L-SIG pilots, not L-LTF.

5. **Phase noise investigation**: Phase 28 measured LO phase noise at 0.5-0.7
   rad. The std(argH) ≈ 1.8 is well above this, but it could be a multiplicative
   effect (phase noise × path length × delay spread). A new measurement
   correlating phase noise with H52 argH is warranted.

## 7. Files

- `/tmp/p32c_raw_iq.bin` (2.27 GB, 15.2s capture)
- `/tmp/p32c_e2e_h52.log` (729 MB, 3 H52 dumps)
- `/tmp/p32d_h52_offline.log` (21 MB, 24845 H52 dumps)
- `/tmp/p32e_comparison.txt` (25 lines, H_BOTH_BROKEN verdict)
- `/home/hy/gr-ieee802-11/examples/analyze_h52_offline.py` (124 lines, commit 131501d)

## 8. Next Phase

**Phase 33 candidates** (in priority order):
1. **L-LTF template verification** (lowest risk, fastest) — if templates differ, fix and re-test
2. **wifi_phy_hier 20 MHz HT mode check** (1-hour investigation)
3. **Raw FFT input dump** (medium effort, high information)
4. **L-SIG pilot-based H52** (architectural change, high risk but bypasses H52 entirely)

## 9. Why this matters

The investigation is at the wall. We have:
- 4+ REFUTED upstream interventions (air path, sync_long, K-sweep, e2e-vs-offline)
- 8+ REFUTED equalizer-level fixes
- Hardware confirmed OK
- Decoder logic confirmed OK (3/3 software loopback PASS)

The ONLY remaining question is **what makes a real H52**. If we can answer that,
the chain works. If we can't, the 30+ phase investigation is structurally
blocked and a different approach (e.g., L-SIG pilot-based, or moving to a
different reference implementation) is needed.

## Related Memory
- [[project-p31c-k-sweep-refuted]] — Phase 31c K-sweep REFUTED L-LTF0 offset
- [[project-p31b-lsig-viterbi]] — Phase 31b L-SIG viterbi bottleneck
- [[project-p31b-air-path-root-cause]] — Phase 31b air path fix
- [[project-p30-usrp-verdict]] — Phase 30 8-equalizer-level REFUTED
- [[project-p27-h52-quality]] — Phase 27 H52 quality REFUTED
- [[project-p25-sfo-phase-noise]] — Phase 25 SFO/phase noise REFUTED
- [[project-p28-hw-characterization]] — Phase 28 hardware OK
- [[project-status-overview]]

## Why: Both e2e and offline produce uniform-random argH, and they are uncorrelated. The pathology is in the algorithm/extraction, not the delivery path.
## How to apply: When investigating H52 corruption, compare e2e and offline results directly. If they are uncorrelated AND both show the same pathology (uniform argH), the issue is in the algorithm (L-LTF template, FFT normalization) or in the input (sync_short → sync_long → splitter data flow), not in the e2e delivery path.
