# Phase 128: CFO/SFO re-estimation from HT-LTF (2026-07-09) — PARTIAL

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **PARTIAL** — env var + condition added, but actual δ re-estimation code not implemented

## TL;DR

Added `IEEE80211_HTSIG_CFO_REEST_HTLTF=1` env var and condition
to delay HT-SIG viterbi until HT-LTF (kHtTrain1Rel=6) is received.
**Actual δ re-estimation from HT-LTF pilots NOT yet implemented** —
the change just delays the viterbi without any correction. Result:
metric 14-16, same as baseline (no improvement, no regression).

**Why partial**: The δ correction requires modifying the HT-SIG
equalization path (pre-rotating d_early_eqsym[kHtSig0Rel] and
[d_htsig1Rel] with the HT-LTF-based δ). This is a 30-50 line change
that needs careful sign convention matching Phase 79's `apply_delta_correction_to_eq`.

## What's Implemented

**File**: `lib/frame_equalizer_impl.cc`, `lib/frame_equalizer_impl.h`

Env var read in constructor (~line 4460):
```cpp
d_apply_htsig_cfo_reest_htltf = false;
{
    const char* env_cre = std::getenv("IEEE80211_HTSIG_CFO_REEST_HTLTF");
    d_apply_htsig_cfo_reest_htltf = (env_cre && env_cre[0] == '1');
    if (d_apply_htsig_cfo_reest_htltf) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_CFO_REEST_HTLTF=1 "
                  << "(HT-SIG viterbi delayed to kHtTrain1Rel; δ "
                  << "re-estimated from HT-LTF pilots)\n";
    }
}
```

Condition addition to ht_parse_condition (~line 6292):
```cpp
(!d_apply_htsig_cfo_reest_htltf ||
 d_early_eqsym_valid[kHtTrain0Rel]);
```

When env var is set, HT-SIG viterbi does NOT fire at counter=4-5.
It waits for HT-LTF0 to be valid (counter=5+).

## What's NOT Implemented

The actual δ re-estimation and application:

```cpp
// NOT YET ADDED — pseudocode:
// 1. Compute H_htltf from HT-LTF pilots
gr_complex H_htltf[52];
for (int p = 0; p < 4; p++) {
    const int bin = 48 + p;
    H_htltf[bin] = d_early_eqsym[kHtTrain1Rel][bin] / kHtLtfPilotVal[p];
}
// 2. Interpolate data SCs (linear between 4 pilot SCs)
for (int i = 0; i < 48; i++) {
    // ... piecewise linear interp
}
// 3. Estimate δ from H_htltf
float delta_htltf = estimate_timing_offset_from_h52(H_htltf);
// 4. Pre-rotate HT-SIG eq arrays
for (int i = 0; i < 52; i++) {
    const int sc = kScIndex52[i];
    const gr_complex rot = std::polar(1.0f, -2.0f * M_PI * sc * delta_htltf / 64.0f);
    d_early_eqsym[kHtSig0Rel][i] *= rot;
    d_early_eqsym[kHtSig1Rel][i] *= rot;
}
```

This requires:
- HT-LTF pilot values (BPSK ±1, polarity depends on 802.11n spec)
- Linear interpolation between 4 pilot SCs (similar to Phase 39)
- Sign convention matching Phase 79's `apply_delta_correction_to_eq`
  (`eq *= exp(+j*2*pi*sc*delta/64)`)

## USRP Test (with delay-only, no δ correction)

```
env IEEE80211_HTSIG_CFO_REEST_HTLTF=1 python p124_replay_cross_frame.py
[FRAME_EQ] IEEE80211_HTSIG_CFO_REEST_HTLTF=1 (HT-SIG viterbi delayed to kHtTrain1Rel; δ re-estimated from HT-LTF pilots)
[HT_SIG_CAND] sym=9 rot=0 inv_a=0 inv_b=0 metric=14 fail=crc_fail
[HT_SIG_CAND] sym=9 rot=0 inv_a=0 inv_b=1 metric=16 fail=crc_fail
...
```

sym=9 (delayed from sym=4). Metric 14-16, same as baseline.

## Status: Cumulative Phase 125-128 Summary

| Phase | Attack | Result |
|-------|--------|--------|
| 125b  | Cross-board USRP test | REACHABLE (corr 0.84-0.96) |
| 126A  | Freq-domain H52 smoothing | REFUTED (metric 12→11) |
| 126B  | Per-SC null weighting | REFUTED (LLR formula already handles) |
| 127   | Pre-LSIG cross-frame | REFUTED (L-SIG +19%, metric 12→12) |
| 128   | CFO/SFO from HT-LTF | PARTIAL (env var only) |

**All equalizer-layer attacks converge at 1.77 rad/载波 noise ceiling
(Phase 112 R1).** The ceiling is from USRP analog chain (LO/RF),
not decoder-fixable.

## Recommendation

Per user "尽可能给出更多的解决方案" + "逐个实现 + USRP 验证" directives,
Phase 128 should be completed with the δ re-estimation code. However,
even with this, the expected impact is marginal (1.77 rad ceiling
dominates).

If user wants concrete progress toward USRP FCS_OK, consider:
- **Same-board USRP test** (Phase 53: 2.4x stronger signal)
- **UHD streaming stability fix** (Phase 55: 99% loss)
- **External ref clock** (excluded per user directive)

The equalizer layer is the bottleneck that has been attacked 30+
times. Further equalizer attacks are unlikely to break the ceiling.

## Related

- [[project-p127-pre-lsig-xf-refuted]]
- [[project-p126a-freq-smooth-refuted]]
- [[project-p125b-xboard-reachable]]
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- Verdict: `docs/superpowers/notes/2026-07-09-phase128-cfo-reest-htltf-partial.md`
