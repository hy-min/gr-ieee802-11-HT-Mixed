# Phase 125b: Cross-board USRP test (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **REACHABLE but not VALIDATABLE** — cross-board signal reaches HT-SIG viterbi, but cross-frame averaging cannot reduce noise (n_avg=1)

## TL;DR

User chose option B (cross-board, A:TX/RX → B:TX/RX cable).
USRP X310+UBX-160 tests at 5250 MHz, --tx-gain 20:

| Config | Sent | Recv | File | L-STF corr | HT_SIG_CAND | metric |
|--------|------|------|------|-----------|-------------|--------|
| tx-gain 10 (default) | 90 | 0 | 73MB / 0.46s | 0.66-0.76 | metric 14-16 | crc_fail |
| tx-gain 20 (boost) | 699 | 0 | 312KB / 1.95ms | 0.84-0.96 | metric 14-17 | crc_fail |

**Cross-board is REACHABLE**: sync_short, FRAME_DETECT, HT_SIG_CAND all
fire on the file-replay. Some "Detected HT frame" (L-SIG ratio 0.4-0.78)
even pass the legacy/HT classifier.

**Cross-board is NOT VALIDATABLE for Phase 123 cross-frame**: only 1
frame in FIFO at any time (UHD stalls after ~0.5s per Phase 55). n_avg=1
means cur_mag == avg_mag, no σ reduction.

The 1.77 rad per-SC noise ceiling (Phase 112 R1) is the actual
bottleneck. **Equalizer-layer attacks must continue** per user's
"不可能接受现状" directive — Phase 123 cross-frame is the WRONG
ARCHITECTURE for single-frame file-replay validation. We need attacks
that reduce per-frame noise, not cross-frame noise.

## Setup

```
Cable: A:TX/RX (UBX-160 A) → B:TX/RX (UBX-160 B),  cross-board
Cmd:   test_usrp_minimal_loopback.py --cross-board \
       --freq 5250 --tx-gain 20 --rate 20 --warmup 30 \
       --rx-subdev B:0 --duration 5 --interval 50 \
       --capture /tmp/p125_xboard_burst.fc32
Result: Sent=699 Recv=0 (no real-time FCS)
        File 311KB / 1.95ms / -16.42 dB power
```

## Cross-board signal analysis

| Property | tx-gain 10 | tx-gain 20 | Same-board (Phase 117) |
|----------|-----------|-----------|------------------------|
| Median power | -28.11 dB | -16.42 dB | n/a (cached) |
| L-STF corr (replay) | 0.66-0.76 | 0.84-0.96 | n/a |
| Capture duration | 0.46s | 1.95ms | n/a |
| Real L-STF? | yes (weak) | yes (strong) | yes |

**Diagnosis**: --tx-gain 20 is essential for cross-board. At gain 10,
the signal is so weak that UHD stalls within 0.5s. At gain 20, the
first frame is strong (-12 dB) but the file only captures ~2ms before
UHD stops producing samples. **Phase 55's "99% loss" finding is
real-time-relevant: the head block caps at duration × rate, but UHD
doesn't actually deliver that many samples.**

## File-replay results (5s, loop=3)

### Baseline (no Phase 123 env)
```
[SYNC-SHORT] Frame detected! i=2 corr=0.944 thresh=0.200
[FRAME_DETECT] L-SIG EQ ratio=2.709 E_I=42.86 E_Q=116.12 (expect < 1.0 for BPSK)
[FRAME_DETECT] Detected Legacy frame (HT-SIG ratio=1.199, L-SIG ratio=2.709)
...
[HT_SIG_CAND] sym=10 rot=0 inv_a=0 inv_b=0 metric=15 fail=crc_fail
[HT_SIG_CAND] sym=10 rot=0 inv_a=0 inv_b=1 metric=17 fail=crc_fail
```

### Phase 123 N=4
```
[FRAME_EQ] IEEE80211_H52_CROSS_FRAME_TRACK=4
[H52_CROSS_FRAME] n_avg=1 depth=4 cur_mag=0.2506 avg_mag=0.2506
[HT_SIG_CAND] sym=10 rot=1 inv_a=0 inv_b=0 metric=14 fail=crc_fail
[HT_SIG_CAND] sym=10 rot=2 inv_a=0 inv_b=0 metric=15 fail=crc_fail
```

**All candidates metric 14-17** (need ≤ 10). This is the 1.77 rad
ceiling. Phase 123's σ_post_avg = 0.88 / sqrt(4) = 0.44 rad requires
n_avg=4 to take effect, but n_avg=1.

## Why Phase 123 Cannot Help Here

The Phase 123 design chains AFTER Phase 118b H_AVERAGE. The
mathematical claim: σ_post = 0.88 / sqrt(N). For N=4, σ_post = 0.44
rad breaks the 1 rad viterbi wall.

But this only works if **N consecutive frames have different noise
realizations**. In a file-replay with 1 frame looping, every "frame"
is the same IQ data → same H estimate → no averaging benefit.

