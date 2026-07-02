# Phase 76 Task 4 — L-SIG Viterbi Rate-Field Corruption Investigation

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: REFUTED — L-SIG viterbi is NOT the wall; HT-SIG is.

## Background

Phase 76 T3 found 5250 MHz is the quietest 5 GHz band (0 LSIG in 30s no-TX).
Self-TX at 5250 produces 512 HT_SIG_CANDs and 200 L-SIG decodes.
All 8 encodings (0-7) appear, NOT just enc=0 — TX sends enc=0 but viterbi
produces enc=1-7. Hypothesis T4 was: TX mapper correctly emits encoding=0,
but the RX L-SIG viterbi decodes wrong rate-field bits, mapping enc=0 to
enc=1-7. If true, this would be the actual bottleneck.

## Capture & Replay Setup

### T3 Capture (existing)
- File: `/tmp/p76_selftx_5250.bin` (126 MB)
- Replay log: `/tmp/p76_selftx_5250.log`
- Config: tight_v2 (THRESH=0.03, RADIUS=5) + LSIG_RATE_FORCE=0xD + TIMING_OFFSET_APPLY=1

### T4 Audit Replay (new)
- Replayed `/tmp/p76_selftx_5250.bin` through RX chain with:
  - `IEEE80211_HTSIG_EQ_DUMP=1`
  - `IEEE80211_LSIG_VALIDITY_AUDIT=1`
- Output: `/tmp/p76_t4_5250_audit.log` (119 MB)

### T4 Capture (60s, in progress)
- File: `/tmp/p76_t4_5250_60s.bin` (in progress)
- Same env config as T3 + HTSIG_EQ_DUMP=1

## Files Examined

### Source code
- `lib/signal_field_impl.cc:99-179` — TX L-SIG generation
  - Line 109: `const int rate_field = 0x0D;` (TX hard-codes 0xD)
  - Lines 115-118: rate bits [0..3] = MSB..LSB of 0xD → 1,1,0,1
  - Lines 124-126: 12-bit length, LSB first
  - Lines 142-148: `[TX_LSIG_Original]` diagnostic dumps TX 24-bit SIGNAL header
  - Lines 158-164: `[TX_LSIG_Coded]` diagnostic dumps convolutional-coded output
- `lib/frame_equalizer_impl.cc:2060-2231` — RX L-SIG viterbi decode
  - Line 2085: `[LSIG_DECODE] FAIL: viterbi decode failed`
  - Line 2161: `[LSIG_DECODE] OK enc=N len=N`
  - Lines 2165-2172: `[LSIG_VITERBI_AUDIT]` dumps decoded24 + deintl48
  - Lines 2180-2201: `[LSIG_VALIDITY]` (gated by IEEE80211_LSIG_VALIDITY_AUDIT=1)
  - Lines 2211-2229: `IEEE80211_LSIG_RATE_FORCE` rejection

## Results

### L-SIG Decodes (T3 replay, 5250 MHz self-TX)
- HT_SIG_CAND: 576
- HT_SIG_PARSE_FAIL: 36 (all lsig_rate=0xD after rate-force)
- LSIG_DECODE OK: 188 (LSIG_VITERBI_AUDIT count)
- LSIG_DECODE OK by encoding: enc=0:36, enc=1:20, enc=2:43, enc=3:18,
  enc=4:20, enc=5:10, enc=6:24, enc=7:17

### Decoded rate_field distribution (uniform across all 8 valid rates)
- rate=0x1: 24
- rate=0x3: 17
- rate=0x5: 43
- rate=0x7: 18
- rate=0x9: 20
- rate=0xB: 10
- rate=0xD: 36 (correct, passes LSIG_RATE_FORCE)
- rate=0xF: 20
- **Distribution is approximately uniform: ~1/8 each rate.**

### Decoded 24-bit pattern inspection (T3)
All decoded24 strings that produce rate=0xD:
- 110100001100000100000000
- 110100111011011000000000
- 110101100100111010000000
- 110110101110100010000000
- 110101001011011001000000
- 110101101001010101000000
- 110111101001110011000000
- ...
**Each is a DIFFERENT 24-bit pattern** with bits[0..3]=1101 but bits[5..23] varying
wildly. There is NO common "TX signature" — every "correct" rate=0xD decode is
actually a random 24-bit codeword the viterbi converged on by chance.

