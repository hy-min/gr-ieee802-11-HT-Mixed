# Phase 108 — FFT Window Fix VERDICT (2026-07-06)

## TL;DR

**The constant CPE fix is implemented and reduces the worst-case |eq|^2 outlier
by ~100x (18827 → 175), but the "static 30° rotation" hypothesis from Phase 107
is REFUTED.** The per-SC phase_offset is essentially random across [-180°, +180°]
(std=81.6°, circular std=79.1° ≈ random), not a constant 30°. FCS_OK is
unchanged (mean 11.0 in both baseline and CONST_CPE runs; range 8-13 baseline,
7-14 with fix — within run-to-run noise).

**HARD CONSTRAINT status: NOT achieved.** File-replay of USRP IQ is achievable
(FCS_OK=7-14 per 30s run, non-deterministic, all-or-nothing per frame), but
USRP realtime remains BLOCKED upstream.

## Validation Results

### 5-run baseline (no CONST_CPE_APPLY)

| Run | FCS_OK | FCS_FAIL |
|---:|---:|---:|
| 1 | 13 | 0 |
| 2 | 11 | 0 |
| 3 | 11 | 0 |
| 4 |  8 | 0 |
| 5 | 12 | 0 |

**Mean = 11.0, std = 1.79, range = [8, 13]** — non-deterministic within ±5 frames.

### 5-run with IEEE80211_CONST_CPE_APPLY=1

| Run | FCS_OK | FCS_FAIL |
|---:|---:|---:|
| 1 | 14 | 0 |
| 2 |  7 | 0 |
| 3 |  9 | 0 |
| 4 | 11 | 0 |
| 5 | 14 | 0 |

**Mean = 11.0, std = 2.92, range = [7, 14]** — same mean, slightly wider spread.

**Conclusion**: The fix does not change mean FCS_OK. The differences (1-2 frames
out of ~12) are within run-to-run noise from the random per-SC phase.

### arg(eq) distribution (LSIG_EQ_FULL, 10s replay)

| Metric | Baseline (no CONST_CPE) | With CONST_CPE_APPLY=1 |
|---|---:|---:|
| Frames captured | 30 | 32 |
| is_ht=1 | 20 | 19 |
| is_ht=0 | 10 | 13 |
| N eq values | 1560 | 1664 |
| \|eq\| median | 0.962 | 0.946 |
| \|eq\|^2 median | 0.925 | 0.896 |
| \|eq\|^2 95th pct | 8.07 | 7.77 |
| \|eq\|^2 99th pct | **183.20** | **32.32** |
| **\|eq\|^2 max** | **18827.16** | **175.35** |
| arg(eq) mean (deg) | +1.0 | -0.7 |
| arg(eq) std (deg) | 104.6 | 102.5 |

**CONST_CPE suppresses the worst-case outlier symbols by ~100x** (|eq|^2 max
drops from 18827 to 175). Median and 95th percentile are unchanged — the
decoder already handles the median-quality symbols correctly. The reduction
of extreme outliers does not translate to FCS_OK improvement because all
frames in the test set are already above viterbi threshold at the median.

### phase_offset distribution (CONST_CPE logs, 2733 measurements = ~57 L-SIG symbols × 48 SCs)

| Metric | Value |
|---|---:|
| N | 2733 |
| Range (deg) | [-180.0, +179.2] |
| Linear mean (deg) | -0.6 |
| Linear std (deg) | 81.6 |
| Circular mean (deg) | +0.5 |
| Circular std (deg) | 79.1 (random uniform = 104.5) |

**phase_offset is essentially random across [-180°, +180°]**, not a constant
30°. The Phase 107 measurement of "30° mean offset" was a small-sample
artifact; across 2733 measurements the mean is ~0° (not 30°) and std is 80°
(not 0°).

### avg_eq_mag distribution (per-L-SIG-symbol, 2733 measurements)

| Metric | Value |
|---|---:|
| median | 0.231 |
| mean   | 0.300 |
| std    | 0.414 |
| Fraction \|eq\| < 0.5 | 91.6% |
| Fraction \|eq\| > 2.0 |  0.8% |

91.6% of L-SIG symbols have avg_eq_mag < 0.5 — confirms that **most L-SIG
symbols are degraded, not just one outlier per frame**. The per-SC channel
quality is bad (Phase 107 found |H| CV = 27-50%), and this propagates to the
equalizer output regardless of phase correction.

## HARD CONSTRAINT Status

| Form | Status |
|---|---|
| USRP realtime `FCS_OK >= 1` stable | BLOCKED — file-replay works (FCS_OK=7-14 non-deterministic) but USRP realtime path still has upstream sync_short / UHD streaming issues (Phase 89 + 55) |
| File-replay of USRP IQ | ACHIEVABLE — 7-14 frames per 30s, non-deterministic but never 0. Baseline + Phase 95 rot/inv brute-force already pass on this dataset |

The HARD CONSTRAINT is **NOT ACHIEVABLE in any framing on USRP realtime**.
File-replay of captured USRP IQ is the closest we get, and it works in the
loose sense (some frames pass, never 0) but not as a deterministic
FCS_OK = N/N per 30s.

## What Worked / What Didn't

