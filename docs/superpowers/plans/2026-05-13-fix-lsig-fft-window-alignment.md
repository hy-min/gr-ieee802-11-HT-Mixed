# Fix L-SIG FFT Window Alignment - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix L-SIG symbol FFT window alignment in SPLITTER/frame_equalizer to achieve <10% bit error rate in hard decision

**Architecture:** The SPLITTER extracts 64-sample time-domain blocks from 80-sample OFDM symbols (skipping CP). The frame_equalizer then FFTs these. The issue is the L-SIG FFT window captures incorrect samples, causing 45% hard判决 error rate.

**Root Cause Hypothesis:** SPLITTER's rel_idx counter may be misaligned for L-SIG symbol boundaries, causing the 64-sample FFT window to capture wrong samples (possibly including CP remnants or skipping actual data).

**Tech Stack:** GNU Radio C++ (ht_symbol_splitter_impl.cc, frame_equalizer_impl.cc)

---

## Problem Evidence

From test output:
```
[TX_LSIG_Original] bits[0:24] = 110101011010000001000000
[RX_LSIG_HardBits] eqbits48[0:24] = 100011100001001111000000
[RX_LSIG_Decoded] bits[0:24] = 111100000000100001000000

[LSIG_RAW] d_sym_idx=2 d_internal_counter=2 - Raw L-SIG subcarriers (before EQ):
  sc[0]=-0.8190-4.9183i | mag=4.9860 phase=-99.5deg
  sc[1]=-2.0466-4.4242i | mag=4.8747 phase=-114.8deg
  sc[44]=3.5048-1.5346i | mag=3.8261 phase=-23.6deg
  sc[45]=3.9942-0.3451i | mag=4.0090 phase=-4.9deg

[SPLITTER_FFTPROBE] type=2 rel_idx=223 td_energy=42.9026 buf_filled=0 first=0.5218-0.9934i last=0.0000+0.0000i
```

**Key observations:**
1. TX L-SIG is BPSK (rate=0x0D, real-axis only)
2. RX L-SIG phases are chaotic (-99.5°, -114.8°, -23.6°, -4.9°) instead of ~0° or ~180°
3. SPLITTER shows `last=0.0000+0.0000i` for L-SIG buffer - possible buffer underflow
4. `buf_filled=0` at output boundary - unusual

---

## File Structure

- Modify: `lib/ht_symbol_splitter_impl.cc` — Fix rel_idx tracking and buffer fill logic
- Modify: `lib/frame_equalizer_impl.cc` — Add absolute position probe
- Test: `test_mcs_end_to_end.py` — Verify L-SIG parity passes

---

## Task 1: Add Absolute Position Probe to SPLITTER

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:420-500`

**步骤:**

- [ ] **Step 1: Add absolute input index probe at L-SIG buffer fill start**

In the main loop where `should_buffer` is set for L-SIG (rel_idx 160-223), add:

```cpp
// Probe: Print absolute input index when L-SIG DATA starts buffering
static int lsig_start_probe = 0;
if (lsig_start_probe < 3 && rel_idx == 160 && should_buffer) {
    uint64_t abs_idx = d_items_processed + i;
    fprintf(stderr, "[SPLITTER_LSIG_ABS] L-SIG DATA starts at abs_idx=%llu current_idx=%llu\n",
            (unsigned long long)abs_idx, (unsigned long long)current_idx);
    lsig_start_probe++;
}
```

- [ ] **Step 2: Add probe at L-SIG buffer output boundary**

At the boundary output section (around line 490), modify the fprintf to include absolute indices:

```cpp
// Add to existing SPLITTER_FFTPROBE output
uint64_t abs_output_idx = d_items_processed;  // approximate
fprintf(stderr, "[SPLITTER_FFTPROBE] type=%d rel_idx=%llu abs_idx=%llu td_energy=%.4f ...",
        symbol_type, (unsigned long long)out_rel_idx,
        (unsigned long long)(d_items_processed - consumed + produced),
        total_energy, ...);
```

- [ ] **Step 3: Build and run to capture absolute indices**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5
```

