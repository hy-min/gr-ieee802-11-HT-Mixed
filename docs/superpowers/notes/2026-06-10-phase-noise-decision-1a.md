# Phase 1a Findings: Phase Noise Hypothesis REFUTED (2026-06-10)

## USRP Run Summary (30s, IEEE80211_PHASE_RESIDUAL=1)

| Metric | Value |
|--------|-------|
| Sent | 31 |
| Recv | 0 |
| LSIG_PARSE_FAIL | 152 |
| HT-SIG timeout | 22 |
| [PHASE_RESIDUAL] dumps | 22 (one per frame reaching equalizer) |

## Decision Gate 1a Verdict

```
Total frames analyzed: 22
Verdict distribution:
  NOISE_LIKE               :   16 ( 72.7%)
  MODEL_INCOMPLETE         :    6 ( 27.3%)

Aggregate over 22 frames:
  mean of mean_phase: +0.006 rad
  mean of std_phase:  1.750 rad
```

**Per-frame std_phase** (sample of 10): 1.911, 1.701, 1.764, 1.962, 1.629, 1.712, 1.561, 1.780, 1.651, 1.767 rad.

All 22 frames have `std_phase > 1.5 rad`. For CLEAN_MODEL, threshold is `< 0.3 rad`.

## Interpretation

**The hypothesis "CFO/SFO residual phase rotation" is REFUTED.**

The equalized L-SIG constellation is dominated by **random noise**, not coherent phase rotation. There is no systematic bias to compensate:
- `mean_phase` aggregates to +0.006 rad (no CPE)
- `std_phase` aggregates to 1.75 rad (essentially uniform on `[-π, +π]`)

This is consistent with the [LSIG_VITERBI_AUDIT] finding (commit `e90e3f5`): viterbi inputs are noise-like, deinterleaver produces mean=24.3/48 std=2.1.

## Root Cause Candidates (Revised, ranked)

1. **H estimation failure** — if H is wrong, rx/H produces garbage regardless of phase. Candidates:
   - Per-SC H interpolation missing (line 576-610 `estimate_header_channel_from_lltf52` uses raw single-point)
   - CFO/SFO compensation is applied to L-LTF0 RX but not consistently to L-SIG RX
2. **FFT window timing off by sub-sample** → ISI leaks into L-SIG
3. **Different FFT scaling** between L-LTF0 (counter=0) and L-SIG (counter=2) — note `[FRAME_DETECT] E_I` swings from 116 to 5308 across frames
4. **Hardware gain not effective** (per memory) — but signal IS reaching the equalizer (we see LSIG_PARSE_FAIL=152)

## Action

**Phase 1b (Tasks 7-11) is NOT applicable** — the proposed fix (direct phase measurement from pilots) cannot help when the underlying constellation is noise.

**Next investigation** should target the **H estimation path** (H itself may be wrong, making rx/H meaningless) and/or **FFT window timing**. A new spec/plan is needed.

## Artifacts

- USRP stderr log: `/tmp/usrp_run_stderr.log`
- PHASE_RESIDUAL extracted: `/tmp/phase_residual_only.log`
- Decision log: `/tmp/phase_noise_decision_1a.log`
- Branch: TEST1, commits `6c2d706` (cherry-pick) → `d6ecf36` (Task 3)
- Script: `examples/test_phase_residual_offline.py` (Task 4)
