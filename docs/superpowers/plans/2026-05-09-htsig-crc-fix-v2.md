# HT-SIG CRC Fix Implementation Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix HT-SIG CRC mismatch — TX sends CRC=0x41 but RX computed CRC never matches.

**Architecture:** The gr-htsig module (`/home/hy/src/gr-htsig/`) is a SEPARATE GNU Radio out-of-tree module that generates HT-SIG headers. wifi_phy_hier.py uses `htsig.ht_sig_field()` from gr-htsig, NOT ieee802_11's signal_field. The fix must be applied to gr-htsig source.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, gr-htsig

---

## Problem Statement

After fixing sync_long d_frame_start=176 and ht_symbol_splitter LTF1 CP skip, HT-SIG CRC still fails:
- TX sends HT-SIG with CRC=0x41 (from gr-htsig's compute_ht_sig_crc)
- RX computed CRC never matches
- Viterbi output dec48 is completely wrong

Root causes identified:
1. **CRC computation mismatch**: gr-htsig's TX uses final inversion (c[j] ^ 1) which produces 0x41. The RX (frame_equalizer_impl.cc) also uses inversion. If both use same inversion, they should match. But they don't - suggesting the issue is in the Viterbi/equalization chain, not just CRC.
2. **gr-htsig is separate**: The wifi_phy_hier uses gr-htsig's ht_sig_field, not ieee802_11's signal_field. Changes to ieee802_11 have NO effect.

---

## Key Discovery

The `[TX][HTSIG] fields: ... crc=0x41` output comes from `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc`, NOT from ieee802_11's signal_field_impl.cc.

The wifi_phy_hier.py uses:
```python
self.header_formatter = htsig.ht_sig_field()  # from gr-htsig module, NOT ieee802_11
```

---

## File Structure

### gr-htsig Module (TX Source)

| File | Lines | Responsibility |
|------|-------|----------------|
| `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc` | 139-184 | `compute_ht_sig_crc` — TX HT-SIG CRC computation |
| `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc` | 78 | `dbg_print_htsig_fields` — prints crc=0x41 |

### ieee802_11 Module (RX)

| File | Lines | Responsibility |
|------|-------|----------------|
| `lib/frame_equalizer_impl.cc` | 859-903 | `ht_sig_crc8_calc` — RX HT-SIG CRC computation |
| `lib/frame_equalizer_impl.cc` | 1468-1673 | `decode_htsig_from_rotated` — full HT-SIG decode |

---

## Task 1: Verify gr-htsig CRC Implementation

**Files:**
- Modify: `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc:139-184` (`compute_ht_sig_crc`)
- Rebuilt: `/home/hy/src/gr-htsig/build/lib/libgnuradio-htsig.so.1.0.0.0`

### Step 1: Read gr-htsig CRC implementation

**File: `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc:139-184`**

```cpp
static uint8_t compute_ht_sig_crc(const unsigned char* info_bits /* len >= 34 */)
{
    int c[8];
    for (int i = 0; i < 8; i++) {
        c[i] = 1;  // init all-ones
    }

    for (int i = 0; i < 34; i++) {
        const int m = (info_bits[i] ? 1 : 0);
        // shift register update
        const int c0 = c[0], c1 = c[1], ..., c7 = c[7];
        c[7] = c6;
        c[6] = c5;
        // ... etc
        c[2] = c1 ^ c7 ^ m;
        c[1] = c0 ^ c7 ^ m;
        c[0] = c7 ^ m;
    }

    uint8_t crc = 0;
    for (int j = 0; j < 8; j++) {
        const int bit = (c[j] ^ 1) & 0x1;  // FINAL INVERSION
        crc |= (uint8_t)(bit << j);
    }
    return crc;
}
```

### Step 2: Verify Python CRC matches

Create `/home/hy/gr-ieee802-11/examples/debug_htsig_crc_grhtsig.py`:

```python
#!/usr/bin/env python3
"""Debug gr-htsig HT-SIG CRC computation."""
import numpy as np

# bits[0:33] for CRC computation (from TX output)
# MCS=0, CBW=0, Length=96
bits = [0,0,0,0,0,0,0,  # MCS bits 0-6 (LSB first)
        0,               # CBW bit 7
        0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,0,0,  # Length=96 bits 8-23 (LSB first)
        0,0,0,0,0,0,0,0,0,0]  # Reserved bits 24-33

def crc8_grhtsig(bits):
    """gr-htsig style: init=[1,1,1,1,1,1,1,1], G(x)=x^8+x^2+x+1, final=c[j]^1"""
    c = [1]*8
    for i in range(34):
        m = bits[i] & 0x1
        c0, c1, c2, c3, c4, c5, c6, c7 = c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    # Final: c[j] ^ 1
    crc = 0
    for j in range(8):
        bit = (c[j] ^ 1) & 0x1
        crc |= bit << j
    return crc

def crc8_no_inversion(bits):
    """Without final inversion: return c directly"""
    c = [1]*8
    for i in range(34):
        m = bits[i] & 0x1
        c0, c1, c2, c3, c4, c5, c6, c7 = c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    # No final inversion
    crc = 0
    for j in range(8):
        bit = c[j] & 0x1
        crc |= bit << j
    return crc

crc_with_inv = crc8_grhtsig(bits)
crc_without = crc8_no_inversion(bits)
print(f"CRC with c[j]^1 (gr-htsig): 0x{crc_with_inv:02X}")
print(f"CRC without inversion:       0x{crc_without:02X}")
print(f"TX output:                  0x41")
```

Run: `python examples/debug_htsig_crc_grhtsig.py`

Expected: gr-htsig CRC should be 0x41, without should be 0xBE.

---

## Task 2: Trace RX HT-SIG Decode Chain

**Files:**
- `lib/frame_equalizer_impl.cc` — `decode_htsig_from_rotated`
- `lib/frame_equalizer_impl.cc` — `ht_sig_crc8_calc`

### Step 1: Check RX CRC implementation

**File: `lib/frame_equalizer_impl.cc:859-903`**

```cpp
static uint8_t ht_sig_crc8_calc(const uint8_t* bits0_33)
{
    int c[8];
    for (int i = 0; i < 8; i++) c[i] = 1;  // init all-ones

    for (int i = 0; i < 34; i++) {
        const int m = bits0_33[i] ? 1 : 0;
        // shift register update same as gr-htsig
        // new2 = c1 ^ c7 ^ m, new1 = c0 ^ c7 ^ m, new0 = c7 ^ m
    }

    // Final output: c[j] ^ 1
    uint8_t crc = 0;
    for (int j = 0; j < 8; j++) {
        const int bit = (c[j] ^ 1) & 0x1;
        crc |= (uint8_t)(bit << j);
    }
    return crc;
}
```

Both TX (gr-htsig) and RX (frame_equalizer) use the same algorithm with final inversion. They SHOULD match if the input bits are the same.

### Step 2: Add debug output to RX CRC

At line ~900 in frame_equalizer_impl.cc:

```cpp
fprintf(stderr, "[RX_CRC] decoded_bits[0:34] = ");
for (int i = 0; i < 34; i++) {
    fprintf(stderr, "%d", decoded_bits[i] ? 1 : 0);
}
fprintf(stderr, "\n");
fprintf(stderr, "[RX_CRC] computed_crc = 0x%02X, rx_crc = 0x%02X\n", crc_calc, rx_crc);
```

### Step 3: Rebuild ieee802_11

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

### Step 4: Run test and check RX vs TX CRC

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "RX_CRC|HTSIG.*crc"
```

---

## Task 3: Fix the Actual Issue

If TX and RX CRC computations match (both with inversion), but CRC still fails, the issue is in the Viterbi/equalization chain:

1. TX HT-SIG bits are encoded, interleaved, QBPSK rotated, IFFT'd
2. RX gets signal, FFT, equalization, deinterleaved, Viterbi decoded
3. The decoded bits at RX must match TX raw48 bits for CRC to pass

### Possible Issues

1. **QBPSK rotation mismatch**: TX rotates HT-SIG by 90°, RX must detect and undo rotation
2. **FFT shift mismatch**: TX IFFT and RX FFT must use same shift parameter
3. **Viterbi polynomial mismatch**: TX uses {0133, 0171}, RX must use same

### Step 1: Verify TX QBPSK rotation

TX ht_sig_field uses QBPSK (90° rotation). Check mixed_mode_carrier_allocator.py.

### Step 2: Verify RX QBPSK detection

RX frame_equalizer has `detect_htsig_rotation` or similar. Check if it's detecting correctly.

---

## Key Constants Reference

### TX HT-SIG Reference (from test_loopback_noqt.py output)
```
mcs=0, len=96, bw40=0, crc=0x41
raw48 = 000000000000011000000000000000000010000010000000
enc96 = 000000000000000000000000001101101100101011000000000000000000000000001110001111010010001111011100
intl96 = 000000000111100000000000000001000000000011101100000000010100010100000001011101100000001011001110
```

### CRC Computation

| Aspect | gr-htsig TX | ieee802_11 RX |
|--------|-------------|----------------|
| Init | `c[i] = 1` (all ones) | `c[i] = 1` (all ones) |
| G(x) | x^8+x^2+x+1 | x^8+x^2+x+1 |
| Shift | new0=c7^m, new1=c0^c7^m, new2=c1^c7^m | Same |
| Output | `c[j] ^ 1` (inversion) | `c[j] ^ 1` (inversion) |
| **Result** | **0x41** | **Should match TX** |

---

## Debug Commands Summary

```bash
cd /home/hy/gr-ieee802-11
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# CRC debug
python examples/debug_htsig_crc_grhtsig.py

# Rebuild ieee802_11
cd build && make -j$(nproc)

# Run test with CRC debug
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "RX_CRC|TX.*HTSIG.*crc"
```
