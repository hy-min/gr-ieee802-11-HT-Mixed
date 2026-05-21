# HT-SIG FFT Window Alignment Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix HT-SIG decoding by ensuring FFT window is aligned with HT-SIG DATA boundaries, not CP boundaries.

**Architecture:** In HT-mixed mode, the ht_symbol_splitter outputs FFT blocks at symbol boundaries. However, the FFT window may be capturing CP (cyclic prefix) data instead of the actual HT-SIG DATA region, causing the equalized symbols to have wrong phases.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, NumPy

---

## Current State (2026-05-11)

### Problem
- HT-SIG CRC fails: `computed_crc=0x41` but `rx_crc` never matches
- ~50% bit error rate between TX encoded bits and RX decoded bits
- Equalized HT-SIG symbols show phases scattered across -180° to +180° instead of clustering near ±90°

### Key Finding from Previous Debug Session
The ht_symbol_splitter outputs FFT blocks at rel_idx positions like 303, 383, 463... These boundaries are calculated as `out_rel_idx % 80 == 63`, which means:
- The FFT window ends 1 sample before the 80-sample boundary
- For HT-SIG0 at rel_idx 240-303 (DATA region), the FFT output at rel_idx 303 is the LAST sample of the HT-SIG0 DATA, not the first

This causes the FFT to capture samples from the transition region (end of HT-SIG0 DATA + start of HT-SIG1 CP) instead of clean HT-SIG DATA.

### HT-Mixed Mode Symbol Structure
```
Symbol:      CP (16 samples) | DATA (64 samples)
HT-SIG0:     rel_idx 240-255  | rel_idx 256-319
HT-SIG1:     rel_idx 320-335  | rel_idx 336-399
HT-STF:      rel_idx 400-415  | rel_idx 416-479
```

Current boundary detection outputs at rel_idx 303, 383, 463... which is the END of one symbol's DATA, not the start of the next.

### Relevant Files
- `lib/ht_symbol_splitter_impl.cc` - Symbol boundary detection
- `lib/frame_equalizer_impl.cc` - FFT processing and HT-SIG decoding
- `lib/sync_long.cc` - Frame timing (d_frame_start=176)

---

## Task 1: Verify FFT Output Position for HT-SIG

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - Add targeted debug

- [ ] **Step 1: Add debug to print FFT output rel_idx for HT-SIG symbols**

In `general_work`, when outputting a symbol at a boundary, print the rel_idx:

```cpp
// Debug: Print which symbol type is being output
if (d_internal_symbol_counter >= 2 && d_internal_symbol_counter <= 4) {
    fprintf(stderr, "[SPLITTER] Output symbol %d at rel_idx=%llu\n",
            d_internal_symbol_counter, (unsigned long long)out_rel_idx);
}
```

- [ ] **Step 2: Compile**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 3: Run test and check output positions**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep "SPLITTER.*Output"
```

Expected output should show HT-SIG0 (counter 3) and HT-SIG1 (counter 4) output positions.

---

## Task 2: Analyze Symbol Boundary Calculation

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - Analyze boundary formula

- [ ] **Step 1: Examine the boundary detection formula**

The current formula at line 334 is:
```cpp
bool at_boundary = (out_rel_idx % 80 == 63);
```

This outputs when `out_rel_idx % 80 == 63`, meaning:
- out_rel_idx = 63, 143, 223, 303, 383, 463...

For HT-SIG0 DATA (rel_idx 256-319), the symbol ends at 319. But the boundary at 303 is INSIDE the HT-SIG0 DATA region, not at a clean symbol boundary.

- [ ] **Step 2: Calculate correct boundary positions**

For 80-sample symbols with 16-sample CP:
- HT-SIG0: CP=240-255, DATA=256-319 → DATA ends at 319, CP of next symbol starts at 320
- HT-SIG1: CP=320-335, DATA=336-399 → DATA ends at 399

The FFT should output at rel_idx positions: 319, 399, 479... (END of each symbol's DATA)
NOT: 303, 383, 463... (which are in the middle of DATA regions)

Wait - actually the FFT window captures 64 samples. If we want to capture HT-SIG0 DATA (256-319), we should output when rel_idx reaches 319 (so buffer contains 256-319).

Current formula `out_rel_idx % 80 == 63` gives:
- At out_rel_idx=63: buffer[0-63] = rel_idx 0-63 (L-LTF0 DATA) ✓
- At out_rel_idx=143: buffer[0-63] = rel_idx 80-143 (L-LTF1 DATA) ✓
- At out_rel_idx=223: buffer[0-63] = rel_idx 160-223 (L-SIG DATA) ✓
- At out_rel_idx=303: buffer[0-63] = rel_idx 240-303 ???

240-303 is NOT clean! It includes part of HT-SIG0 CP (240-255) and HT-SIG0 DATA (256-303).

- [ ] **Step 3: Verify with debug output**

The output at out_rel_idx=303 captures rel_idx 240-303, which is:
- HT-SIG0 CP (240-255) + HT-SIG0 DATA (256-303)

This is WRONG. We want HT-SIG0 DATA (256-319), which would be output at out_rel_idx=319.

---

## Task 3: Fix Symbol Boundary Calculation

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - Fix boundary formula

- [ ] **Step 1: Identify correct boundary positions**

For 80-sample symbols:
```
Symbol N:    CP = rel_idx 80k to 80k+15
             DATA = rel_idx 80k+16 to 80k+79

