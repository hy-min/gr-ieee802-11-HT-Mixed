# HT Mixed Mode TX/RX Debug Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Debug why RX only processes one symbol and L-SIG decoding fails after TX modifications

**Architecture:** TX chain now uses mixed_mode_carrier_allocator + insert_ht_training for HT mixed mode. Need to verify TX output format matches RX expectations.

**Tech Stack:** GNU Radio, IEEE 802.11, Python, C++

---

## Problem Statement

After modifying wifi_phy_hier.py to use:
- `htsig.ht_sig_field()` for HT-SIG generation
- `mixed_mode_carrier_allocator` for L-STF/L-LTF preambles
- `insert_ht_training` for HT-STF/HT-LTF insertion

RX behavior:
- L-LTF detected successfully (`lltf0=1`)
- L-SIG decoding fails (`lsig=0`)
- Only one symbol processed (`d_sym_idx=0`) before stopping

---

## Task 1: Verify TX L-STF/L-LTF Preamble Generation

**Files:**
- Debug: `examples/mixed_mode_carrier_allocator.py` - TX preamble output
- Debug: `examples/wifi_constellation.py` - capture TX output

- [ ] **Step 1: Add debug print in mixed_mode_carrier_allocator to verify preamble output**

In `mixed_mode_carrier_allocator.py`, add at the start of `general_work`:

```python
# Debug: print first few outputs
if self._debug_call_count < 3:
    print(f"[MM-CA] general_work called, noutput_items={noutput_items}, ninput={len(input_items[0])}")
    print(f"[MM-CA] First output symbol (64 bins): {output_items[0][0][:8]}")
    self._debug_call_count += 1
```

- [ ] **Step 2: Run wifi_constellation.py and check MM-CA output**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "MM-CA"`
Expected: Should see mixed_mode_carrier_allocator output with 4 preambles

- [ ] **Step 3: Verify preamble sequence**

Expected output after header processing:
- Symbol 0: L-STF (fftshift order)
- Symbol 1: L-STF
- Symbol 2: L-LTF
- Symbol 3: L-LTF

---

## Task 2: Verify TX IFFT Output (Time Domain)

**Files:**
- Debug: `examples/wifi_constellation.py` - capture TX before CP

- [ ] **Step 1: Add file sink after IFFT to capture TX time-domain signal**

In wifi_phy_hier.py, add debug sink:
```python
# Debug: TX time domain capture
self.tx_td_sink = blocks.file_sink(gr.sizeof_gr_complex, "/tmp/tx_time_domain.dat", False)
self.connect(self.ifft, self.tx_td_sink)  # Add this connection
```

- [ ] **Step 2: Run and capture TX output**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | head -50`

- [ ] **Step 3: Analyze TX time domain in Python**

```python
import numpy as np
data = np.fromfile("/tmp/tx_time_domain.dat", dtype=np.complex64)
print(f"TX samples: {len(data)}")
# L-STF is 160 samples (16 CP + 64 data) * 2
# L-LTF is 160 samples * 2
# HT-STF + HT-LTF = 320 samples
print(f"First 80 samples magnitude: {np.abs(data[:80])}")
```

---

## Task 3: Verify RX FFT Input Matches TX IFFT Output

**Files:**
- Debug: `examples/wifi_constellation.py` - capture RX after channel

- [ ] **Step 1: Add file sink to capture RX input to FFT**

In wifi_constellation.py, after channel:
```python
# Debug: RX input capture
self.rx_in_sink = blocks.file_sink(gr.sizeof_gr_complex, "/tmp/rx_input.dat", False)
tb.connect((chan, 0), (self.rx_in_sink, 0))  # Add this connection
```

- [ ] **Step 2: Run and capture RX input**

- [ ] **Step 3: Compare TX output and RX input**

Expected: RX input should be identical to TX output (loopback with epsilon=1.0)

---

## Task 4: Debug RX Frame Equalizer Symbol Processing

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - add detailed symbol tracking

- [ ] **Step 1: Add debug output for each consumed symbol**

In `general_work`, after `consumed++`:
```cpp
fprintf(stderr, "[EQ][SYMBOL] consumed=%d d_sym_idx=%d d_internal_counter=%d abs_off=%llu\n",
        consumed, d_sym_idx, d_internal_symbol_counter, (unsigned long long)abs_in_off);
```

