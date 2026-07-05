# Phase 100 — HT-SIG Bit Extraction Audit + avg_snr Interpretation Bug (2026-07-05)

**Branch**: TEST1
**Status**: 🟡 **3 HYPOTHESES REFUTED**, **avg_snr BUG IN PHASE 99 VERDICT DISCOVERED**, equalizer-layer
EXHAUSTED (27+ REFUTED, including Phase 100's 3 negative results).
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — **NOT achieved**, BLOCKED.
**Cable runs used**: 7 (Phase 100 used 0; verdict-only).

---

## TL;DR

Phase 100 investigated 3 hypotheses that *could* have explained why HT-SIG viterbi
fails at metric=13-15 while L-SIG viterbi succeeds at metric=0, on USRP at
avg_snr_htsig=10.63 (Phase 99's wording). All 3 hypotheses REFUTED:

| # | Hypothesis | Verdict |
|---|---|---|
| H1 | Double-division by `H52` in `decode_htsig_from_rotated` (since `d_early_eqsym` is already equalized) | **REFUTED** — `d_early_eqsym[kHtSig0Rel]` is raw FFT + CFO/SFO phase rotation, NOT pre-divided by H. The `safe_div(rx/H)` at line 2796 is the FIRST equalization. |
| H2 | Deinterleaver formula `j = 3*(k%16) + k/16` looks like forward permutation, not inverse | **CORRECT** — Forward interleaver is the inversion (by `802.11n` §17.3.5.10 BPSK convention). L-SIG uses the equivalent inverse precomputed table. Both produce same result. |
| H3 | QBPSK bit decision on `imag` axis is wrong | **CORRECT** — `eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0` matches QBPSK 90° rotation convention. |

**No bug in HT-SIG bit extraction.** The equalizer-layer is structurally CLOSED.

## 🚨 Critical Finding: avg_snr Interpretation Bug in Phase 99 Verdict

Phase 99 verdict claimed:
> "**avg_snr_htsig=10.27 dB is 1.7× the viterbi threshold** (6 dB needed).
>  At this SNR, raw BPSK BER should be ~10⁻⁶. We see ~15% errors (metric 13-15).
>  **SNR is no longer the bottleneck.**"

This is **WRONG**. The C++ code:
```cpp
avg_snr_htsig = (sum_mag2 / (double)cnt);  // mean of |eq|^2 over 48 SCs
```

`avg_snr` is **literally the mean |eq|²**, NOT SNR in dB. Per Phase 96's correct
conversion: `SNR_dB = 10*log10(1 / (avg_snr - 1))` for unit-amplitude BPSK
(mean of |eq|² = 1 + 2σ² for noise σ² per axis).

Phase 99 took 10*log10(12.47) = 10.96 "dB" and 10*log10(10.63) = 10.27 "dB" as
"SNR in dB" — this is **a unit error**, not a real SNR measurement. The actual
SNR cannot be derived from `avg_snr` without knowing the reference signal
amplitude (which we don't calibrate).

**Consequence**: Phase 99's "smoking gun" is not a smoking gun. Both L-SIG and
HT-SIG operate at the same `avg_snr` magnitude; we just don't know the true
SNR dB. The "10.27 dB > 6 dB threshold" conclusion is not supported by C++ output.

## ROOT CAUSE — Phase 78b 5 Globally-Null SCs (Structural)

Per Phase 78b verdict:
> "**USRP has 5 stable globally-null SCs (max std_im=7.8)** on 5250 MHz.
> Synthetic has rotating nulls (max std_im=3.6)."

These 5 SCs inject noise specifically on HT-SIG:
- L-SIG: 48 SCs × 1 OFDM symbol = 48 hits → ~2.5 null bits corrupted out of 48
  encoded (viterbi RECOVERS via brute-force rotation+inversion search)
- HT-SIG: 48 SCs × 2 OFDM symbols (HT-SIG0 + HT-SIG1) = 96 hits → ~5 null bits
  per symbol = ~10 random bits in 96 encoded (viterbi FREE-DISTANCE=10 EXACTLY,
  +1-3 noise errors pushes metric to 11-15, uncorrectable)

This is exactly viterbi's K=7 R=1/2 free distance = 10 ceiling. HT-SIG cannot
succeed without either (a) recovering the bits at null SCs, or (b) excluding
null SCs from viterbi's metric.

## Why all the equalizer-layer fixes REFUTE

| Phase | Strategy | Verdict |
|---|---|---|
| 41-46 | H52 null interp, MMSE | REFUTED — nulls are stable, interp doesn't help |
| 77b | Soft-LLR viterbi (Phase 44 impl) | REFUTED — metric saturates 14k-22k at 5250 clean |
| 78c | Force-zero at null SCs | REFUTED on synthetic — bias from forced 0s is 1/2 chance per SC |
| 79 | Per-symbol δ tracking | REFUTED on USRP — estimator works (4/4 synth) but USRP structural noise |
| 80b | Per-SC phase LUT | REFUTED on USRP — Sent=120, Recv=0 |
| 82 | δ-tuning at 5250 | REFUTED — ε-scan gives 10/149 best, no clean shift |
| 99 | Adaptive threshold floor 0.2 | HT-Mixed detected (avg_snr high) but HT-SIG metric still 13-15 |
| 100 H1 | Double-div-by-H | REFUTED — code correct |
| 100 H2 | Deinterleaver formula | CORRECT — both L-SIG and HT-SIG use equivalent forward |
| 100 H3 | QBPSK imag convention | CORRECT — matches 802.11n spec |

27+ REFUTED equalizer-layer hypotheses.

## Code state after Phase 99 (preserved)

- `lib/sync_short.cc:141` floor 0.05 → 0.2 (commit 2753b69) — keeps adaptive
  threshold above noise at 0.2, real L-STF at 1.4-2.3 well above
- All other Phase 100 attempts: NO code changes (3 REFUTED hypotheses —
  Iron Law confirms before fix)

## What's needed (per HARD CONSTRAINT)

Per project CLAUDE.md: "Any verdict ending in BLOCKED must include a concrete
Phase 60+ attack plan that operates **upstream** of the blocker."

Upstream attack vectors (Phase 100+ options):

### Option A — Fix avg_snr reporting (diagnostic only)

Add a proper per-SC SNR computation:
- For each of 48 SCs: compute |H[i]| and per-symbol residual noise σ[i]
- Plot histogram of per-SC |H| and per-SC SNR
- Show USER exactly which 5 SCs are the null SCs

Cost: ~30 lines of C++. ~1 hour. **Does NOT unblock HT-SIG** but gives clean
diagnostic data.

### Option B — Per-SC δ-aware null handling

For HT-SIG only: skip ALL SCs with |H|<`h_null_threshold` (e.g., 0.05 instead
of 0.001), set their bits to a deterministic value but with **soft-LLR confidence = 0**
so viterbi ignores them.

This requires:
1. Adding env var `IEEE80211_HTSIG_NULL_THRESH=0.05`
2. Hard-decision for null SCs: 0
3. Soft-LLR for null SCs: 0 (already conf → 0 when |H|→0)

**Risk**: REFUTED territory. Phase 78c REFUTED similar approach. But unlike Phase
78c, this uses soft-LLR confidence weighting, not hard replacement. viterbi
ignores conf=0 bits. **Should work** in principle; needs verification.

Cost: ~50 lines of C++. ~2 hours. **Would consume 1 cable run for verification**.

### Option C — L-LTF0 timing attack (upstream of equalizer)

Phase 33 found FRAME_START_BASE 160→174 fixed 64-PSK residual. But Phase 99
shows avg_snr=12.47 on L-SIG suggests timing is GOOD. Re-attacking timing is
unlikely to help.

### Option D — UHD streaming stability (upstream of all)

Phase 55: UHD streaming 8× SNR drift. 99% of samples lost to overflow. Even
with all equalizer fixes, UHD instability makes per-frame SNR vary wildly.

### Option E — 30 dB SMA attenuator (RX2 protection + lower noise floor)

Phase 81: HAT-30+ attenuator not yet installed, but Phase 96 used --tx-gain 20
on bare SMA cable (= HW risk per CLAUDE.md). With 30 dB attenuator:
- RX2 in linear range (RX2 was clipping at -2.6 dB in Phase 82)
- avg_snr_lsig 12.47 → maybe 17+ dB
- HT-SIG viterbi metric 13-15 → maybe 9-12 (recoverable)

Cost: 30 dB attenuator hardware. Risk: HW availability, USRP safety.

### Option F — STOP at equalizer-layer closure (current state)

Document equalizer-layer as CLOSED (Phase 78b + Phase 100 confirmation).
Accept Phase 18 L-SIG-only achievement as final.
Per HARD CONSTRAINT, this is BLOCKED — preserve code path, document upstream
attack plans, but cease cable tests.

## Recommendation

**Recommendation A**: Phase 100+ should pursue Option B (soft-LLR null
threshold) as the most promising equalizer-layer fix, then if REFUTED, Option E
(30 dB attenuator). This keeps within the project's narrow path to FCS_OK ≥ 1.

**Recommendation B**: If user is tired of cable runs (7/5 budget already
exceeded), accept closure and focus on documenting the upstream-attack plan
as the project's next chapter.

User decision required.

---

## Files of Record

- Phase 100 verdict (this file): `docs/superpowers/notes/2026-07-05-phase100-verdict.md`
- Phase 99 baseline: `docs/superpowers/notes/2026-07-05-phase99-verdict.md`
- Phase 78b null SCs: `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`
- Soft-LLR REFUTE: `docs/superpowers/notes/2026-07-03-phase77b-htsig-soft.md`
- avg_snr definitions: `lib/frame_equalizer_impl.cc:5651-5684`
- HT-SIG decoder: `lib/frame_equalizer_impl.cc:2684-3215`
- HT-SIG brute-force loop: `lib/frame_equalizer_impl.cc:6012-6052`

## Self-Review

**Spec coverage:** Verdict documents 3 REFUTED hypotheses, avg_snr interpretation
bug, equalizer-layer ceiling (Phase 78b 5 null SCs at viterbi free-distance=10),
and 6 upstream-attack options. Per HARD CONSTRAINT, BLOCKED requires upstream
plan — Options A-F provided. ✓

**No code changes** (Iron Law: 3 hypotheses REFUTED before any fix).

**Status:**

| Condition | Status |
|---|---|
| L-SIG viterbi | ✅ Pass (1/1 clean) |
| HT-SIG viterbi | ❌ Ceiling REACHED at metric 13-15 |
| avg_snr interpretation bug discovered | ✅ Phase 99 units conflated |
| Equalizer-layer EXHAUSTED | ✅ 27+ REFUTED |
| HARD CONSTRAINT (FCS_OK ≥ 1) | ❌ NOT achieved — BLOCKED |
