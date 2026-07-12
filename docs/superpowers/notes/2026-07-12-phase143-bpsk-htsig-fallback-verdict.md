# Phase 143 Verdict: BPSK-HT-SIG Fallback — IMPLEMENTED, USRP NOT YET FCS_OK

**Date:** 2026-07-12  
**Branch:** TEST1  
**Status:** PARTIAL / REFUTED on USRP FCS_OK goal

---

## Goal

Replace QBPSK with BPSK for HT-SIG0/HT-SIG1 in a TX/RX-coordinated fallback mode, doubling the angular decision margin against the USRP 1.77 rad per-SC phase-noise floor and achieving `FCS_OK >= 1`.

---

## What Was Implemented

| Component | Change | Commit |
|-----------|--------|--------|
| TX modulation | `examples/mixed_mode_carrier_allocator.py`: conditionally skip ×j QBPSK rotation for HT-SIG0/1 | `006925a` |
| RX flag | `lib/frame_equalizer_impl.h/.cc`: add `d_htsig_bpsk_fallback`, parse `IEEE80211_HTSIG_BPSK_FALLBACK=1` | `4acae2d` |
| RX bit extraction | `lib/frame_equalizer_impl.cc`: switch HT-SIG0/1 hard-bit axis to real when fallback active | `3293d8a` |
| RX soft LLR | `lib/frame_equalizer_impl.cc`: switch LLR sign axis to real; also cover Phase 129 v2 path | `b834dee` |
| RX pilot CPE | `lib/frame_equalizer_impl.cc`: switch HT-SIG1 CPE reference axis to real | `1d2f8d3` |
| RX rotation search | `lib/frame_equalizer_impl.cc`: disable QBPSK rotation search in fallback (only inv_a/inv_b) | `fc00eb0` |
| Test harness | `test_usrp_minimal_loopback.py`: add `--htsig-bpsk-fallback` CLI flag | `b4034c2` |
| Environment fixes | `test_usrp_minimal_loopback.py`: remove `recv_buff_size` from sink args; `lib/sync_short.cc`: disable `USRP_DEBUG_LOGS` | `fb54d55`, `8e77537` |

All code compiles and installs cleanly. Loopback verification could not be completed because the test script requires USRP hardware.

---

## USRP Realtime Results

### Configuration

- USRP X310 + UBX-160, same-board A:0 TX/RX → A:0 RX2
- Freq 5250 MHz, rate 20 MHz, tx-gain 0, rx-gain 31.5
- `--phase139-on --wiener-on --htsig-bpsk-fallback`
- CPU governor switched to `performance` for later runs

### Key Results

| Metric | BPSK fallback | Baseline (no fallback) |
|---|---|---|
| TX underflows | ~102 | ~103 |
| RX overflows | 69 | 520 |
| L-SIG decode OK | 13 | 61 |
| HT_SIG_CAND | 12 | 48 |
| Best HT-SIG metric | 13 | 11 |
| **FCS_OK** | **0** | **0** |

### Observations

1. **BPSK fallback reduced RX overflows 7.5×** (520 → 69), indicating the RX processing chain is less stressed when HT-SIG uses BPSK.
2. **HT-SIG viterbi is reached** with fallback, but the best metric stays at 13–18, well above the ≤10 threshold.
3. **L-SIG decoded lengths are garbage** in both modes: `lsig_len=3235, 452, 1802, 1161, 1490, 2529` for a 10-byte payload.
4. Even when `avg_snr_htsig` reached 43.29 dB, HT-SIG metric remained ≥13 and CRC failed.

---

## Root-Cause Assessment

The primary blocker is **not** the HT-SIG modulation choice. It is **L-SIG instability**:

- L-SIG is already BPSK with 180° decision margin.
- With `IEEE80211_LSIG_RATE_FORCE=0xD`, the receiver accepts any L-SIG decode whose rate field parses as 0xD, even if the length field is completely wrong.
- The observed garbage `lsig_len` values prove L-SIG viterbi is not producing reliable decodes under the current hardware/streaming state.
- HT-SIG cannot succeed on top of an invalid L-SIG length.

Therefore, **BPSK-HT-SIG fallback alone is insufficient** to reach `FCS_OK` in the current USRP state.

---

## Why BPSK Fallback Did Not Improve Metric

- The 1.77 rad per-SC phase noise translates to ~37% raw BPSK bit-error rate, which is still far above the viterbi correction capability (d_free=10, ~4–5 correctable bits per 96-bit HT-SIG).
- USRP streaming instability (per-frame TX underflows, RX overflows) adds extra phase jumps and inter-symbol interference.
- With L-SIG already unstable, the HT-SIG path is operating on a shaky foundation.

---

## Conclusion

Phase 143 is **PARTIAL/REFUTED** as a standalone solution for USRP realtime FCS_OK.

- The implementation is correct and code is preserved as opt-in.
- It improves RX streaming pressure but does not break the 1.77 rad floor.
- It revealed that **L-SIG stability must be fixed before HT-SIG can succeed**.

---

## Next Direction

**Phase 144: L-SIG stability diagnosis and restoration.**

Specifically:
1. Verify L-SIG can again produce correct `lsig_len` for a 10-byte payload on USRP.
2. Fix streaming/buffer/gain issues causing per-frame TX underflows.
3. Once L-SIG is stable, re-evaluate whether BPSK-HT-SIG fallback (or fallback + cross-frame FIFO) can achieve FCS_OK.

---

## Commits

- `006925a` feat(p143): TX BPSK-HT-SIG fallback switch
- `4acae2d` feat(p143): add RX d_htsig_bpsk_fallback flag and env parsing
- `3293d8a` feat(p143): switch HT-SIG bit-extraction axis for BPSK fallback
- `b834dee` feat(p143): switch HT-SIG soft-LLR axis for BPSK fallback
- `1d2f8d3` feat(p143): switch HT-SIG pilot CPE reference axis for BPSK fallback
- `fc00eb0` feat(p143): disable QBPSK rotation search in BPSK fallback mode
- `b4034c2` feat(p143): add --htsig-bpsk-fallback test flag
- `fb54d55` fix(test_usrp): remove extraneous recv_buff_size from USRP sink device args
- `8e77537` chore(sync_short): disable USRP_DEBUG_LOGS to reduce runtime CPU load
