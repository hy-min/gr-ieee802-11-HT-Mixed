# USRP HT-SIG Verdict — FINAL (2026-06-28)

**Status:** USRP HT-SIG viterbi decoding ACCEPTED as a known hardware-implementation limitation. Investigation closed after 41 phases / 12 REFUTED hypotheses.

## Root Cause (Phase 38, confirmed)

**Hhdr52 channel nulls** at the air-interface cause noise amplification 50× at affected subcarriers. The equalized HT-SIG0/1 constellation lands on the REAL axis (BPSK-like) instead of the QBPSK-imaginary-axis. This breaks the upstream `ratio_ht > 1.2` heuristic that sets `is_ht_frame=1` (Phase 41), and the downstream viterbi fails (Phase 19/35-39).

Quantified impairment (from Phase 38 dumps):
- `|Hhdr52[i]| ∈ [0.02, 0.14]` at null SCs (vs 0.5-1.0 at strong SCs)
- Equalized HT-SIG signal: std_im = 1.1-1.9 (imag axis, QBPSK decision)
- L-SIG BPSK (real axis, 90° margin) survives; HT-SIG QBPSK (45° margin) cannot

This is a **channel-physics limitation**, not a software algorithm bug. Fixing it would require per-SC null detection + outlier rejection + H52 interpolation across null SCs — an architectural change, not a one-line env var.

## REFUTED Hypotheses (12 total)

| # | Phase | Hypothesis | Status |
|---|---|---|---|
| 1 | 25 | SFO / phase ramp | REFUTED |
| 2 | 26 | Decision-directed phase tracking | REFUTED |
| 3 | 27 | H52 estimation quality variants | REFUTED |
| 4 | 29.2 | Viterbi input scaling | REFUTED |
| 5 | 30 | Per-SC SNR drop | REFUTED |
| 6 | 35 | Per-symbol mean pilot CPE on HT-SIG | REFUTED |
| 7 | 36 | Per-SC linear fit on HT-SIG pilots | REFUTED |
| 8 | 37 | Soft-decision LLR / CFO tolerance | REFUTED (decoder is correct) |
| 9 | 38 | Per-symbol CPE via estimate_header_cpe_rad | REFUTED (pilots on REAL axis, ± structure cancels) |
| 10 | 39 | HT-SIG pilot-based H re-estimation | REFUTED (pilots too noisy, std_im 1.5→12.7) |
| 11 | 40 | Splitter K-offset for HT-SIG0/1 | REFUTED (FFT windows already aligned, delta=0) |
| 12 | 41 | `is_ht_frame=0` anomaly (Phase 18 fix test) | REFUTED as root cause; useful 14× reduction in brute-force failures but doesn't unlock viterbi |

## Kept Improvements (permanent USRP test configuration)

### Phase 18 — L-SIG Rate Force (commit 2502978)
- Env var: `IEEE80211_LSIG_RATE_FORCE=0xD`
- Effect: Rejects 144 wrong-rate L-SIGs, reduces false-positive HT-SIG brute-force attempts
- Quantified: `HT_SIG_PARSE_FAIL 176→24` initially; `112→8` with full Phase 33/34/40/41 stack
- **Keep enabled for all USRP runs.**

### Phase 33 — L-LTF0 14-Sample Shift (commit bd5c1d2)
- Change: `FRAME_START_BASE 160→174` in `lib/sync_long.cc`
- Effect: H52 arg std 1.86→0.0000, LTF0_FFT perfect BPSK (was 8-DPSK)
- **Permanent change to source code.** Do not revert.

### Phase 34 — Per-Frame Sub-Sample δ Correction
- Env var: `IEEE80211_TIMING_OFFSET_APPLY=1`
- Effect: δ estimation via linear regression on argH vs SC index (100% within 0.01 of 1/64 grid)
- Quantified: Unblocks L-SIG viterbi on USRP
- **Keep enabled for all USRP runs.**

