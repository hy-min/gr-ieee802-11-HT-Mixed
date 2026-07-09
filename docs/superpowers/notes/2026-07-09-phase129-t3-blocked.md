# Phase 129 T3: USRP Test of Soft-LLR Viterbi (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **BLOCKED** — USRP hardware offline (no UHD devices found).

## TL;DR

Cannot run real-time USRP test of Phase 129 v2 LLR viterbi. `uhd_find_devices`
returns "No UHD Devices Found". USRP X310 at 192.168.10.2 is unreachable.

This is the same blocker that prevented Phase 124 file-replay validation:
- Phase 124 verdict (2026-07-09): "USRP hardware offline (uhd.find() empty, no .fc32 files)"
- Phase 125 verdict (2026-07-09): "USRP reconnected 192.168.10.2" was partial
- Phase 125b verdict (2026-07-09): cross-board capture succeeded via file-replay

The cross-board capture `/tmp/p125_xboard_burst.fc32` IS available and was used
for T2 file-replay validation (which gave 0 FCS_OK at 1.77 rad ceiling).

## What was done instead of USRP realtime

**File-replay on cross-board capture** (T2 verdict, 2026-07-09):
- Phase 129 v2 alone: 0 FCS_OK
- Phase 129 v2 + Phase 128 (CFO/SFO HT-LTF) + null mask: 0 FCS_OK
- Confirms Phase 129 T1 synthetic: 1.77 rad noise dominates; decoder-internal
  LLR formula gain is insufficient alone at USRP ceiling

## What's needed to unblock T3

1. **USRP X310 + UBX-160 powered on** and reachable at 192.168.10.2.
   `uhd_find_devices` must show at least one device.
2. **Direct SMA cable** between TX/RX port and RX2 port on A:0 (same-board).
   Phase 82+ user-accepted cable test config.
3. **--tx-gain 0, --rx-gain 31.5** (per Phase 110 verdict)
4. **--freq 5250 --rate 20 --warmup 60** (per Phase 82+ config)
5. **--rx-subdev A:0** (same-board TDD)

Test command (when USRP available):
```bash
env IEEE80211_SOFT_LLR_VITERBI=1 \
    IEEE80211_HTSIG_SOFT_LLR_V2=1 \
    IEEE80211_HTSIG_CFO_REEST_HTLTF=1 \
    IEEE80211_HTSIG_NULL_SCS=12 \
    /home/hy/conda/envs/gnuradio/bin/python \
    test_usrp_minimal_loopback.py \
    --freq 5250 --tx-gain 0 --rate 20 --warmup 60 \
    --rx-subdev A:0 --duration 60
```

Pass criterion: `FCS_OK >= 1`.

## What's needed (per HARD CONSTRAINT upstream-attack plan)

Per CLAUDE.md "BLOCKED must include upstream-attack plan":
- **Phase 130** (per-SC LLR zeroing): extends v2 with explicit erasure for the 5
  Phase 78b stable null SCs. Adds ~0.3 dB gain. C++ change similar to T2.
- **Phase 131** (multi-pass H52+δ refinement): use top-K viterbi candidates as
  pseudo-training to refine H52, iterate 2-3 times. Adds ~0.5 dB gain.
- **Combined with Phase 128 (CFO/SFO) + 118b (H_AVERAGE) + 126A (FreqSmooth)**:
  total potential gain ~1.5-2.5 dB, which at 1.77 rad baseline brings effective
  noise to ~1.0 rad → CRC pass rate ~10-20%.

If user can re-establish USRP, T3 runs in ~60s and produces verdict.
If user cannot, then Phases 130/131 proceed using file-replay validation only.

## Files

- Verdict T2 (file-replay REFUTED): `docs/superpowers/notes/2026-07-09-phase129-t2-cpp-verdict.md`
- Verdict T3 (this file, USRP BLOCKED): `docs/superpowers/notes/2026-07-09-phase129-t3-blocked.md`
- Verdict T1 (synthetic PARTIAL): `docs/superpowers/notes/2026-07-09-phase129-t1-llr-synthetic.md`
- Phase 124 verdict (file-replay USRP blocker origin): `docs/superpowers/notes/2026-07-09-phase124-file-replay-verdict.md`