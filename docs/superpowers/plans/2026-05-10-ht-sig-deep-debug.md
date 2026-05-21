# HT-SIG Deep Debug Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Debug HT-SIG CRC failure where computed_crc=0x41 but rx_crc never matches. Goal is to identify the root cause in the signal path and fix it.

**Architecture:** IEEE 802.11n HT-mixed mode with L-SIG and HT-SIG headers. The issue is that HT-SIG decoded bits have ~50% error rate even though L-SIG sometimes decodes correctly and all 16 rotation/inversion combinations are tried.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, NumPy

---

## Current State (2026-05-10)

### Symptoms
- HT-SIG CRC: computed_crc=0x41 never matches rx_crc (tried all 16 combinations)
- TX HT-SIG: mcs=0, len=96, bw40=0, crc=0x41
- L-SIG: Sometimes decodes correctly (rate=0x0D, enc=0)
- HT-SIG pilots at indices 48-51 show unexpected phases

### Key Observations from Debugging
1. **Deinterleaver is correct** - Verified with Python that `j = 16*(k%3) + k/3` correctly inverts TX interleaver
2. **Viterbi decoder logic is correct** - Standard 64-state Viterbi with Hamming distance
3. **All 16 rot/inv combinations tried** - None produce valid CRC
4. **Equalized symbols have wrong phases** - HT-SIG eq symbols show phases like 123°, 146° instead of ±90°

### Critical Debug Output

TX HT-SIG0 interleaved bits: `000000000111100000000000000001000000000011101100`
After deinterleave: `000000000000000000000000001101101100101011` (matches TX enc96)

HT-SIG0 pilots (indices 48-51): `-8.493+-2.576i, 8.832+0.870i, 8.832+-0.870i, -8.493+2.576i`
These pilots have phases inconsistent with a flat channel.

---

## Root Cause Hypothesis

The channel estimate `Hhdr52` computed from L-LTF does not correctly compensate HT-SIG symbols. Possible causes:

1. **FFT window misalignment** - HT-SIG FFT captures wrong samples (CP instead of data)
2. **FFT bin mapping error** - kHeader48Bin or kPilot4Bin don't match actual FFT output
3. **Channel estimate error** - L-LTF pilots give wrong phase reference for HT-SIG
4. **CPE estimation error** - HT-SIG CPE estimation adds wrong rotation

---

## Task 1: Add TX Symbol Capture for Known-Answer Test

**Files:**
- Modify: `examples/test_loopback_noqt.py` - Add TX symbol capture

- [ ] **Step 1: Add debug output to capture TX HT-SIG symbols**

Add to the TX path (wifi_phy_hier or related) to print the actual TX HT-SIG symbols before they go to the channel. This will let us compare TX and RX symbols directly.

```python
# In test_loopback_noqt.py, after wifi construction:
# Add a message probe to capture TX HT-SIG
```

- [ ] **Step 2: Run test to capture TX symbols**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "TX.*HT-SIG|TX.*intl96"
```

Expected: Capture TX HT-SIG intl96 bits for comparison with RX

---

## Task 2: Verify FFT Bin Mapping

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add FFT bin verification

- [ ] **Step 1: Add debug to print raw FFT bins for HT-SIG0**

In the function that extracts HT-SIG0 (before any processing), add:

```cpp
// After extract_header52_from_sym64 is called for HT-SIG0
fprintf(stderr, "[FFT_RAW_HT0] sym64 bins (relative to kHeader48Bin[0]):\n");
for (int i = 0; i < 48; i++) {
    int bin = kHeader48Bin[i];
    fprintf(stderr, "  SC%d (bin%d) = %.3f+%.3fi\n",
            kHeader48Sc[i], bin, sym64[bin].real(), sym64[bin].imag());
}
fprintf(stderr, "[FFT_RAW_HT0] Pilots:\n");
for (int i = 0; i < 4; i++) {
    int bin = kPilot4Bin[i];
    fprintf(stderr, "  Pilot idx%d (bin%d) = %.3f+%.3fi\n",
            48+i, bin, sym64[bin].real(), sym64[bin].imag());
}
```

- [ ] **Step 2: Compile**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 3: Run test and capture FFT bins**

```bash
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep "FFT_RAW_HT0"
```

- [ ] **Step 4: Analyze FFT bin values**

Check if the FFT bins contain expected HT-SIG data (should see ±1 values for BPSK).

---

## Task 3: Add Per-Symbol RX/TX Comparison

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add TX reference capture

- [ ] **Step 1: Add TX bit capture to file**

Add to the TX path in chunks_to_symbols or related block to write TX HT-SIG bits to a file:

```cpp
// When generating HT-SIG symbols, write to file
static void write_htsig_bits_to_file(const char* filename, const uint8_t* bits, int n) {
    static bool first = true;
    FILE* f = fopen(filename, first ? "w" : "a");
    if (f) {
        for (int i = 0; i < n; i++) fprintf(f, "%d", bits[i]);
        fprintf(f, "\n");
        fclose(f);
    }
    first = false;
}
```

- [ ] **Step 2: Add RX bit capture**

In `decode_htsig_from_rotated`, write the deinterleaved bits before Viterbi:

```cpp
fprintf(stderr, "[RX_ENC96] ");
for (int i = 0; i < 96; i++) fprintf(stderr, "%d", enc96[i]);
fprintf(stderr, "\n");
```

- [ ] **Step 3: Compare TX vs RX bits**

```bash
# Capture TX
LD_LIBRARY_PATH=... timeout 30 python ... 2>&1 | grep "TX.*intl96" | head -1 > /tmp/tx_htsig.txt