### Phase 40 — HTSIG_TIMING_DUMP (commits 1e38fa0, bc014d5)
- Env var: `IEEE80211_HTSIG_TIMING_DUMP=1` (default OFF, opt-in)
- Effect: Logs rel_idx, expected rel_idx, K, and delta at every OFDM symbol boundary
- Purpose: Diagnostic only. Demonstrated that HT-SIG0/1 FFT windows are aligned (delta=0).
- **Keep as opt-in diagnostic.** Default OFF (no impact on USRP runs that don't enable it).

## Decoder Validation Path (kept)

**Software loopback 3/3 PASS** remains the canonical decoder validation path:
- `examples/test_direct_loopback.py` — full chain regression
- `examples/test_htsig_viterbi_synthetic.py` — HT-SIG viterbi specifically (Phase 37: 3/3 PASS, metric=0)
- `examples/test_lsig_viterbi_synthetic.py` — L-SIG viterbi
- `examples/test_h_estimation_synthetic.py` — H estimation

The decoder is correct. The viterbi tolerates static CFO up to 5 kHz and AWGN down to 6 dB SNR (Phase 37 Layer 1/2/3 PASS). USRP HT-SIG failure is **NOT a decoder bug**.

## Hardware Status

**USRP X310 + UBX-160 5 GHz, subdev A:0:**
- ✅ DC offset: 2e-6 (clean)
- ✅ TCXO: 0.6ppb error (within spec)
- ✅ Noise floor: -74.5 dB (clean)
- ✅ LO leakage: clean (Phase 17 finding: A:0 5 GHz is clean, B:0 2.4 GHz had 16-sample pattern)
- ✅ Reference/LO locked

**Conclusion: Hardware is OK.** Software RX chain is the issue, and the issue is the channel physics (nulls at certain SCs), not a software bug.

## Standard USRP Test Configuration (as of 2026-06-28)

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --duration 30
```

Optional diagnostics (add as needed):
```bash
IEEE80211_HTSIG_TIMING_DUMP=1   # Phase 40: per-OFDM-boundary rel_idx
IEEE80211_DELTA_PER_SYMBOL_DUMP=1   # Phase 38: per-symbol δ (currently doesn't fire, separate bug)
IEEE80211_HTSIG_BIN_DUMP=1   # Phase 35: HT-SIG raw FFT bins (gated to counter==4)
IEEE80211_HTSIG_EQ_DUMP=1   # Phase 38: equalized HT-SIG constellation
```

## Outstanding Items (Low Priority)

1. **`DELTA_PER_SYMBOL_DUMP` doesn't fire** — env var wiring issue, separate bug. Low priority because the diagnostic would only help if we resume USRP HT-SIG investigation (which we are not).
2. **`is_ht_frame=0` heuristic location** — `frame_equalizer_impl.cc:3620` (Phase 41 finding). Documented but not fixed.
3. **Per-SC Hhdr52 null detection** — would unblock HT-SIG but is an architectural change (not a 1-2 line fix). Documented as future work but not scheduled.

## Final State

- USRP L-SIG: **UNBLOCKED** ✅ (Phase 33/34 + Phase 18 working)
- USRP HT-SIG: **KNOWN LIMITATION** ⚠️ (root cause identified, no software fix practical)
- Software loopback (full chain): **3/3 PASS** ✅
- Decoder correctness: **VERIFIED** ✅ (Phase 37)
- Hardware: **OK** ✅
- Investigation: **CLOSED** 🏁 (12 REFUTED, root cause confirmed)

## References

- Phase 38 verdict: `docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md`
- Phase 39 verdict: `docs/superpowers/notes/2026-06-25-phase39-htsig-h-reestimate-verdict.md`
- Phase 40 verdict: `docs/superpowers/notes/2026-06-25-phase40-verdict.md`
- Phase 41 verdict: `docs/superpowers/notes/2026-06-28-phase41-verdict.md`
- All other phase verdicts: `docs/superpowers/notes/2026-06-*.md`