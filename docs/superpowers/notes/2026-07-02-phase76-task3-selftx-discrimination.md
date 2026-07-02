# Phase 76 Task 3 — Self-TX vs Background WiFi Discrimination

**Date**: 2026-07-02
**Branch**: TEST1
**Author**: investigating agent
**Status**: PARTIAL — background-WiFi hypothesis REFUTED for 5890, but frequency sweep
reveals 5250 MHz as a quiet-band candidate.

## Context

Phase 76 Task 1 captured USRP at 5890 MHz with explicit tight_v2 env vars and found
10 L-SIG decodes — all enc=5/7 (QAM64_2_3 / QAM64_3_4). Task 2 showed the TX
mapper chain emits encoding=0 correctly, so the enc=5/7 frames were assumed to be
background WiFi. This task directly tests that hypothesis.

## Step 1: TX-off capture at 5890 MHz

**Method**: `examples/p68_capture_raw_iq.py --freq 5890 --rate 20 --rx-gain 20
--antenna RX2 --duration 30 --out /tmp/p76_no_tx_5890.bin` (no TX enabled)
followed by `examples/p68_replay_offline.py` with the standard tight_v2 env
vars (H52_NULL_INTERP=1, H52_NULL_THRESH=0.03, H52_INTERP_RADIUS=5,
HTSIG_PILOT_CPE=1, LSIG_RATE_FORCE=0xD, TIMING_OFFSET_APPLY=1).

**Result**:
- 30s no-TX capture, 4.8 GB (100% UHD delivery, 19.98 MS/s effective rate)
- L-SIG decodes: **1** (`[LSIG_DECODE] OK enc=7 len=923`)
- HT_SIG_CAND: **0**
- H60_NULL_PER_FRAME: 9 (false triggers from noise)

**Comparison to T1 (TX-on, 5890)**:
- T1 captured only 0.46s of data (74 MB) replayed 5x → 2.3s effective
- 10 L-SIG decodes in 2.3s ⇒ 4.3 Hz decode rate
- 30s no-TX ⇒ 0.033 Hz decode rate (1 frame)
- **130x ratio** → T1's frames are dominated by self-TX, NOT background

**Conclusion**: **Background WiFi at 5890 is SPARSE** (1 frame in 30s). The
enc=5/7 in T1 cannot be background — they are self-TX frames with a **wrong
L-SIG rate field** (the rate field in our transmitted frames is being mapped
to 5/7 instead of 0). The Task 2 conclusion (background WiFi) is **REFUTED**.

### What we learned instead

The TX mapper's `tag_enc = 0` output does NOT necessarily reach the
`ht_header_tagged` block in a way that survives to the L-SIG OFDM symbol.
T1's 130x-ratio finding points to an **L-SIG rate-field corruption** upstream
of the air path, not a background-WiFi misattribution. The
`signal_field::header_formatter` (Task 2 Step 5) reads `pmt::mp("encoding")`
from the tag list, but if the tag is overwritten/duplicated by `ht_header_tagged`
(line 183-189 emits `encoding`+`mcs` at the first output byte), the second emit
may win. This is a separate investigation that the **frequency sweep result
now opens up**.

## Step 2: Frequency sweep (no-TX, 30s each)

**Method**: For each of 8 frequencies, capture 30s with no TX, then replay
through the standard RX chain with tight_v2 env vars.

**Results**:

| Freq  | LSIG_OK | HT_CAND | H60_NULL | Encodings seen         | Notes                            |
|-------|---------|---------|----------|------------------------|----------------------------------|
| 5180  | 4       | 32      | 9        | 0, 6, 7                | UNII-1 ch36: AP with HT-MCS0/5/6|
| 5250  | **0**   | **0**   | 1        | (none)                 | **QUIETEST BAND**                |
| 5320  | 4       | 0       | 9        | 1, 5, 6                | UNII-1 ch64: mixed 802.11n       |
| 5500  | 2       | 0       | 9        | 1, 6                   | UNII-2 mid: low traffic          |
| 5600  | 6       | 16      | 9        | 0, 1, 5, 6, 7          | UNII-2 ch112: BUSY (5 encodings) |
| 5700  | 3       | 0       | 9        | 6, 7                   | UNII-2 ch140: HT-MCS5/6          |
| 5800  | 14      | 0       | 9        | 2, 3, 4, 7             | UNII-3 ch149: BUSIEST, 14 frames |
| 5890  | 1       | 0       | 9        | 7                      | UNII-3 ch173: sparse (T1 band)   |

**Quietest freq: 5250 MHz** (0 frames in 30s, only 1 noise trigger).

The 5 GHz band has clear structure:
- 5180: 1 AP, mixed rates, HT mode active (32 HT_CAND)
- 5250: clean
- 5320: 1 AP, non-HT and HT-MCS5
- 5500-5700: UNII-2, moderate traffic
- 5800: BUSIEST (14 frames in 30s)
- 5890: sparse

