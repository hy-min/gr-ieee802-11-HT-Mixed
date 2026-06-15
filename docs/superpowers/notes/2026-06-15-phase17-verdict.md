# Phase 17 — 5 GHz Subdev Isolation VERDICT (2026-06-15)

## TL;DR
**Phase 16 root cause (16-sample LO leakage) is subdev-specific, NOT frequency-specific**.
At 5 GHz **A:0**, the 16-sample LO leakage is ABSENT, the chain runs end-to-end,
frame detection succeeds, and the equalizer receives real L-STF data (e_in≈4440).
**The remaining bottleneck is L-SIG viterbi decode failure (algorithmic, Phase 10 issue)**.

## Discovery path

### 5 GHz direct dump (test_p17_usrp_5ghz_dump.py, A:0 subdev, no TX)
- 99.95% samples mag < 0.05
- Max mag 0.0201
- Median 16-sample correlation 0.5351 (NOT 16-sample repeating)
- VERDICT: 5 GHz A:0 standalone is **CLEAN** (no 16-sample leakage pattern)

### 5 GHz direct dump (test_p17c_5ghz_txrx with B:0 subdev, TX active)
- 99.99% samples mag < 0.05
- **Median 16-sample correlation 0.9998** (16-sample repeating!)
- L-STF present (max mag 0.306, 21 samples)
- VERDICT: 5 GHz B:0 with TX has the SAME 16-sample LO leakage as 2.4 GHz

### 5 GHz A:0 TX+RX direct dump (test_p17e_5ghz_a0_dump.py)
- 99.99% samples mag < 0.001 (true cable background noise)
- 3 L-STF bursts at 501/1001/1501 ms (matches strobe 500ms)
- Peak mag **1.41** (4.5× stronger than 2.4 GHz 0.31)
- Median 16-sample correlation **0.23** (random noise, NOT 16-sample repeating)
- p99 correlation 0.88 (high in burst regions)
- VERDICT: 5 GHz A:0 is the IDEAL clean channel for the RX chain

### 5 GHz A:0 end-to-end chain (test_p17d_5ghz_a0_e2e)
- 3975 sync_short "Frame detected" events in 30s
- 15,077 sync_short calls in COPY state
- fcs.ok=0
- VERDICT: sync_short works at 5 GHz A:0, but downstream chain still fails

### 5 GHz A:0 end-to-end with full chain probes (test_p17f_5ghz_a0_chain)
- 30,367 SYNC_LONG_OUT messages (sync_long working)
- SPLITTER_TIMING shows frame_start_abs=1017, 1016242, 1002288... (real wifi_start tags)
- **FRAME_GAIN_DUMP: e_in=4440, e_in_mean=69.4** (L-STF data reaching equalizer)
- **117 FRAME_DETECT events** (frame detection succeeds)
- **184 LSIG_PARSE_FAIL events** (L-SIG viterbi decode fails)
- fcs.ok=0
- VERDICT: Chain runs end-to-end. viterbi decode is the remaining issue.

## Key insight: 16-sample LO leakage is SUBDEV-SPECIFIC

| Frequency | Subdev | TX active | Median 16-sample corr | Chain works? |
|-----------|--------|-----------|----------------------|---------------|
| 2.4 GHz | B:0 | no | 0.9997 | ❌ sync_short fooled |
| 5 GHz | A:0 | no | 0.5351 | n/a (clean) |
| 5 GHz | B:0 | yes | 0.9998 | ❌ sync_short fooled |
| 5 GHz | A:0 | yes | 0.23 (in noise), 0.88 (in burst) | ✅ chain runs to viterbi |

The 16-sample LO leakage is:
- **Present at 2.4 GHz B:0** (regardless of TX)
- **Present at 5 GHz B:0** (when TX is on)
- **ABSENT at 5 GHz A:0** (clean channel)

This is hardware-specific: B:0 is the side with LO leakage, A:0 is clean.

## Why software could not fix this (recap)
- Phase 5-7: misdiagnosed as LO phase noise (measurement bug, see Phase 8)
- Phase 8: correctly measured 0.5-0.7 rad LO phase noise (borderline) but missed the 16-sample pattern
- Phase 14: fixed sync_long scheduler deadlock (still needed)
- Phase 15: chain probe localized signal loss to sync_short output
- Phase 16: bypass test identified 16-sample pattern (root cause)
- Phase 17: subdev isolation test confirmed A:0 is the clean path

## What 5 GHz A:0 e2e reveals: viterbi is the new bottleneck
The 5 GHz A:0 chain runs end-to-end (sync_short → sync_long → splitter → fft → equalizer → viterbi).
The frame_equalizer sees real L-STF data (e_in=4440 vs garbage at 2.4 GHz B:0).
Frame detection succeeds (L-SIG EQ ratio=0.757, expect < 1.0 for BPSK).
**But viterbi decode fails on every L-SIG attempt** (rate=-1, length=-1, avg_snr=23.43).

This is the SAME issue documented in Phase 10:
- L-SIG viterbi miscoding (enc ≠ BPSK 1/2) caused by upstream signal corruption
- Phase 12 sweep tried per-SC CPE and other fixes — Task 5 combined got 15.2% enc=0 (worse than 31% baseline)
- Original root cause: per-frame std=12.7 at L-LTF0 FFT input (vs loopback 0.0)

At 5 GHz A:0, the L-STF reaches the equalizer with high SNR (avg_snr=23.43), but viterbi
still fails. This is downstream of the splitter/FFT/equalizer and is ALGORITHMIC, not RF.

## Physical fix: use 5 GHz A:0 subdev for both TX and RX
- X300 SBX/CBX has A:0 and B:0 subdevs
- A:0 is clean at 5 GHz (no 16-sample LO leakage)
- B:0 is contaminated at both 2.4 and 5 GHz (same 16-sample pattern)
- Workaround: configure both TX and RX to A:0 subdev, use 5 GHz band
- Confirmed working: signal reaches equalizer with e_in=4440, frame detection succeeds

## Remaining work
1. **Fix L-SIG viterbi decode** (Phase 10 issue, now unblocked)
   - Investigate why viterbi fails despite high SNR
   - Try viterbi with different decoder options
   - Add per-SC channel estimate smoothing
2. **Document subdev isolation** as the recommended hardware configuration
3. **Optional: try 2.4 GHz with A:0 subdev** (might also be clean at 2.4 GHz)

## Lessons learned
- **Hardware can have asymmetric behavior** between subdevs on the same USRP
- **Bypass tests** (USRP source direct dump) are essential to find RF-layer issues
- **Full chain probes** (sync_short, sync_long, splitter, equalizer) localize failures
- **Subdev isolation** can sometimes be a software-only fix without new hardware

## Related memory
- [[project-p16-usrp-lo-leakage]] — Phase 16 root cause (16-sample pattern, subdev-isolated)
- [[project-p14-sync-long-deadlock]] — Phase 14 scheduler fix (still needed)
- [[project-p10-finding-enc-mismatch]] — Phase 10 viterbi issue (now unblocked)
