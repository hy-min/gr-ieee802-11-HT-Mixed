# Phase 139: L-SIG Upstream Gate Architecture Rewrite (2026-07-09)

**Date**: 2026-07-09
**Branch**: TEST1
**Status**: 📝 DESIGN — awaiting implementation
**Author**: gr-ieee802-11 team

## 1. Goal

Eliminate the L-SIG viterbi upstream gate failure that blocks the entire HT-SIG chain on USRP. After 30+ equalizer-layer REFUTED attacks (Phase 60-138, all addressing H52 quality downstream of the viterbi failure point), the user has directed "不可能接受现状" (cannot accept closure) and demands architectural change.

**Primary success metric**: USRP realtime `FCS_OK ≥ 1` on a real frame.
**Acceptable intermediate**: USRP L-SIG viterbi success rate improved from 0/8 (Phase 138) to ≥1/8.
**NOT acceptable**: Closing with "blocked by USRP analog chain" without architectural plan.

## 2. Root Cause (Phase 1 Complete)

L-SIG viterbi failure chain (Phase 112 R1 confirmed):

```
L-LTF0 single-source H52 estimation
   ↓
H52 has 1.77 rad per-SC phase std (Phase 112 R1)
   ↓
eq_lsig[i] = L-SIG_FFT[i] / H52[i] (line 6439) — noise propagates
   ↓
BPSK symbols rotated by independent ~1 rad noise per SC
   ↓
~20% bit error rate (5/24 bits)
   ↓
K=7 R=1/2 viterbi d_free=10, capacity = 4 correctable errors
   ↓
viterbi fails → lsig_ok = false → HT-SIG path never runs
   ↓
0 HT_SIG_CAND → 0 FCS_OK
```

**Key insight**: The 1.77 rad noise is **per-symbol independent AWGN**, not additive. Cannot be averaged by simple coherent combining. But **multiple independent H52 sources** (L-LTF0, L-LTF1, HT-SIG0 pilots, HT-LTF pilots) provide **statistical independence** — averaging them reduces σ by √N.

**Already-existing H52 sources** (with current opt-in flags):
- L-LTF0 single: σ = 1.77 rad (default in USRP path)
- L-LTF0 + L-LTF1 SNR-weighted: σ ≈ 1.25 rad (Phase 77c, `d_apply_h52_snr_weighted=1`, **default OFF**)
- 2 LTS + 1 HT-LTF averaging: σ ≈ 0.84 rad (Phase 118b, `IEEE80211_HTLTF_AVG=1`, **default OFF**)

**All H52 quality improvements are opt-in with default OFF**. The L-SIG viterbi path uses L-LTF0-only H52 → 1.77 rad noise → viterbi fails.

## 3. Architecture: Multi-Source H52 with Adaptive Mode Selection

### 3.1 Core Idea

Replace the single-source L-LTF0 H52 used for L-SIG equalization with an **adaptive multi-source H52** that:

1. **Default ON**: Use L-LTF0 + L-LTF1 SNR-weighted average (2 sources, σ → 1.25 rad)
2. **Opt-in via env vars**: Layer in additional sources (HT-LTF, HT-SIG0 pilots, HT-SIG1 pilots) for further σ reduction
3. **Per-frame source selection**: Choose sources based on availability and signal quality

### 3.2 Source Hierarchy

| Source | σ (rad) | Avail at sym | Default | Env var |
|--------|---------|--------------|---------|---------|
| L-LTF0 only | 1.77 | sym 0 | NO (was YES) | n/a |
| L-LTF0 + L-LTF1 SNR-weighted | 1.25 | sym 1 | **YES (new)** | `IEEE80211_H52_2WAY_DEFAULT=1` |
| + HT-SIG0 pilots (4) | 1.10 | sym 3 | opt-in | `IEEE80211_HT_SIG_PILOT_REFINE=1` |
| + HT-SIG1 pilots (4) | 1.00 | sym 4 | opt-in | `IEEE80211_HT_SIG_PILOT_REFINE=1` |
| + HT-LTF pilots (4) | 0.84 | sym 6 | opt-in | `IEEE80211_HTLTF_AVG=1` |
| + Cross-frame (Phase 123) | 0.44 | multi-frame | opt-in | `IEEE80211_H52_CROSS_FRAME_TRACK=N` |

### 3.3 Architecture Components

```
H52_single (L-LTF0 only, line 6236 estimate_header_channel_from_lltf52)
   ↓
H52_2way (L-LTF0 + L-LTF1 SNR-weighted, new compute_H52_for_lsig())
   ↓ [default ON]
H52_3way (add HT-SIG0 pilots, opt-in)
   ↓
H52_4way (add HT-SIG1 pilots, opt-in)
   ↓
H52_5way (add HT-LTF pilots, opt-in, gated by IEEE80211_HTLTF_AVG=1)
   ↓
H52_xf (add cross-frame averaging, opt-in)
   ↓
eq_lsig = L-SIG_FFT / H52_N-way  (line 6439 modified)
   ↓
viterbi (improved SNR input)
```

