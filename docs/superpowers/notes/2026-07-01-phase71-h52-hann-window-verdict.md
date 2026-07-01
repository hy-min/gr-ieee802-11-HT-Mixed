# Phase 71 Verdict — L-LTF Hann Window (REFUTED on loopback)

**Date**: 2026-07-01
**Branch**: TEST1
**Status**: REFUTED on loopback (BLOCKED)
**Commits**:
- 9c7b0e9 → b83a356 feat(p71): synthetic test for RX FFT window comparison
- 491c969 feat(p71): env-var-gated RX FFT window (default rectangular)

## Goal
Apply Hann window to L-LTF RX FFT to reduce spectral leakage and improve H52 quality.

## Hypothesis Tested
Rectangular window's -13 dB sidelobes cause inter-bin spectral leakage that
inflates H52 null magnitudes. Hann window's -31 dB sidelobes reduce leakage by
~18 dB, improving H52 channel estimate.

## Results

### Synthetic test (Task 1) — PASS (limited validity)
- Hann window reduces main-lobe amplitude ~50% (expected scalloping loss)
- Hann does NOT reduce n_nulls vs rectangular in pure CFO scenario
- Test confirms Hann does not catastrophically destroy the signal
- Test does NOT confirm Hann reduces leakage (synthetic signal model too simple)

### Loopback regression (Task 3) — REFUTED (FAIL 0/1)
- **Rectangular: Final: OK=1 FAIL=0** (1/1 PASS, baseline)
- **Hann: Final: OK=0 FAIL=0** (0/1 FAIL — REGRESSION)
- H_mag wildly distorted: range 0.048 to 8.906 (matches Hann(64) response)
- Explicit nulls at 12 of 64 SCs
- 4 LSIG_DECODE OK events with enc=4,5,6 (wrong rates; should be enc=0/BPSK)
- 8 LSIG_PARSE_FAIL events with reason='viterbi_fail' avg_snr=640.88
- 0 HT_SIG_EQ, 0 HT_SIG_DECODE — chain dies at L-SIG viterbi

### Offline replay (Task 4) — SKIPPED
- Loopback failure is structural, not environmental
- Offline replay would also fail with Hann on RX-only

### USRP realtime (Task 5) — SKIPPED
- Same reason as offline replay

## Root Cause Analysis

The Hann window creates a fake frequency-selective "channel" in
H52 = Y_LTF0 / X_LTF0:
- Y_LTF0 is windowed by Hann (RX side: `wifi_phy_hier.py:130`)
- X_LTF0 (TX reference) is NOT windowed
- The Hann window response itself shows up as the "channel" in loopback
- H_mag variation 0.05-8.9 across 64 bins exactly matches a Hann(64) frequency response

On a flat loopback channel (which is what we test for unit verification),
this fake channel breaks the L-SIG viterbi. On USRP with a real channel,
the same fake channel would be ADDED to the real channel — making things
worse, not better.

## Decision

**Phase 71 Hann-window approach is REFUTED on loopback.** The Hann-on-RX-only
modification cannot work without compensating the equalizer. Per HARD
CONSTRAINT, BLOCKED outcomes require an upstream-attack plan:

## Phase 72+ Attack Plan (per HARD CONSTRAINT)

Three options to address H52 channel quality (the actual bottleneck per
Phase 70 verdict):

### Option A: Apply Hann to BOTH TX and RX (matched-window)
- Change `wifi_phy_hier.py:100` (TX IFFT) to use the same Hann window
- Channel estimate H52 = (Y_LTF0 · Hann) / (X_LTF0 · Hann) = Y/X (no fake channel)
- **Drawback**: Changes transmitted waveform. Real-world receivers won't see
  the same signal as our RX. Loopback works because TX and RX are the same
  code; USRP works because TX/RX share the same .py file.

### Option B: Compensate Hann envelope in equalizer (recommended)
- Modify `estimate_header_channel_from_lltf52()` in `lib/frame_equalizer_impl.cc:1005`
- Divide computed H52 by the Hann frequency response envelope before use
- Add env var `IEEE80211_RX_FFT_WINDOW_COMPENSATE=1` (default ON when
  `IEEE80211_RX_FFT_WINDOW` is non-rectangular)
- Preserves TX waveform (no real-world interoperability issue)
- Requires C++ changes (in scope for Phase 72)

### Option C: Pivot to Phase 72 (MMSE equalizer) directly
- Skip Hann approach entirely
- Replace `safe_div(rx, H)` with `(H* rx) / (|H|² + N0)` in EQ loop
- Phase 47 env vars exist: `IEEE80211_MMSE_EQUALIZE=1 IEEE80211_MMSE_N0_PERCENTILE=25`
  but were never validated on USRP

### Option D: Per-symbol H re-estimation (Phase 73)
- Phase 39 was REFUTED with 8× worse std_im, but that was without
  Hann/Hamming compensation. With Phase 71/72 fixes, the H estimate
  may be stable enough that per-symbol re-estimation becomes viable.

## Recommendation

**Phase 72 should pursue Option B (Hann compensation) + Option C (MMSE EQ)
in a single phase.** Both attack H52 quality at the equalizer layer. The
Hann compensation is small (~10 lines of C++); the MMSE EQ is medium
(~30 lines of C++). Combined: ~40 lines, single env var gate.

## Files

- `wifi_phy_hier.py` (env-var-gated RX FFT window)
- `examples/test_rx_fft_window_synthetic.py` (synthetic test)
- `/tmp/p71_loopback_rect.log`, `/tmp/p71_loopback_hann.log` (loopback tests)

## Related

- [[project_p70_lsig_viterbi_candidate]] — Phase 70 verdict (channel-physics limit)
- [[project_p44_soft_llr_viterbi]] — Phase 44 soft-LLR REFUTED (precedent)
- [[project_p27_h52_quality]] — Phase 27 H52 quality REFUTED (precedent)
- [[project_p41_usrp_htsig_final_verdict]] — Phase 41 USRP HT-SIG final closure
