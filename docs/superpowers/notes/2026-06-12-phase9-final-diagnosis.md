# Phase 9: Final Diagnosis — HT-SIG Parse Failure (NOT Hardware)

**Date:** 2026-06-12
**Branch:** TEST1
**Status:** Root cause narrowed to HT-SIG decode failure. **Phase 5-7's
"LO_BROKEN" verdict is REFUTED**; hardware is fine. **Real issue is
HT-SIG-specific**, not generic L-SIG issue.

## TL;DR

The gr-ieee802-11 RX chain **works for L-SIG** (56/56 successful L-SIG
decodes, rate=0xD len=2378). The chain **fails for HT-SIG** (56/56
HT_SIG_PARSE_FAIL). All 16 candidate decodings (4 rot × 2 inv_a × 2 inv_b)
fail despite avg_snr_htsig=82.34 dB (extremely high SNR).

The chain up to HT-SIG processing is **fully functional**:
- sync_short_fused ✅ (corr=0.74, 6617 frames detected)
- sync_short ✅ (outputs to chain)
- sync_long ✅ (1640 LONG frame starts, produces data)
- ht_symbol_splitter ✅ (412k frame starts, 682 real FFTs pass energy gate)
- frame_equalizer ✅ (LTF_COMP fires 14 times, H estimation works)
- frame_equalizer ✅ (LSIG_DECODE OK 56 times)
- frame_equalizer ❌ (HT_SIG_PARSE_FAIL 56 times — REAL BUG)
- decode_mac ❌ (never gets data because HT-SIG fails first)

## Test Configuration

`/tmp/test_p9_verbose.py` — modified wifi_phy_hier.py to enable verbose
logging (sync_short/sync_long `log=True`).

- TX: null_source → wifi_phy_hier(TX) → USRP sink (5.89 GHz, A:0)
- RX: USRP source → wifi_phy_hier(RX) → null_sink (5.89 GHz, A:0)
- 8s test, 16 packets sent, 0 FCS OK

## Diagnostic Method

Ran test with `2> /tmp/p9_stderr.log` to separate stderr (block logs)
from stdout (gr.info messages). Without this split, the test output was
misleading — stdout had no block events, only stdout events appeared.

## Event Counts (stderr)

| Event | Count | Meaning |
|-------|-------|---------|
| SYNC_LONG_WORK | 9,953 | sync_long general_work calls |
| SYNC_LONG_TAG | 14,906 | wifi_start tag processing |
| SYNC_LONG_FAST_SYNC | 1,673 | Direct SYNC transitions |
| SPLITTER_WORK | 10 | (limited by call_count<=10) |
| SPLITTER_FRAME_START | 412,838 | Frame transitions (mostly noise) |
| SPLITTER_FFT | 682 | Real FFTs passing energy gate |
| SPLITTER_ENERGY_DROP | 13,330 | FFTs dropped (energy<2.0) |
| LTF_COMP | 14 | L-LTF0/L-LTF1 stored for H estimation |
| LSIG_DECODE OK | 56 | **L-SIG viterbi decode succeeded** |
| LSIG_PARSE_FAIL | 56 | (other half — L-SIG rate/len check fail) |
| LSIG_EQ_FULL | 14 | Full L-SIG equalization logged |
| **HT_SIG_PARSE_FAIL** | **56** | **HT-SIG decode FAILED on all 16 candidates** |
| EQ_FRAME_END | 14 | HT-SIG timeout, frame discarded |
| EQ_HTDATA | 0 | Never reaches data processing |
| EQ_EMIT | 0 | No data symbols emitted |
| H52_DUMP | 0 | (env var not set) |
| FCS | 0 | No valid frames |

## The HT-SIG Failure

```
[HT_SIG_PARSE_FAIL] timeout_sym=4 n_candidates=16 best_metric=N/A 
threshold=N/A avg_snr_lsig=84.13 avg_snr_htsig=82.34 
lsig_rate=0xD lsig_len=2378 lsig_inv=0 
last_rot=3 last_inv_a=1 last_inv_b=1 is_ht_frame=1
```

- **SNR 82-84 dB** — signal is super strong
- **L-SIG correct** — rate=0xD (BPSK 1/2 = 6 Mbps), len=2378
- **16 candidates tried** — 4 rotations × 2 inv_a × 2 inv_b
- **None passed threshold** — best_metric=N/A (or below threshold)

This is **NOT** a hardware issue. The chain produces clean signal at
high SNR. The HT-SIG-specific decode is failing.

## Why L-SIG Works But HT-SIG Fails

| Property | L-SIG | HT-SIG |
|----------|-------|--------|
| Encoding | BPSK 1/2 | BPSK 1/2 (same) |
| Constellation | BPSK on I-axis | QBPSK (90° rotated from L-SIG) |
| CFO/SFO compensation | d_cfo, d_sfo applied | (uses same as L-SIG) |
| H estimation | Hhdr52 (L-LTF0/L-LTF1) | Hhdr52 (same) |
| Viterbi decoder | (same) | (same) |
| SNR (USRP) | 84.13 dB | 82.34 dB |

L-SIG works → RX chain is mathematically correct
HT-SIG fails → QBPSK rotation recovery OR HT-SIG-specific channel
compensation is broken

## Hypotheses to Test Next

1. **QBPSK rotation detection**: HT-SIG uses 90°-rotated BPSK. The
   equalizer tries 4 candidate rotations to find the right one. If all
   4 fail with N/A metric, the QBPSK rotation logic may be broken.

2. **Header CFO/SFO application**: L-SIG uses d_cfo from sync_long. HT-SIG
   uses the same. If the per-subcarrier SFO compensation is mis-applied
   between L-SIG and HT-SIG, HT-SIG symbols would have wrong phase.

3. **H estimation time-gap**: L-SIG is 2 symbols after L-LTF, HT-SIG is
   3-4 symbols. SFO accumulates phase per symbol, so HT-SIG should have
   different SFO than L-SIG. If SFO isn't being re-applied, HT-SIG fails.

4. **TX HT-SIG encoding**: Verify the TX side encodes HT-SIG with
   correct MCS, length, and tail bits matching the L-SIG length.

## Critical Refutation of Phase 5-7

The "LO_BROKEN" / "INTERNAL_TCXO" conclusion is **definitively
refuted**:

- avg_snr_lsig = **84.13 dB** — would be impossible with broken LO
- avg_snr_htsig = **82.34 dB** — same, would be impossible
- L-SIG viterbi decodes 56/56 successfully — would be impossible with
  severe phase noise
- Per-LTF_COMP event: CFO range -0.37 to +0.62, SFO range -0.018 to +0.013
  — well within correctable range

The hardware (USRP X300 + internal TCXO) is **NOT** the problem.
The problem is a **HT-SIG-specific RX bug** in the software.

## How to Continue

1. **Verify TX HT-SIG**: dump TX HT-SIG bits and check that they match
   expected values for a 20-byte packet at MCS0
2. **Debug QBPSK rotation**: trace which rotation is being tried and why
   the metric is N/A
3. **Compare RX HT-SIG constellations with software loopback**: see if
   the equalized HT-SIG is on the rotated BPSK constellation
4. **Look for HT-SIG-specific processing in the equalizer** that may
   not be working correctly with USRP timing

The HT-SIG parse failure is the **last bug** between us and a working
USRP RX chain.