- [ ] **Step 2: Run and check symbol processing sequence**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "SYMBOL"`
Expected: Should see sequence 0, 1, 2, 3... for each symbol

- [ ] **Step 3: Check if there's a continue statement blocking processing**

In frame_equalizer_impl.cc, search for `continue` inside the while loop and verify none are incorrectly跳过 symbols.

---

## Task 5: Verify L-SIG Decode Logic

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - check decode_lsig_direct_from_header52

- [ ] **Step 1: Add debug output before L-SIG decode**

In the L-SIG decode section (around line 2891):
```cpp
fprintf(stderr, "[EQ][LSIG_DECODE] Calling decode_lsig_direct, d_early_eqsym[kLSigRel] first 4 bins:\n");
for (int i = 0; i < 4; i++) {
    fprintf(stderr, "  [%d] = %.4f+%.4fi\n", i,
            d_early_eqsym[kLSigRel][i].real(),
            d_early_eqsym[kLSigRel][i].imag());
}
```

- [ ] **Step 2: Run and check L-SIG input values**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "LSIG_DECODE"`
Expected: Should show L-SIG symbol values (BPSK modulated)

- [ ] **Step 3: Verify decode function behavior**

If L-SIG values are non-zero but decode fails, problem is in decode_lsig_direct_from_header52.

---

## Task 6: Check wifi_start Tag Propagation

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - track wifi_start tags

- [ ] **Step 1: Add debug output for wifi_start detection**

In general_work, after wifi_start detection (line 2221):
```cpp
fprintf(stderr, "[EQ][WIFI_START] abs_off=%llu wifi_start=%d d_in_frame=%d\n",
        (unsigned long long)abs_in_off, wifi_start ? 1 : 0, d_in_frame ? 1 : 0);
```

- [ ] **Step 2: Run and check wifi_start tags**

Run: `timeout 15 python examples/wifi_constellation.py 2>&1 | grep "WIFI_START"`
Expected: wifi_start should be detected at the beginning of the frame

- [ ] **Step 3: Verify tag source**

Check where wifi_start tags are generated - should be in sync_long or tag_symidx_tagger.

---

## Task 7: Fix Identified Issues

Based on Tasks 1-6, fix the root cause:

**Possible issues:**
1. **TX/RX subcarrier format mismatch**: TX uses fftshift order, RX expects different
2. **Symbol timing mismatch**: RX expects symbols at different positions
3. **Pilot configuration mismatch**: HT-SIG pilots not correctly configured
4. **Packet length tag mismatch**: Tag value doesn't match actual symbol count

**After identifying issue:**
- [ ] Fix the root cause
- [ ] Rebuild: `cd build && make -j$(nproc)`
- [ ] Re-run test

---

## Task 8: Verify Complete TX/RX Loop

**Files:**
- Test: `examples/wifi_constellation.py`

- [ ] **Step 1: Run full test**

Run: `timeout 30 python examples/wifi_constellation.py 2>&1 | grep -E "(LSIG|HT_SIG|d_have_ht|FCS|PARSE)"`
Expected:
- `d_have_ht_header=1`
- HT-SIG CRC match
- FCS PASS

- [ ] **Step 2: Verify constellation plot**

Constellation should show two clusters at ±1 (BPSK) for HT-DATA.

---

## Key Files Reference

### TX Chain
```
mapper → ht_header (htsig.ht_sig_field) → chunks_to_symbols → mux →
carrier_allocator (mixed_mode) → insert_ht → ifft → cp → output
```

### RX Chain
```
input → sync_short → sync_long → s2v(64) → fft → tag_tagger →
frame_equalizer → v2s(52) → decode_mac
```

### Important Constants
```cpp
static constexpr int kLltf0Rel = 0;      // L-LTF symbol position
static constexpr int kLSigRel = 2;       // L-SIG symbol position
static constexpr int kHtSig0Rel = 3;     // HT-SIG1 position
static constexpr int kHtSig1Rel = 4;    // HT-SIG2 position
static constexpr int kDataStartRel = 7;   // First HT-DATA symbol
```

### Expected Frame Structure (HT Mixed Mode)
```
Symbol 0: L-STF
Symbol 1: L-STF
Symbol 2: L-LTF
Symbol 3: L-LTF
Symbol 4: L-SIG
Symbol 5: HT-SIG1
Symbol 6: HT-SIG2
Symbol 7: HT-STF (insert_ht_training)
Symbol 8: HT-LTF (insert_ht_training)
Symbol 9+: HT-DATA
```

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Run with filters
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 20 python examples/wifi_constellation.py 2>&1 | grep "PATTERN"
```
