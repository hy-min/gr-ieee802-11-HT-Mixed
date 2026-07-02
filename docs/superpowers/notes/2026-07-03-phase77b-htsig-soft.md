# Phase 77b HT-SIG Soft-LLR Viterbi — Findings

**Date**: 2026-07-02
**Branch**: TEST1
**Capture**: /tmp/p76_selftx_5250.bin (5250 MHz clean channel)

## Implementation

**NO new code required** — Phase 44 (commit 6e1209e) already added the full
soft-LLR infrastructure:

- `lib/frame_equalizer_impl.cc:1352-1438` — `viterbi_decode_133_171_soft()`
  (squared-error branch metric, Q8.8 fixed-point, 64 states)
- `lib/frame_equalizer_impl.cc:1455-1473` — `compute_soft_llr_qbpsk()`
  (LLR[i] = sign(eq.imag()) * |H[i]|/max(|H|))
- `lib/frame_equalizer_impl.cc:2422-2682` — soft path through the
  candidate loop: when `use_soft_llr=true`, computes `llr48_a`/`llr48_b`,
  deinterleaves to `enc96_soft[]`, calls `viterbi_decode_133_171_soft`.
- `lib/frame_equalizer_impl.cc:3258-3262` — `IEEE80211_SOFT_LLR_VITERBI=1`
  env var (default OFF) sets `d_use_soft_llr_viterbi`.
- `lib/frame_equalizer_impl.cc:5378` — passes `d_use_soft_llr_viterbi` to
  `decode_htsig_from_rotated()` for each (rot, inv_a, inv_b) candidate.

This is a **re-test** of the Phase 44 implementation on a clean 5250 MHz
channel where the Phase 59 H52 null bottleneck is structurally absent
(n_nulls=0/52). Phase 44 originally REFUTED on noisy 5800/5890 MHz USRP
air path with avg_snr_htsig ~1.59 dB.

## Test config (5250 MHz clean channel, all opt-in)

```
IEEE80211_H52_NULL_INTERP=1
IEEE80211_H52_NULL_THRESH=0.03
IEEE80211_H52_INTERP_RADIUS=5
IEEE80211_HTSIG_PILOT_CPE=1
IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_TIMING_OFFSET_APPLY=1
IEEE80211_LSIG_PILOT_CPE=1
IEEE80211_SOFT_LLR_VITERBI=1
```

Build: up-to-date (no source changes). `make install` verified
`install/lib/libgnuradio-ieee802_11.so` updated.

## Test results (5250 MHz, 77a + 77b combined)

| Metric | 77a baseline (hard viterbi) | 77b (soft-LLR ON) | Change |
|--------|-----------------------------|--------------------|--------|
| HT_SIG_CAND | 80 | 512 | +440% |
| avg_snr_htsig (mean) | 4.71 dB (n=5) | 6.10 dB (n=32) | +1.39 dB |
| avg_snr_htsig (max) | 5.17 dB | 15.36 dB | +10.19 dB |
| HT_SIG_PARSE_FAIL | 5 | 32 | +540% |
| HT_SIG_PARSE_OK | 0 | 0 | unchanged |
| FCS_OK | 0 | 0 | unchanged |
| Viterbi metric scale | 13-17 (hard) | 14790-22432 (soft Q8.8) | N/A (different scale) |
| is_ht_frame=1 (in PARSE_FAIL) | 2/5 | 13/32 | 41% same |

## Analysis

1. **More candidates fire**: 77a hard viterbi only produced 5 PARSE_FAIL
   lines from a 5-loop capture. 77b soft-LLR produced 32 PARSE_FAIL lines
   (6.4× more). This suggests soft-LLR's reduced hard-bit threshold
   (LLR<0 vs real(eq.imag)>0) lets the equalizer pass more raw frames
   into the parse path.

2. **Higher avg_snr_htsig**: Mean improves +1.4 dB. Maximum jumps to
   15.36 dB (vs 5.17 dB in 77a). The |H|-weighted LLR effectively
   down-weights the residual H52 nulls that are still present even
   after Phase 73 tight_v2 pre-clean.

3. **Viterbi metric saturates**: 510/512 candidates produce
   `metric=14790-22432, fail=crc_fail`. Soft-LLR squared-error metric
   runs in a 14k-22k range — well above any "good CRC" threshold the
   decoder would recognize. The decoder's CRC pass/fail logic still
   rejects all 512 candidates.

4. **No CRC pass**: Despite higher avg_snr_htsig, 0/512 candidates pass
   HT-SIG CRC. The soft-LLR hypothesis — that weighted down-nulled
   SCs would unblock viterbi — does NOT translate to CRC pass at this
   SNR. The impairment persists: equalized HT-SIG constellation still
   has the structural corruption (likely argH phase rotation per
   Phase 31b) that makes QBPSK → 0/1 decisions uncorrelated with
   the transmitted bits.

## Verdict

**REFUTED on 5250 MHz clean channel**.

Soft-LLR viterbi (Phase 44 analog) improves raw signal metrics
(+1.4 dB avg_snr_htsig, +540% candidate throughput) but does NOT
unblock HT-SIG CRC. The hypothesis "down-weighting null SCs restores
viterbi" is REFUTED at 5250 MHz n_nulls=0 baseline, meaning the
remaining impairment is not the Phase 41 H52 null issue but a
structural QBPSK rotation/phase coherence problem downstream.

**No USRP loopback regression check needed** — no code changed.

## Implications for Phase 77c/d

- 18+ REFUTED hypotheses total at HT-SIG viterbi layer
- L-SIG layer also fails (LSIG_DECODE OK = 0 in both 77a and 77b)
- Path forward per HARD CONSTRAINT: must attack upstream of viterbi
  (77c per-frame H52 refinement, or RF/antenna swap per 77d)

## Files inspected (no changes)

- lib/frame_equalizer_impl.cc (lines 1352-1473, 2422-2682, 3258-3262, 5378)
- examples/p68_replay_offline.py (test script)
- /tmp/p77b_htsig_soft_5250.log (test output)
- /tmp/p77a_lsig_cpe_5250_eq.log (77a baseline)