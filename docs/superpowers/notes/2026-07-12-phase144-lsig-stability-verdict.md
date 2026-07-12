# Phase 144 Verdict: L-SIG Stability Diagnosis — BLOCKED by Unstable USRP Analog State

**Date:** 2026-07-12  
**Branch:** TEST1  
**Status:** BLOCKED (hardware/analog instability)

---

## Goal

Restore stable L-SIG decoding on USRP so that HT-SIG attacks (including Phase 143 BPSK fallback) have a reliable upstream gate.

---

## What Was Tested

Parameter sweep across gain, buffer size, 2-way H52 enable/disable, and L-SIG rotation brute-force:

- `tx-gain ∈ {0, 10, 20}`
- `rx-gain ∈ {20, 25, 31.5}`
- `--uhd-tune` on/off
- `--phase139-on` on/off
- `IEEE80211_H52_2WAY_DEFAULT=0/1`
- `IEEE80211_LSIG_FINE_ROT=1` + `IEEE80211_LSIG_VITERBI_CANDIDATE=1`
- Larger kernel buffers (`wmem_max/rmem_max=33554432`) and larger UHD buffers
- Extended warmup (300 s)

---

## Key Results

| Config | Closest `lsig_len` to 45 | avg_snr_lsig | FCS_OK |
|---|---|---|---|
| L-SIG rotation, tx0/rx31.5, no phase139 | 41, 42, 44, 50 | 2.81 dB | 0 |
| L-SIG rotation, tx0/rx31.5, phase139 | garbage (64, 70 etc.) | 7.85 dB | 0 |
| L-SIG rotation, H52_2WAY=0, tx0/rx31.5 | 64, 70 | 4.31 dB | 0 |
| 5-min warmup, L-SIG rotation, tx0/rx31.5 | 361, 651, 1191 | **2.21 dB** | 0 |

- **No configuration produced a correct, stable `lsig_len = 45`.**
- The closest values (41–50) were observed once but **not reproducible** in repeat runs.
- `avg_snr_lsig` varied wildly across runs, from 2.21 dB to 77.09 dB, indicating run-to-run analog/hardware instability.
- TX underflows occur at ~1 Hz regardless of configuration.
- RX overflows range from 5 to 888 per 30 s run.

---

## Root-Cause Assessment

The dominant blocker is no longer algorithmic. It is **USRP analog/streaming instability**:

1. **Signal quality is too low and inconsistent**
   - Some runs show `avg_snr_lsig < 3 dB`, which is below the BPSK decoding threshold.
   - Run-to-run SNR variance of 30+ dB cannot be explained by algorithm choice.

2. **L-SIG length is systematically wrong**
   - Even when L-SIG is "accepted" (rate=0xD), the length field is garbage or off-by-several-bits.
   - This points to phase-rotation or sample-alignment issues upstream of the equalizer.

3. **Streaming errors persist**
   - Per-frame TX underflows and variable RX overflows indicate host↔USRP real-time delivery is unreliable.

4. **Warmup does not help**
   - 5-minute warmup produced `avg_snr_lsig=2.21 dB` and garbage lengths, ruling out simple LO warm-up.

---

## Conclusion

Phase 144 is **BLOCKED** by hardware/analog instability.

No equalizer or L-SIG algorithm tweak can produce FCS_OK when the USRP chain is delivering unstable, low-SNR samples. The next attack must be on the analog chain or UHD streaming layer.

---

## Recommended Next Steps

1. **Hardware inspection (highest priority)**
   - Check SMA cable between TX/RX and RX2 ports for looseness or damage.
   - Verify the bare cable is still connected (Phase 82+ default).
   - Try a different SMA cable.
   - Verify UBX-160 daughterboard seating.

2. **USRP/UHD health check**
   - `uhd_usrp_probe` to verify device identity and firmware version.
   - Check for firmware update mismatch between UHD host library and X310 image.
   - Verify 1 GigE link quality to `192.168.10.2`.

3. **Try alternative RF path**
   - Cross-board A:0 → B:0 RX2 (per CLAUDE.md it is weaker, but a sanity check).
   - Try a different frequency (5180 / 5500 / 5890 MHz).

4. **External 10 MHz reference clock**
   - User previously excluded, but Phase 142 found PLL could not lock. If the issue is LO drift, external ref clock may be the only fix.

5. **Once hardware is stable**, re-run:
   - `IEEE80211_LSIG_FINE_ROT=1 IEEE80211_LSIG_VITERBI_CANDIDATE=1 tx0/rx31.5 no phase139`
   - Verify `lsig_len=45` consistently.
   - Then re-evaluate Phase 143 BPSK-HT-SIG fallback and Phase 140 cross-frame FIFO.

---

## Relation to Phase 143

Phase 143 code is preserved as opt-in (`IEEE80211_HTSIG_BPSK_FALLBACK=1`, `--htsig-bpsk-fallback`). It cannot be validated for FCS_OK until the upstream L-SIG instability is resolved.
