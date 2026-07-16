# Phase 150: Realtime USRP FCS_OK Path — SOLIDIFIED (防回退)

**Date:** 2026-07-16
**Branch:** TEST1
**Status:** ✅ Working realtime path solidified into a reproducible, regression-gated,
reboot-persistent baseline. Best result this session: **DECODE_SUCCESS=55/45s, arrival 12.2%, 0 underflow/overflow.**

---

## What "这条路径" is (the solidified working config)

RX-only decode chain (no idle-TX scheduler stall) + 145c winning decoder config +
underflow fix. One command reproduces it:

```bash
./usrp_realtime_validate.sh            # ~65s, prints funnel + PASS/FAIL
```

- **Harness:** `test_usrp_rxonly_instrumented.py` (Phase 147), driven by the script.
- **Decoder env (145c winning):** `IEEE80211_LSIG_RATE_FORCE=0xD`, `IEEE80211_TIMING_OFFSET_APPLY=1`,
  `IEEE80211_HDR_COMP_DISABLE=1`, `IEEE80211_H52_2WAY_DEFAULT=0`,
  `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1`, `IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1`
  (baked into the harness via `os.environ.setdefault`).
- **RF config (antenna / air path):** `freq=5250 tx-gain=0 rx-gain=31.5 rx-scale=40 interval=100ms`.
  ~10 strobe frames/s; est_sent ≈ 150 per 15 s window.

## Regression gate

- **Metric:** ground-truth C++ stderr `DECODE_SUCCESS` (NOT the PDU message queue, which undercounts).
- **Default threshold:** `DECODE_SUCCESS >= 15` across 45 s → PASS. Tuned to catch a *broken*
  path (near 0) while tolerating run-to-run variance (observed 37–55). Override: `--threshold N`.

## System-layer underflow fix (PERSISTED across reboot)

The TX underflow fix is now reboot-persistent (machine reboots had been wiping it):

| Fix | Mechanism | File / service |
|---|---|---|
| UHD socket buffers 1MB→2.4MB | `/etc/sysctl.d/99-gr-ieee80211-uhd.conf` (applies AFTER `/usr/lib/sysctl.d/50-uhd-usrp2.conf`, so it wins) | `net.core.wmem_max=2453333`, `net.core.rmem_max=2453333` |
| CPU governor powersave→performance | systemd oneshot | `gr-cpu-performance.service` (enabled) |

Effect: **TX underflow 0, RX overflow 0** in the validated run. (Note: eliminating underflow did
NOT change arrival — underflow was cosmetic, hitting inter-frame gaps, not frames.)

## Verified result (this session)

```
DECODE_SUCCESS = 55 (ground truth)   arrival = 55/450 = 12.2%
PDU FCS_OK = 35   underflow = 0   overflow = 0   ->  PASS (>=15)
```
(Best prior: Phase 147 = 46/45s. Variance across 45 s runs ≈ 37–55.)

## The honest ceiling (from the Phase 150 systematic investigation)

The realtime path WORKS and is now regression-protected. The rate is capped by the
**H52 1.77 rad per-SC phase-noise wall** (UBX-160 internal LO/PLL). Confirmed this session
on a clean ground-truth antenna testbed (100 known frames) with a statistical ruler (N-run mean±std):

| Software lever | decoded (arrival) | verdict |
|---|---|---|
| baseline (145c) | 4.50 | reference |
| 2-way H52 (P139) | 5.5 | best, marginal +22% |
| Wiener (P141) | 0.12 | hurts |
| cross-frame (P140/127) | 1.44 | hurts |

Streaming fix (governor/buffers): underflow→0 but arrival unchanged.
**Conclusion: the H52 wall is software/streaming-unbreakable. The only real lever to raise the
rate is an external 10 MHz reference clock / GPSDO (reduces LO phase noise) — currently unavailable.**

## Files (this solidification)

- `usrp_realtime_validate.sh` — one-command validation + regression gate.
- `/etc/sysctl.d/99-gr-ieee80211-uhd.conf`, `gr-cpu-performance.service` — persisted underflow fix.
- Offline statistical-ruler tooling: `p148_parse.py`, `p148_stats.py`, `p148_funnel.py`,
  `p148_determinism_test.py`, `p150_count_frames.py`.

## Related

- Phase 147 (realtime segfault fix): `2026-07-15-phase147-sync-short-race-fix-verdict.md`
- Phase 148 (trustworthy funnel root-cause): `2026-07-15-phase148-trustworthy-funnel-verdict.md`
