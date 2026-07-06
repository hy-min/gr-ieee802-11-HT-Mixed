# gr-ieee802-11 Project Instructions

## Project Goal: USRP End-to-End Validation (HARD CONSTRAINT)

**The goal of this project is USRP realtime end-to-end validation.**
Loopback-only verification is **NOT acceptable** as a final outcome.

When evaluating any proposed solution, investigation, or verdict, the following
hierarchy applies:

1. **Primary success metric**: USRP realtime `FCS_OK ≥ Sent/N` (per-frame
   success ratio on actual air path through X310 + UBX-160 hardware).
2. **Acceptable intermediate evidence**:
   - Software loopback 3/3 PASS (decoder correctness validation only — not
     sufficient alone)
   - Synthetic unit tests / cross-check (algorithm correctness validation
     only — not sufficient alone)
3. **NOT acceptable**:
   - Concluding "BLOCKED" without an upstream-attack plan that targets
     the actual USRP gate
   - Closing an investigation with "use loopback for verification" as the
     final state
   - Treating `d_is_ht=false` (HT-SIG viterbi upstream gate) as a permanent
     architectural barrier — it is a problem to solve, not to accept

## Implications for Phase 60+

- Any verdict ending in BLOCKED must include a concrete Phase 60+
  attack plan that operates **upstream** of the blocker (L-LTF0 path,
  splitter port, RF chain, etc.) — not just "leave the code as opt-in".
- Accepting USRP HT-SIG as "not solvable" is NOT an acceptable project
  outcome. The project's success criterion is USRP realtime FCS_OK.
- Software loopback 3/3 PASS is preserved as a regression check, but
  cannot substitute for USRP verification.

## Project-Specific Conventions

- **make install** must run after every `make` (else Python loads stale .so).
- **GRC ban**: never generate Python from `wifi_phy_hier.grc` (segfaults);
  edit `wifi_phy_hier.py` directly.
- **Thread-safe logging**: `USRP_LOG` is non-atomic. Multi-value dumps use
  `snprintf` + `USRP_LOG("%s", buf)` per commit `e90e3f5`.
- **env vars default OFF**: all new env vars opt-in to preserve baseline.
- **Standard USRP test config** (preserved across all phases):
  `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_TIMING_OFFSET_APPLY=1
  --freq 5890 --tx-gain 20`
  (Phase 65: removed `IEEE80211_LLTF_OFFSET_CORRECT=14` — 14-sample shift
  already correctly applied at `sync_long.cc FRAME_START_BASE 160→174`;
  re-shifting at splitter is the wrong axis per Phase 64 verdict.)
- **Same-board default**: `A:0` TX → `A:0` RX2 (per Phase 53 verdict,
  cross-board is 2.4x weaker).
- **禁止 `--rate 5`** (Phase 58 REFUTED, 48× more overflows than `--rate 20`).
- **Phase 82+ cable test config (NEW 2026-07-04, user accepted)**:
  Direct SMA cable (NO attenuator) is Phase 82 default per user
  decision after Phase 81 verdict. 30 dB SMA attenuator (HAT-30+)
  no longer required upfront. 5250 MHz cable gives +5.7 dB
  avg_snr_htsig boost vs 5890 air (9.61 dB vs 4.25 dB), well above
  6 dB viterbi threshold. **HW risk**: bare cable at --tx-gain 0
  sends ~+5 dBm into RX2 (20 dB above UBX-160 -15 dBm max). **Limit
  total cable runs ≤5** until 30 dB attenuator arrives.
  Test command: `test_usrp_minimal_loopback.py --freq 5250
  --tx-gain 0 --rate 20 --warmup 60 --rx-subdev A:0`
  (no `--cross-board`; same-board TDD with bare SMA male-male cable
  connecting TX/RX port to RX2 port on A:0).
- **Phase 82 attack direction (NEW 2026-07-04)**: Root-cause fix of
  Phase 34 δ correction algorithm at 5250 MHz cable. Bottleneck
  shifted from "viterbi wall" (5890 air, 4 dB SNR) to "Phase 18
  strict rate=0xD check rejects 0x9 decode at 5250" (Phase 81).
  Strategy: fix δ estimator so 5250 produces correct rate=0xD;
  HT-SIG decoder then unlocks naturally without LUT or rate-accept
  knob. Plan: T1 clean raw IQ capture → T2 offline δ analysis →
  T3 root-cause ID → T4 minimal fix → T5 USRP realtime verification.
  **VERDICT 2026-07-04: REFUTED** (`docs/superpowers/notes/2026-07-04-phase82-verdict.md`).
  ε-scan over [-32, +32]/64 produces at most 10/149 frames (6.7%) at
  rate=0xD — no clean shift. SNR on this capture (-2.6 dB) is 10 dB
  below Phase 81's reported 7.11 dB; LTF ref division (T3.5) confirmed
  it is NOT a Python analysis bug. **Equalizer-layer attack surface
  EXHAUSTED (20+ REFUTED including Phase 82).** No further cable runs
  pending a deliberate upstream-attack plan.
- **IEEE80211_HTSIG_PER_SYMBOL_DELTA=1** — Phase 79 per-symbol δ tracking
  for HT-SIG0/1 + data symbols (QBPSK-aware grid-search over 64-point δ).
  Default OFF. **REFUTED on USRP** (Phase 79 verdict 2026-07-02,
  FCS_OK=0/90, avg_snr_htsig=2.80 dB). Estimator works (synthetic 4/4 PASS)
  but USRP structural noise dominates. Kept as opt-in for triage.
- **IEEE80211_HTSIG_DELTA_DUMP=1** — Phase 79 diagnostic: logs δ_htsig0,
  δ_htsig1, per-data-symbol δ. Default OFF.
