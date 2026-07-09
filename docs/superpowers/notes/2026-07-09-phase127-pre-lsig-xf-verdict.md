# Phase 127: Pre-LSIG cross-frame H tracking (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on USRP** — pre-LSIG cross-frame slightly WORSENS metric (12 → 12, no metric=10 frames)

## TL;DR

Implemented `IEEE80211_LSIG_H52_CROSS_FRAME_TRACK=N` (N ∈ 1..8) in
`frame_equalizer_impl.cc`. Applies Phase 123 cross-frame logic to
Hhdr52 BEFORE L-SIG viterbi (different code path from Phase 123's
HT-SIG chain apply). FIFO accumulates history per frame.

**Result on USRP cross-board capture**:
- FIFO fills correctly: n_avg=1→8 observed in 5s replay
- L-SIG "Detected HT frame" count: baseline 58, N=4 → 69 (+19%),
  N=8 → 48 (-17%)
- HT_SIG metric distribution: baseline 2 metric=10 + 30 metric=12;
  N=4: 0 metric=10 + 12 metric=11; N=8: 0 metric=10 + 2 metric=11
- **NO FCS_OK in any combination**

**Conclusion**: Pre-LSIG cross-frame adds 11-19% more HT detections
but the metric doesn't drop. The new HT frames are still at metric
12-13, well above the viterbi threshold 10. The 1.77 rad per-SC
ceiling (Phase 112 R1) is the structural limit — neither L-SIG nor
HT-SIG viterbi can decode bits with that noise floor.

## Implementation

**File**: `lib/frame_equalizer_impl.cc`, `lib/frame_equalizer_impl.h`

New env var (in constructor):
```cpp
const char* env_lsig_cft = std::getenv("IEEE80211_LSIG_H52_CROSS_FRAME_TRACK");
if (env_lsig_cft && env_lsig_cft[0] != '\0') {
    int n = atoi(env_lsig_cft);
    if (n >= 1 && n <= kMaxH52History) {
        d_apply_lsig_h_cross_frame = true;
        d_lsig_h52_history_depth = n;
    }
}
```

New member function `ref_lsig_h52_cross_frame_average()` (~line 4120),
identical algorithm to Phase 123's `ref_h52_cross_frame_average()` but
operates on `d_lsig_h52_history[]` FIFO instead of `d_h52_history[]`.

Apply block before L-SIG viterbi call (~line 7100):
```cpp
gr_complex Hhdr52_xf[52];
const gr_complex* Hhdr52_for_lsig = Hhdr52;
if (d_apply_lsig_h_cross_frame) {
    int n_xf = ref_lsig_h52_cross_frame_average(
        Hhdr52, d_freq_offset_from_synclong, Hhdr52_xf);
    Hhdr52_for_lsig = Hhdr52_xf;
    USRP_LOG("[LSIG_H52_CROSS_FRAME] n_avg=%d depth=%d ...\n", ...);
}
// Use Hhdr52_for_lsig in decode_lsig_direct_from_header52() below
```

## USRP File-Replay Results (cross-board burst capture, 5s loop=3)

### Metric distribution (count of HT_SIG_CAND with given metric)

| metric | baseline | N=2 | N=4 | N=8 |
|--------|----------|-----|-----|-----|
| 10     | 2        | 0   | 0   | 0   |
| 11     | 4        | 4   | 12  | 2   |
| 12     | 30       | 24  | 18  | 20  |
| 13     | 62       | 86  | 98  | 66  |
| 14     | 258      | 286 | 182 | 210 |
| 15     | 370      | 388 | 380 | 346 |
| 16     | 456      | 378 | 248 | 278 |
| 17     | 140      | 116 | 126 | 136 |
| 18     | 22       | 6   | 8   | 12  |

**Observation**: N=2/4/8 have FEWER metric=10/12 candidates and MORE
metric=13 candidates compared to baseline. The cross-frame averaging
is NOT reducing effective noise — it's actually introducing slight
correlation with previous frames' H. Theoretical 1/sqrt(N) reduction
only applies when noise realizations are independent. With USRP
cable's slow LO drift, noise may be correlated across frames.

### L-SIG detection count