# Capture RX
LD_LIBRARY_PATH=... timeout 30 python ... 2>&1 | grep "RX_ENC96" | head -1 > /tmp/rx_htsig.txt

# Compare
diff /tmp/tx_htsig.txt /tmp/rx_htsig.txt
```

---

## Task 4: Verify Channel Estimate Quality

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - Add H52 phase analysis

- [ ] **Step 1: Print Hhdr52 phase per subcarrier**

In the HT-SIG decode section (before calling decode_htsig_from_rotated), add:

```cpp
fprintf(stderr, "[CHAN_EST] Hhdr52 phases (subcarriers 0-47):\n");
for (int i = 0; i < 48; i++) {
    float phase_deg = std::arg(Hhdr52[i]) * 180.0f / M_PI;
    fprintf(stderr, "  SC%d: mag=%.3f phase=%+.1fdeg\n",
            kHeader48Sc[i], std::abs(Hhdr52[i]), phase_deg);
}
fprintf(stderr, "[CHAN_EST] Hhdr52 pilot phases:\n");
for (int i = 0; i < 4; i++) {
    float phase_deg = std::arg(Hhdr52[48+i]) * 180.0f / M_PI;
    fprintf(stderr, "  Pilot%d: mag=%.3f phase=%+.1fdeg\n",
            i, std::abs(Hhdr52[48+i]), phase_deg);
}
```

- [ ] **Step 2: Analyze channel phase**

Expected: Channel phases should be relatively consistent (within ±30°) for a flat channel.
If phases vary wildly (e.g., ±90°), the channel estimate is bad.

---

## Task 5: Check ht_symbol_splitter Symbol Boundaries

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - Add symbol boundary debug

- [ ] **Step 1: Add rel_idx tracking debug**

After extracting each symbol, print the rel_idx:

```cpp
fprintf(stderr, "[SPLITTER] Output sym64 at rel_idx=%llu, internal_counter=%d\n",
        (unsigned long long)rel_idx, d_internal_symbol_counter);
```

- [ ] **Step 2: Verify HT-SIG0/1 rel_idx values**

```bash
LD_LIBRARY_PATH=... timeout 30 python ... 2>&1 | grep "SPLITTER.*rel_idx"
```

Expected output should show HT-SIG0 at rel_idx=192 and HT-SIG1 at rel_idx=256.

---

## Task 6: Identify Root Cause and Implement Fix

**Files:**
- TBD based on findings from Tasks 1-5

- [ ] **Step 1: Analyze collected debug data**

Based on Tasks 1-5 output, identify which signal processing stage is corrupted.

- [ ] **Step 2: Implement targeted fix**

Fix the identified issue:
- If FFT bins wrong: Fix kHeader48Bin/kPilot4Bin mapping
- If channel estimate bad: Fix pilot-based channel estimation
- If symbol timing wrong: Fix ht_symbol_splitter boundaries
- If CPE wrong: Fix rotation compensation

- [ ] **Step 3: Verify fix**

```bash
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep "PARSE_HT_SIG.*CRC.*mismatch"
```

Expected: No "CRC mismatch" messages.

---

## Task 7: Clean Up Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Remove all debug fprintf statements added in Tasks 1-5**

- [ ] **Step 2: Compile and verify build**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate conda
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Capture all HT-SIG debug
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "HT_SIG|FFT_RAW|RX_ENC96|TX.*HTSIG|CHAN_EST"

# Quick CRC check
LD_LIBRARY_PATH=... timeout 60 python ... 2>&1 | grep "PARSE_HT_SIG"
```

## Success Criteria

1. HT-SIG CRC matches: `computed_crc == rx_crc == 0x41`
2. HT-SIG fields decoded: `mcs=0, len=96, bw40=0, agg=0, sgi=0, stbc=0, nltf=0`
3. `d_have_ht_header == 1`
4. HT-DATA emitted correctly
5. FCS PASS

## Files to Modify

| File | Changes |
|------|---------|
| `lib/frame_equalizer_impl.cc` | Add FFT bin, channel estimate, and bit capture debug |
| `lib/ht_symbol_splitter_impl.cc` | Add rel_idx tracking debug |
| `examples/test_loopback_noqt.py` | Add TX symbol capture |
