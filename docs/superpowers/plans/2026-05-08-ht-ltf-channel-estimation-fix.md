# HT-LTF Channel Estimation H=0 Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix HT-LTF channel estimation so H ≠ 0 for all 48 data subcarriers

**Architecture:** The issue is in `frame_equalizer_impl.cc` where HT-LTF channel estimation produces all zeros. Need to trace why RX HT-LTF FFT output doesn't match expected reference values.

**Tech Stack:** GNU Radio, IEEE 802.11, C++, Python

---

## Problem Statement

```
[EQ_HEADER] Zero-magnitude H subcarriers: 48/48
[EQ_HEADER] Average RX magnitude: 0.0000
```

All 48 HT-LTF data subcarriers have zero magnitude after channel estimation.

---

## Task 1: Verify TX HT-LTF Frequency Domain Output

**Files:**
- Debug: `lib/insert_ht_training_impl.cc` - TX HT-LTF generation
- Debug: `examples/mixed_mode_carrier_allocator.py` - preamble structure

- [ ] **Step 1: Check TX HT-LTF generation in insert_ht_training_impl.cc**

Location: `lib/insert_ht_training_impl.cc`

Look for:
- `kHtLtf64` array definition
- How HT-LTF is inserted at symbol 7

```cpp
// Expected HT-LTF reference (from 802.11 standard)
static const gr_complex kHtLtfDataRef[52] = {
    // Data subcarrier reference values for HT-LTF
};
```

- [ ] **Step 2: Verify TX inserts HT-LTF at correct position**

Check `kInsertAtSym = 7` in insert_ht_training_impl.cc

- [ ] **Step 3: Capture TX HT-LTF FFT output**

Run test and grep for HT-LTF FFT values:
```bash
timeout 15 python examples/wifi_constellation.py 2>&1 | grep "HT-LTF.*FFT"
```

Expected: Non-zero values at data subcarrier positions

---

## Task 2: Verify RX Receives HT-LTF at Correct Symbol Position

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - HT-LTF detection

- [ ] **Step 1: Check HT-LTF symbol detection**

In `general_work`, find where `d_htltf_H_valid` is set and verify HT-LTF is detected at `d_internal_symbol_counter == kHtLtfRel`.

Constants from frame_equalizer_impl.cc:
```cpp
static constexpr int kLltf0Rel = 0;
static constexpr int kLSigRel = 2;
static constexpr int kHtSig0Rel = 3;
static constexpr int kHtSig1Rel = 4;
static constexpr int kHtLtfRel = 6;   // HT-LTF position
static constexpr int kDataStartRel = 7;
```

- [ ] **Step 2: Add debug output when HT-LTF is processed**

Around line where `d_htltf_H_valid` is set:
```cpp
fprintf(stderr, "[EQ][HTLTF] Processing HT-LTF at d_internal_symbol_counter=%d\n",
        d_internal_symbol_counter);
```

- [ ] **Step 3: Run and verify HT-LTF is detected**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "HTLTF"`

---

## Task 3: Trace HT-LTF Channel Estimation Calculation

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - `estimate_channel_from_htltf52`

- [ ] **Step 1: Find HT-LTF channel estimation function**

Search for:
```cpp
estimate_channel_from_htltf52
or
d_htltf_H
```

- [ ] **Step 2: Add debug output to H calculation**

In the HT-LTF channel estimation function:
```cpp
fprintf(stderr, "[EQ][HT_ESTIM] RX HT-LTF sym64[1]=%.4f+%.4fi\n",
        sym64[1].real(), sym64[1].imag());
fprintf(stderr, "[EQ][HT_ESTIM] Reference kHtLtfDataRef[0]=%.4f+%.4fi\n",
        kHtLtfDataRef[0].real(), kHtLtfDataRef[0].imag());
fprintf(stderr, "[EQ][HT_ESTIM] H[0]=%.4f+%.4fi\n",
        H[0].real(), H[0].imag());
```

- [ ] **Step 3: Run and check H calculation**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "HT_ESTIM"`

Expected: H should be non-zero (channel is ideal loopback H=1)

---

## Task 4: Identify Root Cause of H=0

**Possible causes:**

1. **RX HT-LTF FFT output is zero** - TX not generating HT-LTF correctly
2. **Subcarrier mapping mismatch** - TX/RX using different SC order
3. **Reference values mismatch** - TX uses different HT-LTF sequence than RX expects
4. **Pilot sign error** - HT-LTF pilots have wrong signs
5. **FFT/IFFT order issue** - fftshift mismatch between TX and RX

- [ ] **Step 1: Check TX kHtLtf64 vs RX kHtLtfDataRef**

TX defines HT-LTF in `insert_ht_training_impl.cc`:
```cpp
static const gr_complex kHtLtf64[64] = { ... };
```

RX expects HT-LTF in `frame_equalizer_impl.cc`:
```cpp
static const gr_complex kHtLtfDataRef[52] = { ... };
```

These MUST match for channel estimation to work.

- [ ] **Step 2: Compare actual values**

TX kHtLtf64 (after IFFT if it's frequency-domain):
RX kHtLtfDataRef (used in channel estimation):

---

## Task 5: Fix Identified Mismatch

Based on Task 4 findings, fix the root cause:

**If TX/RX reference mismatch:**
- Make TX and RX use identical HT-LTF sequence

**If subcarrier mapping issue:**
- Fix kTxOrder52 / kRxOrder52 usage

**If pilot sign error:**
- Apply correct pilot signs from 802.11 standard

---

## Task 6: Verify Fix

- [ ] **Step 1: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 2: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/wifi_constellation.py 2>&1 | grep -E "(H=|HT_ESTIM|d_have_ht|FCS)"
```

Expected:
- `d_have_ht_header=1`
- H values non-zero
- HT-SIG CRC pass

---

## Key Files Reference

### TX HT-LTF Generation
- `lib/insert_ht_training_impl.cc:100-150` - kHtLtf64 definition
- `lib/insert_ht_training_impl.cc:180-200` - HT-LTF insertion logic

### RX HT-LTF Processing
- `lib/frame_equalizer_impl.cc:2400-2500` - HT-LTF detection
- `lib/frame_equalizer_impl.cc:2500-2600` - HT-LTF channel estimation

### Expected HT-LTF Structure
```
Symbol 7: HT-STF (short training)
Symbol 8: HT-LTF1 (channel estimation)
```

### HT-LTF Data Subcarrier Reference (from 802.11 standard)
The HT-LTF uses BPSK modulation with alternating signs for channel estimation.