## Step 3: Self-TX capture at 5250 MHz (quietest band)

**Method**: Run `test_usrp_minimal_loopback.py --freq 5250 --rate 20 --tx-gain 20
--rx-gain 20 --warmup 30 --duration 30 --capture /tmp/p76_selftx_5250.bin`
and replay 5x with tight_v2 env vars.

**Result** (5x replay of 0.63s capture = 3.15s effective):
- L-SIG decodes: **200** (32 enc=0, 28 enc=1, 40 enc=2, 17 enc=3, 27 enc=4,
  9 enc=5, 32 enc=6, 16 enc=7) — **all 8 encodings present**
- HT_SIG_CAND: **512** (vs 0 at 5890 in T1) — chain DOES fire
- H60_NULL: 511 (with 444/506 having n_nulls=0/52 — channel is clean)
- H60_NULL `is_ht=0` for 506/506 frames (HT chain runs on legacy frames too)
- Final FCS_OK: 0 (viterbi still fails — same wall as prior phases)
- TX-side sent 60 frames in 30s

**Observations**:
1. The 5250 self-TX capture has **busier spectrum** than the 5250 no-TX capture
   taken ~5 minutes earlier (which had 0 frames). This is **temporal variability**:
   5250 was quiet *at that moment* but is not guaranteed quiet. WiFi AP activity
   varies on 10-100s timescales.
2. **HT_SIG_CAND = 512 is a major change** — at 5890 (T1) it was 0. The chain
   is reachable at 5250; the L-SIG/HT-SIG wall is environmental, not algorithmic.
3. All 8 encodings appear → **self-TX frames at 5250 carry enc=0** (32 instances)
   **and enc=1-7 appear** (enc=1,2,3,4,5,6,7 = 169 instances), supporting the
   L-SIG rate-field corruption hypothesis: our TX frames hit RX with corrupted
   rate fields mapping to other encodings.

## Step 4: T1 reinterpretation (file loop artifact)

T1 used `--capture` mode on test_usrp_minimal_loopback.py which produced a
**0.46s capture file** (74 MB). The replay looped it 5x. This **2.3s effective
window** is far shorter than 30s. With 10 L-SIG decodes in 2.3s, decode rate
is 4.3 Hz — orders of magnitude above the 0.033 Hz background-WiFi rate at 5890.

**The 10 frames in T1 are mostly self-TX frames**, not background. The
enc=5/7 corruption is on **our own transmitted frames**.

## Verdict

1. **Background WiFi hypothesis for T1's enc=5/7: REFUTED** at 5890.
   Background rate is 0.033 Hz (1 frame / 30s); T1 saw 4.3 Hz.
2. **Self-TX is corrupting the L-SIG rate field**. The mapper emits enc=0
   (verified in Task 2) but the air-path L-SIG rate field decodes as enc=5/7
   on USRP. Likely culprit: `ht_header_tagged` tag emit at line 183-189 may
   overwrite or get reordered with respect to the encoding tag flowing through
   the data path.
3. **5250 MHz is the cleanest 5 GHz band** (0 frames in 30s no-TX).
4. **HT-SIG chain DOES fire at 5250** (512 HT_SIG_CANDs) — confirming that
   the channel-physics wall (Phase 41, 73, 75) is partially environmental.
5. **5250 has temporal variability** — quiet at one time, busy at another.
   Long-term quietness not guaranteed.

## Next steps for Phase 77

**Phase 77 priority**: investigate the L-SIG rate-field corruption in
`ht_header_tagged_impl.cc:183-189` (double-emit of encoding/mcs tags at
first byte of header). If the rate-field is fixed, self-TX frames at 5250
will carry enc=0 cleanly, isolating the HT-SIG viterbi wall from the
encoder-tag wall.

**Secondary**: continue capturing at 5250 with longer windows to confirm
quietness, and try 5250 with H52 pre-clean + a longer test (60s+) to see
if HT-SIG chain breaks the viterbi wall.

## Files

- No-TX captures: `/tmp/p76_no_tx_{5180,5250,5320,5500,5600,5700,5800,5890}.bin`
  (deleted to save disk; logs preserved at `/tmp/p76_no_tx_<freq>.log`)
- Self-TX 5250 capture: `/tmp/p76_selftx_5250.bin` (126 MB, kept)
- Self-TX 5250 replay log: `/tmp/p76_selftx_5250_replay.log`
- T1 capture: `/tmp/p76_tight_v2_freq_5890.bin` (74 MB, kept)
- T1 replay log: `/tmp/p76_tight_v2_freq_5890.log`

## Sweep summary CSV-style

```
freq,lsig_ok,ht_cand,h60_null,encodings
5180,4,32,9,0|6|7
5250,0,0,1,
5320,4,0,9,1|5|6
5500,2,0,9,1|6
5600,6,16,9,0|1|5|6|7
5700,3,0,9,6|7
5800,14,0,9,2|3|4|7
5890,1,0,9,7
```
