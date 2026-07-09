# Phase 138 Design: H52 Frequency-Domain Low-Pass Filtering

**Date**: 2026-07-09
**Branch**: TEST1
**Status**: 📝 DESIGN — awaiting implementation
**Author**: gr-ieee802-11 team

## 1. Goal

Reduce per-SC H52 estimation noise from 1.25 rad (current Phase 118b baseline) to
~0.55-0.78 rad (target), thereby enabling HT-SIG viterbi metric to drop from
12-13 toward ≤10 (viterbi free-distance ceiling). Attack the per-SC AWGN from
Phase 112 R1 USRP analog chain by exploiting OFDM channel sparsity in the
frequency domain.

## 2. Background

### Phase 112 R1 Root Cause

USRP analog chain (oscillator + RF frontend + ADC) introduces per-SC phase noise
with std ≈ 1.77 rad. This is a physical noise floor independent of software
processing.

### Current H52 Estimation Flow

```
H_LTS0[52]    ← 4 pilots only (bins 48-51) from L-LTF0
H_LTS1[52]    ← 4 pilots + 48 data SCs (from L-LTF1 training)
   ↓
Hhdr52[52]    ← SNR-weighted average (每 SC 噪声 ~1.25 rad = 1.77/√2)
   ↓
H_htsig0/1[52] ← estimate_H_from_htsig_pilots: 4 pilots → piecewise linear
                  interpolation (噪声放大 at edge SCs)
   ↓
H52[52] = (2*Hhdr52 + H_htsig0 + H_htsig1) / 4   ← Phase 118b averaging
```

After Phase 118b averaging, per-SC H52 std is ~0.84 rad per the in-code comment
at line 532. This drives viterbi metric to 12-13 (just above ≤10 threshold).

### OFDM Channel Sparsity

For a typical indoor multipath channel with delay spread < 50 ns:
- Channel impulse response `h(τ)` has ~5-10 effective paths
- Frequency response `H[k] = DFT{h(τ)}` is a sum of 5-10 complex exponentials
- In the DFT domain, energy concentrates in the first ~5-10 bins

For our USRP cable scenario (almost LOS), delay spread is even smaller:
- Likely 1-3 effective paths
- Frequency-domain energy in first ~3-5 bins

## 3. Architecture

Single new opt-in filter applied to H52 AFTER all existing H52 estimation paths
complete, BEFORE H52 is passed to the equalizer.

### 3.1 Algorithm: DFT-domain low-pass

```cpp
// Input: H52[52] (kScIndex52 layout: bins 0..51)
// Output: H52_filtered[52] (same layout)

1. Rearrange kScIndex52 bins into SC-ordered sequence (skip DC, 0-indexed)
   H_seq[t] for t ∈ [0, 51]
   
2. DFT(52) → H_freq[f] for f ∈ [0, 51]
   Direct DFT implementation (O(N²) = 2704 complex multiplies, fine)
   
3. Low-pass filter:
   H_freq[f] = 0 for f ≥ K
   H_freq[0] preserved (DC component)
   
4. IDFT(52) → H_seq_filtered[t]
   
5. Rearrange back to kScIndex52 layout
```

### 3.2 K Selection

| K | σ_filtered (rad) | Channel distortion risk | Use case |
|---|------------------|------------------------|----------|
| 5  | 0.55 | High (LOS only) | USRP cable |
| 10 | 0.78 | Medium | General indoor |
| 15 | 0.94 | Low | Dense multipath |
| 20 | 1.12 | Very low | Conservative |

**Default K=10** (balances noise reduction vs channel preservation).

**Auto-K mode**: Read `avg_snr_lsig` from frame:
- avg_snr_lsig > 8 dB → K=20 (channel is clean)
- 4 ≤ avg_snr_lsig ≤ 8 → K=10 (default)
- avg_snr_lsig < 4 dB → K=5 (strong noise)

### 3.3 Env Var Interface

```bash
# Enable frequency-domain low-pass (default OFF to preserve baseline)
IEEE80211_H52_FREQ_LOWPASS=1

# Explicit K override (overrides auto-K mode if both set)
IEEE80211_H52_FREQ_LOWPASS_K=10

# Combined with Phase 137: pilot mask + freq lowpass
IEEE80211_HTSIG_NULL_PILOT_MASK=1 \
IEEE80211_H52_FREQ_LOWPASS=1 \
IEEE80211_H52_FREQ_LOWPASS_K=10
```

## 4. Implementation Locations

### 4.1 New static function

Insert `apply_freq_lowpass_h52()` in `lib/frame_equalizer_impl.cc` near
`refine_h52_average_pilots()` (line 538 area). Pure function, ~50 lines.

