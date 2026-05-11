# L-SIG Parity Check Failure Debugging Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix L-SIG parity check failure - TX L-SIG bits (`110100...`) don't match RX decoded bits (`011010...`)

**Architecture:** Systematically trace L-SIG data path from SPLITTER output → FFT → EQ → deinterleaver → Viterbi → parity check. Each stage boundary will be instrumented to identify where bits diverge.

**Tech Stack:** GNU Radio, C++, Python debug scripts, numpy

---

## Context from Prior Debugging

**TX L-SIG bits (correct):**
- raw24: `110100000011000001000000`
- enc48: `110110001010010001111101101100101000100011110111`
- intl48: `110000111110101111011010000001110010011110100101`

**RX L-SIG bits (wrong):**
- First 10 bits: `0110101100`
- Parity check: FAIL

**SPLITTER outputs verified:**
- L-SIG at rel_idx=223 ✅
- HT-SIG0 at rel_idx=303 ✅
- HT-SIG1 at rel_idx=383 ✅

**Known working components:**
- Deinterleaver formula `j = 16*(k%3) + k/3` verified correct
- HT-SIG boundary positions now correct

---

## Task 1: Capture SPLITTER Output FFT Data

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (add FFT sample dump around line 372)
- Test: Run test and capture SPLITTER output

- [ ] **Step 1: Add debug output to dump L-SIG FFT samples (first 8 subcarriers)**

In `ht_symbol_splitter_impl.cc`, after line 372 (`memcpy(&out[produced]...`), add:

```cpp
// Debug: Dump L-SIG FFT first 8 bins to stderr
if (out_rel_idx == 223) {
    fprintf(stderr, "[SPLITTER_DUMP] L-SIG FFT samples (bins 0-7):\n");
    for (int di = 0; di < 8 && di < d_fft_size; di++) {
        fprintf(stderr, "  bin[%d]=%.6f%+.6fi\n",
                di, d_buffer[di].real(), d_buffer[di].imag());
    }
}
```

- [ ] **Step 2: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -5
```

- [ ] **Step 3: Run test and capture SPLITTER FFT dump**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
timeout 15 python /home/hy/gr-ieee802-11/examples/test_loopback_final.py 2>&1 | \
grep "SPLITTER_DUMP" | head -5
```

- [ ] **Step 4: Verify FFT samples are non-zero and have expected BPSK constellation**

Expected: BPSK constellation points should be approximately ±1 on real axis only

- [ ] **Step 5: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "debug: Add L-SIG FFT sample dump in SPLITTER"
```

---

## Task 2: Verify LTF Channel Estimation

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add channel estimate debug
- Test: Check d_H values

- [ ] **Step 1: Check current channel estimate debug output**

Run test and look for `d_H[6-10]` values:
```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
timeout 15 python /home/hy/gr-ieee802-11/examples/test_loopback_final.py 2>&1 | \
grep "n=0: d_H\[6-10\]" | head -3
```

**Expected:** With ideal channel (taps=[1.0]), d_H should be approximately 1.0 (no distortion)

- [ ] **Step 2: If d_H looks wrong, add more detailed LTF debug**

In `frame_equalizer_impl.cc`, find `equalize_header52_to_bits48` function. Add debug to print raw FFT input vs equalized output for L-SIG symbol.

- [ ] **Step 3: Commit if changes made**

---

## Task 3: Verify L-SIG Bit Extraction in frame_equalizer

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add bit-level debug at EQ input
- Test: Compare TX vs RX subcarrier values

- [ ] **Step 1: Find L-SIG extraction code**

```bash
grep -n "kLSigRel\|L-SIG.*extract" lib/frame_equalizer_impl.cc | head -10
```

- [ ] **Step 2: Add debug to print raw L-SIG subcarrier values before EQ**

In `frame_equalizer_impl.cc`, find where `d_early_eqsym[kLSigRel]` is populated. Add:
```cpp
fprintf(stderr, "[LSIG_RAW] sym52 subcarriers 0-7:\n");
for (int di = 0; di < 8; di++) {
    fprintf(stderr, "  sc[%d]=%.6f%+.6fi\n",
            di, d_early_eqsym[kLSigRel][di].real(),
            d_early_eqsym[kLSigRel][di].imag());
}
```

- [ ] **Step 3: Run test and compare TX vs RX subcarrier values**

TX L-SIG uses BPSK on subcarriers. For ideal channel with no rotation, should see points on real axis.

- [ ] **Step 4: Analyze discrepancy**

If TX subcarriers are on real axis but RX shows significant imaginary component:
- Could indicate FFT window misalignment
- Could indicate CPE (carrier phase error) not corrected
- Could indicate timing offset

- [ ] **Step 5: Commit**

---

## Task 4: Check CPE (Carrier Phase Estimation) Correction

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Check CPE application
- Test: Verify CPE is applied correctly

- [ ] **Step 1: Find CPE estimation code**

```bash
grep -n "CPE\|cpe_estimate\|phase.*rot" lib/frame_equalizer_impl.cc | head -15
```

- [ ] **Step 2: Check if CPE is applied before L-SIG decoding**

From prior debug output:
```
[EQ_HEADER] CPE estimate: -2.110 rad, rot=-0.513+0.858i
```

This shows CPE IS being estimated. Need to verify it's applied to L-SIG EQ.

- [ ] **Step 3: Check rotation application in equalize_header52_to_bits48**

Find where `rot` (complex rotation) is applied to L-SIG subcarriers. The rotation should undo the CPE.

- [ ] **Step 4: If rotation is wrong, document the issue and propose fix**

---

## Task 5: Verify Deinterleaver and Viterbi Input

**Files:**
- Create: `debug_deinterleaver.py` - Standalone verification
- Test: Run deinterleaver verification

- [ ] **Step 1: Create debug script to verify deinterleaver**

```python
#!/usr/bin/env python3
"""Verify deinterleaver with known L-SIG bits"""
import numpy as np

