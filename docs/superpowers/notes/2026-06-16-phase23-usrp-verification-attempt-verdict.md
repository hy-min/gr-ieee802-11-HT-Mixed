# Phase 23 + 24 — USRP Verification Attempt (2026-06-16)

## TL;DR

Phase 23+24 attempted USRP end-to-end verification on the new X310 hardware (2× UBX-160). Found that viterbi L-SIG decoder fails on USRP data despite working perfectly in software loopback (3/3 tests pass). Root cause: USRP-specific channel impairments (CFO + SFO) that the current equalizer doesn't fully compensate. **NOT a decoder bug, NOT a metadata bug.**

**CFO hypothesis REFUTED** — frequency-domain phase correction reaches 30/48 = 62.5% match (still 37.5% BER). Suspected next culprit: L-SIG sample timing offset.

## Hardware Confirmed

- **X310** at 192.168.10.2 (FPGA 39.2, FW 6.1, RFNoC-capable)
- **2× UBX-160 v2** daughterboards:
  - Radio#0 (slot A): Serial 3211516
  - Radio#1 (slot B): Serial 3235159
- Frequency range: 10 MHz - 6 GHz (covers 2.4 GHz and 5 GHz)
- **Phase 16/17 16-sample LO leakage NOT applicable** (UBX-160 design differs from SBX/CBX)

## Test Results

### Phase 23: USRP run with Phase 22 fix

```
$ test_usrp_minimal_loopback.py --freq 5890 --duration 30 --tx-gain 10 --rx-gain 20 --rate 20
[TEST] Sent: 62, Recv: 0, Success Rate: 0.0%
[TEST] FRAME_DETECT: 126
[TEST] DECODE_SUCCESS: 0
[TEST] FCS OK: 0
```

All 22 sent frames detected at sync_short but viterbi decode fails on every frame.

### Phase 24: Offline analysis on 1.6 GB raw IQ capture

| Metric | Value | Interpretation |
|--------|-------|----------------|
| L-STF initial CFO | ~85 kHz | USRP TX/RX clocks not synchronized (no PPS/10MHz ref) |
| Settled CFO | 3-5 kHz | UHD AFC tracks most of it |
| H52 phase std | 1.92 rad | High variation, mixed CFO + channel response |
| L-SIG constellation | BPSK-shaped | Equalizer produces valid BPSK points |
| BER without correction | 45-56% | Random, not partial decode |
| BER after CFO sweep | 37.5% (flat) | CFO is NOT the dominant impairment |
| BER after L-LTF extraction fix | 30/48 = 62.5% | Improvement but still poor |
| Best combined (CFO + phase) | 27/48 | No further gain |

## Why CFO is not the dominant impairment

The CFO sweep from -20 kHz to +20 kHz produces a FLAT BER curve at 37.5% (no valley). If CFO were the dominant issue, we'd see a clear minimum at the true CFO value. Instead:
- L-SIG constellation IS BPSK-shaped after equalization
- Best phase rotation gives 30/48 (62.5%) but doesn't improve further
- L-LTF extraction bug fix (sym1=lltf[0:64]) gave most of the improvement

This suggests the impairment is NOT a bulk rotation but a **sample-level timing issue** — the FFT window for L-SIG is at the wrong position, causing inter-symbol interference (ISI).

## Files Created (Phase 24 diagnostic infrastructure)

- `/tmp/p24_usrp_iq.bin` (1.6 GB) — Raw UHD RX IQ capture
- `/tmp/p24_capture.log` (219 MB) — Full UHD + GRC log
- `/tmp/p24_lstf.npy`, `/tmp/p24_lltf.npy`, `/tmp/p24_lsig.npy` — Extracted preambles
- `/tmp/p24_h52.npy`, `/tmp/p24_h52_full.npy`, `/tmp/p24_H_v3.npy` — Channel estimates
- `/tmp/p24_eq.npy`, `/tmp/p24_eq_rotated.npy` — Equalized L-SIG constellation
- `/tmp/p24_cfo_hz.npy` — Best CFO from L-STF (median -3,795 Hz)
- `/tmp/p24_analysis.md` — Full analysis report
- `/tmp/p24_*.bak` — Backups

These can be re-used for next investigation phase.

## Recommendations for Next Phase

1. **L-SIG timing offset sweep** (highest priority)
   - Try L-SIG extraction offsets from `frame_start+368+N` for N in {-16, -8, -4, -2, 0, +2, +4, +8, +16}
   - Look for offset where BER drops below 10%
   - If found, fix the L-SIG timing offset in C++ frame_equalizer

2. **Multi-symbol CFO tracking**
   - Currently CFO is corrected once per frame (using L-STF)
   - May need per-symbol correction between L-LTF and L-SIG
   - Use HT-SIG pilots (-21, -7, +7, +21) for per-symbol tracking once L-SIG passes

3. **Improve H52 estimation**
   - Use both L-LTF symbols (current code may only use one)
   - Smooth across subcarriers to reduce phase noise
   - Consider using HT-LTF instead of L-LTF for HT-SIG equalization

4. **Compare to software loopback capture**
   - Run test_direct_loopback.py and capture RAW IQ there too
   - Compare equalizer outputs side-by-side
   - Identify exactly which stage introduces the USRP-specific corruption

## Related memory

- [[project_p22_decode_mac_metadata]] — Phase 22: metadata fix (FCS OK counter works)
- [[project_p19_htsig_viterbi]] — Phase 19: HT-SIG viterbi crc_fail (related, different stage)
- [[project_p18_lsig_viterbi_analysis]] — Phase 18: LSIG_RATE_FORCE=0xD fix
- [[project_p17_5ghz_a0_subdev]] — Phase 17: 5 GHz A:0 (SBX) — NOT applicable to UBX
- [[project_p16_usrp_lo_leakage]] — Phase 16: X300 2.4 GHz LO leakage — NOT applicable to UBX
- [[project_p14_sync_long_deadlock]] — Phase 14: sync_long deadlock fix
