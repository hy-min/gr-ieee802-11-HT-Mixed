# L-SIG/HT-SIG Fix Implementation Plan - Updated 2026-05-10

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix L-SIG decoding failure (currently ~40% bit error rate) to enable HT-SIG decoding.

**Architecture:** L-SIG decoding fails because of systematic bit errors in bits 3-17. The first 3 bits (rate field "110") decode correctly, but the length field and reserved bit are consistently wrong. This suggests a subcarrier mapping, FFT timing, or symbol alignment issue.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python

---

## Current State

### Commit History
- `cf9e436`: debug: Add extensive L-SIG decode debugging and pilot value fix
- `f41a0bf`: debug: Add valid flags breakdown to HT-SIG parse condition
- `bfaa724`: fix: Correct deinterleaver formulas for L-SIG and HT-SIG

### Problem Description
L-SIG decoding still fails with ~40% bit error rate:
- Rate field bits 0-2 ("110") decode correctly
- Bits 3-17 (length + reserved) decode incorrectly
- Reserved bit 17 should be 0 but decodes as 3 or 7
- Errors are systematic (same bits wrong every frame)

### Key Observations
1. HT-DATA works (uses different deinterleaver path with n_col=13, n_row=4)
2. L-SIG/HT-SIG use 48-bit deinterleaver (n_col=16, n_row=3)
3. Errors are systematic, not random → problem is in signal path, not noise
4. Pilot value fix (kHeaderPilotBase) was applied but didn't resolve issue

---

## Task 1: Verify Deinterleaver Formula (Confirmed Correct)

**Status:** DONE - The deinterleaver formula `j = 16*(k%3) + k/16` IS correct.

**Verification:**
- Forward interleaver: i = 3*(k%16) + k/16 produces unique values 0-47
- Inverse formula: j = 16*(i%3) + i/3 correctly inverts it
- The formula maps 48 unique values - not the bug

---

## Task 2: Investigate FFT Output / Symbol Timing

**Files to Examine:**
- `lib/ht_symbol_splitter_impl.cc` - symbol boundary logic
- `lib/sync_long.cc` - d_frame_start value
- `lib/frame_equalizer_impl.cc` - FFT output processing

**Step 1: Check sync_long d_frame_start value**

```bash
grep -n "d_frame_start" /home/hy/gr-ieee802-11/lib/sync_long.cc | head -20
```

The code forces d_frame_start=192 (line 262).

**Step 2: Check ht_symbol_splitter timing**

```bash
grep -n "d_frame_start_abs\|rel_idx" /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc | head -30
```

Key question: Does ht_symbol_splitter correctly align HT-SIG symbols?

**Step 3: Add debug to verify FFT output timing**

At `frame_equalizer_impl.cc` around line 2227, add debug to print absolute input offset:

```cpp
std::printf("[EQ][TIMING] d_sym_idx=%d, abs_offset=%llu, internal_counter=%d\n",
            d_sym_idx, (unsigned long long)abs_in_off, d_internal_symbol_counter);
```

---

## Task 3: Check Subcarrier Mapping

**Files:**
- `lib/frame_equalizer_impl.cc:360-384` - kHeader48Sc and kHeader48Bin arrays

**The kHeader48Bin array defines FFT bin → subcarrier mapping:**

```cpp
static constexpr int kHeader48Bin[48] = {
    // Negative freq (SC -26 to -1): bins 38-63
    38, 39, 40, 41, 42,         // SC -26 to -22
    44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, // SC -20 to -8 (skip -7 pilot)
    58, 59, 60, 61, 62, 63,     // SC -6 to -1
    // Positive freq (SC +1 to +26): bins 1-26
    1, 2, 3, 4, 5, 6,           // SC +1 to +6
    8, 9,10,11,12,13,14,15,16,17,18,19,20,  // SC +8 to +20 (skip +7 pilot)
    22,23,24,25,26              // SC +22 to +26
};
```

**Verify:** This mapping should match TX carrier allocator subcarrier order.

---

## Task 4: Compare L-SIG and HT-DATA Processing Paths

**Key difference:**
- L-SIG/HT-SIG: BPSK, n_col=16, n_row=3
- HT-DATA: Uses constellation-specific deinterleaver (n_col=13 for HT-DATA)

**Why HT-DATA works but L-SIG fails:**
1. HT-DATA uses a different deinterleaver path
2. HT-DATA gets its own channel estimate from HT-LTF (not L-LTF)
3. HT-DATA phase errors can be corrected by Viterbi

**Hypothesis:** The 48-bit header deinterleaver has a bug that affects only certain bit positions (3-17).

---

## Task 5: Direct Comparison of TX and RX L-SIG Bits

**Step 1: Capture TX L-SIG bits from gr-htsig**

The gr-htsig module outputs:
```
[TX][HTSIG] raw48 = [bits before convolution encoding]
```

But L-SIG bits are NOT in this output - only HT-SIG bits are printed.

**Step 2: Verify expected L-SIG bits**

For a 48-byte PSDU with 6 Mbps rate (0x0D):
- L-SIG rate field: 0x0D = 0b1101
- L-SIG length: 48 bytes
- Parity bit: even parity of bits 0-17

TX should transmit: `1101 0000 0011 0000 01 [parity] 0000 00`

**Step 3: Compare with RX decoded bits**

From debug output:
```
[LSIG_DECODE] decoded_bits[0:24]=011111111001001011000000
[LSIG_DECODE] Expected for rate 0x0D: bits[0:18]=110100000011000001
```

Bit positions that differ:
- Bit 3: expected 1, decoded 0
- Bits 4-17: all differ

This suggests bits 0-2 are correct but 3-17 are systematically wrong.

---

## Task 6: Implement Fix

### Possible Fixes

**Fix A: FFT Timing Offset**
If ht_symbol_splitter is misaligned by 16 samples (one CP length), the FFT would capture wrong portion of L-SIG symbol.

**Fix B: Subcarrier Index Mapping**
If kHeader48Bin has wrong bin indices, the FFT output would be mapped to wrong subcarriers.

**Fix C: CPE Correction Issue**
If CPE estimation is wrong for L-SIG, the equalized bits would have phase errors.

### Recommended Fix Approach

1. First, verify FFT output is correctly aligned by checking L-LTF channel estimation
2. Check if L-LTF DATA symbols (rel_idx 0-63 and 80-143) decode correctly
3. If L-LTF is correct but L-SIG is wrong, the issue is in L-SIG processing path

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate environment
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Check sync_long d_frame_start
grep -n "d_frame_start" /home/hy/gr-ieee802-11/lib/sync_long.cc | head -10

# Run test with L-SIG debug
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG_DECODE|TIMING|CHAN_EST"
```

---

## Success Criteria

1. L-SIG decoded bits match expected: `110100000011000001XX`
2. L-SIG parity check passes consistently
3. HT-SIG decode is reached
4. HT-SIG CRC matches TX (0x41)
