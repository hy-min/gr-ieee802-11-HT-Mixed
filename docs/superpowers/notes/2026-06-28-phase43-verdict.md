# Phase 43 Verdict — Per-SC HT-SIG Null Gating REFUTED on USRP

**Date**: 2026-06-28
**Branch**: TEST1
**Status**: ❌ REFUTED on USRP
**Commits**: 29b5b2b (implementation), 1fef243 (revert)

## Background

Phase 42 (median-based H52 null detection) REFUTED on USRP. Layer 2 was therefore
re-tested independently with a more robust detection statistic (90th percentile
of |H| instead of median). It was also redesigned to gate at the **hard-bit level**
(since the existing decoder uses hard-decision viterbi, not soft LLRs).

## Revised Layer 2 Design

- **Detection**: 90th percentile of |H[0..48)| is the reference. SCs with
  |H[i]| < 0.3 × ref are flagged null.
- **Action**: At the bit-extraction point inside `decode_htsig_from_rotated`,
  for null SCs the bit is forced to 0 (the "no-information" choice, lower-variance
  for viterbi than random noise).
- **Rationale**: Phase 42's median-based detection had a high false-positive
  rate at low SNR. The 90th percentile is **upper-tail-biased** — noise cannot
  pull it up, only further up. This makes it more robust under low SNR.

## Test Configuration

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=./build/python/bindings:./python:./examples \
  IEEE80211_LSIG_RATE_FORCE=0xD \
  IEEE80211_LLTF_OFFSET_CORRECT=14 \
  IEEE80211_TIMING_OFFSET_APPLY=1 \
  IEEE80211_HTSIG_LLR_WEIGHT=1 \
  /home/hy/conda/envs/gnuradio/bin/python \
  test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 30
```

## Results

| Metric | Phase 41 baseline (no fix) | Phase 43 Layer 2 ON | Delta |
|---|---:|---:|---:|
| Sent | 31 | 31 | 0 |
| Recv | 0 | 0 | 0 |
| FCS_OK | 0 | 0 | 0 |
| HT_SIG_PARSE_FAIL | 8 | **14** | **+75% (worse)** |
| LSIG_DECODE OK | 104 | 110 | +6 (no L-SIG regression) |
| LSIG_PARSE_FAIL | — | 176 | (regression in L-SIG) |
| HTSIG_LLR_GATE fired | n/a | 226 frames | (detection works) |
| ref (90th percentile \|H\|) | n/a | 0.1291 | (Phase 38 null range) |
| n_null_a per frame | n/a | 6/48 = 12.5% | (matches Phase 38 5-10) |

## Root Cause: Bit=0 Systematic Bias + 6 Forced SCs Per Frame

Layer 2 was correctly **detecting** the null SCs — `n_null_a=6` per frame matches
the Phase 38 observation of 5-10 null SCs per frame. The gating fired on every
frame (226 times).

The problem is that **forcing bit=0 on 6/48 = 12.5% of subcarriers introduces a
deterministic bias** into the viterbi input. With 6 SCs always returning bit=0
regardless of the transmitted bit, the effective HT-SIG frame is corrupted at
the input level. Viterbi then either:

1. **Converges to a different valid codeword** (frame looks OK at the viterbi
   metric level, but the decoded bits are wrong — `crc_fail`).
2. **Fails to converge** if the bias pushes too far from any valid codeword.

Either way, `HT_SIG_PARSE_FAIL` rises (8 → 14, +75%). The 75% regression is
small in absolute terms but the direction is unambiguously wrong: **any increase
in HT_SIG_PARSE_FAIL means the fix is making things worse, not better**.

## Why Python Synthetic Test Passed But USRP Failed

`examples/test_htsig_null_injection.py` (Phase 43 version, 7 tests) verified
that the gating correctly identifies null SCs and sets them to bit=0 in
isolation. It did **not** test the end-to-end viterbi decoder behavior on a frame
with 12.5% of SCs forced to bit=0. This is exactly the gap that USRP exposed.

The Python test pattern that would have caught this:
1. Encode a valid HT-SIG frame (24 bits + tail).
2. Force 6 random SCs to bit=0 (the gating pattern).
3. Pass to viterbi_decode_133_171.
4. Verify CRC passes.
5. Repeat for K=100 random patterns.

This test would likely have shown CRC failures.

## Verdict

❌ **Phase 43 Layer 2 REFUTED on USRP**.

Forcing bit=0 on null SCs introduces a deterministic bias that **degrades
viterbi convergence**. The 6/48 SCs flagged as null per frame is too many to
gating without compromising frame integrity.

## Action Taken

1. **Reverted** commit `29b5b2b` (Layer 2 C++ implementation) via `git revert`
   → `1fef243`.
2. **Rebuilt + installed** to restore default behavior.
3. **Layer 2 env var (`IEEE80211_HTSIG_LLR_WEIGHT`) defaults to OFF** — code
   in git history, no behavior change for existing runs.
4. **Loopback regression test PASS** after revert: `Final: OK=1 FAIL=0`.

## Counter-Increment

14 REFUTED hypotheses on USRP HT-SIG. Phases 25, 26, 27, 29.2, 30, 35, 36, 37,
38, 39, 40, 41, 42, **43**.

## Architectural Lesson

The architecture has **two architectural constraints that together make this
class of fix infeasible**:

1. **Hard-decision viterbi input** — cannot inject "low confidence" without
   picking a specific bit, which is a bias.
2. **No soft-decision LLR support** — adding it would require viterbi algorithm
   changes, which Phase 37 verified is correct as-is.

Any future attempt must either:
- Modify the viterbi algorithm to accept soft LLR inputs (Phase 37 verified
  current hard-bit decoder is correct — risk of regression).
- Find a way to **improve the equalized eq values directly** so that the hard
  decision is more reliable at null SCs (e.g., better H estimation, frequency-
  domain interpolation that does NOT drag the median down).

Given 14 REFUTED hypotheses and the channel-physics nature of the bottleneck
(Phase 38 evidence), the **recommended path forward remains accepting the
limitation and using software loopback as the decoder validation path**.

## References

- `docs/superpowers/notes/2026-06-28-phase42-verdict.md` — Layer 1 REFUTED (median fragile)
- `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md` — Phase 41 closure
- `lib/frame_equalizer_impl.cc:2139-2153` — HT-SIG0 bit extraction (where gating was applied)
- `lib/frame_equalizer_impl.cc:2241-2252` — HT-SIG1 bit extraction (where gating was applied)