Output at end of DATA: rel_idx = 80k+79 = 80*(k+1) - 1
```

So boundaries should be at: 63, 143, 223, 303, 383, 463, 543...

Wait - 63 = 80*1 - 1, 143 = 80*2 - 1, 223 = 80*3 - 1, etc.

But HT-SIG symbols have DIFFERENT structure:
- HT-SIG0 CP: 240-255 (16 samples)
- HT-SIG0 DATA: 256-319 (64 samples) - should output here
- HT-SIG1 CP: 320-335 (16 samples)
- HT-SIG1 DATA: 336-399 (64 samples) - should output here

So for HT-SIG0, we want to output at rel_idx 319 (end of DATA).
For HT-SIG1, we want to output at rel_idx 399 (end of DATA).

Current formula `out_rel_idx % 80 == 63` gives:
- 63, 143, 223, 303, 383, 463, 543...

303 is NOT 319! 303 corresponds to capturing rel_idx 240-303, which includes HT-SIG0 CP (240-255).

The issue is that for HT-SIG symbols, the CP is 16 samples but the formula assumes CP=16 always starts at 80k.

- [ ] **Step 2: Implement correct boundary detection for HT-Mixed mode**

HT-Mixed 20MHz structure:
```
L-LTF0: CP(160-175) DATA(176-239) → output at 239 (end)
L-LTF1: CP(240-255) DATA(256-319) → output at 319 (end)
L-SIG:  CP(320-335) DATA(336-399) → output at 399 (end)
HT-SIG0: CP(368-383) DATA(384-447) → output at 447 (end)
HT-SIG1: CP(448-463) DATA(464-527) → output at 527 (end)
```

But d_frame_start=176 means rel_idx 0 = input 176.

So output should be at rel_idx:
- L-LTF0 DATA: 0-63 → output at 63
- L-LTF1 DATA: 80-143 → output at 143
- L-SIG DATA: 160-223 → output at 223
- HT-SIG0 DATA: 240-303 → output at 303? NO! 240-303 is wrong.

Wait, I need to recalculate.

If d_frame_start=176 (L-LTF0 DATA start), then:
- L-LTF0 DATA: rel_idx 0-63 (input 176-239)
- L-LTF1 DATA: rel_idx 80-143 (input 256-319) - but where's CP?

Actually, the structure is:
- L-LTF0: input 160-239 = CP(160-175) + DATA(176-239)
- Since sync_long outputs starting at 176, we see DATA(176-239) at rel_idx 0-63

- L-LTF1: input 240-319 = CP(240-255) + DATA(256-319)
- At rel_idx: 64-79 (CP), 80-143 (DATA)

So L-LTF1 DATA starts at rel_idx 80, ends at 143.

But 143 = 80*2 - 1 = 80*(k+1) - 1 for k=1.

The pattern `out_rel_idx % 80 == 63` works for L-LTF because:
- k=0: output at 63 (L-LTF0 DATA end)
- k=1: output at 143 (L-LTF1 DATA end)

But for HT-SIG, the structure changes:
- HT-SIG0: CP(368-383), DATA(384-447) in input
- With d_frame_start=176: rel_idx = input - 176
- HT-SIG0 CP: rel_idx 192-207 (368-383 - 176)
- HT-SIG0 DATA: rel_idx 208-271 (384-447 - 176)

So HT-SIG0 DATA ends at rel_idx 271, not 303!

Current formula gives 303, which is INSIDE HT-SIG0 DATA (208-271 is DATA, 272+ is next CP).

The issue: current formula doesn't account for the fact that HT-SIG symbols have CP BEFORE their DATA, and the CP lengths vary.

- [ ] **Step 3: Implement HT-specific boundary detection**

```cpp
// For HT-mixed mode, boundaries are:
// L-LTF0 DATA: 0-63 → output at 63
// L-LTF1 DATA: 80-143 → output at 143
// L-SIG DATA: 160-223 → output at 223
// HT-SIG0 DATA: 208-271 → output at 271 (not 303!)
// HT-SIG1 DATA: 288-351 → output at 351 (not 383!)

