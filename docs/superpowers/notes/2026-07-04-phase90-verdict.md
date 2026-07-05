# Phase 90 — 5250 Cable USRP Run Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: 🔴 **REGRESSION** — Phase 89 detector fix DEFeated by sync_short_fused energy gate;
avg_snr dropped 9.5 dB on USRP (14.61 → 5.06 dB); FCS_OK=0 still
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved

## Background

Phase 89 verdict claimed detector fix would route real L-STF through sync_short →
sync_long → equalizer pipeline. Phase 90 validates on USRP 5250 MHz cable (per Phase 81
SNR boost).

## T1 — USRP Verification

- USRP X310 reachable: `ping 192.168.10.2` 0% loss, 0.79ms avg
- `uhd_usrp_probe`: 2 radios (Radio#0 = A:0, Radio#1 = B:0)
- A:0 = UBX-160 v2 (rev 7), freq range 10 MHz - 6 GHz (good for 5250 MHz)
- LO sensor `lo_locked` available, internal TCXO 0.6 ppb

## T2 — Standard 5250 Cable Baseline (no Phase 89 env vars)

```
Sent: 90 | Recv: 0 | FCS_OK=0 | FCS_FAIL=0 | Success Rate: 0.0%
sync_short detections: 27 at corr=0.031-0.203 (NOISE spikes)
avg_snr=14.61 avg_snr_ht=15.09 is_ht_frame=1
LSIG_PARSE_FAIL: viterbi_fail (rate=-1, length=-1)
HT_SIG_CAND: 0 entries (viterbi never reached)
```

**Baseline observation**: sync_short fires on noise (per Phase 88 diagnosis), equalizer
sees noise → produces SNR ~14 dB but viterbi fails because input is noise not real L-SIG.
This matches Phase 82 finding.

## T3 — Phase 89 Detector (USE_BOXCAR=1, USE_ADAPTIVE=1)

```
Sent: 90 | Recv: 0 | FCS_OK=0 | FCS_FAIL=0 | Success Rate: 0.0%
sync_short detections: 40 at corr=0.076-1.399
avg_snr=5.06 avg_snr_ht=6.64 is_ht_frame=0
LSIG_PARSE_FAIL: viterbi_fail
HT_SIG_CAND: 0 entries
```

**REGRESSION**: avg_snr dropped from 14.61 → 5.06 dB (-9.5 dB). is_ht_frame=0 (worse).

## T3b — Phase 89 + DUMP Env Vars (debug)

Added `IEEE80211_SYNC_SHORT_FUSED_BOXCAR_DUMP=1` and
`IEEE80211_SYNC_SHORT_ADAPTIVE_DUMP=1` to confirm env var propagation.

Key dump lines:
```
[SYNC-SHORT-FUSED-BOXCAR] call=4 n=3908 batch_power=0.080880 max_out2=1.6123 min_out2=0.9283
[SYNC-SHORT-ADAPTIVE] filled=4096 median=0.000000 adaptive_thresh=0.010000
[SYNC-SHORT-ADAPTIVE] filled=4096 median=0.000000 adaptive_thresh=0.010000
...
```

**Smoking gun found**: USE_BOXCAR is ACTIVE (max_out2=1.6 at L-STF, noise ~0.13).
But adaptive median is **0.000000** for ALL adaptive calls. Adaptive threshold
collapses to `max(0*10, 0.01) = 0.010000` — the constructor argument.

## ROOT CAUSE — Energy Gate Defeats Adaptive Threshold

`sync_short_fused.cc` has an energy gate (factor=3.0) that outputs `out2=0` when
batch_power < d_noise_floor * 3.0. Looking at the dump:

```
[SYNC-SHORT-FUSED-BOXCAR] call=0 n=3908 batch_power=0.000000 max_out2=0.0000 min_out2=0.0000
[SYNC-SHORT-FUSED-BOXCAR] call=2 n=3908 batch_power=0.000000 max_out2=0.0000 min_out2=0.0000
```

When the energy gate fires, `out2[i]=0` for the entire batch. The adaptive threshold
in sync_short.cc tracks the median of `in_cor` (= out2 from sync_short_fused) over
4096 samples. With many zero batches in the window, the median collapses to 0.

`adaptive_thresh = max(median * 10, 0.01) = max(0 * 10, 0.01) = 0.01`

So the adaptive threshold is **FLOORED at 0.01** — the same as the constructor arg.
The adaptive logic is effectively disabled.

**This is why Phase 89 fix did not improve USRP performance.**

## What's Needed (Phase 91 attack plan)

To fix the energy gate defeating adaptive threshold:

1. **Option A — Don't gate out2**: Remove the `out2=0` line in sync_short_fused.cc
   when the energy gate fires. Output the actual boxcar value even during gated
   batches. Tradeoff: noise level rises, but adaptive threshold rises with it.

2. **Option B — Use max instead of median**: In sync_short.cc adaptive threshold,
   use `max(percentile_95 * 1.5, 0.01)` instead of median. Zeros stay below
   signal peak, but at least the threshold reflects noise level.

3. **Option C — Disable energy gate**: Set `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`.
   This removes the gating entirely. Then adaptive threshold should work correctly
   because median of (noise ≈ 0.13) * 10 = 1.3 — above noise.

4. **Option D — Compute noise separately**: Track noise floor (EMA) in
   sync_short_fused and emit it via a separate stream. Use that for adaptive
   threshold instead of in_cor.

**Recommended**: Option C (disable energy gate). Lowest risk, smallest change.
Already an existing env var `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`.

## HARD CONSTRAINT Status

- USRP realtime FCS_OK ≥ 1: **NOT achieved**
- Cable runs used: **1 of 5 budget** (this Phase 90 only)
- 0 HT_SIG_CAND, 0 L-SIG parses, 0 FCS_OK
- Phase 89 detector fix unblocks algorithm but is defeated on USRP by energy gate
- Phase 91 must address adaptive threshold interaction with energy gate

## Files of Record

- T1: `uhd_usrp_probe --args="addr=192.168.10.2"`
- T2: `/tmp/p90_baseline.log` (sent=90, recv=0)
- T3: `/tmp/p90_p89.log` (sent=90, recv=0, REGRESSION)
- T3b: `/tmp/p90_t3b.log` (sent=34+, recv=0, dump confirms USE_BOXCAR active)
- Implementation: `lib/sync_short_fused.cc`, `lib/sync_short.cc`

## Related

- Phase 89 verdict: `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- Phase 88 verdict: `docs/superpowers/notes/2026-07-04-phase88-verdict.md`
- Phase 81 verdict (cable @ 5250 = +5.7 dB): `docs/superpowers/notes/2026-07-04-p81-cable-verdict.md`
- Phase 82 verdict (δ-tuning REFUTED): `docs/superpowers/notes/2026-07-04-phase82-verdict.md`