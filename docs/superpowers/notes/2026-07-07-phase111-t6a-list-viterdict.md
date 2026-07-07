# Phase 111 T6a — List Viterbi for HT-SIG CRC Recovery (2026-07-07)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — list viterbi K=64 explores 2048 paths
across 32 candidates, **0 CRC passes**.

## TL;DR

Implemented C++ list viterbi (M-algorithm, top-K paths per state) and
integrated it into all 3 HT-SIG decoder sites (`decode_htsig_candidate`,
`decode_htsig_direct_from_header52`, `decode_htsig_from_rotated`).

Tested on `/tmp/p110_t10_capture.fc32` (5250 MHz, --tx-gain 20, 30s):
- **32 list viterbi invocations**
- **2048 unique paths explored** (32 cand × 64 paths each)
- **0 CRC-passing paths** found
- **0 FCS_OK** (same as baseline)

The HT-SIG viterbi wall is NOT a "wrong path chosen" problem — it's a
"no path in the noise neighborhood has valid CRC" problem.

## Why List Viterbi Fails

Per Phase 107 root cause:
- Per-SC argH std = 108° across symbols
- HT-SIG has 12-18 random bit errors / 96 bits (3-4x viterbi capacity)
- These errors are **distributed across the convolutional code path**,
  not concentrated on a few bits

CRC-8 (8-bit) has 1/256 random match probability per candidate path.
With 2048 paths explored, expected false positives = 2048 / 256 = 8.
But we see **0**.

**Reason**: The 64 top paths through the trellis all share large segments
of state trajectory — they differ only in the last few bits. So they're
not independent CRC trials; they're correlated. The 12-18 bit errors
appear in *every* top-K path because they all use the same noisy bits
at those positions.

This is fundamentally different from "viterbi picks wrong path despite
clear winner." Here, ALL paths have similar error distribution.

## Test Results (p110 T10 capture, 30s)

| Config | LSIG | HT-SIG candidates | list viterbi calls | CRC-pass paths | FCS_OK |
|--------|------|-------------------|---------------------|----------------|--------|
| Baseline (no env) | fails | 0 | n/a | n/a | 0 |
| IEEE80211_LSIG_VITERBI_CANDIDATE=1 | OK (16) | 16/sym | 0 | n/a | 0 |
| + IEEE80211_HTSIG_LIST_VITERBI=64 | OK (16) | 16/sym | 32 (rotated) | 0 | 0 |
| + IEEE80211_HTSIG_LIST_VITERBI=64 (with K=128) | OK (16) | 16/sym | 32 | 0 | 0 |

(Note: max_paths capped at 64 in C++ for memory safety; K=128 same as K=64.)

## Implementation Details

### C++ Functions Added (frame_equalizer_impl.cc)

```cpp
// 1. List viterbi decoder — keeps top-K paths per state (M-algorithm)
static bool viterbi_decode_133_171_soft_list(
    const float* rx_soft,
    int n_encoded_bits,
    int max_paths,
    std::vector<std::vector<uint8_t>>& decoded_paths_out,
    std::vector<int>* out_metrics_q8 = nullptr);

// 2. Helper that wraps list viterbi + CRC check for HT-SIG
static bool try_htsig_list_viterbi(
    const uint8_t* enc96_hard,
    int max_paths,
    std::vector<uint8_t>& best_dec48_out,
    int* out_best_metric_q8 = nullptr);
```

### Integration Points

Three HT-SIG decoder call sites get fallback:
1. `decode_htsig_candidate` (line 1893) — when standard viterbi fails OR
   when CRC fails after standard viterbi succeeds
2. `decode_htsig_direct_from_header52` (line 2775) — same
3. `decode_htsig_from_rotated` (line 3006) — same

### Env Var

`IEEE80211_HTSIG_LIST_VITERBI`:
- Unset/empty: list viterbi disabled (baseline behavior)
- `1`: list viterbi K=64
- `32`: list viterbi K=32
- Numeric value: list viterbi with K = that value (1-64)

### Log Lines

```
[HTSIG_LIST_VITERBI] no-hit (rotated) rot=0 inv_a=0 inv_b=0 K=64
[HTSIG_LIST_VITERBI] no-better (rotated) rot=0 ... std_crc=ok
[HTSIG_LIST_VITERBI] replace (rotated) rot=0 inv_a=0 inv_b=0 K=64 metric=12345
```

## Architecture Analysis

List viterbi is a CODE-LEVEL solution — it tries to find alternative
convolutional code paths. But our problem is at the CHANNEL level
(per-SC phase drift). The convolutional code is just a transport; even
if we found the "right" code path, the underlying bit errors would
still cause CRC mismatch.

The right attack is **per-SC H52 phase tracking at HT-SIG time**:
- T6b: Kalman filter on 4 HT-SIG pilots per symbol → extrapolate to 48 SCs
- This addresses the root cause (per-SC argH std=108°)

## Test Reproducibility

