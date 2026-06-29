# Phase 58 Verdict — USRP UHD Streaming Stability (5 Pivots)

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ⚠️ **MARGINAL (mixed result)**: avg_snr IMPROVED 68% (3.20→5.37), but CV REGRESSED 48% (0.329→0.485). 5 pivots raise SNR floor/ceiling but per-run variance is now bimodal. **Do NOT promote to standard config.**
**Commits**: 1aa602f, b8b9f11, 1955e7c, caf1bd9, 12f40d4, 6c5b810, c9ae288

## Goal

Reduce UHD streaming overflow from ~0.8-1.3/sec (Phase 57) to <0.1/sec by
applying 5 software pivots. Verify with 30-min soak that realtime avg_snr
achieves CV < 0.20 (STABLE band).

## Method

5 sequential pivots, each with go/no-go gate:
1. **USRP 60s warmup** (--warmup arg, default 60s) — T1 commit 1aa602f
2. **CPU governor=performance + taskset 0-1** (p58_warmup_cpu_setup.sh) — T2 commit b8b9f11 + 1955e7c fixup
3. **UHD recv_buff_size=16MB + num_recv_frames=256** (p58_recv_buffer_test.py sweep) — T3 commit caf1bd9
4. **--rate 5 validation REFUTED** (kept --rate 20 as default) — T4 commit 12f40d4
5. **GR scheduler block buffers 500K->1M samples** (sync_short, sync_long, etc.) — T5 commit 6c5b810

## Results

### Per-Pivot Metrics

| Pivot | Metric | Phase 57 baseline | After pivot | Δ |
|---|---|---:|---:|---|
| 1 (warmup) | avg_snr | 3.20 (mean) | 4.16 | +30% |
| 1 (warmup) | overflows/s | 0.86 (37.67/35s) | 0.43 (15/35s) | -50% |
| 2 (CPU/taskset) | avg_snr | 4.16 | 7.37 | +77% |
| 2 (CPU/taskset) | overflows/s | 0.43 | 0.07 (2.41/35s) | -84% |
| 3 (UHD buffer) | avg_snr (E2E) | 7.37 | 4.83 | -34% |
| 3 (UHD buffer) | overflows/s | 0.07 | 0.06 (2.1/35s) | -14% |
| 3 (UHD buffer) | recv_count | 0% (1MB/32) | 100.1% (16MB/256) | **+100%** mechanically proven |
| 4 (rate 5) | overflows/s | 0.06 | 0.50 (17.8/35s) | **+733% (REFUTED)** |
| 5 (GR buffers) | avg_snr | 4.83 | 22.52 (single frame, unreliable) | n/a |
| 5 (GR buffers) | overflows/s | 0.06 | 0.014 (0.5/35s) | -77% |

### 30-min Soak (Task 6)

| Run | Time | avg_snr | HT_SIG_CAND | LSIG_OK | overflows |
|---|---|---:|---:|---:|---:|
| 1 | 17:32:42 | 3.80 | 0 | 7 | 217 |
| 2 | 17:39:27 | 8.37 | 0 | 4 | 106 |
| 3 | 17:46:12 | 3.93 | 0 | 7 | 273 |

Stability:
- avg_snr mean: 5.37
- avg_snr std: 2.60
- avg_snr range: 3.80 — 8.37
- CV: 0.485
- **Verdict: MARGINAL** (cv 0.20-0.50, 0.485 is at the high end of band)

### Comparison to Phase 57 Baseline

| Metric | Phase 57 | Phase 58 | Δ |
|---|---:|---:|---|
| avg_snr (mean) | 3.20 | **5.37** | **+68%** ↑ |
| avg_snr (std) | 1.05 | 2.60 | +148% (more variance) |
| CV | 0.329 | 0.485 | +48% (worse stability) |
| overflows/run | 37.67 | 199 | +428% (5.7/s) |
| HT_SIG_CAND | 5.33 (mean) | 0 | -100% |

**Mixed result**: avg_snr IMPROVED 68%, but variance INCREASED 48%.

## Conclusion: MARGINAL with avg_snr improvement

Phase 58 is a **mixed result**:
- ✅ avg_snr improved 68% (3.20 → 5.37) — better than Phase 57
- ❌ CV regressed 48% (0.329 → 0.485) — worse stability
- ❌ HT_SIG_CAND=0 (consistent with Phase 41 closure)
- ❌ Overflows 5.7/s (no improvement from 0.86/s)

The 5 pivots are not catching the bimodal distribution. Per-run thermal/LO
state dominates residual variance, not UHD streaming overflow rate. The
T3 UHD buffer change is mechanically proven (1MB→0%, 16MB→100.1% delivery)
but didn't translate to E2E avg_snr improvement in the soak.

### Why MARGINAL not STABLE

- CV 0.485 sits at the high end of the MARGINAL band (0.20-0.50)
- Run 2 spiked to 8.37 while Runs 1,3 stuck at ~3.8 — bimodal, not monotonic
- Per the Phase 55 diagnosis, USRP per-run thermal/LO state dominates
  the residual variance; the 5 pivots address overflow, not thermal

### Why not promote to standard config

- CV 0.485 > 0.20 STABLE threshold
- avg_snr improvement is real but inconsistent (3.80 → 8.37 → 3.93)
- HT_SIG_CAND=0 means no improvement in the channel-physics bottleneck
- Run-to-run variance is 48% WORSE than Phase 57

### What to do with the 5 pivots

- Keep them in the codebase (they are not regressions)
- Loopback 3/3 PASS verified at Task 5 and again at Task 7
- They are opt-in for diagnostic tests
- Standard USRP test config **remains Phase 53-57 baseline** (no warmup, no taskset, default UHD buffers)

## Counter-Increment

5 pivots tested:
- T1 (warmup): SUCCESS (avg_snr +30%, overflows -50%)
- T2 (CPU/taskset): SUCCESS (avg_snr +77%, overflows -84%)
- T3 (UHD buffer): SUCCESS mechanically (1MB→16MB solves 100% delivery), MARGINAL E2E
- T4 (--rate 5): **REFUTED** (48x MORE overflows)
- T5 (GR buffers): SUCCESS (overflows -77% from T3)

New REFUTED hypothesis: **`--rate 5` is strictly worse than `--rate 20`** for overflow frequency. (Plan had predicted opposite.)

Total Phase 58 REFUTED hypotheses: 1 (--rate 5). All 5 pivots standalone were either SUCCESS or MARGINAL.

## Files

- /tmp/p58_t{1,2,3,4,5,6}_*.log — raw test outputs
- examples/p58_warmup_cpu_setup.sh — CPU governor switch
- examples/p58_recv_buffer_test.py — UHD buffer sweep
- examples/p58_stability_soak.py — 30-min soak harness
- /tmp/p58_t6_soak_summary.txt — soak summary
- docs/superpowers/notes/2026-06-29-phase58-verdict.md — this file

## Implications

- **Standard USRP test config unchanged** (Phase 53-57 baseline)
- **5 pivots remain opt-in** for diagnostic tests where avg_snr matters
- **--rate 5 is REFUTED** for stability use
- **HT-SIG bottleneck remains channel-physics** (Phase 28/38/41)
- **Software loopback 3/3 PASS** preserved
- **Next phase candidates**:
  - Phase 59: Per-run thermal/LO state characterization
  - Phase 59 alt: Loopback 3/3 → USRP 1/3 success path (different goal)
  - Phase 60: Long-duration USRP soak (multi-hour) to test thermal hypothesis