- **IEEE80211_HTSIG_PER_SC_LUT=/path/to/lut.json** — Phase 80b per-SC phase
  calibration LUT (median over N≥30 USRP frames, applied at HT-SIG0/HT-SIG1 +
  data symbol equalizer output). Default OFF (env unset). **REFUTED on USRP**
  (Phase 80b verdict 2026-07-04, USRP 5250 MHz 60s tx-gain 0: Sent=120
  Recv=0, HT_SIG_CAND=16/16 crc_fail). C++ preserved for future use if
  upstream gates ever unblock. Equalizer layer is **CLOSED** — Phase 82
  must attack upstream per HARD CONSTRAINT.
- **IEEE80211_FFT_WINDOW_DUMP=1** — Phase 108 diagnostic (opt-in, default OFF):
  dumps abs_in_off, d_data_start_rel, sym_idx_at_h52, d_internal_symbol_counter
  at the H52 compute site. Used to verify upstream FFT window alignment.
  Confirmed upstream is sample-stable per-frame on USRP (all 8 frames have
  identical d_data_start_rel=7). Verdict: 2026-07-06-phase108-fft-window-fix-verdict.md.
- **IEEE80211_CONST_CPE_APPLY=1** — Phase 108 fix (opt-in, default OFF):
  constant CPE rotation at L-SIG boundary to absorb a "static" phase offset.
  **REFUTED on USRP** — per-SC phase_offset varies from -180° to +180° across
  2733 measurements (linear std=81.6°, circular std=79.1° ≈ random); not a
  constant 30° as Phase 107 hypothesized. Fix DOES reduce |eq|^2 max outlier
  by ~100x (18827 → 175) but mean FCS_OK is unchanged (11 → 11 across 5 runs
  each). Preserved as opt-in for debugging. Phase 108 verdict 2026-07-06.

## Reference

- Phase 41 closure: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md`
- Phase 59 BLOCKED: `docs/superpowers/notes/2026-06-29-phase59-h52-null-interp-verdict.md`
  (call site unreachable due to `d_is_ht` gate; Phase 60 must attack
  upstream).
- Phase 77 closure (equalizer ceiling): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 80b REFUTED (per-SC LUT): `docs/superpowers/notes/2026-07-04-p80b-verdict.md`
- Phase 86 verdict (L-LTF0 audit, Phase 78b 5-stable-nulls REFUTED):
  `docs/superpowers/notes/2026-07-04-phase86-verdict.md`
- Phase 87 verdict (sync_short L-STF detection FAILS, sync_long noise fallback):
  `docs/superpowers/notes/2026-07-04-phase87-verdict.md`
- Phase 88 verdict (sync_short_fused MA(48)/MA(64) ratio FLAWED):
  `docs/superpowers/notes/2026-07-04-phase88-verdict.md`
- [Phase 89 verdict (sync_short detector replacement SUCCESS)](docs/superpowers/notes/2026-07-04-phase89-verdict.md)
- **IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1** — Phase 89 fix (opt-in, default OFF):
  replaces |MA(48)/MA(64)| with 16-sample boxcar-smoothed raw period-16 autocorr.
  Boxcar at noise (σ²=0.008) = 0.13; boxcar at L-STF (BPSK ±1) = 16. 100× margin.
- **IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1** — Phase 89 fix (opt-in, default OFF):
  threshold = max(median(last 4096 samples)*10, 0.01). Startup gate uses 3.0
  until window fills to suppress early-window false positives.
- **IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE** — Phase 89 fix (opt-in, default 2).
  Set to 16 to match L-STF plateau structure.
- **Repl results (80M samples, 4s)**: 24 detections at corr=1.95-20876 (was 174
  noise at 0.02-0.18 in Phase 88). HT_SIG_CAND: 16 entries (one frame through
  viterbi). Loopback 1/1 PASS unchanged.
- 5250 MHz cable run still required for USRP realtime FCS_OK ≥ 1 (HARD CONSTRAINT).
  HT-SIG viterbi needs avg_snr_htsig > 6 dB (currently 2-3 dB at 5890 air).
- 21+ REFUTED equalizer-layer hypotheses documented in MEMORY.md
  `禁止方向` section. **Equalizer layer is CLOSED** — Phase 87 found root
  cause UPSTREAM: sync_short fails L-STF detection → sync_long correlation
  search fallback (sync_long.cc:555) produces 156 NOISE frames in
  /tmp/p28_loopback_iq.fc32. **Phase 84 51% rate=0x9 was the equalizer's
  response to NOISE, not a channel property.** Phase 88 IDENTIFIED the
  algorithm flaw; **Phase 89 must replace the detector** (not just threshold
  tune) before any equalizer-layer validation on this dataset.
- **IEEE80211_SYNC_SHORT_FUSED_DUMP=1** — Phase 88 diagnostic: logs
  batch_power, noise_floor, max_cor, n>0.001, n>0.01 per call. Default OFF.

*Last updated: 2026-07-06 (Phase 108 CLOSURE — FFT-window fix PARTIALLY REFUTED.
IEEE80211_FFT_WINDOW_DUMP confirms upstream is sample-stable per-frame (not
the misaligned block). IEEE80211_CONST_CPE_APPLY reduces |eq|^2 max outlier
by 100x (18827→175) but Phase 107's "static 30° rotation" hypothesis is
REFUTED — per-SC phase_offset is random across [-180°,+180°] across 2733
measurements. Mean FCS_OK unchanged (11→11 across 5+5 runs). HARD CONSTRAINT
NOT achieved. Phase 109+ plan: per-frame CFO/SFO estimator investigation +
UHD streaming stability (Phase 55 revisit) + RF chain per-SC |H| check.)*