Run test:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_LSIG_ABS|SPLITTER_FFTPROBE.*type=2" | head -10
```

Expected: See absolute input indices for L-SIG start and output

---

## Task 2: Verify L-SIG Symbol Timing

**Files:**
- Investigate: `lib/ht_symbol_splitter_impl.cc` — Check rel_idx calculation

**步骤:**

- [ ] **Step 1: Verify L-SIG buffer collects exactly 64 samples**

In SPLITTER, the rel_idx should track position relative to frame_start:
- rel_idx 144-159: L-SIG CP (16 samples) → SKIP
- rel_idx 160-223: L-SIG DATA (64 samples) → BUFFER

Check that `should_buffer = (rel_idx >= 160)` correctly captures L-SIG DATA.

- [ ] **Step 2: Check what happens at rel_idx=160 (first L-SIG DATA sample)**

The issue might be that at rel_idx=160, d_buffer_count is not 0 (leftover from previous symbol).

Add probe:
```cpp
// Probe: Check buffer state at rel_idx=160
static int lsig_rel160_probe = 0;
if (lsig_rel160_probe < 3 && rel_idx == 160) {
    fprintf(stderr, "[SPLITTER_LSIG160] rel_idx=160 d_buffer_count=%d d_buffer_filled=%d should_buffer=%d\n",
            d_buffer_count, d_buffer_filled, should_buffer);
    lsig_rel160_probe++;
}
```

- [ ] **Step 3: Verify buffer is reset after L-LTF1 output**

At L-LTF1 boundary (rel_idx=143), the buffer should be output and reset:
```cpp
produced += d_fft_size;
d_buffer_count = 0;
d_buffer_filled = false;
```

Check that this happens BEFORE L-SIG CP (rel_idx 144-159).

---

## Task 3: Fix Buffer Fill Condition Bug

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` — Fix `!d_buffer_filled` condition

**步骤:**

- [ ] **Step 1: Identify the bug**

In SPLITTER, the buffer fill condition is:
```cpp
if (should_buffer && !d_buffer_filled) {
    d_buffer[d_buffer_count++] = in[i];
}
```

The problem: `d_buffer_filled` is set to `true` at non-boundary buffer-full events (line 520), but never reset until the NEXT output boundary. This causes the buffer to NOT be filled for subsequent symbols.

**Root cause scenario:**
1. Buffer fills for L-LTF1 DATA (rel_idx 80-143)
2. At rel_idx=143, buffer is full BUT it's a boundary → output happens, d_buffer_filled=false
3. Buffer fills for L-SIG DATA (rel_idx 160-223)
4. At rel_idx=223, buffer is full AND it's a boundary → output happens

But wait - looking at the output, L-SIG td_energy=42.9 is non-zero. So data IS being buffered. The issue might be different.

Let me re-examine: `buf_filled=0` at L-SIG output might mean the buffer was NOT filled when output happened. This could happen if `d_buffer_filled` was set to `true` prematurely (at rel_idx < 223).

- [ ] **Step 2: Check if d_buffer_filled is being set incorrectly**

Add probe at non-boundary buffer-full events:
```cpp
} else {
    // Buffer filled at non-boundary - hold for next boundary
    d_buffer_filled = true;
    // Debug probe
    static int non_boundary_probe = 0;
    if (non_boundary_probe < 5) {
        fprintf(stderr, "[SPLITTER_NON_BOUNDARY] rel_idx=%llu d_buffer_count=%d - set d_buffer_filled=true\n",
                (unsigned long long)rel_idx, d_buffer_count);
        non_boundary_probe++;
    }
}
```

- [ ] **Step 3: Build and check**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -3
```

Run test and check for non-boundary buffer-full events:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_NON_BOUNDARY" | head -10
```

---

## Task 4: Investigate SPLITTER Output (Time-Domain vs Frequency-Domain)

**Files:**
- Investigate: `lib/ht_symbol_splitter_impl.cc` — Check FFT block placement

**步骤:**

- [ ] **Step 1: Confirm SPLITTER outputs TIME-DOMAIN samples**

SPLITTER description says "Converts 80-sample HT-Mixed OFDM symbols to 64-sample FFT blocks". This is ambiguous - does it do FFT or just strip CP?

