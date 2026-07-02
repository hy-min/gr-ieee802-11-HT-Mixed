# Phase 78b Verdict — USRP vs Synthetic: Persistent per-SC Null SCs Identified

**Date**: 2026-07-03
**Branch**: TEST1
**Status**: **STRUCTURAL DIFFERENCE IDENTIFIED** — USRP has 5 stable globally-null SCs (max std_im=7.8) on 5250 MHz. Synthetic has rotating nulls (max std_im=3.6). Per-SC correlation 0.122 (LOW). The wall is consistent with Phase 33b per-frame sub-sample timing offset δ causing deterministic per-SC phase rotation that survives C++ equalization.

---

## TL;DR

| Metric | USRP (est H) | Synthetic (ideal H) | Gap |
|--------|--------------|---------------------|-----|
| std(im) all SCs | 2.634 | 2.432 | 8% (small) |
| std(im) no-null | 1.715 | 2.432 | -29% (USRP better!) |
| mean(|re|) | 0.977 | 1.471 | -35% |
| per-SC std(im) max | **7.784** | 3.647 | **+113% (USRP worse)** |
| per-SC std(im) median | 1.467 | 1.993 | -26% |
| Globally null SCs (std_im>3) | **5 stable SCs** | 0 | - |
| Per-SC correlation | 0.122 | - | LOW |

**Key insight**: USRP has 5 STABLE per-SC nulls (max std_im=7.8 at the edge SCs). Synthetic has rotating nulls. The 5 USRP null SCs are CONSISTENT with Phase 33b 64-PSK residual — a per-frame sub-sample timing offset δ that creates deterministic per-SC phase rotation exp(j*2π*δ*k/64) affecting edge SCs more than center.

---

## Per-SC Detail

### USRP (8 frames, est H from L-LTF)

- 5 globally null SCs (std_im > 3.0 across all 8 frames)
- max std_im = 7.784
- median std_im = 1.467
- min std_im = 0.572

### Synthetic (100 frames, ideal H, Layer 4 channel)

- 0 globally null SCs (nulls rotate across frames)
- max std_im = 3.647
- median std_im = 1.993
- min std_im = 1.018
- nulls_per_frame: mean 7.66, range [5, 10] — random per-frame distribution

**Per-SC correlation 0.122** = USRP and synthetic do NOT share the same "bad SCs". USRP has persistent impairment on specific SCs; synthetic has random per-frame nulls.

---

## Why USRP has 5 stable null SCs

**Per Phase 33b**: USRP shows 64-PSK residual pattern. Per-frame sub-sample timing offset δ causes:
- For SC k: phase = 2π*δ*k/64
- For k=±26 (edge SCs): phase = ±2π*δ*26/64 = ±2.55*δ
- For δ ~0.85/64 (typical), phase ~ ±2.17 rad
- After C++ equalization with H estimated from L-LTF (also affected by δ), residual phase concentrates on edge SCs

This explains the 5 stable null SCs being concentrated at the high-|k| positions.

**Synthetic model doesn't include this** because Layer 4 channel is a scalar multiply (H[k] per SC, constant per frame), not a per-symbol phase ramp.

---

## Why this matters for HT-SIG viterbi

QBPSK uses IMAG axis decision. With 5 SCs having std_im=7.8, the per-SC contribution to viterbi metric is:
- BM = (eq.imag - target)^2 / σ^2
- For std_im=7.8 at null SC: σ = 7.8, so BM contribution is small (high noise variance = low weight)
- But the systematic BIAS on those SCs (mean(im) ≠ 0) corrupts the BPSK decision

Synthetic 91% pass rate shows the channel is recoverable IF the per-SC biases can be tracked. USRP 0% pass shows the biases are too large to recover with the current C++ equalizer.

---

## Phase 78c Plan — Per-SC Phase Correction

Per HARD CONSTRAINT upstream attack. Phase 78c targets the persistent USRP null SCs.

