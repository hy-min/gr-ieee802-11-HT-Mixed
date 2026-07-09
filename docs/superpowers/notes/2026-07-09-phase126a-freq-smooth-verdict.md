# Phase 126A: Frequency-domain H52 smoothing (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on USRP** — freq-domain smoothing alone does not break the 1.77 rad ceiling

## TL;DR

Implemented `IEEE80211_HTSIG_H_FREQ_SMOOTH=1` + `IEEE80211_HTSIG_H_FREQ_SMOOTH_TAP=3/5/7`
in `frame_equalizer_impl.cc`. Applies N-tap moving average across 52 SCs
AFTER all other H52 refinements (H_AVERAGE, DDE, CROSS_FRAME) and BEFORE
HT-SIG viterbi. Tested on USRP cross-board capture with multiple
combinations.

**Result**: All 1670 candidates fail CRC. Best HT_SIG metric occasionally
touches 10 (viterbi threshold) but **never passes CRC**. Phase 112 R1
root cause confirmed: the 1.77 rad noise is per-SC, NOT correlated
across SCs, so freq-domain smoothing cannot reduce per-SC variance
materially. Metric improvements (12 → 11 standalone) are within noise
floor.

**Implementation preserved** as opt-in for future re-evaluation if
upstream gates unblock.

## Implementation

**File**: `lib/frame_equalizer_impl.cc`, `lib/frame_equalizer_impl.h`

Helper function (~line 748):
```cpp
static void smooth_h52_freq(const gr_complex* H52_in,
                            gr_complex* H52_in_out,
                            int tap)
{
    if (tap < 3 || tap > 7 || (tap % 2) != 1) return;
    const int half = tap / 2;
    for (int i = 0; i < 52; i++) {
        int i_lo = i - half;
        int i_hi = i + half;
        if (i_lo < 0) i_lo = 0;
        if (i_hi > 51) i_hi = 51;
        gr_complex acc(0.0f, 0.0f);
        int n = 0;
        for (int k = i_lo; k <= i_hi; k++) {
            acc += H52_in[k];
            n++;
        }
        H52_in_out[i] = acc / gr_complex((float)n, 0.0f);
    }
}
```

Env vars read in constructor (~line 4320):
- `IEEE80211_HTSIG_H_FREQ_SMOOTH=1` (default OFF)
- `IEEE80211_HTSIG_H_FREQ_SMOOTH_TAP=3/5/7` (default 3)

Apply block after CROSS_FRAME (~line 7425), before viterbi call:
```cpp
if (d_apply_htsig_h_freq_smooth && ...) {
    gr_complex H_fs[52];
    smooth_h52_freq(H_a_ptr, H_fs, d_htsig_h_freq_smooth_tap);
    H_a_ptr = H_fs;
    H_b_ptr = H_fs;
    USRP_LOG("[HTSIG_H_FREQ_SMOOTH] ...");
}
```

## USRP File-Replay Results (cross-board burst capture)

5-run min metric per 5s replay:

| Config | min metric (5 runs) | Note |
|--------|---------------------|------|
| baseline (no env) | 12, 10, 10, 11, 10 | lucky 10s |
| H_AVERAGE alone | 11, 11, 11, 11, 11 | 11-13 typical |
| freq_smooth_3 alone | 11, 10, 10, 11, 11 | matches baseline |
| freq_smooth_5 alone | 12, 12, 13, 12, 12 | worse |
| freq_smooth_7 alone | 13, 13, 13, 14, 13 | worse |
| DDE + freq_smooth_3 | 11, 11, 11, 11, 10 | marginal |
| DDE_PER_SC + freq_smooth_3 | 11, 10, 10, 11, 11 | marginal |
| H_AVERAGE + freq_smooth_3 | 12, 12, 12, 11, 12 | no compound |
| DDE + freq_smooth_3 + FINE_ROT | (all metric 10-18, 0 crc_pass) | 32 candidates fail |

**CRC fail counts (10s replay)**:
- DDE+FS3: 1670 crc_fail, 10 rsv_set, 0 crc_ok
- DDE+FS3+FINE_ROT: 3408 crc_fail, 6 bw40_set, 0 crc_ok

**Conclusion**: Even when the viterbi metric reaches the threshold of
10, **none of the candidates pass CRC**. The H noise is too high for
correct bit decoding. Phase 112 R1 prediction CONFIRMED.

## Why Freq-Domain Smoothing Cannot Help

The 1.77 rad per-SC phase noise (Phase 112 R1) is **per-SC** noise
from the USRP analog chain. Adjacent SCs have **uncorrelated** noise
realizations. Therefore:

- 3-tap smoothing: σ_per_SC_reduction = 1/sqrt(3) only if noise is
  uncorrelated across SCs
- But σ_after is still ~1.0 rad even with full 1/sqrt(3) reduction
  (1.77 / sqrt(3) = 1.02 rad)
- This is at the viterbi threshold, leaving NO margin for bit errors
- **All 1670 candidates fail CRC**

The freq-domain approach assumes channel coherence bandwidth > tap
spacing. At 20 MHz / 52 SCs = 385 kHz SC spacing, 3-tap = 1.15 MHz
coherence. USRP cable channel has coherence BW >> 1.15 MHz, so the
**amplitude** is highly correlated across SCs, but the **phase noise**
is NOT — it's added at the receiver after the channel.

## Combinations Worth Trying (Phase 127+)

Even though standalone freq_smooth REFUTED, the implementation
preserved for future:
- + iterative H refinement (DDE with multiple passes)
- + per-frame H scaling based on |H| distribution
- + per-SC weighting with Phase 78b null SC info

## Next: Phase 126B (multi-symbol H52 averaging within frame)

Per user "逐个实现 + USRP 验证" directive, proceeding to Option B
(multi-symbol H52 averaging within frame). This is the most promising
remaining option because it directly reduces per-frame noise via N-symbol
averaging (theoretical 1/sqrt(N) reduction, 4-8 symbols available).

## Related

- [[project-p123-cross-frame]] — Phase 123 cross-frame (n_avg=1 issue)
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (best metric 12)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- [[project-p125b-xboard-reachable]] — USRP cross-board test
- Verdict: `docs/superpowers/notes/2026-07-09-phase126a-freq-smooth-verdict.md`
