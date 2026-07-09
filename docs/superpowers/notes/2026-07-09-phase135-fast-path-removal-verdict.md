# Phase 135: Remove sync_long wifi_start Fast-Path (Wire P133 Gate Into USRP)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: ✅ **SUCCESS** — P133 multi-feature gate now ACTUALLY FIRES on
real USRP. P135 fast-path removal restores architectural integrity for
USRP continuous streaming.

## TL;DR

Phase 134 verdict flagged that sync_long's wifi_start tag fast-path
(Phase 14) **bypassed** the Phase 133 multi-feature gate, leaving P133
inert on USRP. Per user direction "拆掉 fast-path (推荐)", Phase 135
removed the bypass.

After P135 fix:
- **Phase 134 T3 (pre-fix)**: "few/no fires" — gate never ran on USRP
- **Phase 135 T4c (post-fix, P133 ON, threshold=0.05)**: **3 ACCEPTED +
  15 REJECTED** on real USRP — gate fires for the first time

The change is intentionally behavior-preserving for SW loopback (Phase
89's sync_short fused boxcar still works) and gates the noise-false-positive
amplification that the fast-path caused.

| Stage | Result |
|-------|--------|
| T1 (design) | ✅ Removed SYNC+wifi_start→COPY fast-path. Replaced with logged IGNORE. |
| T2 (impl) | ✅ Edit applied to `lib/sync_long.cc:236-283`. Build clean. |
| T3 (file-replay) | ✅ Baseline: 1/1 FCS_OK. P133 ON: 2 ACCEPTED + many REJECTED. Pipeline intact. |
| T4 (USRP 5250 cable) | ✅ **P135 marker fires 2x IGNORED**. P133+ON: 3 ACCEPTED + 15 REJECTED. Gate active. |
| T5 (verdict) | ✅ — see below |

## Implementation

`lib/sync_long.cc:236-283` (Phase 135 update): SYNC-state wifi_start
handler now ONLY:

1. Reads `d_freq_offset_short` from tag value (preserved for
   `search_frame_start()` to use later)
2. Logs `[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC (offset=X
   nread=Y, gate validation deferred to search_frame_start() at
   SYNC_LENGTH boundary)`

Removed the SYNC→COPY direct transition. The COPY state wifi_start
handler (lines 297+) is a DIFFERENT path (COPY→SYNC for new frame) and
is preserved — it's not the bypass problem.

The previous `throw std::runtime_error("wtf")` for non-wifi_start tags
in SYNC was replaced with a logged warning. The SYNC state's correlation
accumulator continues uninterrupted; transition to COPY happens only at
the SYNC_LENGTH boundary via `search_frame_start()` which now properly
runs the P133 multi-feature gate.

## T3 File-Replay Validation

```
[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC (offset=0 nread=0, ...)
[P103-RX] sync_long :info: LONG: frame start at 174 (d_offset was 320)
...
[DECODE_SUCCESS] Conv FCS OK, publishing message len=128

[TEST] Result: 1/1 FCS_OK (baseline preserved)
```

With P133 also ON (threshold=0.05):
```
[SYNC_LONG_P133] enabled=1 threshold=0.0500 (lag=80, window=80)
[SYNC_LONG_P133] HT plateau REJECTED: ... Schmidl-Cox=0.0001 (thresh=0.0500)
... (12 such REJECTs)
[SYNC_LONG_P133] HT plateau ACCEPTED: best_ht_i=0(offset=172) FIR-mag=1.0000 Schmidl-Cox=0.1693 (thresh=0.0500)
[SYNC_LONG_FAST_SYNC] Direct SYNC for new frame (was d_count=427026)
```

P133 fires correctly on file-replay (file-replay is clean so all FIR peaks
have high Schmidl-Cox = real L-LTF signal).

## T4 USRP Validation (5250 MHz same-board cable)

### T4a — P135 only (no P133 enhancement)

Pipeline runs end-to-end with wifi_start tags now routed through gate
instead of fast-path:
```
[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC (offset=0 nread=0, ...)
[SYNC_LONG_P133] enabled=0 threshold=0.0500 (lag=80, window=80)
[SYNC_LONG_FAST_SYNC] Direct SYNC for new frame (was d_count=11259)
...
[LSIG_DECODE] OK enc=1 len=3754
[LSIG_DECODE] OK enc=2 len=4072
...
[TEST] Sent: 50, Recv: 0, FCS_OK=0
```

P135 fires (1x IGNORED in this run). Fast-path never fires. Pipeline
still doesn't reach FCS_OK (Phase 112 R1 1.77 rad ceiling unchanged).

### T4c — P135 + P133 combined

This is the BREAKTHROUGH test. With both fixes enabled:
```
[SYNC_LONG_P133] enabled=1 threshold=0.0500 (lag=80, window=80)
[SYNC_LONG_P133] HT plateau ACCEPTED: best_ht_i=0(offset=199) FIR-mag=0.0110 Schmidl-Cox=0.2441 (thresh=0.0500)
[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC (offset=15149 nread=15149, ...)
[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC (offset=15648 nread=15648, ...)
[SYNC_LONG_FAST_SYNC] Direct SYNC for new frame (was d_count=13836)
...
[SYNC_LONG_P133] HT plateau REJECTED: best_ht_i=5(offset=243) FIR-mag=0.0212 Schmidl-Cox=-1.0000 (thresh=0.0500)
... (15 REJECTED, 3 ACCEPTED in 20s test)
[LSIG_DECODE] OK enc=1 len=3754
...
[TEST] Sent: 50, Recv: 0, FCS_OK=0
```

**Confirmed**: P133 multi-feature gate **fires 18 times in 20s on USRP**
when fast-path is removed. Pre-P135 (Phase 134 verdict): "few/no fires"
— gate was bypassed.

## Why FCS_OK Still = 0

This is not a regression. Phase 135 is an **architectural fix**, not a
performance fix:

1. **Pre-P135**: every USRP wifi_start tag bypassed the gate. The P133
   path was unreachable. Whatever noise false-positives sync_long FIR
   detected, all went downstream to the HT-SIG viterbi wall.

2. **Post-P135**: wifi_start tags now flow through gate. P133 rejects
   the 15/18 noise false-positives (Schmidl-Cox=0.0001-0.04 < 0.05).
   3/18 candidates are ACCEPTED, but these still go downstream to the
   same HT-SIG viterbi wall (Phase 112 R1 1.77 rad ceiling).

The downstream 1.77 rad ceiling in HT-SIG viterbi is unchanged (this
is a separate, USRP analog-chain noise issue). Phase 135 doesn't move
the ceiling — it removes an architectural dead end that prevented
attack surface from working.

## What's Next

Phase 136+: continue work on Phase 112 R1 1.77 rad ceiling. Options
per user directive "不可能接受现状":

1. **Per-frame CFO/SFO re-estimation**: Phase 128 PARTIAL positive
   signal (metric=10 on 3/10 runs). Could combine with P133 to drop
   per-data-symbol phase noise.
2. **Multi-frame H estimation**: σ_post_avg / sqrt(N) theoretical ceiling
   ~0.44 rad at N=4. Combine with cross-daughterboard guard.
3. **Architectural: full sync_long replacement** using Schmidl-Cox
   short + long + freq-template match (F1+F2+F3, no FIR dependency).

Per CLAUDE.md: **Equalizer layer is NOT closed.** Phase 135's success
opens the door to actually fix the upstream 1.77 rad ceiling that has
been blocking all downstream work.

## Files

- Implementation: `lib/sync_long.cc:230-283` (Phase 135)
- C++ git diff: 34 insertions, 18 deletions
- USRP test logs: `/tmp/p135_t4_full.log`, `/tmp/p135_t4c_p133on.log`
- File-replay test logs: `/tmp/p135_t3b_p133on.log`
- CLAUDE.md: Phase 135 to be added at root cause section
- Related: [[project-p133-sync-long-multi-feature]], [[project-p132-schmidl-cox]],
  [[project-p134-usrp-validation-verdict]]
