# Phase 53 Verdict — Cross-Board WEAKER Than Same-Board (2.4x SNR penalty)

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ❌ Cross-board has 2.4x LOWER avg_snr than same-board (4.0 dB worse)
**Commits**: (no new commits, infrastructure change reverted)

## Goal

Phase 52 claimed cross-board and same-board produce identical results (Sent=30+,
Recv=0). User pointed out that Phase 47/51 baseline had 1-5 HT_SIG_CAND metric=0
events. Phase 53 re-investigated with proper stderr handling.

## Phase 53 Discovery: subprocess+stderr wrapper was BROKEN

Phase 52 used a subprocess+stderr=DEVNULL wrapper (lifted from
`test_usrp_air_loopback.py`) to suppress C-layer fprintf noise. Phase 53
testing revealed this wrapper **breaks UHD streaming**:

| Mode | usrp_source overflow | HT_SIG_CAND | LSIG_DECODE OK | avg_snr |
|---|---:|---:|---:|---:|
| --internal-run (no subprocess) | 14 | 16 | 3-7 | 6.12 |
| subprocess (stderr→file) | 0 | 0 | 7 | 3.84 |
| subprocess (stderr→DEVNULL) | ? | 0 | 0 | ? |

The subprocess wrapper redirects stderr to either /dev/null or a file. This
redirect somehow starves the UHD async stream. The Phase 52 numbers were
an artifact of broken test infrastructure, not real signal measurements.

**Action taken**: Reverted subprocess wrapper. `test_usrp_minimal_loopback.py`
now runs directly (no subprocess). stderr goes to terminal (use shell `2>/dev/null`
if you want to suppress).

## Phase 53 Correct Cross-Board vs Same-Board Numbers

### Same-Board (A:0 TX → A:0 RX, RX2), 35s

| Metric | Value |
|---|---:|
| Sent | 36 |
| Recv | 0 |
| LSIG_DECODE OK | 3-7 |
| HT_SIG_CAND | **16** |
| HT_SIG_PARSE_FAIL | 1 |
| metric=0 events | 0 |
| avg_snr | 6.12 |
| avg_snr_lsig (linear) | ~6.12 |
| usrp_source overflow | 14 |

### Cross-Board (A:0 TX → B:0 RX, TX/RX), 35s

| Metric | Value |
|---|---:|
| Sent | 36 |
| Recv | 0 |
| LSIG_DECODE OK | 4 |
| HT_SIG_CAND | **0** |
| HT_SIG_PARSE_FAIL | 0 |
| metric=0 events | 0 |
| avg_snr | 2.54 |
| avg_snr_lsig (linear) | ~2.54 |
| usrp_source overflow | 41 |

## Cross-Board vs Same-Board Summary

**Cross-board is 2.4x WEAKER than same-board** in raw SNR:
- avg_snr cross-board: 2.54
- avg_snr same-board: 6.12
- Ratio: 6.12 / 2.54 = **2.41**
- dB delta: 10·log10(2.41) = **3.8 dB weaker**

This is a real physical difference. Cross-board (A:0 TX → B:0 RX) goes through
two separate UBX-160 daughterboards with separate LOs and separate antenna
ports. Same-board (A:0 TX → A:0 RX, RX2) uses the same UBX-160 with TDD
loopback, which provides stronger internal coupling.

## Implications

1. **Cross-board is not a better SNR option**. Same-board RX2 gives 2.4x
   stronger signal because of internal TDD coupling.

2. **Phase 52 verdict was misleading**. The "0 HT_SIG_CAND" numbers were
   subprocess-mode artifacts, not signal measurements. Without subprocess
   wrapper, same-board DOES produce 16 HT_SIG_CAND events per 35s run.

3. **Same-board with HT_SIG_CAND=16 still produces 0 metric=0 events**. This
   confirms that even the "healthier" same-board path has degraded enough
   (from Phase 47 baseline's 19.5 dB down to ~7.9 dB linear / 9.0 dB) that
   HT-SIG viterbi cannot achieve perfect convergence.

4. **Software cannot fix the SNR gap**. avg_snr=6.12 (linear) = 7.9 dB is
   still below the 19.5 dB Phase 31b baseline.

## Recommended Action

Use **same-board A:0/A:0 (RX2)** for USRP testing. The cross-board option
exists for diagnostic purposes (when A:0 RX2 is suspected bad) but produces
weaker signal.

## Files Modified

- `test_usrp_minimal_loopback.py`:
  - Reverted subprocess+stderr wrapper (was breaking UHD stream)
  - Stderr now goes to terminal directly
  - `--internal-run` flag preserved for backward compat
  - 20MB rx_buffer and rx_buffer2 (10MB) still in place (not the cause of
    UHD starvation — the subprocess wrapper was)
  - Diagnostic subdev/antenna prints still in place

## Software Loopback Regression

✅ `examples/test_direct_loopback.py` still produces Final: OK=1 FAIL=0

## Counter-Increment

Phase 53 is a methodology correction to Phase 52, not a new REFUTED hypothesis.
The USRP HT-SIG verdict from Phase 51 still stands (15 REFUTED hypotheses).
The new finding is that cross-board is 2.4x weaker than same-board, which
shifts the recommendation back to same-board.

## References

- Phase 52 verdict: docs/superpowers/notes/2026-06-29-phase52-verdict.md (now
  superseded by this verdict)
- Phase 51 verdict: docs/superpowers/notes/2026-06-29-phase51-verdict.md
- Phase 48 verdict: docs/superpowers/notes/2026-06-29-phase48-verdict.md
  (avg_snr_lsig=2.82 measurement at degraded SNR)
- Phase 31b: 14-sample L-LTF0 fix (avg_snr_lsig=12.91 baseline, 19.5 dB)