- baseline: 58 "Detected HT frame"
- N=4: 69 (+19%) — better L-SIG detection
- N=8: 48 (-17%) — over-smoothing hurts

### CRC / FCS result

- All 6 configurations: 0 FCS_OK
- All candidates crc_fail

## Why Pre-LSIG Cross-Frame Doesn't Help

1. **The HT_SIG viterbi metric is the bottleneck, not L-SIG detection**.
   Phase 125b already had L-SIG mostly succeeding (5/5 HT candidates
   per loop). The new HT frames from Phase 127 still face the same
   1.77 rad noise at HT-SIG viterbi.
2. **Cross-frame noise may be correlated**. USRP LO drift is slow
   (Phase 122: 0.5-1 rad over 5-6 symbols). If consecutive frames
   have correlated noise, cross-frame averaging does not give
   1/sqrt(N) reduction.
3. **The 1.77 rad ceiling is per-symbol, per-frame**. Each HT-SIG
   symbol sees independent 1.77 rad noise. Smoothing over frames
   cannot reduce this — only smoothing over multiple symbols within
   the SAME frame (which requires delayed re-decode) could.

## Combinations Tested (all REFUTED)

| Config | Detected HT | metric=10 | FCS_OK |
|--------|-------------|-----------|--------|
| baseline | 58 | 2 | 0 |
| lsig_xf_4 | 69 | 0 | 0 |
| lsig_xf_8 | 48 | 0 | 0 |
| lsig_xf_4 + htsig_xf_4 | (similar) | 0 | 0 |
| lsig_xf_4 + H_AVERAGE | (similar) | 0 | 0 |
| lsig_xf_4 + DDE + freq_smooth_3 | (similar) | 0 | 0 |

## Implementation Preserved

The implementation is opt-in (`IEEE80211_LSIG_H52_CROSS_FRAME_TRACK=N`,
default OFF) so it does not affect baseline behavior. Preserved for
future re-evaluation if upstream gates unblock (e.g., UHD streaming
stability improvements, ref clock input, etc.).

## What Now?

Per user "逐个实现 + USRP 验证" directive, Phase 127 is REFUTED. All
tested combinations of equalizer-layer attacks have been tried:

| Phase | Attack | Result |
|-------|--------|--------|
| 118b | H_AVERAGE (2 LTS + 2 HT-SIG pilots) | metric 12 |
| 119 | H_AVERAGE + per-bin safety filter | metric 12-17 |
| 120a | Scalar DDE | metric 13-18 |
| 121 | Per-SC DDE | metric 14-17 |
| 122 | HT-LTF 3-way | REFUTED on cross-board |
| 123 | Cross-frame (HT-SIG chain) | INCONCLUSIVE (n_avg=1) |
| 126A | Freq-domain smoothing | metric 12→11 standalone |
| 127 | Pre-LSIG cross-frame | metric 12→12 |

The **1.77 rad per-SC phase ceiling** (Phase 112 R1) is the documented
structural limit. **Per user directive "不可能接受现状", equalizer attacks
MUST continue** — Phase 128+ should consider:

- **Option F (CFO/SFO per-symbol from HT-LTF)** — Re-estimate
  CFO/SFO at HT-LTF (between HT-SIG and DATA). Different from
  Phase 79's per-symbol delta (which used HT-SIG pilots).
- **Option G (Iterative H refinement)** — Decode HT-SIG with current
  H, use decoded bits to re-estimate H, re-decode. Iterate until
  convergence. Phase 120a/121 attempted DDE, but not multi-pass
  iteration.
- **Same-board USRP test** — Per Phase 53, same-board is 2.4x
  stronger. User's cable is on cross-board. If user can swap cable,
  same-board might give cleaner signal and lower effective noise.
- **UHD streaming stability fix** — Phase 55's 99% loss. If
  capture doesn't drop samples, file-replay could test more
  architectures.

## Related

- [[project-p123-cross-frame]] — Phase 123 (HT-SIG cross-frame, n_avg=1)
- [[project-p125b-xboard-reachable]] — USRP cross-board reachability
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- Verdict: `docs/superpowers/notes/2026-07-09-phase127-pre-lsig-xf-verdict.md`
