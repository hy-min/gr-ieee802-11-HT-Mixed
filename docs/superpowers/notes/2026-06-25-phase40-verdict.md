# Phase 40 Verdict — HT-SIG0/1 FFT Window Timing (SPLITTER-LEVEL)

**Date:** 2026-06-25

## Hypothesis

The `ht_symbol_splitter` in `lib/ht_symbol_splitter_impl.cc` applies the
`g_lltf_offset_correct` (K) value to L-LTF0/L-LTF1 region boundaries only
(lines 483, 489). HT-SIG0/1 region boundaries (lines 498-509) are
hardcoded — K=0 is not applied. If HT-SIG0/1 has a similar integer-sample
shift (like L-LTF0's 14-sample shift that Phase 33 fixed), then the
equalizer's H52 (extracted from K-shifted L-LTF0) would not match
HT-SIG0/1's actual channel, causing viterbi failure.

## Investigation

**Diagnostic added:** `IEEE80211_HTSIG_TIMING_DUMP=1` env var in
`lib/ht_symbol_splitter_impl.cc` (commits 1e38fa0, bc014d5). Logs the
absolute rel_idx, expected rel_idx, current K, and delta at every OFDM
symbol boundary (L-LTF0, L-LTF1, L-SIG, HT-SIG0, HT-SIG1, HT-STF, HT-LTF).

**Analyzer created:** `examples/p40_htsig_fft_window_diagnostic.py`
(gitignored per project convention). Groups [HTSIG_TIMING] lines by frame
and computes per-boundary delta statistics.

**Test setup:**
- USRP X310 + UBX-160, 5 GHz, `--freq 5890 --tx-gain 20`
- `IEEE80211_LLTF_OFFSET_CORRECT=14` (Phase 33 fix active)
- `IEEE80211_HTSIG_TIMING_DUMP=1`
- `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` (no data captured, separate issue)
- 30s capture, filtered log via grep to keep size manageable
- 16 complete frames with all 7 OFDM boundaries

## Result: REFUTED

Analyzer output:

```
=== Phase 40: HT-SIG FFT Window Timing Analysis ===
Log: /tmp/p40_htsig_timing.log
Frames analyzed: 16 (only frames with all 6 header boundaries)

Complete frames (all 6 header boundaries): 16

Label           N   mean_δ    std_δ    min    max
--------------------------------------------------
L-LTF1         16     4.00     0.00      4      4
L-SIG          16     0.00     0.00      0      0
HT-SIG0        16     0.00     0.00      0      0
HT-SIG1        16     0.00     0.00      0      0
HT-STF         16     0.00     0.00      0      0
HT-LTF         16     0.00     0.00      0      0

=== Verdict ===
HT-SIG0 delta mean=0.00 std=0.00
HT-SIG1 delta mean=0.00 std=0.00
→ Both HT-SIG0 and HT-SIG1 deltas ≈ 0 (within noise)
→ HT-SIG FFT windows ARE ALIGNED with L-LTF0-derived H52.
→ Hypothesis REFUTED at splitter level.
```

The L-LTF1 delta=4 is an internal K=4 offset, not a misalignment — all
boundaries show consistent K=4 expected, so the structural splitter
timing is correct.

**HT-SIG0 and HT-SIG1 FFT windows are perfectly aligned with the
L-LTF0-derived H52** (delta=0, std=0 across all 16 frames). Splitter
sample-boundary timing is NOT the impairment.

## Cumulative REFUTED count: 11

| Phase | Hypothesis | Status |
|---|---|---|
| 25 | SFO/phase ramp | REFUTED |
| 26 | Decision-directed phase tracking | REFUTED |
| 27 | H52 estimation quality variants | REFUTED |
| 29.2 | Viterbi input scaling | REFUTED |
| 30 | Per-SC SNR drop | REFUTED |
| 35 | Per-symbol mean pilot CPE | REFUTED |
| 36 | Per-SC linear fit on HT-SIG pilots | REFUTED |
| 37 | Soft-decision LLR / CFO tolerance | REFUTED (decoder is correct) |
| 38 | Per-symbol CPE via estimate_header_cpe_rad | REFUTED |
| 39 | HT-SIG pilot-based H re-estimation | REFUTED |
| **40** | **Splitter K-offset for HT-SIG regions** | **REFUTED** |

## Decision: PIVOT to impairment characterization (Option B-lite)

The splitter timing is correct. The impairment is downstream of the
splitter. Two concrete pivot options:

### Option A (recommended): Investigate the `is_ht_frame=0` anomaly

The 112 `HT_SIG_PARSE_FAIL` lines in this run all show `is_ht_frame=0`.
This means the equalizer is NOT recognizing the frame as an HT frame
even though HT-SIG was received. This could be a pre-Phase 18 regression
(the `IEEE80211_LSIG_RATE_FORCE=0xD` env var was NOT used in this run,
but is_ht_frame should still be set to 1 when HT-SIG is detected).

If `is_ht_frame=0` persists even with `IEEE80211_LSIG_RATE_FORCE=0xD`,
then HT-SIG is being SKIPPED entirely (the equalizer treats it as legacy
mode and only decodes L-SIG). This would explain viterbi failure without
needing any "real" impairment.

### Option B: Investigate why `DELTA_PER_SYMBOL_DUMP` doesn't fire

The `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` env var was set but no per-symbol
data was captured (only the ENABLED banner appeared). This diagnostic was
added in Phase 38 specifically to help diagnose HT-SIG viterbi issues. If
it's not working, we have less visibility into per-symbol channel
variations. Fix the env var wiring before re-running.

### Option C: Accept HT-SIG not solvable on USRP

After 11 REFUTED hypotheses, evidence supports the position that
HT-SIG viterbi failure is a fundamental USRP hardware limitation
(e.g., frequency-selective channel, RF impairment, IQ imbalance) that
cannot be fixed in software without significant architectural changes
(e.g., LDPC-only fallback, more robust CFO tracking, ML-based decoder).

Software loopback 3/3 PASS remains the decoder validation path.

## Files

- Diagnostic: `lib/ht_symbol_splitter_impl.cc` (commits 1e38fa0, bc014d5)
- Analyzer: `examples/p40_htsig_fft_window_diagnostic.py` (gitignored)
- USRP log: `/tmp/p40_htsig_timing.log` (126KB filtered)
- Analyzer output: `/tmp/p40_analyzer_output.txt`
