# L-LTF Channel Estimation & L-SIG/HT-SIG Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix L-LTF channel estimation so d_H ≠ 0, enabling L-SIG decode success and d_have_ht_header=1

**Architecture:** The issue is in `frame_equalizer_impl.cc` - L-LTF channel estimation produces d_H=0, which causes L-SIG decode to fail.

**Tech Stack:** GNU Radio, IEEE 802.11, C++, Python

---

## Problem Statement

After fixing HT-LTF position constants (kHtTrain1Rel: 6→8, kDataStartRel: 7→9), the L-LTF channel estimation now produces d_H=0:

```
[EQ] d_H[6:14] before L-SIG eq: 0.000+0.000i
decoded24[0:24]=010001010110000000000000
Unknown rate field: 0x0E
Parity check failed! parity_sum=1, parity_bit=0
d_have_ht_header=0 (never becomes 1)
```

The HT-LTF position fix may have affected the boundary check or L-LTF processing.

---

## Task 1: Verify L-LTF Channel Estimation Location

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - L-LTF processing

- [ ] **Step 1: Find where d_H (L-LTF channel estimate) is computed**

Search for `estimate_header_channel_from_lltf52` or `d_H` initialization

- [ ] **Step 2: Verify L-LTF is processed at correct position**

Constants should be:
```cpp
static constexpr int kLltf0Rel = 0;   // L-LTF0 position
static constexpr int kLltf1Rel = 1;   // L-LTF1 position
static constexpr int kLSigRel = 2;      // L-SIG position
```

- [ ] **Step 3: Check boundary condition changes**

In Task 5, boundary was changed from `< 8` to `< 9`. This may have affected L-LTF processing.

---

## Task 2: Trace L-LTF Channel Estimation Calculation

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - add L-LTF H debug

- [ ] **Step 1: Add debug output for d_H calculation**

Around where `estimate_header_channel_from_lltf52` is called:
```cpp
fprintf(stderr, "[EQ][L_LTF_ESTIM] d_internal_symbol_counter=%d\n", d_internal_symbol_counter);
fprintf(stderr, "[EQ][L_LTF_ESTIM] L-LTF sym64[1]=%.4f+%.4fi\n",
        sym64[1].real(), sym64[1].imag());
```

- [ ] **Step 2: Check kLltfRef64 reference values**

```cpp
static const gr_complex kLltfRef64[64] = { ... };
```

- [ ] **Step 3: Run and verify L-LTF is processed**

Run: `timeout 30 python examples/test_constellation_real.py 2>&1 | grep "L_LTF_ESTIM"`

---

## Task 3: Identify Why d_H is Zero

**Possible causes:**

1. **Boundary check issue**: The `< 9` change may skip L-LTF processing
2. **L-LTF reference mismatch**: TX and RX use different L-LTF sequences
3. **L-LTF position wrong**: kLltf0Rel may not be 0
4. **FFT output issue**: L-LTF FFT is zero

- [ ] **Step 1: Check if L-LTF is being processed at all**

```cpp
fprintf(stderr, "[EQ][DEBUG] Processing symbol at d_internal_symbol_counter=%d\n",
        d_internal_symbol_counter);
```

- [ ] **Step 2: Compare TX L-LTF vs RX L-LTF reference**

TX: kLltfRef64 in insert_ht_training or mixed_mode_carrier_allocator
RX: kLltfRef64 in frame_equalizer_impl.cc

- [ ] **Step 3: Identify root cause**

---

## Task 4: Fix d_H Calculation

Based on Task 3 findings, fix the root cause:

**If boundary issue:**
```cpp
// Original: if (d_internal_symbol_counter < 8)
// Fixed: if (d_internal_symbol_counter < 9)  // Allow counter 8 (HT-LTF)
// But this may have broken L-LTF processing at counter 0-1
```

**If reference mismatch:**
- Ensure TX and RX use identical L-LTF sequences

---

## Task 5: Verify Fix

- [ ] **Step 1: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 2: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep -E "(d_have_ht|L_SIG|H=)"
```

Expected:
- d_H non-zero
- L-SIG decode success
- d_have_ht_header=1
- HT-SIG CRC pass

---

## Key Files Reference

### L-LTF Processing
- `lib/frame_equalizer_impl.cc:2700-2800` - estimate_header_channel_from_lltf52 call
- `lib/frame_equalizer_impl.cc:2400-2500` - L-LTF detection

### L-LTF Reference
TX uses `LEGACY_LTF` in `examples/mixed_mode_carrier_allocator.py`
RX uses `kLltfRef64` in `lib/frame_equalizer_impl.cc`

### Expected Frame Structure
```
Symbol 0: L-STF
Symbol 1: L-STF
Symbol 2: L-LTF    ← kLltf0Rel = 0
Symbol 3: L-LTF    ← kLltf1Rel = 1
Symbol 4: L-SIG    ← kLSigRel = 2
Symbol 5: HT-SIG1  ← kHtSig0Rel = 3
Symbol 6: HT-SIG2  ← kHtSig1Rel = 4
Symbol 7: HT-STF   ← kHtTrain0Rel = 7 (fixed from 5)
Symbol 8: HT-LTF   ← kHtTrain1Rel = 8 (fixed from 6)
Symbol 9: HT-DATA  ← kDataStartRel = 9 (fixed from 7)
```

---

## Debug Commands

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Test
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep "PATTERN"
```
