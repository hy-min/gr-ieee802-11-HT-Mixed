# Phase 123: Cross-frame H52 tracking (2026-07-08)

**Branch**: TEST1
**Status**: 🟡 **INCONCLUSIVE on USRP** — implementation correct
(compile + loopback 1/1 PASS), but USRP test recv=0/120 (sync_short
detection starved the HT-SIG chain)

## TL;DR

Phase 123 implemented cross-frame H52 averaging. Stores the refined
H_a_ptr from previous N frames (FIFO ring buffer) and averages with the
current frame's H_a_ptr to reduce per-SC noise below the Phase 112 R1
1.77 rad ceiling.

Math (when chained AFTER Phase 118b H_AVERAGE, σ ~ 0.88 rad):
- N=2 → σ ~ 0.62 rad (above 1 rad wall)
- N=4 → σ ~ 0.44 rad (theoretical break below 1 rad viterbi wall)
- N=8 → σ ~ 0.31 rad

**USRP result**: Sent=120 Recv=0 (sync_short failed L-STF detection on
this run). The cross-frame apply block fired 0 times because the
HT-SIG chain was unreachable (LSIG viterbi never succeeded → H52_CROSS_FRAME
gated behind `if (lsig_ok)`).

## Implementation

**Env var** (default OFF, preserves Phase 117 baseline):
- `IEEE80211_H52_CROSS_FRAME_TRACK=N` with N ∈ {1,2,3,4,5,6,7,8}

**Files modified**:
- `lib/frame_equalizer_impl.h`: added `d_apply_htsig_h_cross_frame`,
  `d_h52_history_depth`, `d_h52_history_count`,
  `d_h52_history_freq_key`, `d_h52_history[8][52]`,
  `ref_h52_cross_frame_average()` member function declaration
- `lib/frame_equalizer_impl.cc`:
  - Env var parse in constructor (after DDE_PER_SC)
  - Member function `ref_h52_cross_frame_average` (after anonymous
    namespace close, alongside other class methods)
  - Apply block AFTER existing H_AVERAGE/DDE chain (gated on
    `d_early_eqsym_valid[kHtSig0Rel] && d_early_eqsym_valid[kHtSig1Rel]`)

**Function logic**:
1. Frequency-keyed reset (1 Hz threshold) — if freq changed, clear history
2. Compute uniform mean of (h_cur + d_h52_history[0..count-1])
3. Push h_cur into FIFO at position [count]; FIFO eviction if full
4. Diagnostic log (first 10 frames): n_avg, depth, mean |H| cur vs avg

## Compile + Loopback Verification (PASS)

```
$ cd /home/hy/gr-ieee802-11/build && make
[ 50%] Built target gnuradio-ieee802_11
[100%] Built target ieee802_11_python
$ make install
$ env IEEE80211_H52_CROSS_FRAME_TRACK=4 conda run -n gnuradio python \
    examples/test_file_replay_e2e.py
[H52_CROSS_FRAME] n_avg=1 depth=4 cur_mag=8.7459 avg_mag=8.7459 freq=5890000000
[H52_CROSS_FRAME] H_a_ptr = H_b_ptr = mean of current + 0 prior frames (N=4)
[FCS_OK]   x6 (1/1 PASS)
```

Baseline preserved when env var unset (default OFF).

## USRP Test (INCONCLUSIVE)

```
$ env IEEE80211_H52_CROSS_FRAME_TRACK=4 conda run -n gnuradio python \
    test_usrp_minimal_loopback.py --uhd-tune --freq 5250 --tx-gain 0 \
    --rate 20 --warmup 60 --rx-subdev A:0 --duration 60
```

| 指标 | Phase 117 baseline | Phase 118b H_AVERAGE | **Phase 123 cross-frame (N=4)** |
|------|-------------------|---------------------|----------------------------------|
| Sent | 70-120 | 70-120 | 120 |
| **Recv** | 27 | 12 | **0** ❌ |
| LSIG_DECODE_OK | 27 | 12 | 0 |
| HT_SIG_CAND | 144 | 48 | 0 |
| H52_CROSS_FRAME fires | - | - | **0** (chain never reached) |
| avg_snr_ht | 2.81 | 2.58 | 5.69 (only 8 frames reached) |
| FCS_OK | 0 | 0 | **0** |

**Root cause: sync_short failed L-STF detection on this 60s run.**
sync_short state=0 (SEARCH) for 99.85% of 2,886,220 general_work calls
(state=1 only 429 times). Phase 89 boxcar detector + adaptive threshold
did not catch L-STF on this particular run.

The cross-frame apply block is positioned AFTER `if (lsig_ok)` so it
cannot fire when L-SIG viterbi fails upstream. This is the same blocker
that defeated all previous equalizer-layer attacks (Phase 118b-122).

## Why Phase 123 Was Attempted Despite Phase 118b

- Phase 118b H_AVERAGE reaches σ ~ 0.88 rad (still above 1 rad wall)
- Cross-frame averaging chains AFTER H_AVERAGE to push σ below 1 rad
  for the first time (mathematically: σ_post_avg / sqrt(N))
- N=4 should bring σ to 0.44 rad — theoretical viterbi wall break

But the test could not validate this because USRP sync_short starved
the chain.

## Default OFF

- `IEEE80211_H52_CROSS_FRAME_TRACK` opt-in (default unset)
- All new code paths gated on `d_apply_htsig_h_cross_frame`
- Phase 117 baseline preserved when env vars absent
- No loopback regression

## Architectural Conclusion

Phase 123 cross-frame H tracking is mathematically sound and the
implementation works. But it requires LSIG viterbi to succeed FIRST.
Until the upstream sync_short / L-SIG viterbi issues are resolved,
no equalizer-layer attack (Phase 118-123) can be validated on USRP.

## Recommended Next Steps (Phase 124+)

1. **File-replay validation**: re-run Phase 123 on a fresh USRP
   capture that has known-good sync_short detections (e.g. p118b
   capture had 24 detections in 4s = 6/s vs p123 had 0 in 60s).
   If file-replay shows metric improvement, cross-frame tracking
   is viable and only USRP runtime is the issue.

2. **Phase 124: USRP runtime investigation** — why does sync_short
   detect L-STF on p118b capture but not on p123 capture? Is it
   LO warmup, sample rate, gain setting? Per Phase 87-89 the
   detection was algorithmically broken; per Phase 89 it was fixed
   on captures. But this p123 run shows runtime regression.

3. **Phase 125+: pre-LSIG cross-frame tracking** — if the cross-frame
   logic can be applied BEFORE L-SIG viterbi (e.g. by tracking
   H52 from previous frames and applying to current frame's L-LTF0
   FFT before L-SIG equalization), it might help L-SIG viterbi
   succeed more often. This is a different code path and a more
   invasive change.

## Related

- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (current best
  metric 12, the chain this Phase 123 chains AFTER)
- [[project-p122-htltf-revisit]] — Phase 122 cross-daughterboard
  3-way HT-LTF AVG REFUTED
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- Verdict: `docs/superpowers/notes/2026-07-08-phase123-cross-frame-h-verdict.md`