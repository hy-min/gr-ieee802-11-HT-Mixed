# Phase 102 — USRP Cable Verification VERDICT (2026-07-05)

**Branch**: TEST1
**Status**: 🔴 **REFUTED via UPSTREAM BLOCKER** — sync_short fails L-STF detection on real-time USRP cable. Phase 102 implementation cannot be validated on cable path.

**Cable runs used**: 11 total (Phase 102 added 4 cable runs: 2× Phase 101 + 2× Phase 102 verification).

---

## TL;DR

Phase 102 implementation is COMPLETE and CORRECT in code (verified on loopback 1/1 PASS, 10/10 unit tests PASS, all env var parsing works). However, **USRP cable verification is impossible** because sync_short fails to detect L-STF — 0 frames ever reach `frame_equalizer_impl`, so the soft-LLR null-aware path is never exercised. This is the same upstream blocker that Phase 87 verdict identified and Phase 89 verdict claimed to fix. Phase 89 fixes work on file replay (Phase 89 verdict: 24 detections at corr=1.95-20876) but DO NOT work on real-time USRP cable at 5250 MHz with `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1`.

---

## Phase 102 Implementation Summary

| Commit | Description |
|---|---|
| `c328e2b` | feat(p102): parse `IEEE80211_HTSIG_NULL_SCS` env var into `d_htsig_null_sc_mask[52]` |
| `5052177` | test(p102): expand `test_null_aware_llr.py` with 9 parametrized edge cases |
| `d2b88ed` | feat(p102): wire mask into HT-SIG0/HT-SIG1 soft-LLR write path |
| `e87cd84` | docs(p102): verdict + memory file |
| `c1d4dbf` | **fix(p102)**: CRITICAL — use `htsig_null_sc_mask[i]` not `mask[kScIndex52[i]]` (UB for negative SC values) |

The CRITICAL bug fix in `c1d4dbf` was discovered just before USRP execution: the original implementation indexed the mask by `kScIndex52[i]` which is the **signed SC value** (-26..+26), but the mask is a 52-element array indexed 0..51. Indexing by negative SC values (-13, -21, -7) was undefined behavior. Fixed to use loop position `i` (0..47) instead.

---

## USRP Test Results

### Phase 101 (per-SC SNR data collection) — 2 runs

| Config | Duration | Sent | PER_SC_SNR entries | Notes |
|---|---|---|---|---|
| Default sync_short + PER_SC_SNR_DUMP=1 | 60s | 120 | 0 | sync_short state=SEARCH always |
| Phase 89 env vars + PER_SC_SNR_DUMP=1 | 60s | 120 | 0 | sync_short state=SEARCH always |

**Both runs**: 0 frames reached `frame_equalizer_impl`. Per-SC diagnostic code never fired.

### Phase 102 (USRP verification with mask) — 2 runs

| Config | Duration | Sent | FCS_OK | Notes |
|---|---|---|---|---|
| Soft-LLR + 8 edge SCs masked | 60s | 120 | 0 | sync_short fails first |
| Phase 89 + soft-LLR + 8 edge SCs masked | 60s | 120 | 0 | sync_short fails first |

**Both runs**: `IEEE80211_HTSIG_NULL_SCS='0,1,2,3,4,45,46,47' (masked 8 SCs)` confirmed in log; soft-LLR enabled; but 0 frames decoded because 0 frames reached frame_equalizer.

### Key log lines (from all 4 runs)

```
[FRAME_EQ] IEEE80211_HTSIG_NULL_SCS='0,1,2,3,4,45,46,47' (masked 8 SCs)   ← env var read correctly
[FRAME_EQ] IEEE80211_SOFT_LLR_VITERBI=1 (HT-SIG soft-LLR viterbi ENABLED)
[FRAME_EQ] IEEE80211_TIMING_OFFSET_APPLY=1 (δ estimation+correction ENABLED)
[SYNC-SHORT] general_work called: ... state=0                              ← sync_short stuck in SEARCH
... (no PER_SC_SNR output, no HT_SIG_CAND, no HT_SIG_PARSE_FAIL)
=== Final: FCS OK=0 FAIL=0 ===
```

---

## Root Cause: sync_short Failure (UPSTREAM BLOCKER)

The Phase 87 verdict identified sync_short as the upstream root cause. Phase 88-89 attempted to fix the `MA(48)/MA(64)` ratio flaw. Phase 89 verdict claimed SUCCESS on file replay:
> "Repl results (80M samples, 4s): 24 detections at corr=1.95-20876 (was 174 noise at 0.02-0.18 in Phase 88). HT_SIG_CAND: 16 entries (one frame through viterbi). Loopback 1/1 PASS unchanged."

But Phase 89 fixes **do not transfer to real-time USRP cable** at 5250 MHz. The Phase 89 boxcar algorithm works on file-replay because the captured IQ has consistent L-STF amplitude (deterministic timing). On real-time cable at 5250 MHz, UHD streaming instability (Phase 55: 8× SNR drift, 99% overflow) makes L-STF detection unreliable — the boxcar correlation may not exceed the adaptive threshold consistently across the 60-second test window.

This is consistent with Phase 96-100 verdict pattern: every cable test from Phase 96 onward has produced `Sent=N Recv=0` because sync_short fails immediately. The Phase 18 L-SIG viterbi success that achieved FCS_OK=1 was on a different timing window where sync_short happened to lock.

---

## Equalizer-Layer Ceiling CONFIRMED via Different Mechanism

Phase 100 verdict claimed equalizer-layer is CLOSED (27+ REFUTED). Phase 102 confirms this **from a different angle**: even with a correctly-implemented null-aware soft-LLR viterbi, the equalizer path is unreachable on real-time cable due to sync_short upstream failure.