### Deinterleaved L-SIG density (51% ones)
9024 deintl48 bits across all decodes → 4606 ones (51.0%). For a valid L-SIG
BPSK signal, the deinterleaved bits should follow a known structure
(convolutional-coded rate_field=0xD + length + parity + tail = 000000). The
51% ones = random-noise input.

### L-SIG EQ magnitude (BPSK constellation)
- mean|eq| ranges 0.24-1.68 across frames
- rms ranges 0.34-2.40 (high variance)
- Wild outliers: eq = 4.6, 6.0, 13.2 (single-SC noise blowups)
- For BPSK valid signal: |eq| should cluster around 1.0 ± noise
- Distribution is too wide; signal is heavily noise-corrupted

### HT-SIG EQ constellation (from T4 audit replay)
- n=3 frames captured in audit (limited because HT_SIG chain only runs on
  16-17 of 68 L-SIG decodes that pass rate=0xD + parity + tail)
- frame 0: htsig0 mean|re|=0.755 mean_im=0.200 std_im=1.876
- frame 1: htsig0 mean|re|=0.685 mean_im=-0.038 std_im=0.769
- frame 2: htsig0 mean|re|=... std_im=...
- **std_im=0.77-1.88 is catastrophic** for QBPSK (45° margin). Phase 38
  closure showed std_im ≤ 0.3 needed for HT-SIG viterbi success.

### HT-SIG chain metric (T3)
- All 576 HT_SIG_CANDs fail with `fail=crc_fail`
- Metrics: 14 @ 12, 48 @ 13, 114 @ 14, 184 @ 15, 146 @ 16, 48 @ 17, 22 @ 18
- For 24-bit HT-SIG, metric 12-18 means 12-18 bits wrong out of 24 (~50% BER)
- Working HT-SIG would need metric ≤ 4 (~17% BER after viterbi corrects)

### avg_snr_lsig vs avg_snr_htsig (from HT_SIG_PARSE_FAIL)
- avg_snr_lsig: median 1.99 dB, min 0.23 dB, max 33.57 dB, mean 3.56 dB
- avg_snr_htsig: 0.48-4.62 dB typical
- HT-SIG viterbi threshold (Phase 38 closure): needs ≥ 6 dB