Search for FFT execution in SPLITTER:
```bash
grep -n "fftwf_\|volk_\|fftw_\|complex_to_" /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

Expected: No FFT execution in SPLITTER (FFT happens elsewhere)

- [ ] **Step 2: Check what block does FFT after SPLITTER**

Look at the flowgraph connection. The SPLITTER output goes to...?

In `wifi_phy_hier.py`, find the connection after `ht_symbol_splitter`.

- [ ] **Step 3: If SPLITTER does FFT, check the FFT window alignment**

If SPLITTER does FFT internally, the 64 samples in d_buffer should be time-domain, and FFT should be applied before output.

But the probe name "SPLITTER_FFTPROBE" measures "td_energy" (time-domain energy). So SPLITTER outputs time-domain.

---

## Task 5: Fix L-SIG FFT Window Alignment

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` — Fix L-SIG boundary tracking

**步骤:**

Based on findings from Task 1-4, fix the specific issue causing L-SIG misalignment.

Common fixes:
1. **Adjust L-SIG DATA start from rel_idx 160 to 161** (if 1-sample offset)
2. **Reset d_buffer_count explicitly at L-SIG boundary** (if leftover from previous)
3. **Adjust CP skip for L-SIG** (if CP not properly skipped)

Example fix (if offset by 1):
```cpp
} else if (rel_idx < 224) {
    // Stage 2: L-SIG (rel_idx 144-159 CP, 160-223 DATA)
    // FIX: Adjust start from 160 to 161 if needed
    should_buffer = (rel_idx >= 161);  // was >= 160
}
```

---

## Task 6: Verify L-SIG Decoding

**步骤:**

- [ ] **Step 1: Run test and check parity**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LSIG_DECODE|Parity check)" | head -10
```

Expected: `Parity check passed` or no parity failure messages

- [ ] **Step 2: Check hard判决 error rate**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(TX_LSIG_Original|RX_LSIG_HardBits)" | head -5
```

Count bit errors between TX and RX:
- TX: `110101011010000001000000`
- RX: Should be close (≤3 bit errors for Viterbi to correct)

- [ ] **Step 3: Check L-SIG phases are BPSK-like**

Expected: phases clustered near 0° or 180° (not -99.5°, -114.8° etc.)

---

## Task 7: Commit

- [ ] **Step 1: Verify changes**

```bash
cd /home/hy/gr-ieee802-11 && git diff lib/ht_symbol_splitter_impl.cc
```

- [ ] **Step 2: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): fix L-SIG FFT window alignment

Root cause: SPLITTER rel_idx misaligned causing L-SIG FFT window
to capture incorrect samples. RX L-SIG hard判决 error rate was 45%.

Fix: [specific fix based on Task 5 findings]

Changes:
- Add absolute position probes for L-SIG timing verification
- Fix buffer fill condition if d_buffer_filled was set prematurely
- Adjust L-SIG DATA boundary if offset detected"
```

---

## Verification Checklist

- [ ] Build succeeds without errors
- [ ] L-SIG hard判决 error rate < 10% (was 45.8%)
- [ ] L-SIG phases cluster near 0° and 180° (BPSK)
- [ ] L-SIG parity check passes
- [ ] HT-SIG decoding proceeds (if applicable)
- [ ] End-to-end MCS0 reception works

---

## Appendix: SPLITTER rel_idx Reference

```
HT-Mixed 20MHz Preamble Structure (samples from frame_start):

rel_idx 0-63:    L-LTF0 DATA (64 samples, no CP)
rel_idx 64-79:   L-LTF1 CP (16 samples) - SKIP
rel_idx 80-143:  L-LTF1 DATA (64 samples) - BUFFER → FFT
rel_idx 144-159: L-SIG CP (16 samples) - SKIP
rel_idx 160-223: L-SIG DATA (64 samples) - BUFFER → FFT ← L-SIG ISSUE HERE
rel_idx 224-239: HT-SIG0 CP (16 samples) - SKIP
rel_idx 240-303: HT-SIG0 DATA (64 samples) - BUFFER → FFT
rel_idx 304-319: HT-SIG1 CP (16 samples) - SKIP
rel_idx 320-383: HT-SIG1 DATA (64 samples) - BUFFER → FFT
rel_idx 384-399: HT-STF CP (16 samples) - SKIP
rel_idx 400-463: HT-STF DATA (64 samples) - BUFFER → FFT
rel_idx 464+:    HT-DATA (80-sample period: 16 CP + 64 Data)
```
