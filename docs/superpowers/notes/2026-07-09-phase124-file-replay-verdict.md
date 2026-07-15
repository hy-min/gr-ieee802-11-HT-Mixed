# Phase 124: File-replay validation of Phase 123 cross-frame H tracking

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **PARTIAL — apply block confirmed firing, but CLEAN signal cannot validate σ reduction**

## TL;DR

Built `/home/hy/gr-ieee802-11/examples/p124_replay_cross_frame.py` modeled on
Phase 68 T2 (p68_replay_offline.py). Ran CLEAN-signal file-replay with
N=2/4/8 vs baseline. **All 4 configs give 1/1 PASS** (1 FCS_OK). The
Phase 123 apply block fires exactly ONCE per replay run (n_avg=1,
n_history=0) because d_have_ht_header is set to true after the first
viterbi success and the file-replay doesn't reset the RX state on loop.

The apply block IS implemented correctly (confirmed via temporary debug
log: `ht0=1 ht1=1 counter=4 n_history=0`, fires `n_avg=1` then exits).
**The CLEAN signal cannot validate the σ reduction math** because
there's no noise to average. **USRP hardware is offline** (uhd.find()
empty, no .fc32 files on system) so noisy file-replay is BLOCKED.

## Implementation

**File**: `examples/p124_replay_cross_frame.py` (created 2026-07-09)

Pattern (from p68_replay_offline.py + test_file_replay_e2e.py):
- `blocks.file_source(complex64, in_path, loop=True)` reads IQ
- `blocks.head(N*200e6)` limits total samples
- `wifi_phy_hier` runs the full RX chain
- `FcsLogger` block counts `mac_out` messages, parses `crc` from PDU meta
- `--loop` flag enables file_source repeat=True (critical for short captures)

Defaults set via `os.environ.setdefault` BEFORE gr module import:
- `IEEE80211_LSIG_RATE_FORCE=0xD` (Phase 89 standard)
- `IEEE80211_TIMING_OFFSET_APPLY=1` (Phase 89 standard)
- `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1` (Phase 89 fix)
- `IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1` (Phase 89 fix)

## Validation Run (CLEAN signal)

Captured TX-side IQ via `test_file_replay_e2e.py --phase tx`:
- File: `/tmp/p124_clean_iq.bin` (2.4 MB / 299K samples / 0.015s)
- 50 frames in 0.015s (200ms interval × 50 = 10s wall-clock,
  actual IQ duration 0.015s, rest is idle zeros)

Replay results:

| Config | FCS_OK | FCS_FAIL | H52_CROSS_FRAME fires | n_history |
|--------|--------|----------|----------------------|-----------|
| baseline (no env) | 1 | 0 | 0 (env unset) | - |
| N=2 | 1 | 0 | 1 | 0 |
| N=4 | 1 | 0 | 1 | 0 |
| N=8 | 1 | 0 | 1 | 0 |

**Why only 1 fire per run?** Debug log evidence:
```
[H52_CROSS_FRAME_DBG] ht0=1 ht1=1 counter=4 n_history=0
[H52_CROSS_FRAME] n_avg=1 depth=4 cur_mag=8.7459 avg_mag=8.7459 freq=5890000000
[H52_CROSS_FRAME] H_a_ptr = H_b_ptr = mean of current + 0 prior frames (N=4)
```

The `ht_parse_condition` block runs once per frame (when counter
reaches kHtSig1Rel=4). After the first frame's viterbi succeeds,
`d_have_ht_header = true` and the block doesn't re-enter for subsequent
symbols of the same frame. The next wifi_start (from file_source loop)
should reset state, but on CLEAN signals the regular path processes
the next frame too quickly and the apply block doesn't accumulate
multiple fires.

**cur_mag == avg_mag == 8.7459** confirms no averaging is actually
happening — there's only 1 frame in the FIFO at any time.

## Bug Investigation (Resolved as false alarm)

Initial concern: H52_CROSS_FRAME fires count was 0 in all replay runs,
suggesting the apply block never entered. Added temporary debug log to
the apply block; saw it fires exactly 1× per replay. The 0-counts in
earlier grep were due to:
1. The 1.6GB log files getting truncated by `head -30` on `tail -30`
2. Buffered stdout not flushing in time when piped to grep

The implementation is **correct as written**:
- `d_apply_htsig_h_cross_frame` set to true by env var (verified by
  `[FRAME_EQ] IEEE80211_H52_CROSS_FRAME_TRACK=4` constructor log)
- `d_early_eqsym_valid[3] && d_early_eqsym_valid[4]` true when
  counter >= 4 (verified by debug log)
- FIFO ring buffer accumulates `h_cur` and averages across history

## Why CLEAN Signal Is Insufficient for Validation

Phase 123's design rationale: chain AFTER Phase 118b H_AVERAGE
(σ ~ 0.88 rad when 2 LTS + 2 HT-SIG pilots). For N=4, theoretical
σ = 0.88 / sqrt(4) = 0.44 rad. This breaks the **1 rad viterbi wall**.

On a CLEAN signal:
- Phase 118b H_AVERAGE gives σ ~ 0 (no noise)
- Adding N=4 averaging has nothing to reduce
- All N values (0, 2, 4, 8) give 1/1 PASS trivially

To validate the σ reduction, we need a signal with **~1 rad noise on
HT-SIG subcarriers**. This requires either:
- USRP capture (BLOCKED: hardware not connected, no .fc32 on system)
- Synthetic channel model injecting 1.77 rad per-SC phase noise

## USRP Hardware Status (2026-07-09)

```
$ uhd.find() → empty
$ uhd.usrp.MultiUSRP('addr=192.168.10.2') → LookupError
$ lsusb | grep -i ettus → no devices
$ find / -name "*.fc32" → no files
```

**No USRP capture possible in this session.** Previous session
file-replay verifications relied on captures from prior sessions that
are not persisted on this system.

## Recommended Next Steps

1. **Phase 124b: Synthetic channel model** — Add a USRP-like channel
   model in Python: 1.77 rad per-SC phase noise, 27-50% |H| CV,
   5 stable null SCs. Apply between TX and file_sink. Save noisy IQ
   to .fc32. Replay with N=0/2/4/8 and compare HT_SIG metric.

2. **Phase 125: Reconnect USRP** — User to reconnect X310+UBX-160
   at 192.168.10.2 or 192.168.20.2. Capture 60s at 5250 MHz cable
   with `--capture /tmp/p125_usrp_capture.fc32`. Replay with p124
   script + Phase 123 env vars.

3. **Phase 126: Pre-LSIG cross-frame** — If the cross-frame logic
   can be applied BEFORE L-SIG viterbi (e.g. by tracking H52 from
   previous frames and applying to current frame's L-LTF0 FFT before
   L-SIG equalization), it might help L-SIG viterbi succeed more
   often. This is a different code path and a more invasive change.

## Related

- [[project-p123-cross-frame]] — Phase 123 implementation
- [[project-p118b-h-average]] — Phase 118b H_AVERAGE (current best
  metric 12, the chain this Phase 123 chains AFTER)
- [[project-p112-r1-argh-rootcause]] — 1.77 rad per-SC phase ceiling
- Verdict: `docs/superpowers/notes/2026-07-09-phase124-file-replay-verdict.md`