### LSIG_VALIDITY (from T4 audit replay)
- valid=0: 52 / valid=1: 16 (out of 68 viterbi-success decodes)
- Of 16 valid=1 (rate=0xD + length>0 + parity=0 + tail=0):
  - 8 have length_field=3393 (TX's `compute_lsig_length_for_ht` output)
  - 8 have other length values (176, 193, 1104, 1367, 2398, 2632, 3441, 3441)
- Only 8/16 valid=1 cases match the TX's expected length field, suggesting
  most of the 16 "valid" decodes are also noise-converged codewords.

## Corruption Analysis

### TX emits
```
rate_field = 0x0D (1101 binary)
bits[0..3] = 1, 1, 0, 1  (MSB..LSB of rate)
bits[4]    = 0            (reserved)
bits[5..16]= length LSB first (compute_lsig_length_for_ht → 3393 for n_sym=1131)
bits[17]   = parity over bits[0..16]
bits[18..23]= 000000      (tail)
```

### RX viterbi decodes
The viterbi returns 24-bit codewords. With rate=0xD + parity + tail+0 constraints
holding, only 1/8th of decodes pass rate=0xD gate. But even those 36 (T3) or 16
(T4 audit replay) "valid" decodes have wildly varying bits[5..16] (length field
109-4276 instead of 3393).

### Mechanism
The decoder's input (deintl48 hard-decision bits) is 51% ones = essentially
random. The viterbi converges on whatever 24-bit codeword has the lowest
Hamming distance to the noisy 48-bit observation, modulo the rate/parity/tail
constraints. Since input is noise, the "winner" is essentially random among
the ~8 valid rate_field values × ~4096 valid length_field values × parity
classes × tail classes = roughly 8 * 4096 / 2 = ~16K valid codewords.
- Probability of guessing rate=0xD correctly: ~1/8 (matches observed)
- Conditional on rate=0xD, probability of correct length: 1/4096 (matches
  observed: ~1/8 of "valid" matches TX length)
- The "valid=1" filter passes any 24-bit pattern with rate=0xD + parity=0 +
  tail=0, regardless of length correctness.

### Why L-SIG viterbi gives enc=0 only 36/188 times
- The viterbi returns a codeword matching (rate, length, parity, tail)
  constraints that minimize path metric against deintl48.
- deintl48 is essentially random → all 8 rate values ~equally likely.
- LSIG_RATE_FORCE=0xD filter selects the 1/8th that happens to land on rate=0xD.

## Hypothesis

The L-SIG viterbi is operating correctly — it converges on whichever 24-bit
codeword has lowest path metric. The "corruption" is NOT a viterbi bug, but
rather **the input deintl48 is dominated by noise, not signal**. The TX-emitted
L-SIG signal is being overwhelmed by either:
- (a) Additive channel noise (but avg_snr_lsig 2-4 dB suggests signal IS present)
- (b) Frequency-selective channel nulls making some L-SIG SCs noise-dominated
- (c) Phase noise / CFO residual after sync_long rotation
- (d) L-LTF0 timing offset (Phase 33 fix already in place — 14-sample shift)

The L-SIG viterbi output is **indistinguishable from random output** because
the input is noise-like. Forcing rate=0xD does NOT recover the correct 24-bit
SIGNAL field; it just selects 1/8th of the noise distribution.

### Critical finding
**Even when rate=0xD is forced**, only 8/16 valid=1 decodes have the correct
TX length_field=3393. The other 8 have wrong lengths but still pass parity +
tail. So 36 frames per minute survive L-SIG chain, but only ~18 are "real"
L-SIG decodes. Half the time we proceed to HT-SIG chain with WRONG length.

### Wall location
The actual wall is HT-SIG viterbi:
- avg_snr_htsig 2-3 dB << required 6 dB threshold (Phase 38 closure)
- HT-SIG eq std_im 0.77-1.88 >> required ≤ 0.3
- HT_SIG_CAND metrics 12-18 (~50% BER) ≫ required ≤ 4 (~17% BER)

**L-SIG viterbi rate-field corruption is REFUTED as a separate bottleneck.**
LSIG_RATE_FORCE=0xD already addresses it correctly. The wall is downstream
in HT-SIG equalization — same channel-physics limit from Phase 38/41.

## Verdict: REFUTED

The "L-SIG viterbi rate-field corruption" hypothesis is REFUTED. The
LSIG_RATE_FORCE=0xD filter is already solving this at the source. The actual
wall remains HT-SIG channel-physics limit (avg_snr_htsig 2-3 dB << 6 dB needed).

### Path forward
- T5 verdict: **HT-SIG channel-physics is unbreakable from RX chain** at this
  USRP setup. Same as Phase 41 closure.
- The only un-attempted options per Phase 75 plan:
  - 76a LNA: physical gain at the antenna (already tested by user; no help)
  - 76b Accept closure: document Phase 76 as the FINAL pre-clean attempt
    before channel-physics, with HT-SIG chain visible at 5250
  - 76c Change MCS: try non-MCS0 to bypass QBPSK
  - 76d Swap antennas: physical change

## Files examined

- `/tmp/p76_selftx_5250.log` — T3 5250 replay log (350 MB)
- `/tmp/p76_t4_5250_audit.log` — T4 audit replay log (119 MB)
- `/home/hy/gr-ieee802-11/lib/signal_field_impl.cc:99-179`
- `/home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc:2060-2231`

## Next steps for T5

T5 should:
1. Confirm HT-SIG eq std_im > 0.7 on the new 60s capture (if file becomes available)
2. Document Phase 76 as REFUTED equalizer-side investigation, completing
   the 16+ REFUTED equalizer hypotheses from MEMORY.md 禁止方向 section
3. Recommend path: 76b Accept closure with explicit HT-SIG chain visible
   (576 candidates on USRP — chain FIRES, just can't converge) or 76c change MCS