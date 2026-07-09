# Phase 134: USRP Validation of Phase 132 + Phase 133

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🟡 **MIXED** — USRP pipeline reaches **L-SIG viterbi**
(`LSIG_DECODE OK enc=0 len=472`) but 0 FCS_OK across all 3 conditions. CRITICAL
finding: **Phase 133 wifi_start fast-path BYPASSES the multi-feature gate**.

## TL;DR

User confirmed USRP X310 connected at 192.168.10.2 (serial 323850C). Three
30s validation runs at 5250 MHz cable config:

| Run | Env vars | Sent | Recv | LSIG_DECODE | HT_SIG_CAND | FCS_OK |
|-----|----------|------|------|-------------|-------------|--------|
| T1 (baseline) | --uhd-tune OFF (default Phase 89) | 60 | 0 | yes | metric=14, crc_fail | 0 |
| T2 (+ P132 sync_short) | + IEEE80211_SYNC_SHORT_FUSED_SCHMIDL_COX=1 | 60 | 0 | yes | few/no fires | 0 |
| T3 (+ P132+P133, --tx-gain 20) | + IEEE80211_SYNC_LONG_SCHMIDL_COX=1 (threshold=0.02) | 60 | 0 | yes | few/no fires | 0 |
| T3b (+ P132+P133, --tx-gain 0) | same as T3 with --tx-gain 0 per CLAUDE.md | 60 | 0 | yes | few/no fires | 0 |

## What Was Verified

1. **USRP hardware reachable**: `uhd_find_devices --args="addr=192.168.10.2"`
   returned X310 + serial 323850C
2. **Pipeline reaches L-SIG viterbi** (T1 baseline): `LSIG_DECODE OK enc=0 len=472`
   confirms sync_short → sync_long → frame_equalizer → L-SIG chain is intact
3. **HT-SIG viterbi still fails** (T1 baseline): `HT_SIG_CAND sym=6 rot=0
   inv_a=0 inv_b=0 metric=14 fail=crc_fail` confirms Phase 112 R1 1.77 rad
   ceiling is still the downstream blocker
4. **P132 doesn't break pipeline** (T2): sync_short still fires ("Frame detected"
   on first call), no crashes
5. **P133 doesn't break pipeline** (T3, T3b): sync_long processes wifi_start tags,
   no crashes, gates never fire (bypass issue)

## CRITICAL: Phase 133 wifi_start fast-path BYPASSES P133 gate

`lib/sync_long.cc` lines 173-186:

```cpp
if (d_state == SYNC && tag_key == "wifi_start") {
    d_freq_offset_short = pmt::to_double(d_tags.front().value);
    d_freq_offset = static_cast<float>(d_freq_offset_short);
    d_tag_skip_count = SYNC_LENGTH;
    d_sync_samples = SYNC_LENGTH;
    d_frame_start = FRAME_START_BASE + get_frame_start_offset();
    d_state = COPY;       // <-- direct SYNC->COPY
    d_offset = 0;
    d_count = 0;
    ...
}
```

When sync_short emits a `wifi_start` tag during SYNC state, sync_long
directly enters COPY state — **`search_frame_start()` never runs, so the
Phase 133 multi-feature gate is bypassed**.

This was originally added in Phase 14 (continuous USRP streaming) and
Phase 31b (atomic input dump).

In USRP continuous streaming mode (which is the production use case),
wifi_start tags arrive frequently enough that the fast-path dominates,
making the P133 gate a no-op.

## File-Replay vs USRP Behavioral Asymmetry

| Path | fast-path bypasses gate? | Phase 133 impact |
|------|--------------------------|------------------|
| **File-replay** (`test_file_replay_e2e.py`) | No (no wifi_start tags) | Gate fires, rejects noise false-positives |
| **USRP continuous** (`test_usrp_minimal_loopback.py`) | **YES** | Gate never runs, all P133 logic inert |

The Phase 134 verdict tests file-replay-positive results do NOT
extrapolate to USRP. This is a real architectural gap.

## What's Next?

### Phase 135 (proposed): Apply P133 gate to wifi_start fast-path

The cleanest fix: when wifi_start tag arrives during SYNC, still RUN
the SYNC state's correlation search to validate via multi-feature gate.
Only allow SYNC->COPY transition if gate passes.

Implementation:
- Treat the wifi_start tag as a SOFT trigger, not immediate COPY
- Run the search logic anyway (start SYNC state correlation search)
- Add P133 gate as additional validation before the search yields candidate

Or alternatively:
- Remove fast-path entirely (force search-path always)
- Accept performance cost in continuous streaming

### Phase 136 (proposed): Channel-aware P133 threshold

T4 shows Schmidl-Cox on USRP noise = 0.001-0.04 (well below 0.05 threshold).
This may be because USRP phase noise (1.77 rad from RF chain) destroys
the coherent phase between two L-LTF halves.

If we use the lag-80 autocorr sqrt scale (raw correlation at lag=80),
or larger integration window, may extract more signal from the noise.

But this might not actually help — 1.77 rad phase noise is fundamental.

## Files

- Implementation: `lib/sync_long.cc` (Phase 133 commit b45c15f)
- Test logs: `/tmp/p134_t1_baseline.log`, `/tmp/p134_t2_p132.log`,
  `/tmp/p134_t3_p132_p133.log`, `/tmp/p134_t3b_txgain0.log`
- USRP connectivity: 192.168.10.2, serial 323850C, X310
- CLAUDE.md: --freq 5250 cable config reference
- Related: [[project-p132-schmidl-cox]], [[project-p133-sync-long-multi-feature]],
  [[project-p112-r1-argh-rootcause]]
