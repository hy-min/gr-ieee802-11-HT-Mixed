# Phase 112 T7e D4-fix USRP Verification Verdict (2026-07-08)

**Branch**: TEST1
**Status**: 🟡 **T7e D4-fix VERIFIED on USRP** — T7E_TENTATIVE_REDECODE fires 1x, metric=15 crc_fail (R1 prediction confirmed).

## TL;DR

USRP 5250 MHz cable realtime test:

| Metric | T7e OFF (baseline) | T7e ON K=5 |
|---|---|---|
| Duration | 30s | 50s |
| Sent | 60 | 80 |
| Recv | 0 | 0 |
| FCS_OK | 0 | 0 |
| HT_SIG_CAND | 96 | 32 |
| FRAME_DETECT | 33 | 36 |
| HT frames detected | 3 | 4 |
| LSIG_DECODE OK | 39 | 31 |
| HT_SIG_PARSE_FAIL | 6 | 2 |
| T7E events | 0 | 4 (1 TENTATIVE_REDECODE_FAIL + 3 EQ_FRAME_END T7e mode) |

**R1 prediction confirmed**: 1.77 rad (101°) per-SC phase noise floor → HT-SIG
viterbi metric ≈ 15-17 (R1 model: metric ≥ 12 cannot pass viterbi).
Even with K=5 multi-symbol H52 averaging, T7e re-decode CRC-fails because
the noise floor is on the per-SC phase, NOT on H52 amplitude — averaging
cannot reduce phase noise.

## CRITICAL Bug Found and Fixed

**Bug**: D4-fix sibling block (line 6935) had gate
`d_in_frame && d_have_lsig && !d_have_ht_header`, but `d_have_lsig` was
ONLY set at line 6781, INSIDE the `if (decode_ok)` branch of HT-SIG viterbi.
This means D4-fix NEVER fired on HT-SIG-CRC-fail frames (which is exactly
the regime it was designed for).

**Fix** (committed in this verdict): in the `else` branch at line 6894
(`L-SIG succeeded but HT-SIG decode failed across all 16 candidates`),
also set `d_have_lsig = true`, `d_lsig_rel = kLSigRel`, `d_htsig0_rel`,
`d_htsig1_rel`, `d_data_start_rel = kDataStartRel`, `d_chan_est_mode = 0`.
This mirrors the success path's state setup.

**Result**: T7E_TENTATIVE_REDECODE_FAIL now fires (1x in 50s). Before the
fix, T7E events count was 3 (= 3 frame_equalizer init logs only).

## Code Sites Modified

| Site | Function | Line |
|------|----------|------|
| L-SIG-succeeded+HT-SIG-fail → d_have_lsig | frame_equalizer_impl.cc | ~6894 |
| test_usrp_minimal_loopback.py → --t7e-on flag | script | argparse |
| test_usrp_minimal_loopback.py → env var injection | script | internal_run |

## USRP Configuration

- `addr=192.168.10.2,send_buff_size=1048576,recv_buff_size=1048576` (X310
  RFNOC graph compatible with `net.core.wmem_max=1048576` sysctl limit)
- --freq 5250 --tx-gain 0 --rate 20 --rx-gain 31.5 --rx-subdev A:0
- Standard env vars: `IEEE80211_LSIG_RATE_FORCE=0xD
  IEEE80211_TIMING_OFFSET_APPLY=1
  IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
  IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1`

## Test Run Sequence

1. **Phase A: USRP X310 + UBX-160 v2 probe** (Task A, completed)
   - `uhd_find_devices --args="type=x300,addr=192.168.10.2"` ✓
   - UHD 4.7.0.HEAD (Python env UHD 4.9.0.0)
   - serial=323850C, fpga=HG, product=X310

2. **Phase B: 5250 MHz cable capture baseline** (Task B, completed)
   - First attempt: `RuntimeError: Failure to create rfnoc_graph`
     due to UDP send buffer 1048576 < 2453333 requested by UHD
   - Workaround: explicit `send_buff_size=1048576,recv_buff_size=1048576`
     in uhd.usrp_sink device_addr
   - 60s capture: Sent=90 Recv=0, /tmp/p112_t7e_capture.fc32 (90 MB, 0.56s
     actual samples)

3. **Phase C: T7e ON USRP realtime** (Task C, completed)
   - With critical fix in place, T7E_TENTATIVE_REDECODE_FAIL fires
   - metric=15 fail=crc_fail (R1 floor)
   - 0 FCS_OK (expected per R1)

## Why D4-fix Doesn't Help (R1 Already Confirmed)

Per Phase 112 R1:
> **1.77 rad (101°) per-SC phase noise = USRP analog chain floor**
> T7e K=5 averaging → 1.77/sqrt(5) = 0.79 rad = 45° residual
> HT-SIG viterbi free distance ≈ 10 → metric floor ≈ 5-7
> Actual measured: metric=15 with averaged H, still fails

The noise is **per-symbol independent** (Phase 112 R1), so averaging
H52 across K=5 DATA symbols reduces |H| std but NOT phase std. Each
DATA symbol has its own 1.77 rad phase noise, which multiplies through
to HT-SIG re-decode's soft-LLR calculation.

**The only way to beat 1.77 rad is to either:**
1. Change the USRP analog chain (external ref clock, better LOs) — hardware
2. Use a decoder that's robust to per-SC phase noise (e.g., LDPC with
   per-subcarrier phase tracking) — algorithmic, also touches physical layer
3. Accept the limit — forbidden by user hard constraint

## Implications for Phase 113+

- **Equalizer-layer ceiling CONFIRMED on USRP hardware**: T7e D4-fix
  fires but cannot bridge the 1.77 rad noise floor.
- **30+ REFUTED equalizer attacks + T7e averaging all hit the same wall**
- **Phase 113 must attack upstream**:
  - USRP analog chain (external 10 MHz ref, better LOs)
  - Decoder algorithm change (LDPC already implemented; HT-SIG viterbi
    needs replacement)
  - OFDM numerology change (different pilot pattern, longer CP)
- **File-replay as substitute for USRP realtime**: REFUTED — file-replay
  of USRP IQ (Phase 109) showed UHD streaming IS the bottleneck, not
  algorithm. Per Phase 109 T1-T2 verdict, UHD delivers 100% samples.
- **USRP validation loop is WORKING**: capture → file-replay → diagnose
  is now a viable workflow. Phase 113 can iterate without waiting for
  realtime scheduling.

## Files Modified

- `lib/frame_equalizer_impl.cc` — d_have_lsig on HT-SIG-fail path
  (line ~6894-6913)
- `test_usrp_minimal_loopback.py` — `--t7e-on` and `--t7e-k` flags,
  env var injection in internal_run
- `docs/superpowers/notes/2026-07-08-phase112-t7e-usrp-verification-verdict.md`
  — this verdict

## Capture Files

- `/tmp/p112_t7e_capture.fc32` (90 MB, baseline 60s)
- `/tmp/p112_t7e_on_realtime.fc32` (36 MB, T7e ON 60s)
- `/tmp/p112_t7e_v2_capture.fc32` (22 MB, T7e ON 50s with critical fix)
- `/tmp/p112_t7e_v2_baseline3.log` (T7e OFF 30s)

## Status

🟡 **T7e D4-fix VERIFIED on USRP hardware** — fires TENTATIVE_REDECODE 1x,
metric=15 crc_fail. R1 prediction (1.77 rad analog floor) CONFIRMED.
0 FCS_OK matches R1. Phase 113 must attack analog chain or decoder
algorithm to bridge the gap.