### 4.2 Call site

Insert at the convergence point where H52 is finalized for the equalizer. The
most natural location is in `general_work()` after all H52 averaging paths
(Phase 118b / 119 / 137) but before `d_equalizer->set_H()`.

### 4.3 Global flags (file-static)

```cpp
// In file scope, near other g_* flags
static bool g_apply_freq_lowpass_h52 = false;
static int g_h52_freq_lowpass_k = 10;  // default K when env var set without K
```

Read from env at constructor init (line ~4888 area, near Phase 137 wire-up).

## 5. Verification Plan

| Test | Config | Success criterion |
|------|--------|-------------------|
| T1 file-replay baseline | no env | 1/1 FCS_OK (no regression) |
| T2 file-replay K=10 | `IEEE80211_H52_FREQ_LOWPASS=1 IEEE80211_H52_FREQ_LOWPASS_K=10` | 1/1 PASS + filter log shows K=10 |
| T3 file-replay K=5 | K=5 | 1/1 PASS + filter log shows K=5 |
| T4 USRP K=10 single-run | 5250 MHz cable, full Phase 138 env | HT_SIG_CAND events appear + metric shift |
| T5 USRP multi-K sweep | K ∈ {5, 10, 15, 20} × 2 runs each = 8 runs | Find best K for USRP channel |
| T6 USRP auto-K mode | auto | K auto-selected based on avg_snr_lsig |

## 6. Failure Modes & Fallback

| Failure | Mitigation |
|---------|-----------|
| K=10 metric still 11+ | Try K=5 |
| K=5 channel distortion (viterbi fails entirely) | Use K=15 or K=20 |
| DFT computation too slow (rare for N=52) | Precompute twiddle factors |
| Conflict with Phase 137 pilot mask | Test in series (Phase 137 first, then Phase 138) |
| Phase 138 alone doesn't break ceiling | Combine with Phase 137 (pilot mask + freq lowpass) |

## 7. Out of Scope (YAGNI)

- ❌ No Wiener filtering (Phase 139 fallback)
- ❌ No 2D time-frequency filtering (only frequency domain)
- ❌ No viterbi decoder changes
- ❌ No soft LLR formula changes
- ❌ No CPE estimator changes (Phase 137 still in place)

## 8. Implementation Commits (planned)

1. `feat(p138): apply_freq_lowpass_h52() function + K-selection logic`
2. `feat(p138): IEEE80211_H52_FREQ_LOWPASS env parser + K default = 10`
3. `feat(p138): call site: insert filter before d_equalizer->set_H()`
4. `feat(p138): add --phase138-on arg to test_file_replay_e2e.py`
5. `feat(p138): add --phase138-on arg to test_usrp_minimal_loopback.py`
6. `docs(p138): T1-T3 file-replay verification verdict`
7. `docs(p138): T4-T6 USRP 5250 validation + final verdict`

## 9. Files Touched

- `lib/frame_equalizer_impl.cc` — 3 edits (new function, env parser, call site)
- `examples/test_file_replay_e2e.py` — 1 edit (--phase138-on arg)
- `test_usrp_minimal_loopback.py` — 1 edit (--phase138-on arg)
- `docs/superpowers/notes/2026-07-09-phase138-freq-lowpass-verdict.md` — create after T5
- `CLAUDE.md` — add Phase 138 env vars
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md` — add Phase 138 entry

## 10. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| K=10 too aggressive → metric worse | Medium | High | Test K=10, 15, 20 first |
| Real USRP channel non-sparse (10+ paths) | Low | Medium | K=20 conservative default |
| DFT numerical precision insufficient | Low | Low | Direct DFT at N=52 is well-conditioned |
| Phase 137 + 138 conflict | Low | Low | Series test plan in T1-T3 |
| Cable run budget exhaustion (≤5) | Medium | Medium | T5 multi-K sweep uses 4 runs + T4 = 5 runs |

## 11. Success Criteria (HARD CONSTRAINT alignment)

**Phase 138 success** = at least 1 USRP run with HT-SIG viterbi metric ≤ 10.

**Architectural success** = Phase 138 reduces metric vs Phase 137 baseline (even
if still > 10) → provides new insight into noise structure for Phase 139+.

**Per project CLAUDE.md "USRP realtime FCS_OK is the absolute goal"** — Phase 138
is one more attack in the multi-phase sequence. Closure is not acceptable if
this attack is REFUTED. Phase 139+ continues with new architectures (Wiener
filtering, ML detection, etc.) per the "Equalizer layer is NOT closed"
directive.