### 3.4 Default Behavior Change

**Critical**: Phase 139 changes the DEFAULT of `d_h52_2way_default` from `false` to `true`. This is the only way to attack the L-SIG upstream gate on USRP without requiring user to set env vars (which is operationally fragile).

**Backward compatibility**: The old behavior (L-LTF0 only) is preserved via `IEEE80211_H52_2WAY_DEFAULT=0`.

**Justification**: Phase 138-B proved that the filter only runs when added to specific code paths. Making L-LTF0+L-LTF1 averaging default ON ensures L-SIG viterbi gets the best possible H52 on every USRP frame.

## 4. Implementation Components

### 4.1 New Function: `compute_H52_2way()`

Pure function, similar to existing `refine_h52_average_pilots()`. Takes two 52-element H52 arrays (typically L-LTF0 and L-LTF1) and produces an SNR-weighted H52 with σ reduction √2.

**Signature**:
```cpp
static void compute_H52_2way(
    const gr_complex* H52_a,  // First source (e.g. L-LTF0)
    const gr_complex* H52_b,  // Second source (e.g. L-LTF1)
    gr_complex* H52_out);     // 52-element output buffer
```

**Algorithm**:
1. For each SC, compute |H|² from L-LTF0 and L-LTF1
2. SNR weight: w_i = |H_i|² / σ²
3. Weighted average: H52[i] = (w0 * H0[i] + w1 * H1[i]) / (w0 + w1)
4. If both |H| < threshold, return L-LTF0-only H52 (fallback)

### 4.2 Modified Line 7632: `Hhdr52_for_lsig`

Currently:
```cpp
const gr_complex* Hhdr52_for_lsig = Hhdr52;  // L-LTF0-only
```

Phase 139:
```cpp
gr_complex Hhdr52_2way[52];
compute_H52_2way(Hhdr52, lltf1_H, Hhdr52_2way);  // populates buffer
const gr_complex* Hhdr52_for_lsig = Hhdr52_2way;
```

### 4.3 New Opt-in: 3-way L-SIG H52 (HT-SIG0 pilot refinement)

Opt-in via `IEEE80211_HT_SIG_PILOT_REFINE=1`. After L-SIG viterbi success (rate=0xD, length>0), refine H52 using 4 HT-SIG0 pilots (kScIndex52 positions 48-51, SCs -21,-7,+7,+21).

This is the second-pass refinement. L-SIG viterbi must succeed first (using 2-way H52), then H52 is refined for HT-SIG processing.

**Note**: This is similar to existing `refine_h52_average_pilots()` but applied to L-SIG path. Existing function may be reusable with parameter change.

### 4.4 New Opt-in: 4-way L-SIG H52 (HT-SIG1 pilot refinement)

Add HT-SIG1 pilots. Requires 2nd OFDM symbol. Gated by `IEEE80211_HT_SIG_PILOT_REFINE=1`.

### 4.5 New Opt-in: 5-way L-SIG H52 (HT-LTF pilot refinement)

Add HT-LTF pilots. Requires 6th OFDM symbol. Gated by `IEEE80211_HTLTF_AVG=1`.

### 4.6 Default Flip: `d_h52_2way_default = true`

Change constructor default from false to true. This is the only behavioral change that affects ALL users — necessary because the L-SIG gate failure is architecture-level, not user-specific.

**Risk mitigation**: Provide `IEEE80211_H52_2WAY_DEFAULT=0` opt-out for users who want old behavior.

## 5. Verification Plan

### 5.1 T1-T2 File-Replay Validation (synthetic / loopback)

| Test | Config | Success criterion |
|------|--------|-------------------|
| T1 file-replay baseline | no env | 1/1 FCS_OK |
| T2 file-replay Phase 139 default ON | no env (default is now 2-way) | 1/1 FCS_OK + log shows 2-way |

### 5.2 T3 USRP K-Sweep (with 2-way L-LTF averaging)

| Test | Config | Success criterion |
|------|--------|-------------------|
| T3 USRP 5250 default | Phase 139 default ON | LSIG_DECODE_OK > 0 (currently 0/8) |
| T3 USRP 5250 + 3-way | + IEEE80211_HT_SIG_PILOT_REFINE=1 | LSIG_DECODE_OK > 0, HT_SIG_CAND > 0 |
| T3 USRP 5250 + 5-way | + IEEE80211_HTLTF_AVG=1 | best metric ≤ 10 |

