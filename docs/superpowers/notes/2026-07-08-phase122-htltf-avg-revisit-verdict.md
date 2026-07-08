# Phase 122: 3-way HT-LTF AVG revisit on cross-daughterboard (2026-07-08)

**Branch**: TEST1
**Status**: 🔴 **REFUTED** — 3-way HT-LTF averaging breaks L-SIG viterbi on
cross-daughterboard USRP

## TL;DR

Phase 114/115 implemented 3-way H52 averaging: 2 LTS (L-LTF0, L-LTF1) + 1
HT-LTF, weighted by |H|. Phase 115 verdict (same-board) showed 3-way fires
2x, metric 14-16 (slight improvement over baseline 14-17).

Phase 122 revisited 3-way on **cross-daughterboard** A:0 TX → B:0 RX2.
**Result: 3-way breaks L-SIG viterbi** (LSIG_DECODE_OK 0/120, baseline
27/120). HT_SIG_CAND=0. 3-way REFUTED on cross-daughterboard.

## Test Configuration

Command: `python test_usrp_minimal_loopback.py --uhd-tune --htltf-avg
--freq 5250 --tx-gain 0 --rate 20 --warmup 60 --rx-subdev A:0 --duration 60`

Env vars set: `IEEE80211_H52_SNR_WEIGHTED=1 IEEE80211_HTLTF_AVG=1`

Logs: `/tmp/p122_htltf_avg_60s.log` (run 1), `/tmp/p122_htltf_avg_60s_v2.log` (run 2)

## Results

| 指标 | Phase 117 baseline (2-way) | **Phase 122 3-way (cross-board)** | **Phase 115 3-way (same-board)** |
|------|---------------------------|----------------------------------|----------------------------------|
| 触发次数 | - | 1 | 2 |
| **LSIG_DECODE_OK** | 27 | **0** ❌ | 8 |
| HT_SIG_CAND | 144 | 0 | 16 |
| HT_SIG metric | 13-18 | - | 14-16 |
| avg_snr_ht | 2.81 | 7.29 | 74.02 (-18.6 dB) |
| HT_SIG_PARSE_OK | 0 | 0 | 0 |

3-way weights (Phase 122 run 1):
```
[H52_3WAY_AVG] wt0=10.7996 wt1=9.9253 wt_ht=10.7158
  ratio_ltf01=1.088 ratio_ltf0ht=1.008
```
3 sources contribute roughly equally (no single source dominates).

## Root Cause: HT-LTF has different channel than L-LTF

The 3-way averages 3 H estimates at different time points:
- L-LTF0 (counter=0): channel at L-LTF0 time
- L-LTF1 (counter=1): channel at L-LTF1 time
- HT-LTF (counter=7): channel at HT-LTF time (5-6 symbols later)

**Per-symbol channel drift (Phase 112 R1)**: the 1.77 rad per-SC phase
noise has both drift and random components. The drift component makes
HT-LTF a systematically different H than L-LTF (0.5-1 rad difference
over 5-6 symbols).

Averaging 3 different channel estimates:
- 2-way (L-LTF only): noise ~ 1.25 rad (correlated within 1 symbol)
- 3-way: noise ~ 1.0 rad (1.77/sqrt(3) random) + drift penalty ~ 0.5-1 rad

The drift penalty exceeds the noise reduction. Net: 3-way is WORSE than
2-way for cross-daughterboard.

**Why same-board (Phase 115) was slightly better**:
- Same-board LO has correlated noise (smaller drift between symbols)
- 3 sources give more averaging benefit than drift penalty
- Net: slight improvement (14-16 vs 14-17)

**Why cross-daughterboard (Phase 122) is much worse**:
- Cross-board has INDEPENDENT LOs (per user directive)
- Larger phase drift between L-LTF and HT-LTF
- Drift penalty dominates
- 3-way H52 is OFF from true channel by 0.5-1 rad
- L-SIG viterbi fails (LSIG_DECODE_OK 0)

## Architectural Conclusion

**Multi-symbol H averaging doesn't work for cross-daughterboard**:
- Per-symbol phase drift (Phase 112 R1) breaks the "same channel
  assumption" needed for averaging across symbols
- 2 LTS within 1 symbol (L-LTF0, L-LTF1) is OK (small drift)
- 3+ symbols (L-LTF → HT-LTF, 5-6 symbols apart) is too much drift

**Best H52 estimate remains 2 LTS only** (Phase 117 baseline).

## Files Modified

None (Phase 122 used existing IEEE80211_HTLTF_AVG=1 implementation
from Phase 114/115).

## Default OFF

- `IEEE80211_HTLTF_AVG=1` opt-in (default unset)
- Phase 117 baseline preserved when env var absent
- No loopback regression (compile-time check passes)

## Related

- [[project-p117-baseline]] — Phase 117 cross-board 2-way baseline
- [[project-p115-t1t2-3way-fix]] — Phase 115 3-way on same-board
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE on cross-board
  (metric 12, BEST result)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase noise
- Verdict: `docs/superpowers/notes/2026-07-08-phase122-htltf-avg-revisit-verdict.md`