### 78c-1: Per-SC phase calibration from L-LTF
- After H52 estimation, identify the 5 stable null SCs
- For each, estimate per-SC phase bias from the L-LTF data (not from HT-SIG pilots)
- Apply per-SC phase rotation: eq[i] *= exp(-j*phase_bias[i])
- This is different from per-symbol CPE — it operates on SPECIFIC SCs, not all 52

### 78c-2: Per-frame δ with per-SC gradient
- Phase 33b showed per-frame δ causes deterministic per-SC phase
- If we can estimate δ more accurately (using the H52 null pattern as a fingerprint), we can correct all 52 SCs simultaneously
- Risk: similar to Phase 36/39 (REFUTED) but with the new insight from 78b

### 78c-3: Re-test 77a-77c on USRP with 78c calibration
- Apply per-SC phase correction BEFORE 77a-77c
- Measure HT_SIG_PARSE_OK count
- If 0 → 76 → 50 → 0 (no improvement), accept closure
- If > 0 → 78c worked, write final verdict

### 78c-4: Accept HT-SIG closure if 78c-1/2/3 all fail
- 19+ REFUTED total (was 18, add 78b findings as new evidence)
- Software loopback 3/3 PASS preserved as decoder validation path
- HARD CONSTRAINT upstream plan: not implementable without changing USRP hardware (Phase 33b 64-PSK is physical)

---

## Files

### 新增 (this verdict)
- `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md` (this file)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p78b_per_sc_nulls.md`

### Phase 78b implementation
- `examples/p78b_parse_log.py` (log → JSON parser)
- `examples/p78b_synthetic_metrics.py` (synthetic reference)
- `examples/p78b_compare_usrp_synthetic.py` (refined comparison)
- `/tmp/p78b_per_frame.json` (8 USRP frames)
- `/tmp/p78b_synthetic_metrics.json` (100 synthetic frames)
- `/tmp/p78b_comparison.json` (summary)

### C++ fix
- `lib/frame_equalizer_impl.cc` (commit 5901cdd — HTSIG_EQ_DUMP moved out of L-SIG-required loop)
- `docs/superpowers/notes/p78b_dump_v2.log` (25128 lines, 10 eq dumps)

### Commits
- `8c98cba` — docs(p78b): raw dump log from p76 5250 replay
- `5901cdd` — fix(p78b): HTSIG_EQ_DUMP gating
- `e5a8c0c` — feat(p78b): log parser
- `af1a492` — feat(p78b): synthetic metrics
- `d8f453f` — feat(p78b): comparison

---

## Related

- [[project_p77_equalizer_ceiling]] — Phase 77 (cumulative +4.15 dB but wall)
- [[project_p76_htsig_chain_partial]] — Phase 76 (576 HT_SIG_CAND at 5250)
- [[project_p78a_synthetic_refuted]] — Phase 78a (77a REFUTED, 91% baseline)
- [[project_usrp_htsig_final_verdict]] — Phase 41 (HT-SIG closure)
- [[project_p33b_usrp_validation_64psk]] — Phase 33b (64-PSK residual = USRP timing)
- [[project_p38_per_symbol_delta_drift]] — Phase 38 (HT-SIG eq std_im wall)

## Self-Review

**1. Spec coverage:** Verdict documents structural difference (USRP 5 stable null SCs vs synthetic rotating nulls), traces cause to Phase 33b 64-PSK residual, and provides Phase 78c plan with 3 attack vectors + closure. Per HARD CONSTRAINT, REFUTED verdict requires upstream plan — 78c is upstream. ✓

**2. Placeholder scan:** No TBD placeholders. ✓

**3. Type consistency:** Env var names match. ✓

**Notes:**
- This is the FIRST time we have direct evidence of USRP's per-SC impairment pattern.
- 5 stable null SCs is a much more actionable signal than "H estimation is broken".
- 78c has 3 attack vectors, but risk is high (similar REFUTED territory).
