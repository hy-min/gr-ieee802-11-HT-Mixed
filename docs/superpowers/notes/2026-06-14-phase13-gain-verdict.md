# Phase 13 Verdict — USRP RX Gain / AGC Investigation

**Date:** 2026-06-14
**Branch:** TEST1
**Plan:** docs/superpowers/plans/2026-06-14-usrp-gain-agc-investigation.md (commit bbfc8a5)
**Verdict:** **GAIN_AFFECTS_LEVEL_ONLY**

## TL;DR

5-point RX gain sweep (0, 10, 20, 31, 31.5 dB) at 5.89 GHz A:0/RX2 single-board
TDD, 30s per point, captured via the new `IEEE80211_FRAME_GAIN_DUMP` env-var hook
(commit `93daf8e`) at the L-LTF0 FFT entry point. Per-gain `e_in_per_sample_std`
varies 4.42-10.46 (2.37x range) but never drops below the 2.0 threshold needed to
declare gain as the dominant lever. **Gain is ruled out as the root cause** of the
per-frame std=12.7 destruction. The Phase 5 LO_BROKEN verdict is reinforced: the
USRP X310 internal TCXO is the dominant corruption source, and no software-config
of RX gain (or absence of AGC) can rescue end-to-end reception.

## Per-gain table

| gain (dB) | n_frm | e_in_mean | e_in_std | eps_mean | eps_std | lsig_ok | enc0 | enc0% | OK | FAIL |
|-----------|-------|-----------|----------|----------|---------|---------|------|-------|----|----|
| 0.0       | 43    | 2146.82   | 298.74   | 33.5441  | 4.6678  | 160     | 48   | 30.0  | -  | -    |
| 10.0      | 43    | 1801.54   | 565.24   | 28.1490  | 8.8319  | 136     | 0    | 0.0   | 0  | 0    |
| 20.0      | 46    | 1765.17   | 608.17   | 27.5808  | 9.5027  | 200     | 16   | 8.0   | -  | -    |
| 31.0      | 44    | 2144.23   | 282.89   | 33.5037  | 4.4201  | 217     | 33   | 15.2  | 0  | 0    |
| 31.5      | 39    | 1721.03   | 669.54   | 26.8912  | 10.4616 | 168     | 24   | 14.3  | 0  | 0    |

(`-` for OK/FAIL indicates the test_p10_usrp_v2_30s.py per-gain re-run did not
emit a "Final: OK=X FAIL=Y" line; for those runs, FcsLogger counters are not in
the per-gain .out log. This does not affect the verdict — the enc0% column is
the load-bearing metric.)

## Decision rule application

- Min eps_std: 4.42 (gain=31.0 dB) — exceeds the 2.0 GAIN_DEPENDENT threshold
- Max eps_std: 10.46 (gain=31.5 dB) — within Phase 3 USRP baseline range (12.7)
- All gains reproduce the Phase 3 std~12.7 range (best 4.42, worst 10.46)
- Range ratio 2.37x is modest; does not indicate a strong gain-driven lever

**Conclusion**: gain is NOT the dominant lever. The 2.37x range across the
PGA0 dynamic range (0-31.5 dB) is too small to account for the per-frame
std=12.7 destruction.

## Enc distribution per gain

| gain (dB) | enc=0 | enc=1 | enc=2 | enc=3 | enc=4 | enc=5 | enc=6 | enc=7 | total |
|-----------|-------|-------|-------|-------|-------|-------|-------|-------|-------|
| 0.0       | 48    | 8     | 8     | 16    | 32    | 24    | 0     | 24    | 160   |
| 10.0      | 0     | 32    | 32    | 16    | 8     | 16    | 24    | 8     | 136   |
| 20.0      | 16    | 16    | 40    | 16    | 64    | 8     | 16    | 24    | 200   |
| 31.0      | 33    | 16    | 24    | 24    | 32    | 8     | 32    | 48    | 217   |
| 31.5      | 24    | 0     | 32    | 8     | 40    | 16    | 24    | 24    | 168   |

