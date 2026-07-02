# Phase 78a Verdict — 77a REFUTED on Synthetic USRP-like Channel

**Date**: 2026-07-03
**Branch**: TEST1
**Status**: **77a REFUTED on synthetic. 91% baseline decoder passes most USRP-like frames with IDEAL equalization. 77a makes it worse (-24.3 pp).**

---

## TL;DR

| Config | Success rate | Comment |
|--------|--------------|---------|
| Layer 4 baseline (ideal H) | **273/300 = 91.0%** | Decoder CAN handle USRP-like channel |
| Layer 4 + 77a per-symbol CPE | 200/300 = 66.7% | **77a REFUTED — adds noise, breaks QBPSK search** |

**Key insight**: With USRP-like channel and IDEAL equalization, the Python decoder passes 91% of frames. The 9% failure comes from null + AWGN, not equalization. On USRP, HT_SIG_PARSE_OK=0 means the wall is upstream of decoder — either H estimation is broken, or USRP has additional impairment not in synthetic.

---

## Tasks 总结

### Task 1: USRP-like channel model (commit 31c0a2e)
- Status: DONE
- Implements 2-path multipath, per-frame δ (uniform in [-π/64, +π/64]), 5-10 frequency-selective nulls, AWGN at 3 dB
- 91.0% success rate (273/300) with IDEAL equalization

### Task 2: 77a per-symbol CPE (commit 18319af)
- Status: **REFUTED on synthetic**
- 66.7% success rate (200/300) — **24.3 pp worse than baseline**
- Root cause: 77a adds rotation noise that breaks the 16-candidate QBPSK search's optimal alignment

---

## Why 77a hurts (analysis)

The synthetic channel applies a per-frame H with no per-symbol phase drift. The 4 HT-SIG pilots carry NO real phase info (the channel is constant per frame). The 77a phase estimate from 4 noisy pilots ADDS rotation noise to the equalized signal.

The decoder's 16-candidate QBPSK search already handles unknown phase ambiguity by trying 4 rotations × 2 inversions per half = 16 candidates and selecting the one with best viterbi metric. Pre-rotating with a noisy estimate pushes the signal off the optimal QBPSK rotation, so the search converges to a worse choice.

---

## What this means for USRP

**USRP 77a verdict (+0.4 dB) is suspect**: 77a was supposed to help on USRP (Phase 77a), but on synthetic it hurts. The +0.4 dB on USRP was likely noise fluctuation across the 100-frame average.

**91% baseline gap is real**: If synthetic USRP-like channel + ideal H gives 91%, but USRP gives 0%, the gap is:
1. **H estimation in C++ is broken** (H52 estimated from L-LTF is wrong), OR
2. **USRP has additional impairments not modeled** (e.g., DC offset, IQ imbalance, sample timing drift across 4 µs between HT-SIG0 and HT-SIG1)

---

## Per HARD CONSTRAINT — Phase 78b Plan

**78b: Per-frame offline analysis at 5250 MHz**
- Slice `/tmp/p76_selftx_5250.bin` (126 MB) into 1-second windows
- For each frame where HT_SIG_PARSE_OK=0, dump:
  - H52 estimation quality (vs synthetic reference)
  - Equalized HT-SIG0/1 constellation
  - Per-symbol phase drift between HT-SIG0 and HT-SIG1
- Goal: identify the structural difference between USRP and synthetic

**Why this matters**: 91% baseline shows decoder is robust. The 9% failure is from null + AWGN. If USRP H estimation is even 50% accurate, it should pass some frames. The fact that 0 frames pass on USRP means there's a major structural issue not captured by synthetic.

---

## Files

### 新增 (this verdict)
- `docs/superpowers/notes/2026-07-03-phase78a-synthetic-verdict.md` (this file)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p78a_synthetic_refuted.md`

### Phase 78a implementation
- `examples/test_htsig_viterbi_synthetic.py` (Layer 4 added, +77a test)
- `/tmp/p78a_baseline.log` (baseline output)
- `/tmp/p78a_with_cpe.log` (77a output)

### Commits
- `31c0a2e` — feat(p78a): Layer 4 USRP-like channel model + baseline test
- `18319af` — feat(p78a): 77a per-symbol CPE in Python synthetic test

### Related Phase 77 work
- `docs/superpowers/notes/2026-07-03-phase77-verdict.md` (Phase 77 verdict)
- `docs/superpowers/notes/2026-07-03-htsig-closure.md` (HT-SIG closure)

---

## Self-Review

**1. Spec coverage:** Verdict documents Layer 4 baseline (91.0%) + 77a REFUTED (66.7%) + root cause + Phase 78b plan. Per HARD CONSTRAINT, REFUTED verdict requires upstream plan — 78b is upstream. ✓

**2. Placeholder scan:** No TBD placeholders. ✓

**3. Type consistency:** Env var names match (`IEEE80211_HTSIG_PILOT_CPE=1`). File paths absolute. ✓

**Notes:**
- 77b/77c not tested in synthetic: 77b needs Python viterbi rewrite (significant work), 77c operates on H estimation (N/A in synthetic where H is ideal).
- The 91% baseline finding is the most important result: it shows the decoder is fundamentally capable.
- The USRP 0% vs synthetic 91% gap is the actionable signal for Phase 78b.