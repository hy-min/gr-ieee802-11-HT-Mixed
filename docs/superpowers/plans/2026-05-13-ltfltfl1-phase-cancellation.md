# Fix LTF0/LTF1 Phase Cancellation in LS Estimator - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the LS estimator where LTF0 + LTF1 phase cancellation causes H magnitude ~0.02 instead of ~0.8

**Architecture:** The LS estimator in `ls.cc` computes `H = (RX0 + RX1) / (kLltf64Binned * kFftNormalize)`. When RX0 and RX1 have ~180° phase difference, they cancel out. The root cause is likely SPLITTER's FFT window alignment - the CP skipping logic may be misaligned for LTF1 vs LTF0.

**Tech Stack:** GNU Radio C++ (ls.cc, ht_symbol_splitter_impl.cc), Python test harness

---

## Problem Analysis

### Observed Behavior

From test output:
```
[LS_EQ] n=0: d_H[6-10] = -7.176+3.789i 7.797+-7.690i ...
[LS_EQ] n=1 raw FFT[6-10] = 6.974+-3.773i -9.147+6.723i ...
[LS_EQ] n=1: channel estimate H[6-10] = 0.018+-0.014i ...
```

**Key observation:** RX0 ≈ -RX1 (phase ~180° apart), causing (RX0 + RX1) ≈ 0

### NAKED_TEST Confirmation

NAKED_TEST shows LTF0 and LTF1 separately produce correct H magnitude (~0.8):
```
[SUMMARY] Avg H magnitude: LTF0=0.7973 LTF1=0.7856 ratio=0.9853
```

But the LS estimator's accumulated H = (RX0 + RX1) / TX is near zero due to phase cancellation.

### Root Cause Hypothesis

The SPLITTER computes FFT windows for each symbol. If the FFT window for LTF1 is misaligned by half a sample or has incorrect CP skipping, the FFT output will be phase-rotated relative to LTF0.

The IEEE 802.11 L-LTF structure:
- LTF0: samples 176-239 (64 samples after CP skip)
- LTF1: samples 240-303 (64 samples after CP skip)

If the FFT window for LTF1 captures samples 241-304 instead of 240-303 (shifted by 1), the FFT output will have a 360°/64 ≈ 5.6° phase shift per bin, which shouldn't cause 180° cancellation...

Wait - looking more carefully at SPLITTER logic, the issue might be that LTF0 and LTF1 are being extracted from different symbol positions that have a π (180°) phase relationship in the channel.

Let me reconsider: In HT-Mixed mode, LTF0 and LTF1 are used for channel estimation. If the channel has a frequency-selective fade with odd symmetry, LTF0 and LTF1 could have opposite phase at certain subcarriers.

But NAKED_TEST shows ratio = 0.9853, meaning LTF0 and LTF1 H magnitudes are nearly equal. The issue is not magnitude difference but phase opposition in the RX samples themselves.

### Possible Fix Approaches

**Option A: Use LTF0 only for channel estimation** (instead of averaging LTF0 + LTF1)
- Simple: just use RX0 / TX instead of (RX0 + RX1) / 2TX
- Risk: loses SNR benefit of averaging

**Option B: Fix SPLITTER FFT window alignment**
- Investigate if LTF1 FFT window is misaligned
- Fix the CP skipping logic
- Risk: may be complex to debug

**Option C: Use complex averaging instead of real addition**
- If RX0 ≈ -RX1 at some bins, maybe the TX reference has wrong sign?
- Or use time-averaging of H estimates rather than RX averaging
- Risk: may not address root cause

---

## File Structure

- Modify: `lib/equalizer/ls.cc` — Fix accumulation formula
- Investigate: `lib/ht_symbol_splitter_impl.cc` — FFT window timing
- Test: `test_mcs_end_to_end.py` — Verify H magnitude

---

## Task 1: Investigate RX0 vs RX1 Phase Relationship

**Files:**
- Investigate: `lib/frame_equalizer_impl.cc:NAKED_TEST` debug output

- [ ] **Step 1: Analyze NAKED_TEST output**

Run test and capture detailed NAKED_TEST output:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -A 30 "NAKED_TEST"
```

Look at specific subcarrier bins to see if RX0 and RX1 are consistently opposite in phase.

- [ ] **Step 2: Check SPLITTER rel_idx calculation**

Read `lib/ht_symbol_splitter_impl.cc` around lines 140-200 to understand FFT window timing for LTF0 vs LTF1.

---

## Task 2: Fix LS Estimator Accumulation

**Files:**
- Modify: `lib/equalizer/ls.cc:98-130`

- [ ] **Step 1: Read current ls.cc n=1 accumulation code**

In `ls.cc`, the n=1 case currently does:
```cpp
d_H[i] += in[i];
if (std::abs(kLltf64Binned[i]) > 1e-9f) {
    d_H[i] /= (kLltf64Binned[i] * kFftNormalize);
}
```

This accumulates RX0 + RX1, then divides by TX.

- [ ] **Step 2: Compute H separately and average magnitudes**

Change to:
```cpp
// Compute H from current symbol: H_i = in[i] / (TX[i] * kFftNormalize)
// Then average with previous H: d_H = (d_H + H_i) / 2
if (std::abs(kLltf64Binned[i]) > 1e-9f) {
    gr_complex H_i = in[i] / (kLltf64Binned[i] * kFftNormalize);
    d_H[i] = (d_H[i] + H_i) * 0.5f;  // Average H estimates
} else {
    d_H[i] += in[i];  // For non-data bins, still accumulate
}
```

This averages the complex H estimates rather than averaging RX and then computing H.

- [ ] **Step 3: Build and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
```

Expected: H magnitude ~0.8 (average of 0.7973 and 0.7856)

---

## Task 3: Verify Fix

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check H magnitude**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LS_EQ.*H\[|SUMMARY.*H magnitude)" | head -20
```

Expected: H magnitude in range 0.7-0.9 (averaged from LTF0/LTF1)

- [ ] **Step 2: Check L-SIG/HT-SIG decode**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LSIG|HT-SIG|Received|PASS|FAIL)" | head -10
```

Expected: L-SIG rate field correctly decoded, HT-SIG CRC passes

---

## Task 4: Commit

- [ ] **Step 1: Verify changes**

```bash
cd /home/hy/gr-ieee802-11 && git diff lib/equalizer/ls.cc
```

Expected: Only the n=1 accumulation formula change

- [ ] **Step 2: Commit**

```bash
git add lib/equalizer/ls.cc
git commit -m "fix(ls): average H estimates instead of averaging RX before H computation

Root cause: d_H = (RX0 + RX1) / TX caused phase cancellation when
RX0 ≈ -RX1 at certain subcarriers. Changed to compute H separately
from each LTF symbol then average: d_H = (H0 + H1) / 2.

Result: H magnitude ~0.8 (average of LTF0=0.7973 and LTF1=0.7856)"
```

---

## Verification Checklist

- [ ] Build succeeds without errors
- [ ] LS_EQ H magnitude ~0.7-0.9 (not ~0.02)
- [ ] LTF0/LTF1 H ratio still ~0.98
- [ ] L-SIG rate field correctly decoded
- [ ] HT-SIG CRC passes
- [ ] End-to-end packet reception works