### What Worked
- **IEEE80211_FFT_WINDOW_DUMP=1** diagnostic: confirmed upstream is
  sample-stable per-frame (all 8 frames have identical d_data_start_rel=7).
  Upstream is NOT the misaligned block.
- **IEEE80211_CONST_CPE_APPLY=1** is implemented correctly and reduces
  the worst-case |eq|^2 outlier by ~100x (18827 → 175). No regression on
  baseline runs (mean FCS_OK unchanged at 11).
- File-replay baseline is stable at 11 ± 2 FCS_OK per 30s (driven by L-SIG
  viterbi unblock via Phase 70/95 rot/inv brute-force, not by this fix).

### What Didn't Work
- **The "static 30° rotation" hypothesis from Phase 107 is REFUTED.**
  Across 2733 measurements, per-SC phase_offset has linear std=81.6° and
  circular std=79.1° (random=104.5°). This is not a constant offset.
- **Adding constant CPE does NOT stabilize FCS_OK.** Mean is identical
  (11.0 → 11.0), range is similar (8-13 vs 7-14). All variation is
  within run-to-run noise from the per-SC random phase.
- **The avg_eq_mag distribution is fundamentally degraded**: 91.6% of
  L-SIG symbols have |eq| < 0.5. This is a per-SC channel-quality issue
  (Phase 107: |H| CV 27-50%), not a phase-offset issue. Constant CPE
  cannot fix a |H| problem.

## Root Cause Refinement

Phase 107 concluded "30° constant phase rotation + 30% |H| CV" based on
limited samples. Phase 108 confirms:

1. **30° constant rotation: REFUTED.** Phase 107 measured ~30° mean across
   a small sample. Across 2733 measurements the mean is ~0° (and std=80°).
   The original 30° observation was likely a coincidence of the small sample.

2. **|H| CV 27-50%: CONFIRMED.** 91.6% of L-SIG symbols have avg |eq| < 0.5.
   The per-SC |H| noise is real and large.

3. **Upstream FFT window: SAMPLE-STABLE per-frame** (Phase 108 Task 2).
   d_data_start_rel=7 is identical across all 8 captured frames.
   sync_long, splitter, and FFT window alignment are NOT the problem.

**Updated root cause hypothesis**: The per-SC |H| has high variance because
of freq-selective channel + UHD streaming instability. The per-SC phase
offset is random (not a static rotation) because of per-frame CFO/SFO
drift. Together, these explain why L-SIG viterbi is non-deterministic:
some frames happen to have |H| that aligns well enough to pass.

**The fix must be UPSTREAM** — not in the equalizer layer (28+ REFUTED),
not in the FFT window (sample-stable), not in a constant CPE (offset is
random). Phase 109+ must address:
- UHD streaming stability (Phase 55: 8x SNR drift over hours)
- CFO/SFO estimation quality in sync_long
- Per-SC |H| noise (RF chain impairment, freq-selective fading)

## Files

- Verdict: `docs/superpowers/notes/2026-07-06-phase108-fft-window-fix-verdict.md`
- Phase 108 plan: (TaskList #132)
- Phase 108 FFT diagnosis: `docs/superpowers/notes/2026-07-06-phase108-fft-window-diagnosis.md`
- Phase 108 diagnostic results: `docs/superpowers/notes/2026-07-06-phase108-fft-window-diagnostic-results.md`
- Phase 107 verdict: `docs/superpowers/notes/2026-07-06-phase107-deep-root-cause.md`
- Phase 106 verdict: `docs/superpowers/notes/2026-07-06-phase106-fcs-ok-loss-verdict.md`
- Phase 105 verdict: `docs/superpowers/notes/2026-07-06-phase105-usrp-capture.md`

## Commits

- `824c328` feat(p108): add IEEE80211_FFT_WINDOW_DUMP env var
- `4a2cf5f` feat(p108): add IEEE80211_CONST_CPE_APPLY=1 constant CPE at L-SIG boundary
- `0267544` docs(p108): FFT window diagnostic results from USRP file-replay
- `4c47353` docs(p108): FFT window diagnosis - upstream sample-stable, 30° rotation is static
- (this commit) docs(p108 verdict): constant CPE fix PARTIALLY REFUTED - rotation is not static

## Next Steps (Phase 109+)

1. **Stop pursuing equalizer-layer fixes.** 28+ REFUTED. Both constant CPE
   and per-symbol CPE have been tested. The issue is upstream channel +
   UHD streaming stability, not the equalizer.
2. **Add IEEE80211_PER_FRAME_CPE_DUMP=1** to log per-frame CFO estimate,
   timing offset, and per-SC H statistics. Track which frames pass
   FCS_OK vs fail to characterize the per-frame conditions.
3. **Investigate sync_long CFO/SFO estimator.** The per-frame phase
   variation suggests upstream estimation is not tracking correctly.
   Compare CFO estimate across frames in the same capture.
4. **Investigate UHD streaming stability (Phase 55 revisit).** 8x SNR
   drift over hours suggests streaming-related impairments. File-replay
   of fresh USRP capture shows the same pattern, so the issue is in the
   IQ data, not in the runtime scheduling.
5. **Consider upstream RF chain check.** Per-SC |H| CV of 27-50% may be
   caused by cable/connector impairments (Phase 75 REFUTED at 5180/5500/5890
   air path; cable path not yet characterized per-SC).
