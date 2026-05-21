# HT-Symbol Splitter CP Boundary Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix ht_symbol_splitter to output FFT blocks at proper DATA boundaries instead of mid-symbol positions.

**Root Cause Identified:** The ht_symbol_splitter outputs FFT blocks at rel_idx=63, 127, 207, 287, 367... but proper DATA boundaries are at rel_idx=143 (LTF1), 223 (L-SIG), 303 (HT-SIG0), etc. This causes CP contamination in FFT windows.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++

---

## Problem Description

With `d_frame_start=192`, the symbol boundaries are:
- LTF0 DATA: rel_idx 16-79
- LTF1 CP: rel_idx 64-79, LTF1 DATA: rel_idx 80-143
- L-SIG CP: rel_idx 144-159, L-SIG DATA: rel_idx 160-223
- HT-SIG0 CP: rel_idx 224-239, HT-SIG0 DATA: rel_idx 240-303
- HT-SIG1 CP: rel_idx 304-319, HT-SIG1 DATA: rel_idx 320-383

**Current behavior:** The FFT fires when d_buffer_count reaches 64, which happens at:
- rel_idx=63 (mid-LTF0 DATA)
- rel_idx=127 (mid-LTF1, capturing 48 LTF1 DATA + 16 LTF1 CP)
- rel_idx=207 (mid-L-SIG, capturing 48 L-SIG DATA + 16 L-SIG CP)
- rel_idx=287 (mid-HT-SIG0, same issue)
- etc.

**Correct behavior:** FFT should fire at:
- rel_idx=143 (end of LTF1 DATA)
- rel_idx=223 (end of L-SIG DATA)
- rel_idx=303 (end of HT-SIG0 DATA)
- rel_idx=383 (end of HT-SIG1 DATA)
- rel_idx=511 (end of HT-STF DATA)

---

## Task 1: Analyze Current ht_symbol_splitter Logic

**Files:**
- Read: `lib/ht_symbol_splitter_impl.cc`

**Step 1: Understand current buffer logic**

The current logic:
1. `should_buffer = true` when inside a DATA region
2. When `d_buffer_count == 64`, output the buffer
3. Buffer fills continuously from rel_idx 16 onwards

**Problem:** Buffer fills in 64-sample chunks regardless of symbol boundaries.

**Step 2: Identify correct output positions**

For HT-Mixed 20MHz with d_frame_start=192:
- LTF0: rel_idx 16-79 → output at 79 (end of LTF0 DATA)
- LTF1: rel_idx 80-143 → output at 143 (end of LTF1 DATA)
- L-SIG: rel_idx 160-223 → output at 223 (end of L-SIG DATA)
- HT-SIG0: rel_idx 240-303 → output at 303 (end of HT-SIG0 DATA)
- HT-SIG1: rel_idx 320-383 → output at 383 (end of HT-SIG1 DATA)
- HT-STF: rel_idx 384-447 → output at 447 (end of HT-STF DATA)
- HT-DATA: rel_idx 464+ → output every 80 samples (at 511, 591, 671...)

---

## Task 2: Implement Fix

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

**Approach:** Instead of outputting when buffer is full, track symbol boundaries and output at correct positions.

**Key changes:**

1. Calculate expected "end of DATA" positions for each header symbol
2. When buffer count reaches 64 AND we're at a DATA end position, output
3. For HT-DATA (beyond rel_idx 447), output every 80 samples

**Pseudo-code for the fix:**

```cpp
// Correct output positions for HT-Mixed 20MHz header
// These are rel_idx values where DATA region ends
const int kHeaderOutputPositions[] = {
    79,   // LTF0 DATA end
    143,  // LTF1 DATA end
    223,  // L-SIG DATA end
    303,  // HT-SIG0 DATA end
    383,  // HT-SIG1 DATA end
    447,  // HT-STF DATA end
};

// After header, HT-DATA outputs every 80 samples starting at 511
// (rel_idx 448-463 is HT-STF CP, 464-527 is HT-STF DATA)

bool should_output_at_rel_idx(uint64_t rel_idx) {
    // Check if at end of a header DATA region
    for (int pos : kHeaderOutputPositions) {
        if (rel_idx == (uint64_t)pos) {
            return d_buffer_count == 64;
        }
    }
    // HT-DATA: every 80 samples starting at 511
    if (rel_idx >= 511 && ((rel_idx - 511) % 80 == 0)) {
        return d_buffer_count == 64;
    }
    return false;
}
```

---

## Task 3: Test the Fix

**Commands:**

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Test
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG_DECODE|CHAN_EST|HT-SIG"
```

**Expected:**
- L-SIG decoded bits should be `110100000011000001XX`
- Channel estimation should show no "opposite signs" between LTF0 and LTF1
- HT-SIG CRC should match TX (0x41)

---

## Alternative Approach: Timing Diagram

If the above approach is too complex, consider:

The issue is that d_frame_start=192 creates an offset problem. Perhaps we should change d_frame_start to align properly with symbol boundaries.

With d_frame_start=176 (L-LTF0 DATA start), the boundaries are cleaner:
- LTF0 DATA: rel_idx 0-63
- LTF1 CP: rel_idx 64-79, LTF1 DATA: rel_idx 80-143
- L-SIG CP: rel_idx 144-159, L-SIG DATA: rel_idx 160-223
- HT-SIG0 CP: rel_idx 224-239, HT-SIG0 DATA: rel_idx 240-303

Still has the same CP issue.

The real fix is to ensure the FFT output happens at the correct relative position within each symbol's DATA region.

---

## Success Criteria

1. L-LTF channel estimation shows no "opposite signs" warnings
2. L-SIG decoded bits match expected `110100000011000001XX`
3. L-SIG parity check passes consistently
4. HT-SIG decode is reached
5. HT-SIG CRC matches TX (0x41)