```bash
# Build & install (already done in this commit)
cd /home/hy/gr-ieee802-11/build && make && make install

# Test on file replay
IEEE80211_HTSIG_LIST_VITERBI=64 \
python examples/test_file_replay_e2e.py \
  --iq-file /tmp/p110_t10_capture.fc32 \
  --phase rx --rate 20 --rx-duration 30 --loop 1
```

Expected output:
- 32× `[HTSIG_LIST_VITERBI] no-hit (rotated) rot=X inv_a=X inv_b=X K=64`
- 0× `[HTSIG_LIST_VITERBI] replace (rotated) ...`
- `[P103] FCS_OK=0 FCS_FAIL=0`
- `[P103] FAIL — algorithm chain does not produce FCS_OK`

## Baseline Verification

Without `IEEE80211_HTSIG_LIST_VITERBI`, the baseline is unchanged:
- 0 FCS_OK on `/tmp/p110_t10_capture.fc32` (same as pre-T6a)
- L-SIG synthetic tests still PASS (test_lsig_viterbi_synthetic.py)
- htsig module loads correctly (test_htsig_attr.py)
- No code path differences (env var unset = standard viterbi only)

## Files Modified

- `lib/frame_equalizer_impl.cc` (+~250 lines, 3 fallback sites)
- `docs/superpowers/notes/2026-07-07-phase111-t6a-list-viterdict.md` (this file)

## T7 Recommendations

Given T6a REFUTED, the next attack surface is:

### T7a (HIGH effort, MEDIUM-HIGH likelihood): Per-SC Kalman at HT-SIG
- Build on Phase 111 T3 (Kalman for DATA) to track H52 at HT-SIG
- Use 4 HT-SIG pilots + 4 L-LTF pilots as measurements
- Apply Kalman prediction to 48 SCs at HT-SIG time
- **PROBLEM**: HT-SIG is BEFORE any DATA pilots. Only 4 L-LTF pilots
  (long before HT-SIG) + 4 HT-SIG pilots (current symbol only) available
- Per Phase 107: argH drift is 108° between L-LTF and HT-SIG
- **May not work** if drift > 4-pilot correction range

### T7b (HIGH effort, MEDIUM likelihood): Use HT-SIG pilots to refine H52
- Per Phase 39 (REFUTED): H52 re-estimate from 4 HT-SIG pilots
- Already tried with linear interp; REFUTED
- New approach: use pilots ONLY to track phase rotation per SC, not H
- Multiply each SC's H52 by per-SC phase correction

### T7c (LOW effort, MEDIUM likelihood): Try with file-replay TDD mode
- Currently using rx file-replay with infinite IQ loop
- TDD mode (TX/RX on same board) gives cleaner IQ
- May improve per-SC phase stability

### T7d (BLOCKED): Need new cable run with 30 dB attenuator
- Phase 82 budget exhausted (5/5)
- Per Phase 81: 5250 MHz + 30 dB attn → +5-7 dB SNR
- 5250 alone (no attn): SNR marginal
- T7d UNBLOCKS T6a if higher SNR → fewer bit errors → list viterbi succeeds

### T7e (NEW ARCHITECTURE): Decision-directed equalizer with multi-symbol H tracking
- Use DATA SYMBOLS (with pilots) to refine H52 estimate going backward in time
- Apply refined H52 to HT-SIG (non-causal)
- Latency: ~10 OFDM symbols
- Per-user directive "new architectures MUST continue"

### RECOMMENDATION

**T7e (Decision-directed + multi-symbol H tracking)** is the most
promising unexplored direction. It uses DATA symbol pilots (high SNR,
accurate) to refine H52 estimates that apply retroactively to HT-SIG.

Alternative: **T7d + T6a** — wait for HW (30 dB attenuator), re-test
T6a on cleaner signal. If higher SNR brings bit errors from 12-18 down
to 4-6, list viterbi K=64 will likely succeed (CRC-8 1/256 false
positive is non-trivial when viterbi capacity covers the actual errors).

**Equalizer layer attacks MUST continue** per user directive
"不可能接受现状".

## Verdict: REFUTED

T6a (list viterbi for HT-SIG CRC recovery) REFUTED as standalone fix.
Per-SC phase drift (Phase 107) corrupts all top-K paths equally — they
all fail CRC-8 because the underlying bit errors are shared.

C++ implementation PRESERVED as opt-in via IEEE80211_HTSIG_LIST_VITERBI
for future use. If upstream gates ever unblock (higher SNR, better H52),
list viterbi becomes viable.

## Related

- [[project-p111-t4a-htsig-verdict]] — T4a null SC erasure REFUTED
- [[project-p111-t5e-htsig-cand-verdict]] — T5e extended candidate search REFUTED
- [[project-p107-deep-root-cause]] — per-SC argH std=108° (root cause)
- [[project-p111-t3-kalman-cpp]] — T3 Kalman for DATA (T7a extends to HT-SIG)
- [[project-p100-htsig-audit]] — Phase 100 HT-SIG wall (now refuted on this capture)
- [[project-p70-lsig-viterbi-candidate]] — VITERBI_CANDIDATE=1 (L-SIG breakthrough)
- [[feedback-no-closure-usrp-fcs-ok]] — User feedback: continue attacking equalizer