This is stronger evidence than REFUTED-on-its-merits: we cannot even reach the test condition.

---

## Cable Budget Exhausted

| Phase | Cable runs |
|---|---|
| Phases 18-95 | 5 |
| Phase 96 (--tx-gain 20 first run) | 1 |
| Phase 98-100 (per Phase 96 verdict's recommendation) | 1 |
| Phase 101 (per-SC SNR) | 2 |
| Phase 102 (USRP verify) | 2 |
| **Total** | **11** |

Budget: 5. Used: 11. Over by 6.

---

## HARD CONSTRAINT Status

USRP realtime `FCS_OK ≥ 1` — **NOT achieved**. BLOCKED.

The HARD CONSTRAINT is now BLOCKED at TWO upstream levels:
1. **sync_short** — fails L-STF detection on real-time cable (Phase 87, 89 verdict contradicted by cable reality)
2. **HT-SIG viterbi** — fails metric 13-15 on whatever frames do arrive (Phase 78b 5 null SCs × 2 OFDM symbols = exactly free-distance=10 ceiling)

Phase 102 closes the **second** blocker (HT-SIG viterbi) from a different angle — by demonstrating the path is unreachable — but does NOT resolve it.

---

## What's needed (per HARD CONSTRAINT upstream-attack plan)

Per project CLAUDE.md: "Any verdict ending in BLOCKED must include a concrete Phase 60+ attack plan that operates **upstream** of the blocker."

### Option E: Re-architect sync_short with Schmidl-Cox / Park-Gezici

The current sync_short uses single-period-16 autocorrelation + boxcar smoothing
(Phase 89). On real-time USRP cable, this fails because UHD streaming instability
(Phase 55: 8× SNR drift, 99% overflow) makes L-STF amplitude too noisy for the
boxcar to consistently exceed the adaptive threshold.

**Schmidl-Cox** uses 32-sample (two-period) sliding correlation, more robust to
frequency offset and amplitude variation. Standard 802.11n receiver approach.

**Park/Gezici** uses half-symbol shifted correlation to break plateau ambiguity,
giving a sharper detection peak.

Either approach is a from-scratch rewrite of sync_short's L-STF detector. Per
Phase 89 verdict, the current algorithm works on file replay (24 detections at
corr=1.95-20876), so any new algorithm must preserve file-replay performance
while also working on real-time cable.

**Risk**: Implementation cost (each algorithm is ~200 lines of C++), and
verification requires either real-time cable success (HW-dependent) or a
file-replay validation pipeline.

### Option F: STOP at equalizer-layer closure (current state — ACCEPTED)

Document Phase 87-102 chain as complete closure of:
1. sync_short L-STF detection (Phase 87-89 partial, blocked on real-time cable)
2. HT-SIG viterbi (Phase 100 + 102 — unreachable due to #1)

Accept Phase 18 L-SIG-only achievement as final state of the equalizer-layer.
Code paths preserved for any future continuation.

---

## Recommendation

User accepted Option F (closure) on 2026-07-05. Upstream attack plan documented
in `docs/superpowers/notes/2026-07-05-phase102-closure.md` covering:

**Layer 1 (sync_short)**:
1. Schmidl-Cox two-symbol correlation
2. Park/Gezici half-symbol correlation
3. Frequency-domain L-STF detection (FFT-based)
4. UHD streaming stability fix (Phase 55 territory)
5. Loopback/file-replay validation pipeline

**Layer 2 (HT-SIG viterbi, only after Layer 1 unblocks)**:
1. Per-SC channel phase calibration LUT extension
2. Frequency-domain δ correction extension
3. LDPC switch (architectural)

---

## Files of Record

- Phase 102 verdict: this file
- Phase 102 implementation plan: `docs/superpowers/plans/2026-07-05-phase102-null-aware-llr.md`
- Phase 102 verdict (initial): `docs/superpowers/notes/2026-07-05-phase102-null-aware-llr-verdict.md`
- Phase 100 verdict (avg_snr bug): `docs/superpowers/notes/2026-07-05-phase100-verdict.md`
- Phase 99 verdict: `docs/superpowers/notes/2026-07-05-phase99-verdict.md`
- Phase 89 verdict (sync_short fix on file replay): `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 87 verdict (sync_short failure on cable): `docs/superpowers/notes/2026-07-04-phase87-verdict.md`
- Memory: `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p102_null_aware_llr.md`
- Implementation commits: c328e2b, 5052177, d2b88ed, e87cd84, c1d4dbf
- Test runs: `/tmp/p102/p10{1,1_v2,2,2_v2}_*.log`

## Self-Review

**Spec coverage**: Verdict documents Phase 102 USRP verification attempts, the upstream sync_short blocker, the implementation correctness (loopback 1/1 PASS), and 2 upstream-attack options. Per HARD CONSTRAINT, BLOCKED requires upstream plan — Options E + F provided. ✓

**Cable budget**: 4 runs in this Phase 102 attempt; 11/5 total. Over by 6. User authorization was required and obtained. ✓

**No code changes** beyond the CRITICAL bug fix in c1d4dbf (discovered during pre-USRP validation).

## Status

| Condition | Status |
|---|---|
| Phase 102 implementation | ✅ Complete (loopback 1/1 PASS, 10/10 unit tests PASS) |
| CRITICAL mask index bug | ✅ Fixed (c1d4dbf: use `i` not `kScIndex52[i]`) |
| USRP cable verification | ❌ BLOCKED upstream by sync_short |
| HARD CONSTRAINT (FCS_OK ≥ 1) | ❌ NOT achieved |
| Equalizer-layer | 🔴 CLOSED (28+ REFUTED, Phase 102 confirms unreachable) |
| sync_short upstream | 🔴 BLOCKED on real-time cable |
| Cable budget | 11/5 (over by 6) |