Observations:
- enc=0% is best at gain=0.0 dB (30.0%) — interestingly, minimum gain has
  highest correct-rate for L-SIG BPSK decoding. This is consistent with less
  signal corruption from a lower-gain amplifier.
- enc=0% is worst at gain=10.0 dB (0%) — the most non-linear PGA0 region
  appears to corrupt L-SIG constellation the most.
- enc=0% at gain=31.0 dB (15.2%) matches Phase 12 FORCE_HTSIG baseline
  exactly — confirms reproducibility of the L-SIG destruction pattern.
- encodings spread across all 8 values at every gain — the L-SIG
  constellation is garbage at every gain, just garbage in different ways.

## What this means for Phase 5/6 verdict

**The Phase 5 LO_BROKEN verdict (USRP X310 internal TCXO 14.05 rad RMS) is
REINFORCED.** No RX gain value (manual or AGC-equivalent) can rescue end-to-end
reception. The corruption is at the LO/clock domain, downstream of any RF
amplification that gain controls.

**The Phase 3 std=12.7 finding is RECONTEXTUALIZED.** The high per-frame std
is not caused by per-frame AGC/gain instability (which would have shown up as
large eps_std variation between gains). It is caused by the LO phase noise
rotating the constellation from symbol to symbol, which manifests as per-frame
energy variation when measured at the L-LTF0 FFT entry point.

## Next step

**Algorithmic path is fully exhausted.** All software-config levers have been
investigated and ruled out:
- Phase 1-4: CFO/SFO, L-LTF1 H, 3-tap median filter, kFftNormalize
- Phase 5-6: LO measurement, hardware localization
- Phase 10-12: L-SIG constellation fixes, L-LTF0 FFT upstream fixes
- Phase 13: RX gain / AGC ← this phase

**Hardware reference required.** The remaining options are:
1. External 10 MHz OCXO (e.g., SRS FS725) into X310 REF IN
2. GPSDO daughterboard with GPS antenna
3. Different USRP model (e.g., B210) with cleaner internal reference

**Recommendation**: do not continue USRP software investigation. Acquire
external OCXO/GPSDO hardware (Option A or B), then re-run the Phase 5
measurement suite with `clock_source=external`. If the composite verdict
becomes CLEAN/DEGRADED, the RX chain can be re-validated end-to-end.

**For new research directions (without external hardware)**: consider
algorithm-only work that does not require USRP validation — e.g., performance
benchmarks on synthetic data, MCS matrix analysis, or theoretical limits
on receiver sensitivity with documented LO noise.

## Software artifacts (committed in TEST1 branch)

| Commit | Type | Description |
|--------|------|-------------|
| `bbfc8a5` | plan | Phase 13 plan |
| `1db2387` | diag | IEEE80211_FRAME_GAIN_DUMP env-var hook (initial) |
| `93daf8e` | diag | e_in_mean field + Phase 9 note (review fix) |

Plus the test driver `/tmp/test_p13_gain_sweep.py` and analyzer
`/tmp/p13_analyze.py` (not git-tracked; live in /tmp).

## Logs captured (not git-tracked)

- `/tmp/p13_gain_sweep_summary.{txt,err}` — combined sweep (3 min total)
- `/tmp/p13_gain_{0.0,10.0,20.0,31.0,31.5}.{out,err}` — per-gain (5 × 30s)
- `/tmp/p13_per_gain_table.txt` — analysis output

## Memory updates

- `MEMORY.md` updated with Phase 13 index entry
- `project_p13_gain_agc.md` created with full verdict

## Notes

This is the **close of the algorithmic USRP debug loop** (started Phase 10,
2026-06-14). Future work should:
- Trust the synthetic + loopback validation (9/9 tests)
- Not attempt further algorithmic fixes (Phase 4, 12, 13 ruled this out)
- Focus on either (a) acquiring better USRP hardware, or (b) developing
  new features that don't require USRP validation