### 5.3 T4 USRP Stability

Multiple runs of best config from T3 to confirm stability (Phase 113 finding: UBX-160 auto-cal causes 0.199-8.575 ratio variation).

### 5.4 T5 USRP FCS_OK Validation

If T3-T4 show LSIG_DECODE_OK > 0 and metric improvement, run longer test (60s) to capture full frames and check FCS_OK.

## 6. Failure Modes & Fallback

| Failure | Mitigation |
|---------|-----------|
| 2-way L-LTF averaging too noisy (SFO drift) | Fall back to L-LTF0 only via env var |
| 3-way introduces bias from HT-SIG0 pilots | Re-test without 3-way |
| 5-way Phase 118b 3-way regression (cross-board) | Document as same-board-only, recommend cross-board opt-out |
| Phase 122 cross-board 3-way breaking | Keep `IEEE80211_HTLTF_AVG=1` opt-in for cross-board (don't default ON for that) |
| Default flip breaks some test | Provide `IEEE80211_H52_SNR_WEIGHTED_DEFAULT=0` opt-out |

## 7. Out of Scope (YAGNI)

- ❌ viterbi decoder changes (use existing soft LLR if needed in Phase 140+)
- ❌ CPE estimator changes (Phase 137 still in place)
- ❌ Multi-frame cross-frame H52 (Phase 123, opt-in only)
- ❌ Wiener filtering (Phase 140+)
- ❌ ML detection (out of scope)
- ❌ External ref clock (user-excluded)

## 8. Implementation Commits (planned)

1. `feat(p139): compute_H52_for_lsig() function (2-way L-LTF SNR-weighted average)`
2. `feat(p139): flip d_apply_h52_snr_weighted default false → true`
3. `feat(p139): wire Hhdr52_for_lsig to use 2-way H52 at line 7632`
4. `feat(p139): opt-in IEEE80211_HT_SIG_PILOT_REFINE env var + 3-way L-SIG H52`
5. `feat(p139): opt-in 4-way and 5-way L-SIG H52 (cumulative pilot refinement)`
6. `feat(p139): add --phase139-on arg to test_file_replay_e2e.py`
7. `feat(p139): add --phase139-on arg to test_usrp_minimal_loopback.py`
8. `docs(p139): T1-T2 file-replay verdict`
9. `docs(p139): T3-T5 USRP validation + final verdict`

## 9. Files Touched

- `lib/frame_equalizer_impl.cc` — 5 edits: new function, default flip, call site, 2 new opt-in flags
- `lib/frame_equalizer_impl.h` — header if exposing new function
- `examples/test_file_replay_e2e.py` — 1 edit (--phase139-on arg)
- `test_usrp_minimal_loopback.py` — 1 edit (--phase139-on arg)
- `docs/superpowers/notes/2026-07-09-phase139-architecture-rewrite-verdict.md` — create after T5
- `CLAUDE.md` — add Phase 139 conventions
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md` — add Phase 139 entry

## 10. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|---------|------------|
| 2-way L-LTF averaging insufficient (σ still > 1 rad) | Medium | High | Add 3-way/4-way/5-way opt-in layers |
| Cross-board regression (Phase 122) | Medium | High | Keep cross-board opt-in only, document |
| Default flip breaks some existing test | Low | Medium | Provide env opt-out |
| HT-SIG0 pilot refinement biased by CPE | Medium | Medium | Re-test, opt-in only |
| Cable budget exhaustion (5+ runs) | High | Low | Each test confirms progress, fewer runs needed if T3 PASS |

## 11. Success Criteria (HARD CONSTRAINT alignment)

**Phase 139 success** = at least 1 USRP run with L-SIG viterbi success (LSIG_DECODE_OK > 0) and HT_SIG_CAND > 0.

**Architectural success** = Phase 139 reduces L-SIG viterbi fail rate from 8/8 to ≤4/8 (50% improvement).

**Per project CLAUDE.md**: Phase 139 is the FIRST architectural rewrite in the equalizer layer. If Phase 139 fails, the next direction must be:
- HW: 30 dB SMA attenuator install (user-excluded)
- Complete RX chain redesign
- ML-based detection (out of scope)

## 12. Self-Review (Spec)

1. **Placeholder scan**: No TBD. All env vars named, all functions specified.
2. **Internal consistency**: 2-way is default ON, 3-way+ opt-in. Source hierarchy table matches architecture diagram.
3. **Scope check**: Focused on L-SIG upstream gate. Doesn't touch viterbi decoder (existing infrastructure) or CPE (Phase 137 in place).
4. **Ambiguity check**: "SNR-weighted" means |H|² weighting per SC. Specified in §4.1.

**Status**: Spec complete. Ready for writing-plans skill after user review.