# TX L-SIG interleaved bits (from debug output)
tx_intl48 = [int(b) for b in "110000111110101111011010000001110010011110100101"]
tx_enc48 = [int(b) for b in "110110001010010001111101101100101000100011110111"]

# Deinterleaver: j = 16*(k%3) + k//3
deintl = [0]*48
for k in range(48):
    j = 16*(k%3) + k//3
    deintl[k] = tx_intl48[j]

result = ''.join(str(b) for b in deintl)
expected = ''.join(str(b) for b in tx_enc48)

print(f"Deinterleaved: {result}")
print(f"Expected:      {expected}")
print(f"Match: {result == expected}")
```

Run: `python debug_deinterleaver.py`

- [ ] **Step 2: Verify deinterleaver is being called correctly in RX path**

Check that the deinterleaver is getting the correct input bits from EQ.

- [ ] **Step 3: Commit deinterleaver verification script**

```bash
git add debug_deinterleaver.py
git commit -m "debug: Add deinterleaver verification script"
```

---

## Task 6: Check Viterbi Decoder Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add Viterbi input/output debug
- Test: Verify Viterbi produces correct bits

- [ ] **Step 1: Find Viterbi decode call for L-SIG**

```bash
grep -n "viterbi_decode_133_171\|decode_lsig_direct" lib/frame_equalizer_impl.cc | head -10
```

- [ ] **Step 2: Add debug to print Viterbi input bits (48 bits)**

Before Viterbi call in `decode_lsig_direct_from_header52`, add:
```cpp
fprintf(stderr, "[VITERBI_IN] 48 bits:\n");
for (int di = 0; di < 48; di++) {
    fprintf(stderr, "%d", deintl48[di]);
    if ((di+1) % 12 == 0) fprintf(stderr, "\n");
}
```

- [ ] **Step 3: Run test and check Viterbi input**

Compare with TX encoded bits: `110110001010010001111101101100101000100011110111`

- [ ] **Step 4: If Viterbi input is wrong, problem is upstream (EQ or deinterleaver)**

- [ ] **Step 5: Commit**

---

## Task 7: Create Comprehensive L-SIG Debug Report

**Files:**
- Create: `docs/superpowers/plans/YYYY-MM-DD-lsig-debug-report.md`
- Test: Verify all findings documented

- [ ] **Step 1: Compile all debug output into report**

Document:
- SPLITTER FFT output values
- Channel estimate values
- EQ input/output
- Viterbi input bits
- Where exactly bits diverge

- [ ] **Step 2: Identify root cause**

Based on tasks 1-6, determine where L-SIG bits first become incorrect:
- If SPLITTER FFT is wrong → Problem in sync_long or earlier
- If EQ output is wrong → Problem in channel estimation or EQ
- If deinterleaver output is wrong → Problem in bit extraction
- If Viterbi input is wrong → Problem in deinterleaver
- If Viterbi output is wrong → Viterbi decoder bug

- [ ] **Step 3: Propose fix based on root cause**

---

## Verification Criteria

**Success when:**
```
TX L-SIG: raw24 = 110100000011000001000000 (rate=0x0D)
RX L-SIG: decoded rate = 0x0D
RX L-SIG: parity check PASS
```
