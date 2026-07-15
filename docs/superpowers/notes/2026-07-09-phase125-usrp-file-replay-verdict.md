# Phase 125: USRP file-replay validation of Phase 123 (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on USRP** — same equalizer-chain blocker as Phases 60-122

## TL;DR

USRP X310+UBX-160 reconnected (user-confirmed, 192.168.10.2). Two
USRP test runs with `--capture` flag produced:

- v1 (`--uhd-tune`, --tx-gain 0): Sent=120 Recv=0, IQ 68MB, DC offset
  dominates signal, 0.9990 autocorrelation everywhere = no real signal
- v2 (no `--uhd-tune`, --tx-gain 10): Sent=90 Recv=0, IQ 113MB, no DC
  offset, 0.8169 L-STF autocorrelation (real signal present)

**v2 file-replay** with Phase 123 cross-frame N=0/4:
- sync_short detected **294 frames** (vs 4 in v1, 0 in real-time)
- 0/0 FCS_OK, 0 HT_SIG_CAND, 0 H52_CROSS_FRAME apply fires
- Equalizer chain silently aborts (no debug output between sync_long
  and the viterbi metric)

This is the **same upstream blocker** that defeated Phases 60-122.
sync_short + sync_long work; equalizer chain does not produce
HT_SIG_CAND. Phase 123 cannot be validated on USRP because the
HT-SIG processing stage is never reached.

## USRP Setup (verified)

```
$ uhd.usrp.MultiUSRP('addr=192.168.10.2')
Type: Single USRP, X-Series Device, Mboard 0: X310
  TX: A:0, B:0 (UBX-160)
  RX: A:0, B:0 (UBX-160)
  Master clock: 200 MHz
  TX gain: 0-31.5 dB
  RX gain: 0-37.5 dB
```

## USRP Test Results

### v1: --uhd-tune --freq 5250 --tx-gain 0 (Phase 82+ cable config)

```
[TEST] Sent: 120
[TEST] Recv: 0
[TEST] Success Rate: 0.0%
[TEST] FCS_OK=0 FCS_FAIL=0
[TEST] Capture file: /tmp/p125_usrp_capture.fc32 (68063952 bytes)
```

**Capture analysis**:
- Power: -1.19 dB (low but not noise floor)
- DC offset: **-0.866 - 0.097j** (huge DC dominates signal)
- std(re)=std(im)=0.022 (very small)
- |autocorr(16)|/pwr = 0.9990 across ALL segments (uniform L-STF pattern)
- 0 power bursts >5x median
- USRP overflow/underflow errors throughout (15-16/s overflow, 1-2/s underflow)

**Diagnosis**: DC offset + overflow suggest UBX-160 LO not properly
tuned. `--uhd-tune` (Phase 113 T5.A disable UBX auto-calibration)
appears to make the situation worse for this particular session.

### v2: NO --uhd-tune --freq 5250 --tx-gain 10

```
[TEST] Sent: 90
[TEST] Recv: 0
[TEST] Success Rate: 0.0%
[TEST] FCS_OK=0 FCS_FAIL=0
[TEST] Capture file: /tmp/p125_capture_v2.fc32 (112842928 bytes)
```

**Capture analysis**:
- Power: -29.99 dB (very low signal)
- DC offset: ~0 (good)
- |autocorr(16)|/pwr = 0.8169 (real L-STF present)
- Variance per segment: 1%=0.0007, 50%=0.0009, 99%=0.005, max=0.031

**Diagnosis**: Real signal present, no DC issue, but signal is weak
(-30 dB). v2 is the better capture for file-replay validation.

## v2 File-Replay Results

### Baseline (no env)

```
$ python examples/p124_replay_cross_frame.py \
    --in /tmp/p125_capture_v2.fc32 --duration 15 --loop
sync_short Frame detected: 296
HT_SIG_CAND: 0
decode_mac: 0
FCS_OK: 0  FCS_FAIL: 0
```

### N=4 (Phase 123 cross-frame averaging)

```
$ IEEE80211_H52_CROSS_FRAME_TRACK=4 python examples/p124_replay_cross_frame.py \
    --in /tmp/p125_capture_v2.fc32 --duration 15 --loop
sync_short Frame detected: 294
HT_SIG_CAND: 0
decode_mac: 0
H52_CROSS_FRAME apply: 0
FCS_OK: 0  FCS_FAIL: 0
```

**Key finding**: 0 H52_CROSS_FRAME apply fires means Phase 123 is never
reached. The apply block is gated on
`d_early_eqsym_valid[kHtSig0Rel] && d_early_eqsym_valid[kHtSig1Rel]`
which requires the equalizer to process HT-SIG0 and HT-SIG1 — which
never happens in the file-replay path.

## Why File-Replay Still Fails

Same as Phases 86-122: **the equalizer chain silently aborts between
sync_long and HT-SIG viterbi**. No debug logs from the splitter or
equalizer. No `[SPLITTER_FRAME_START]`, no `[FRAME_EQ]` per-frame
logs, no `[HT_SIG_CAND]`.

This is independent of:
- USRP overflow/underflow (these are streaming artifacts, don't affect
  file-replay)
- File format (v2 is valid complex64, L-STF detectable)
- Sync_short/sync_long (294 detections in 15s)
- Phase 123 N value (never reached)

## Why This Is Not A Phase 123 Failure

Phase 123 is **mathematically correct** (compile OK, clean-signal
file-replay n_avg=1→2→3→4 fires correctly, FIFO accumulating). The
implementation works.

The **fundamental blocker** is upstream of Phase 123:
- sync_long produces wifi_start tag
- splitter should pass samples to equalizer
- equalizer should accumulate symbols and run HT-SIG viterbi
- HT-SIG viterbi is where the 1.77 rad per-SC noise ceiling matters

In this session, steps 3-4 do not happen. **No equalizer-layer
attack (Phase 35+) can succeed on USRP until the equalizer chain
runs.** This is exactly the verdict Phases 60-122 reached.

## Hardware Configuration Questions For User

1. **Cable connection**: Is the SMA cable actually connected between
   TX/RX port and RX2 port on A:0? The 0.0009 median variance (v2) is
   very low — suggests weak signal, possibly antenna ports not
   connected.
2. **UBX-160 LO tuning**: --uhd-tune makes things worse. Maybe the
   auto-calibration is actually needed for this session.
3. **Gain settings**: --tx-gain 10 may be too low. Phase 96 found
   --tx-gain 20 gives CLEAN constellation on this hardware.
4. **Frequency**: 5250 MHz cable is standard per Phase 82+. 5890 MHz
   is the original test freq per CLAUDE.md. Try 5890 first to rule
   out 5250-specific issues.

## Recommended Next Steps

1. **User verify hardware**: Check cable, antenna ports, gain settings.
2. **Phase 126: try 5890 MHz + --tx-gain 20** (CLAUDE.md standard
   config) on the now-reconnected USRP.
3. **Phase 127: pre-LSIG cross-frame tracking** — apply the
   cross-frame logic in the regular HT-SIG processing path (not the
   early-eqsym path), to help L-SIG viterbi succeed more often. This
   is a different code path and a more invasive change.

## Related

- [[project-p124-file-replay]] — Phase 124 file-replay script
- [[project-p123-cross-frame]] — Phase 123 implementation
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- Verdict: `docs/superpowers/notes/2026-07-09-phase125-usrp-file-replay-verdict.md`