// Pattern: output at end of DATA region, which is:
// L-LTF/L-SIG: 64k - 1 (for k=1,2,3)
// HT-SIG: different pattern

// Actually the issue is: after L-SIG, the symbol structure changes
// L-SIG ends at input 399 = rel_idx 223
// HT-SIG0 starts at input 384 = rel_idx 208 (DATA)

// But the ht_symbol_splitter is consuming 80 samples at a time
// and outputting 64-sample FFT blocks

// The key question: where does each 64-sample FFT block START?

// For HT-SIG0 DATA (rel_idx 208-271), the FFT should capture 208-271
// This means output at rel_idx 271 (end of HT-SIG0 DATA)

// Current formula: out_rel_idx % 80 == 63
// At out_rel_idx=303: buffer = rel_idx 240-303

// But 240-303 includes HT-SIG0 CP (240-255) + part of DATA (256-303)!

// Fix: output at rel_idx 271 for HT-SIG0, 351 for HT-SIG1

// The pattern is: for HT symbols, output when buffer contains the LAST 64 samples of DATA
// HT-SIG0 DATA: 208-271 → output when current_idx reaches 271 (so buffer[0] = 208)
// HT-SIG1 DATA: 288-351 → output when current_idx reaches 351

// Current formula gives 303, 383 instead of 271, 351

// The issue is that the CP before HT-SIG is 16 samples (368-383 in input)
// But the formula assumes CP is always at the start of the 80-sample window

// For HT-SIG0:
// - CP: rel_idx 192-207 (16 samples)
// - DATA: rel_idx 208-271 (64 samples)

// To capture DATA, we need to output at rel_idx 271
// But current formula outputs at 303 = 271 + 32

// The 32 extra samples = half a symbol... this suggests a timing offset

// Let me verify with the actual output:
// SPLITTER shows counter 3 at rel_idx 303
// counter 3 = HT-SIG0
// rel_idx 303 in buffer = rel_idx 240-303
// That's CP(240-255) + DATA(256-303) - WRONG!

// We want DATA(208-271) = buffer rel_idx 208-271
// So we need to output at rel_idx 271, not 303
```

Based on analysis, the fix is to change the boundary detection to account for HT-SIG specific timing:

```cpp
// In ht_symbol_splitter_impl.cc, fix the boundary detection

// Instead of just checking out_rel_idx % 80 == 63,
// also check if we're at a HT-specific boundary

// HT-SIG DATA regions:
// HT-SIG0 DATA: rel_idx 208-271 (input 384-447)
// HT-SIG1 DATA: rel_idx 288-351 (input 464-527)

// For HT-SIG symbols, we need to output at rel_idx 271 and 351
// Current formula gives 303 and 383

// The difference is 32 samples = half of 64
// This suggests the HT-SIG symbols are being misaligned by half a symbol

// Fix: adjust the boundary detection for HT symbols
```

---

## Task 4: Verify Fix with Test

- [ ] **Step 1: Apply the boundary fix**

Based on analysis in Task 3, implement the correct boundary detection.

- [ ] **Step 2: Build and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep "HT_SIG\|CRC"
```

Expected: HT-SIG CRC passes (computed_crc == rx_crc == 0x41)

---

## Task 5: Clean Up Debug Output

- [ ] **Step 1: Remove debug statements added in Task 1**

- [ ] **Step 2: Commit**

---

## Debug Commands

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate conda
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Test HT-SIG
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "HT_SIG|CRC|PARSE"
```

## Success Criteria

1. HT-SIG CRC matches: `computed_crc == rx_crc == 0x41`
2. `d_have_ht_header == 1`
3. HT-SIG fields decoded correctly: `mcs=0, len=96, bw40=0, agg=0, sgi=0`
4. HT-DATA emits correctly
5. FCS PASS