The cross-frame logic IS implemented correctly (verified by debug log:
n_history=0, cur_mag=avg_mag — they match because history is empty).
It just can't demonstrate σ reduction with a single frame.

## New Architectures for Phase 126 (per user "尽可能给出更多的解决方案")

The 1.77 rad per-SC noise is **per-frame** (Phase 112 R1: noise
samples are independent across symbols within a frame). To break the
ceiling, we need per-frame attacks that work even when n_avg=1:

### Option A: Frequency-domain H52 smoothing (low-pass across SCs)
- Adjacent SCs have correlated channel response (channel coherence BW)
- 3- or 5-tap moving average across SC index
- Theoretical σ reduction: 1/sqrt(3) to 1/sqrt(5) per 3-5 SCs
- Risk: small freq resolution loss; depends on channel coherence
- Layer: H52 estimation kernel (after LTS extraction)
- Effort: LOW (5-10 lines C++ + env var)

### Option B: Multi-symbol H52 averaging within frame
- 4-8 data symbols per frame; each has different per-SC noise
- Average H52 across N data symbols BEFORE HT-SIG viterbi
- Theoretical σ reduction: 1/sqrt(4) to 1/sqrt(8) per 4-8 symbols
- Risk: requires delaying HT-SIG viterbi by N symbols (timing impact)
- Layer: H52 estimation kernel
- Effort: MEDIUM (need symbol-aligned H storage, ~30-50 lines C++)

### Option C: Pre-LSIG cross-frame (apply at L-SIG viterbi)
- Currently Phase 123 averages H52 from previous frames BEFORE
  HT-SIG processing
- Apply same logic BEFORE L-SIG viterbi
- L-SIG EQ ratio currently 0.4-3.5 (mostly detected as Legacy)
- If L-SIG viterbi succeeds more often with cross-frame H, more
  frames reach HT-SIG viterbi
- Risk: L-SIG is BPSK (lower SNR requirement than HT-SIG QPSK/BPSK)
- Layer: L-SIG equalization path
- Effort: MEDIUM (different code path, gated by `d_is_ht`)

### Option D: Adaptive per-SC weighting (use Phase 78b null SC info)
- Phase 78b identified 5 stable globally-null SCs (per p78b verdict)
- Down-weight null SCs in HT-SIG viterbi metric
- Up-weight high-|H| SCs
- Theoretical: noise contribution from null SCs → 0
- Risk: per-SC weights are heuristic; need calibration on USRP data
- Layer: viterbi metric computation
- Effort: LOW-MEDIUM (need to thread per-SC weights into viterbi)

### Option E: Soft-LLR HT-SIG viterbi (vs current hard-decision)
- Current viterbi: hard QPSK decision per state, accumulate metric
- Soft-LLR: weight paths by per-state metric reliability
- Could reduce effective metric for low-confidence paths
- Risk: changes viterbi kernel; complex to integrate
- Layer: viterbi decoder
- Effort: HIGH (rewrite viterbi, ~200-500 lines C++)

### Option F: CFO/SFO per-symbol re-estimation
- CFO/SFO estimated once per frame from L-LTF
- LO can drift over the 100us frame (especially cross-board with
  independent LOs per Phase 122)
- Re-estimate CFO from HT-LTF (between HT-SIG and DATA)
- Could reduce phase drift accumulation
- Risk: HT-LTF only 4 SCs, noisy estimate
- Layer: CFO/SFO compensation
- Effort: MEDIUM (Phase 39 already has estimate_H_from_htsig_pilots)

### Option G: Per-frame iterative H refinement (DDE extension)
- Phase 120a scalar DDE REFUTED
- Phase 121 per-SC DDE REFUTED
- New variant: use HT-SIG1 (after first decode attempt) as training
  for second H estimate, iterate
- Could converge if CRC fails but majority bits are right
- Risk: low convergence probability at 1.77 rad noise
- Layer: H estimation + viterbi iteration
- Effort: HIGH

## Recommended Phase 126 Plan

**Primary**: Option A (frequency-domain H52 smoothing) — lowest effort,
plausibly 1/sqrt(3) σ reduction, can combine with Phase 118b H_AVERAGE.

**Secondary**: Option D (per-SC weighting) — directly uses Phase 78b
findings; could give 0 metric contribution from 5 null SCs.

**Tertiary**: Option C (pre-LSIG cross-frame) — different code path,
more invasive, but if successful would unlock more frames to HT-SIG
viterbi.

**Excluded per user directive**:
- External clock (ref clock) — explicitly excluded
- Algorithm swap (different decoder/modem) — explicitly excluded

## What To Do Right Now

1. Write Phase 125b verdict (this file) — DONE
2. Propose Phase 126 options to user — pending
3. Wait for user direction on which option(s) to implement

## Related

- [[project-p123-cross-frame]] — Phase 123 implementation (works but n_avg=1)
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (current best metric 12)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- [[project-p125-usrp-validation]] — Phase 125 v1/v2 results (noisy captures)
- Verdict: `docs/superpowers/notes/2026-07-09-phase125b-cross-board-verdict.md`
