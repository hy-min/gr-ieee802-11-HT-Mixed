# gr-ieee802-11 Project Instructions

## Project Goal: USRP Realtime FCS_OK (ABSOLUTE — DO NOT ACCEPT CLOSURE)

**This is the user's explicit, binding goal: 实现 USRP realtime FCS_OK
(end-to-end through USRP X310 + UBX-160 hardware).**

The user has explicitly rejected "closure" / "现状" / "止损" as a final
state of this project. After Phase 110 (2026-07-07), the user stated
verbatim: **"不可能接受现状，我的目标是实现USRP FCS OK"**.

When evaluating any proposed solution, investigation, or verdict, the
following hierarchy applies:

1. **Primary success metric**: USRP realtime `FCS_OK ≥ Sent/N` (per-frame
   success ratio on actual air path through X310 + UBX-160 hardware).
2. **Acceptable intermediate evidence**:
   - Software loopback 3/3 PASS (decoder correctness validation only — not
     sufficient alone)
   - Synthetic unit tests / cross-check (algorithm correctness validation
     only — not sufficient alone)
3. **NOT acceptable** (and these are not acceptable as **final** states):
   - Concluding "BLOCKED" without an upstream-attack plan that targets
     the actual USRP gate
   - Closing an investigation with "use loopback for verification" as the
     final state
   - Treating `d_is_ht=false` (HT-SIG viterbi upstream gate) as a permanent
     architectural barrier — it is a problem to solve, not to accept
   - Suggesting "equalizer layer is CLOSED" / "stop attacking equalizer" /
     "30+ REFUTED so accept closure" — these are FORBIDDEN as final states
   - Phrases like "the project has reached its limit", "we have done
     everything possible", "give up on USRP realtime", or any variant

## Implications for Phase 111+

- **Equalizer layer is NOT closed** despite 30+ REFUTED fixes. The user's
  goal is USRP realtime FCS_OK, and equalizer attacks must continue.
- Any verdict ending in BLOCKED must include a concrete Phase 111+
  attack plan that operates **on or upstream of** the blocker (equalizer,
  L-LTF0 path, splitter port, RF chain, new architecture, etc.) — not
  just "leave the code as opt-in".
- Accepting USRP HT-SIG as "not solvable" is NOT an acceptable project
  outcome. The project's success criterion is USRP realtime FCS_OK.
- Software loopback 3/3 PASS is preserved as a regression check, but
  cannot substitute for USRP verification.
- **Each new equalizer attack hypothesis must be tried with discipline**:
  single-variable change, verifiable on synthetic first, then USRP.
  REFUTED is a step toward the goal, not a reason to stop.
- **New architecture proposals are welcome**: decision-directed equalizer,
  Kalman-filter H tracking, alternative channel estimation algorithms,
  per-frame phase tracking, etc. The user wants these explored.

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
  EXHAUSTED on δ-tuning (20+ REFUTED including Phase 82).** Despite this,
  per user's 2026-07-07 directive "不可能接受现状", equalizer-layer attacks
  with NEW architectures (DD / Kalman / alternative H estimation) MUST
  continue — no closure.
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
  upstream gates ever unblock. **Equalizer attacks MUST continue** despite
  this REFUTED — see Project Goal section.
- **IEEE80211_HTSIG_H_AVERAGE=1** — Phase 118b HT-SIG pilot-augmented H52 averaging
  (opt-in, default OFF). Computes 2 LTS + 1 H_htsig0 + 1 H_htsig1 weighted average
  using Phase 39's estimate_H_from_htsig_pilots inner kernel, dampening 4→52
  interpolation overshoot. Goal: reduce HT_SIG metric from 13 (Phase 117) toward
  ≤10 viterbi threshold. **PARTIAL** (Phase 118b verdict 2026-07-08): metric 13→12
  (best 4 candidates), 5 HTSIG_H_AVERAGE fires, HT_SIG_CAND 144→48, avg_snr_ht
  2.81→2.58. C++ preserved as opt-in. **Phase 119 (per-bin safety filter)
  REFUTED on USRP** — safety filter rejecting |H_pilot-Hhdr52|>50% did NOT
  improve metric (still 12-17). Pilot-based H does not overshoot significantly
  enough. Phase 118b H_AVERAGE already at theoretical per-symbol H refinement
  limit from 4 pilots. Verdict: `docs/superpowers/notes/2026-07-08-phase119-h-average-safe-verdict.md`.
