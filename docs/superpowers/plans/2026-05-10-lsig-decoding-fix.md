# L-SIG Decoding Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix L-SIG decoding failure to enable HT-SIG decoding. Currently L-SIG has ~100% bit error rate with systematic bit errors in positions 3-17.

**Architecture:** L-SIG decoding fails due to systematic bit errors in the Viterbi input. The issue is not noise but corruption in the signal path. The first 3 bits (rate field) decode correctly, but bits 3-17 are consistently wrong.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python

---

## Current State (Updated 2026-05-10)

### Commits
- `e13a594`: fix: Remove dead code from boundary check formula
- `e36ba59`: fix: Output FFT blocks at correct symbol boundaries in ht_symbol_splitter

### Problem Description (Updated)

L-SIG decoding produces **completely inverted bits**:
```
TX raw24:  110100000011000001000000
RX decoded: 001100001100011010000000
Expected:   110100000011000001xxxxxx
```

**Key observations (Updated):**
1. ALL 24 decoded bits are systematically inverted (0↔1)
2. TX raw24 = `110100000011000001000000`
3. RX decoded = `001100001100011010000000`
4. First 18 bits are exactly inverted, indicating a systematic issue
5. The issue is NOT random noise but a systematic inversion in the signal chain

### Root Cause: Systematic Bit Inversion

The fact that bits are **exactly** inverted (not just wrong) suggests:
- **Viterbi decoder outputting inverted bits**, OR
- **Deinterleaver permutation is 180° off**, OR
- **QPSK constellation demapping is rotated 180°**

### Hypotheses (Updated for Systematic Inversion)

| # | Hypothesis | Evidence |
|---|-----------|----------|
| A | Viterbi decoder outputting inverted bits | All bits exactly inverted, not random errors |
| B | Deinterleaver formula wrong for L-SIG | L-SIG uses 48 carriers, formula may be off by 180° |
| C | QPSK constellation demapping rotated | All bits inverted, not just specific positions |
| D | Phase rotation mismatch | L-SIG shouldn't have HT-SIG's 1j rotation |

---

## Task 1: Verify FFT Output Quality

**Files:**
- Test: `examples/test_loopback_noqt.py`

**Step 1: Check LTF channel estimation quality**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "CHAN_EST\[WARNING\]"
```

Expected: No "Opposite signs" warnings for LTF0 vs LTF1 comparison.

**Step 2: Check EQ_HEADER output**

```bash
timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "EQ_HEADER.*First 10 bits"
```

Expected: First 10 bits should be "1101000000" (matching expected L-SIG bits 0-9).

---

## Task 2: Compare TX and RX L-SIG Bits

**Files:**
- Test: `examples/test_loopback_noqt.py`

**Step 1: Capture TX L-SIG bits**

TX outputs:
```
[TX][LSIG] raw24=110100000011000001000000
[TX][LSIG] enc48=110110001010010001111101101100101000100011110111
[TX][LSIG] intl48=110000111110101111011010000001110010011110100101
```

**Step 2: After Viterbi decode, compare**

The decoded bits should match raw24 (after convolution decode). Check [LSIG_DECODE] output.

---

## Task 3: Check Deinterleaver Input

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - add debug at deinterleave

**Step 1: Add debug before deinterleave**

At line 1241, before `deinterleave_bpsk_48(eqbits48, deintl48);`, add:

```cpp
fprintf(stderr, "[DEINTL_IN] eqbits48[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", eqbits48[i]);
fprintf(stderr, "\n");
fprintf(stderr, "[DEINTL_IN] eqbits48[24:48]=");
for (int i = 24; i < 48; i++) fprintf(stderr, "%d", eqbits48[i]);
fprintf(stderr, "\n");
```

**Step 2: Add debug after deinterleave**

```cpp
fprintf(stderr, "[DEINTL_OUT] deintl48[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", deintl48[i]);
fprintf(stderr, "\n");
```

**Step 3: Test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
LD_LIBRARY_PATH=... timeout 30 python ... 2>&1 | grep -E "DEINTL"
```

Expected: Compare DEINTL_IN with TX [TX][LSIG] intl48 to verify bits match.

---

## Task 4: Verify Viterbi Input/Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - debug at viterbi

**Step 1: Add debug at Viterbi call**

In `decode_lsig_direct_from_header52`, before viterbi call at line 1248:

```cpp
fprintf(stderr, "[VITERBI_IN] deintl48[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", deintl48[i]);
fprintf(stderr, "\n");
```

**Step 2: Check Viterbi output**

After viterbi at line 1257:

```cpp
fprintf(stderr, "[VITERBI_OUT] dec24[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", decoded_bits[i] & 1);
fprintf(stderr, "\n");
```

---

## Task 5: Identify Root Cause

Based on Tasks 1-4, identify where the corruption occurs:

| If... | Then... |
|-------|---------|
| DEINTL_IN matches TX intl48 | Viterbi is wrong |
| DEINTL_IN doesn't match TX | Deinterleaver or equalizer is wrong |
| VITERBI_IN correct but OUT wrong | Viterbi bug |
| VITERBI_OUT has bits 0-2 correct, 3-17 wrong | Downstream issue |

---

## Task 6: Implement Fix

Based on root cause identification from Task 5.

---

## Task 7: Clean Up Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`

Remove all debug statements added in Tasks 3-4.

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Full debug
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1

# L-SIG specific
LD_LIBRARY_PATH=... timeout 30 python ... 2>&1 | grep -E "LSIG|TX.*LSIG|DEINTL|VITERBI"
```

## Success Criteria

1. L-SIG decoded bits match TX raw24: `110100000011000001000000`
2. L-SIG parity check passes consistently
3. HT-SIG decode is reached
4. HT-SIG CRC matches TX (0x41)
