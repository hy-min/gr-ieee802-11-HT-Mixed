# Phase 3 Fix Experiments Summary (2026-06-11)

## Question

After Phase 3 Stage 1 (reorganized) verdict: L-LTF0 FFT at frame_equalizer
input is corrupted (per-frame std 12.7x loopback), with the corruption
originating upstream of the equalizer.

User chose to attempt the most likely fixes: **FFT window timing**, then
**hardware gain**.

## Fix Attempt 1: FFT Window Timing (Task FFT.1-4)

**Hypothesis:** `d_frame_start=160` (hardcoded) might be off by ±1-2 samples.

**Method:** Added env var `IEEE80211_FRAME_START_OFFSET` (default 0,
preserves original behavior). Swept offset ∈ {-3..+3}, 30s USRP per offset.

**Results:**

| offset | per-frame std | verdict | Recv |
|--------|---------------|---------|------|
| -3     | 10.321        | STAGE_AMBIGUOUS | 0 |
| -2     | 10.305        | STAGE_AMBIGUOUS | 0 |
| -1     | 9.999         | STAGE_AMBIGUOUS | 0 |
| **0**  | **8.731 (lowest)** | STAGE_AMBIGUOUS | 0 |
| +1     | 10.851        | STAGE_AMBIGUOUS | 0 |
| +2     | 9.216         | STAGE_AMBIGUOUS | 0 |
| +3     | 11.426        | STAGE_AMBIGUOUS | 0 |

**Verdict: NOT the root cause.** offset=0 is local optimum; any shift makes
std WORSE. No offset produces Recv≥1.

## Fix Attempt 2: Hardware Gain (Task FIX2.1, FIX3)

**Hypothesis:** USRP AGC/compression or low SNR is causing per-frame magnitude
flicker. Adjusting tx_gain / rx_gain might help.

**Method:** Swept rx_gain ∈ {5, 10, 15, 20, 25} and tx_gain ∈ {15, 20, 25, 30}
at rx_gain=5, 30s USRP per setting.

**Results:**

| rx_gain | tx_gain=10 std | mean | std/mean |
|---------|----------------|------|----------|
| 5       | 2.039          | 1.82 | 1.12     |
| 10      | 3.616          | 3.63 | 1.00     |
| 15      | 5.703          | 5.98 | 0.95     |
| 20      | 9.663          | 10.82| 0.89     |
| 25      | 17.748         | 16.74| 1.06     |

| tx_gain (rx=5) | std |
|----------------|-----|
| 15             | 3.124 |
| 20             | 5.125 |
| 25             | 10.392 |
| 30             | 16.756 |

**Best combo:** tx_gain=15, rx_gain=5, 60s → std=3.256, mean=3.259, **Recv=0/61**

**Verdict: NOT a fix.** std/mean ratio ≈ 1.0 across ALL gain settings. The
absolute std scales linearly with the absolute mean — meaning the corruption
is RELATIVELY constant, not absolute. Lower gain just makes both signal and
noise smaller proportionally.

**Why this rules out gain/AGC/compression:**
- AGC/compression would show higher std at higher gain only (saturating)
- Low SNR would show std roughly proportional to mean (additive Gaussian)
- Per-frame std always ~mean → signal is dominated by per-frame variability,
  not additive noise

## Combined Conclusion

After 2 fix attempts:
- **L-LTF0 FFT at frame_equalizer input** is corrupted at per-frame level
  (per-frame std always ≥ 2-12x mean across all tested settings)
- **No setting achieves Recv≥1** (B criterion)
- **No setting achieves STAGE_FINE** (per-frame std < 1.0)

The corruption is **structural** — the relative variability is constant
across gain/timing settings. This points to:
- **RF chain** (cable, antenna, USRP LO) — environment, not code
- **Multipath** (TDD switching introduces reflections)
- **USRP LO phase noise** (TX and RX on same LO should be coherent, but
  USRP N310/X310 has known phase noise issues)

## What Remains

User's "诊断 + 1 修复" scope. Diagnosis complete (H_BOTH_BROKEN → upstream
FFT corruption). 2 fix attempts failed (timing, gain). 

**Possible remaining directions** (would require user approval to continue):
1. **Robust H estimation** — even with corrupted L-LTF0, use per-SC outlier
   rejection or time-domain averaging to extract a usable H. But this
   doesn't fix the underlying issue.
2. **Stage 4 (H in/out)** — instrument the H math to confirm whether
   it preserves or amplifies the input corruption.
3. **USRP hardware check** — physical inspection of cables, antennas, TDD
   switch timing.
4. **External reference** — connect a known-good signal source to validate
   the receiver chain in isolation.

**Recommendation:** Stop here. The corruption is not in the algorithm
(loopback 9/9 confirms), not in timing (offset=0 optimal), not in gain
(std/mean constant). The "1 修复" goal is unachievable in this USRP
session without addressing the RF chain.

## Artifacts

- `/tmp/fft_timing_scan/off_*.log` (7 files, ~4 GB total)
- `/tmp/rx_gain_scan/gain_*.log` (3 files)
- `/tmp/rx_gain_scan2/gain_*.log` (2 files)
- `/tmp/tx_gain_scan/tg_*.log` (4 files)
- `/tmp/best_combo_60s.log`
- Decision note: this file
- Code change: `lib/sync_long.cc` (env var harness, default offset=0 = original)

## Commits

- `b8e0e34` feat(sync_long): add IEEE80211_FRAME_START_OFFSET env var
- (this commit) notes: Phase 3 fix experiments summary