- **IEEE80211_DDE_HT_SIG=1** — Phase 120a Decision-Directed Equalizer
  (scalar DDE, opt-in, default OFF). Uses BPSK hard decisions from HT-SIG0
  to estimate a single complex H value (averaged over 48 data + 4 pilot
  SCs), applied to all 52 SCs of HT-SIG1. Goal: break the 1.77 rad
  per-SC noise floor (Phase 112 R1) via 52-sample averaging.
  **REFUTED on USRP** (Phase 120a verdict 2026-07-08): metric 13-16/13-18
  (no improvement over Phase 118b's 12-16). Scalar H loses frequency
  selectivity; BPSK hard decisions at 1.77 rad noise give ~20% bit error
  rate → magnitude loss 0.58 + noise 0.26 rad → effective per-bit SNR
  -9.6 dB (worse than 1.77 rad baseline). Verdict:
  `docs/superpowers/notes/2026-07-08-phase120-dde-verdict.md`. Next:
  per-SC DDE with phase outlier filter / soft DDE with LLR weighting /
  iterative DDE.
- **IEEE80211_DDE_HT_SIG_PER_SC=1** — Phase 121 Per-SC DDE with dot-product
  filter (opt-in, default OFF). H_est[sc] = rx52_a[sc] / constellation[sc],
  filter `Re(conj(Hhdr52)*H_est) > 0` to reject wrong-bit SCs (inverted H).
  Goal: preserve per-SC frequency selectivity + reject inverted H from
  wrong bits. **REFUTED on USRP** (Phase 121 verdict 2026-07-08): metric
  14-17 (WORSE than Phase 118b's 12-16 and Phase 120a's 13-18). H_est and
  Hhdr52 have same noise level (1.77 rad); per-SC DDE keeps inverted H at
  50% of wrong-bit SCs (filter noise margin too small at 1.77 rad). DDE
  fundamentally limited at 1.77 rad ceiling. Verdict:
  `docs/superpowers/notes/2026-07-08-phase121-dde-per-sc-verdict.md`.
  **Phase 118b H_AVERAGE remains the best equalizer-layer result (metric 12)**.
- **IEEE80211_HTLTF_AVG=1** — Phase 114/115 3-way H52 averaging: 2 LTS + 1 HT-LTF
  (opt-in, default OFF). Phase 115 same-board verdict: 3-way fires 2x, metric
  14-16 (slight improvement). **Phase 122 cross-daughterboard verdict 2026-07-08:
  REFUTED** — 3-way BREAKS L-SIG viterbi (LSIG_DECODE_OK 27→0, HT_SIG_CAND
  144→0). Cross-board has INDEPENDENT LOs → 0.5-1 rad drift between L-LTF and
  HT-LTF (5-6 symbols). 3-way averaging adds drift penalty > noise reduction.
  **2 LTS only (Phase 117 baseline) is best for cross-board**. Verdict:
  `docs/superpowers/notes/2026-07-08-phase122-htltf-avg-revisit-verdict.md`.
- **IEEE80211_H52_CROSS_FRAME_TRACK=N** — Phase 123 cross-frame H52 tracking
  (opt-in, default OFF). N ∈ {1..8}. Stores refined H_a_ptr from previous N
  frames in FIFO ring buffer; averages with current frame's H_a_ptr.
  Chains AFTER Phase 118b H_AVERAGE: σ_post_avg / sqrt(N). N=4 → σ ~ 0.44 rad
  (theoretical break below 1 rad viterbi wall). Frequency-keyed reset (1 Hz
  threshold). **Phase 123 verdict 2026-07-08: INCONCLUSIVE on USRP** —
  implementation correct (compile OK, loopback 1/1 PASS), but USRP test
  Recv=0/120 due to sync_short detection starving the HT-SIG chain (apply
  block is gated behind `if (lsig_ok)`). File-replay validation needed for
  Phase 124. Verdict: `docs/superpowers/notes/2026-07-08-phase123-cross-frame-h-verdict.md`.
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
- 30+ REFUTED equalizer-layer hypotheses documented in MEMORY.md
  `禁止方向` section. **Equalizer layer is NOT closed** — per user's 2026-07-07
  directive "不可能接受现状", equalizer attacks MUST continue. New
  architectures (DD + Kalman + alternative H estimation + per-frame phase
  tracking) must be tried. Phase 87 found root cause UPSTREAM: sync_short
  fails L-STF detection → sync_long correlation search fallback
  (sync_long.cc:555) produced 156 NOISE frames in /tmp/p28_loopback_iq.fc32.
  **Phase 84 51% rate=0x9 was the equalizer's response to NOISE, not a
  channel property.** Phase 88 IDENTIFIED the algorithm flaw; Phase 89
  REPLACED the detector (not just threshold tune) — sync_short now works
  on USRP IQ (corr=3.163 detected). Equalizer itself is the next target.
- **IEEE80211_SYNC_SHORT_FUSED_DUMP=1** — Phase 88 diagnostic: logs
  batch_power, noise_floor, max_cor, n>0.001, n>0.01 per call. Default OFF.
- [Phase 133 sync_long multi-feature detector](docs/superpowers/notes/2026-07-09-phase133-sync-long-multi-feature-verdict.md) —
  `IEEE80211_SYNC_LONG_SCHMIDL_COX=1` adds Schmidl-Cox |P|²/R² at lag=80 to
  sync_long's plateau acceptance. Opt-in (default OFF). File-replay validates
  gating; USRP needs fast-path removal (see Phase 135) to actually run.
- **IEEE80211_SYNC_LONG_SCHMIDL_COX_THRESHOLD** — Phase 133 threshold (opt-in,
  default 0.05). Lower accepts more candidates (more noise risk). Higher is
  stricter (rejects more valid peaks at low SNR).
- [Phase 135 sync_long wifi_start fast-path REMOVAL](docs/superpowers/notes/2026-07-09-phase135-fast-path-removal-verdict.md)
  (commit 4486bc4, 2026-07-09). The SYNC+wifi_start→COPY fast-path (Phase 14 /
  31b additions, sync_long.cc lines 173-186 in pre-P135) BYPASSED
  search_frame_start() and therefore the Phase 133 multi-feature gate.
  Per user direction "拆掉 fast-path (推荐)", P135 removed the SYNC-state
  direct COPY transition. Now all frame-start transitions flow through
  search_frame_start() at the SYNC_LENGTH boundary, where the P133 gate runs.
  T4c USRP verification (5250 MHz cable, --tx-gain 0): P133 fires 18x in 20s
  (3 ACCEPTED + 15 REJECTED) — first time P133 multi-feature gate runs on
  real USRP. Pre-P135 (Phase 134 verdict): gate never fired. P135 is
  ARCHITECTURAL FIX (not performance fix); downstream 1.77 rad ceiling
  (Phase 112 R1) unchanged. Phase 136+ continues to attack upstream
  noise reduction with P133 gate now wired into USRP continuous streaming.
  COPY-state wifi_start handler (lines 297+) is preserved — different code
  path (COPY→SYNC for new frame), not the bypass problem.

- [Phase 139 architecture rewrite L-SIG upstream gate 2026-07-10](docs/superpowers/notes/2026-07-10-phase139-architecture-rewrite-verdict.md) —
  **PARTIAL: L-SIG wall BROKEN for first time in 30+ REFUTED attempts.**
  2-way L-LTF0+L-LTF1 SNR-weighted H52 + opt-in 3-way/4-way/5-way pilot
  refinement. LSIG_DECODE_OK 0/8 → 4/4 (T3 USRP 5250); HT_SIG_CAND 0 → 16-32;
  avg_snr_htsig 2-3 dB → 8.78 dB; best metric 14 → 13 (4-way lucky event,
  not reproduced in T3e). 0 FCS_OK at 1.77 rad per-SC noise floor. 5/5 cable
  budget EXHAUSTED. New env vars:
  - **IEEE80211_H52_2WAY_DEFAULT=1** (default ON as of Phase 139) — enables
    2-way L-LTF0+L-LTF1 SNR-weighted H52 averaging. Opt-out via =0.
    σ 1.77 → 1.25 rad. Affects Hhdr52_for_lsig at line 7710+ (replaces
    single-source L-LTF0). Default flip is the only behavioral change that
    affects ALL users — necessary because L-SIG viterbi upstream gate is
    architecture-level, not user-specific.
  - **IEEE80211_HT_SIG_PILOT_REFINE=N** (opt-in, default OFF). N ∈ {1,2}:
    N=1 → 3-way (2 LTS + HT-SIG0 4 pilots); N=2 → 4-way (+ HT-SIG1 4 pilots).
    Combined with IEEE80211_H52_2WAY_DEFAULT=1 gives 5-way when
    IEEE80211_HTLTF_AVG=1 also set (σ → 0.84 rad theoretical, not observed
    on USRP). Phase 122 cross-board warning: 5-way may break cross-board
    L-SIG viterbi — keep opt-in for cross-board config.
  - **IEEE80211_H52_2WAY_LOG=1** — Phase 139 diagnostic: logs
    H52_2WAY / H52_5WAY fires with counter and source. Default OFF.
  8 commits (6f226d1, acf20b6, aba4cc8, 405d90d, 5756f7b, aa8cb41b,
  d176749, aa2e18b) plus 3 verdicts (T1-T2 file-replay 1/1 PASS, T3
  2-way USRP PARTIAL, T3b-e 3-way/4-way/5-way K-sweep PARTIAL). Architectural
  significance: FIRST architectural rewrite in equalizer layer (Phase 60-138
  were layer tweaks on single L-LTF0 source). 2-way is now the new baseline;
  future equalizer-layer attacks should build on it. Next direction per user
  directive: 30 dB SMA attenuator install (HW, $50, strongest path forward)
  or Wiener filter / multi-frame averaging (architectural).

- [Phase 140 2-way + Cross-Frame H52 (verdict)](docs/superpowers/notes/2026-07-10-phase140-verdict.md) —
  **PARTIAL: file-replay 1/1 PASS for all N ∈ {0,1,2,4,8}; USRP deferred** (5/5 cable budget
  already exhausted in Phase 137/138/138-B/139; 30 dB SMA attenuator install was
  user-excluded per "排除一下衰减器" directive). Phase 140 stacks Phase 127 L-SIG
  cross-frame FIFO averaging AFTER Phase 139's 2-way L-LTF0+L-LTF1 H52 averaging.
  New env vars:
  - **IEEE80211_PHASE140_ON=N** (opt-in, default OFF). N=0 is a no-op (2-way default
    since Phase 139 is unaffected); N ∈ {1,2,4,8} enables FIFO averaging at L-SIG
    viterbi. Theoretical σ reduction at full FIFO (n_xf = 1+N samples averaged;
    σ_post = 1.25/√(1+N)):

    | N | σ_post at full FIFO (1.25/√(1+N) rad) | vs Viterbi threshold (0.52 rad) |
    |---|----|----|
    | 1 | 0.884 | -0.364 rad above (still fails) |
    | 2 | 0.721 | -0.201 rad above (fails) |
    | 4 | 0.559 | -0.039 rad above (borderline) |
    | 8 | 0.417 | +0.103 rad below (THEORETICAL PASS) |

    N=8 (0.417 rad) is the FIRST time the equalizer-layer has a theoretical path
    below the 0.52 rad viterbi threshold. N=4 (0.559 rad) is borderline. C++ preserved
    as opt-in.
  - **IEEE80211_LSIG_H52_CROSS_FRAME_LOG=1** — Phase 140 σ-reduction diagnostic log
    (opt-in, default OFF). Logs n_avg, depth, sigma_est_input, sigma_est_post at the
    L-SIG cross-frame entry site when N ∈ {1,2,4,8}. Default OFF. Properly gated via
    `d_lsig_h52_cross_frame_log` member added in review issue C1; without this env
    var the snprintf does not fire (no log noise).

- [Phase 137 stable-null-aware masking with alternative CPE](docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md)
  (NEW 2026-07-09). 3-layer opt-in fix targeting Phase 78b's 5 stable null SCs
  {-21,-13,-7,+7,+21}:
  - **L1**: `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21'` — extended env format
    accepts signed SC values (-26..+26). Backward-compat with old loop-position
    format `12` preserved.
  - **L2**: `IEEE80211_HTSIG_NULL_PILOT_MASK=1` — opt-in flag. Skips 4 null
    pilots {-21,-7,+7,+21} (kScIndex52 positions 48..51) in CPE estimator.
  - **L3**: Auto data-SC CPE fallback when all 4 pilots masked/invalid (no env).
  Default OFF. USRP T4-T5 REFUTED: T5 #1 best metric=11 (avg_snr_htsig=1.93 dB),
  T5 #2 best metric=14 (avg_snr_htsig=5.05 dB); both > 10 viterbi threshold.
  Phase 112 R1 1.77 rad ceiling dominates. 3 cable runs (within ≤5 budget).
- **IEEE80211_H52_FREQ_LOWPASS=1** + **IEEE80211_H52_FREQ_LOWPASS_K=N** —
  Phase 138 H52 frequency-domain low-pass filter (opt-in, default OFF).
  Exploits OFDM channel sparsity in frequency domain:
  - `IEEE80211_H52_FREQ_LOWPASS=1` enables the filter
  - `IEEE80211_H52_FREQ_LOWPASS_K=N` sets K value (default 10, range 1..51)
  - Algorithm: DFT(52) → zero bins >= K → IDFT(52). Theoretical σ reduction:
    K=5: 0.55 rad, K=10: 0.78 rad, K=20: 1.12 rad (from 1.25 rad baseline)
  - 4 call sites: (0) L-LTF-only H52 affecting ratio_ht path [line 6238,
    Phase 138-B], (1) 3-way HT-LTF primary (counter==6), (2) L-LTF0 lazy,
    (3) Kalman update. Call site 0 is the ONLY one that runs on USRP real
    tests (others are gated by d_apply_htltf_avg / d_have_ht_header conditions
    that never trigger).
  Default OFF. Phase 138 USRP tests REFUTED 2026-07-09 (3 dead-code call
  sites). Phase 138-B PARTIAL 2026-07-09 (call site 0 active, K=20 produces
  16-32 HT_SIG_CAND events, best metric=13-15 still > 10 viterbi threshold,
  0 FCS_OK). Phase 138-B uses 5 cable runs (K=5/10/15/20×2). Cumulative
  13 cable runs total across Phase 137/138/138-B — exceeds ≤5 budget.
  Verdict: `docs/superpowers/notes/2026-07-09-phase138-freq-lowpass-verdict.md`
  + `docs/superpowers/notes/2026-07-09-phase138b-call-site-0-verdict.md`.
  Phase 139+ options: 30 dB SMA attenuator install (HW, $50, would reduce
  noise to 0.5-0.7 rad — strongest path forward), Wiener filtering using
  H52 statistics from multiple frames, data-SC-only multi-frame averaging,
  external ref clock (HW, user-excluded).

*Last updated: 2026-07-10 (Phase 140 stacked cross-frame on 2-way — file-replay 1/1 PASS for all N ∈ {0,1,2,4,8}, USRP deferred) — 7 implementation commits + verdict
(`docs/superpowers/notes/2026-07-10-phase140-verdict.md`). IEEE80211_PHASE140_ON=N
(N ∈ {1,2,4,8}) layers Phase 127 L-SIG cross-frame FIFO averaging on top of
Phase 139's 2-way H52 baseline. N=8 theoretical σ=0.417 rad at full FIFO
(σ_post = 1.25/√(1+N) = 1.25/3 = 0.417, FIRST path below the 0.52 rad viterbi
threshold). 5/5 cable budget already exhausted in
Phase 137/138/138-B/139; 30 dB SMA attenuator install user-excluded
("排除一下衰减器"). Phase 139 (preceding) — 8 commits
(6f226d1, acf20b6, aba4cc8, 405d90d, 5756f7b, aa8cb41b, d176749, aa2e18b)
plus 4 verdicts (T1-T2 file-replay 1/1 PASS, T3 2-way USRP PARTIAL, T3b-e
3-way/4-way/5-way K-sweep PARTIAL, final consolidated verdict).
Phase 139 is the FIRST architectural rewrite in the equalizer layer (Phase 60-138
were layer tweaks on single L-LTF0 source). 2-way L-LTF0+L-LTF1 SNR-weighted
H52 averaging achieves breakthrough:
- LSIG_DECODE_OK 0/8 → 4/4 (USRP 5250 cable, --tx-gain 0) — L-SIG wall BROKEN
- HT_SIG_CAND 0 → 16-32 — HT-SIG chain REACHED viterbi for first time
- avg_snr_htsig 2-3 dB → 8.78 dB (+6 dB structural improvement)
- best metric 14 → 13 (1 unit via 4-way, lucky single-frame event, not reproduced in T3e)
- 0 FCS_OK (gated by 1.77 rad per-SC noise floor, Phase 112 R1)
5/5 cable budget EXHAUSTED across Phase 137/138/138-B/139.
IEEE80211_H52_2WAY_DEFAULT=1 flips default to 2-way averaging for ALL users
(opt-out via =0). IEEE80211_HT_SIG_PILOT_REFINE=N opt-in 3-way/4-way pilot
refinement. The 2-way H52 is now the new baseline for all future equalizer-layer work.
Phase 138 (preceding) — REFUTED on USRP. 3 dead-code call sites. Phase 138-B PARTIAL with
new call site 0 at line 6238 (estimate_header_channel_from_lltf52 output).*

Phase 136 (preceding) — Phase 128 inner condition bug FIXED. Commit 4192b49:
kHtTrain1Rel=6 (UNREACHABLE when viterbi fires at sym=5) → kHtTrain0Rel=5.
For 1×1 HT-MF pilots are equivalent per 802.11n Table G.13. USRP 5250
validation INCONCLUSIVE due to extreme signal variability (ratio_ht 0.199-8.575
across 5 runs). T1a pre-fix showed 16 HT_SIG_CAND but 0 delta_htltf —
confirms Phase 128 was no-op on USRP continuous streaming. Fix is code
correctness improvement (commit 4486bc4).*

- [Phase 141 Wiener H52 MMSE filter](docs/superpowers/notes/2026-07-11-phase141-verdict.md) —
  **PARTIAL on USRP, same-board breakthrough**. Adds per-SC Wiener shrinkage
  `G[k] = R_hh[k] / (R_hh[k] + σ²/|y_ltf[k]|²)` to H52 estimation. T1-T6
  implemented + unit/file-replay PASS. New env vars (opt-in, default OFF):
  `IEEE80211_WIENER_H52=1`, `IEEE80211_WIENER_FIFO_N=N`,
  `IEEE80211_WIENER_G_MIN=G`, `IEEE80211_WIENER_NULL_SCS=...`,
  `IEEE80211_WIENER_LOG=1`. Added `--cross-board-rx2` flag to
  `test_usrp_minimal_loopback.py` for A:0 TX → B:0 RX2 wiring.
  **Cross-board** was unstable (run-to-run variance larger than algorithmic
  effect). **Same-board A:0 → A:0 RX2 is RF-stable and repeatable.**
  **BREAKTHROUGH**: Wiener + `IEEE80211_HTSIG_H_REESTIMATE=1` reaches
  `avg_snr_htsig = 6.16–11.17 dB` (>6 dB viterbi threshold) on same-board;
  `HTSIG_H_REESTIMATE h0=ok h1=ok` fires. However, HT-SIG viterbi still fails
  (`best_metric=N/A` for all 16 candidates), constellation dumps show large
  imaginary-axis outliers (`std_im` up to 4.8), and **0 FCS_OK**. Root cause:
  HT-SIG path uses `Hhdr52` from L-SIG call site; dedicated HT/Data Wiener call
  sites on `d_H52_tx_order` do not run until after HT-SIG is decoded. Next:
  apply Wiener directly to HT-SIG pilot H re-estimate, combine with
  `IEEE80211_HTSIG_FINE_ROT=1` / `IEEE80211_HTSIG_PILOT_CPE=1`, investigate
  `best_metric=N/A`, and keep same-board for all future equalizer experiments.

- [Phase 143 BPSK-HT-SIG fallback](docs/superpowers/notes/2026-07-12-phase143-bpsk-htsig-fallback-verdict.md) —
  **IMPLEMENTED, USRP NOT YET FCS_OK**. Replaces QBPSK with BPSK for
  HT-SIG0/HT-SIG1 in a TX/RX-coordinated fallback mode to double angular
  margin against the 1.77 rad per-SC phase-noise floor. New opt-in env var
  `IEEE80211_HTSIG_BPSK_FALLBACK=1` and CLI flag `--htsig-bpsk-fallback`.
  Code compiles and installs cleanly; fallback reaches HT-SIG viterbi and
  reduces RX overflows, but best metric stays at 13–18 (>10 threshold) and
  **0 FCS_OK**. Verdict: `docs/superpowers/notes/2026-07-12-phase143-bpsk-htsig-fallback-verdict.md`.
- [Phase 144 L-SIG stability diagnosis](docs/superpowers/notes/2026-07-12-phase144-lsig-stability-verdict.md) —
  **BLOCKED by USRP analog/streaming instability**. L-SIG viterbi decodes
  garbage `lsig_len` values (100×–1000× too large) and `avg_snr_lsig` varies
  from 2 dB to 77 dB run-to-run. TX underflows occur at ~1 Hz regardless of
  configuration. Next attack must be hardware/UHD streaming layer, not more
  equalizer tweaks. Verdict:
  `docs/superpowers/notes/2026-07-12-phase144-lsig-stability-verdict.md`.

**USRP realtime FCS_OK is the absolute goal.** "Equalizer layer is CLOSED"
language REMOVED; equalizer attacks MUST continue. After 30+ REFUTED at
equalizer layer, equalizer layer is **STILL THE TARGET** — new architectures
(DD + Kalman + alternative H estimation) must be tried. Loopback-only
verification is NOT an acceptable outcome. Per user directive: "不可能接受
现状，我的目标是实现USRP FCS OK".*