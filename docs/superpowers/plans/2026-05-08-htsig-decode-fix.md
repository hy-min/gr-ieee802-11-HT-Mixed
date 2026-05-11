# HT-SIG Decode Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix HT-SIG decoding so `d_have_ht_header = 1` and HT-SIG CRC passes in TX→RX loopback.

**Architecture:** HT-SIG decode has 4 failure points: (1) L-SIG must decode first as gate, (2) QBPSK rotation must be detected correctly, (3) Viterbi input must be correct, (4) CRC must verify. Debug each stage with printf in loopback to identify exact failure point.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, QTGUI constellation

---

## Problem Statement

```
TX generates correct HT-mixed frame with L-SIG + HT-SIG (QBPSK rotated)
RX chain processes preamble but HT-SIG decode fails:
  - sync_long may detect frame as "Legacy (QBPSK failed)"
  - d_have_ht_header stays 0
  - HT-SIG CRC never checked
```

HT-SIG decode is attempted at `lib/frame_equalizer_impl.cc:2537-2640` only after L-SIG decode succeeds. The function `decode_htsig_from_rotated` (line 1468) tries all 4 rotations (0/90/180/270°) and both inversions, but all 8 combinations fail CRC.

---

## File Structure

### Key Files

| File | Responsibility |
|------|----------------|
| `lib/frame_equalizer_impl.cc` | RX PHY: L-SIG/HT-SIG decode, channel est, rotation detection |
| `lib/sync_long.cc` | Frame detection and HT-mixed mode classification |
| `examples/wifi_constellation_eqsyms.py` | TX→RX loopback test with constellation display |
| `examples/test_loopback_constellation.py` | Alternative loopback test |

### Relevant Functions in frame_equalizer_impl.cc

| Function | Line | Purpose |
|----------|------|---------|
| `decode_lsig_direct_from_header52` | 1210 | L-SIG decode (gate for HT-SIG) |
| `detect_htsig_rotation` | 1167 | Pilot-based QBPSK rotation detection |
| `vote_qbpsk_rotation` | 1698 | Energy-based QBPSK rotation detection |
| `apply_htsig_rotation` | 1198 | Rotation compensation: `out = in * conj(rot)` |
| `decode_htsig_from_rotated` | 1468 | Full HT-SIG decode with Viterbi + CRC |
| `compute_subcarrier_energy` | 1687 | E_I/E_Q energy computation |

### Symbol Position Constants (line 37-44)

```cpp
static constexpr int kLltf0Rel      = 0;
static constexpr int kLltf1Rel      = 1;
static constexpr int kLSigRel       = 2;   // L-SIG at rel=2
static constexpr int kHtSig0Rel     = 3;   // HT-SIG0 at rel=3
static constexpr int kHtSig1Rel     = 4;   // HT-SIG1 at rel=4
static constexpr int kHtTrain0Rel   = 5;
static constexpr int kHtTrain1Rel   = 6;
static constexpr int kDataStartRel  = 7;   // First HT-DATA at rel=7
```

### Expected Preamble Structure (from ht_symbol_splitter output)

```
rel=0: L-LTF0 DATA (64 samples from FFT)
rel=1: L-LTF1 DATA
rel=2: L-SIG DATA
rel=3: HT-SIG0 DATA
rel=4: HT-SIG1 DATA
rel=5: HT-STF DATA
rel=6: HT-LTF DATA
rel=7+: HT-DATA
```

---

## Task 1: Verify L-SIG Decode Succeeds First

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — add debug output around L-SIG decode

L-SIG decode is the gate for HT-SIG decode. If L-SIG fails, HT-SIG is never attempted.

- [ ] **Step 1: Check if L-SIG decode succeeds**

In `lib/frame_equalizer_impl.cc`, around line 2537:
```cpp
if (!decode_lsig_direct_from_header52(d_early_eqsym[kLSigRel],
                                      d_early_eqsym_valid[kLltf0Rel],
                                      d_early_eqsym_valid[kLltf1Rel],
                                      lsig_enc, lsig_len, nullptr, nullptr)) {
    fprintf(stderr, "[HT_SIG_GATE] L-SIG decode FAILED - not attempting HT-SIG\n");
    continue;  // ← HT-SIG never attempted if L-SIG fails
}
fprintf(stderr, "[HT_SIG_GATE] L-SIG decode OK (enc=0x%02X len=%d) - proceeding to HT-SIG\n",
        lsig_enc, lsig_len);
```

- [ ] **Step 2: Build and run test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -5
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep "HT_SIG_GATE"
```

Expected output: `L-SIG decode OK` — if it shows `L-SIG decode FAILED`, the gate isn't reached.

- [ ] **Step 3: If L-SIG fails, debug L-SIG decode**

Check `decode_lsig_direct_from_header52` (line 1210):
- Viterbi input `deintl48` bits
- Viterbi output `dec24` bits
- Rate field, length, parity

L-SIG uses BPSK on real axis: `bit = (real(EQ) >= 0) ? 1 : 0`

---

## Task 2: Verify HT-SIG Symbol Data is Non-Zero

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — add probe at rotation detection

If `d_early_eqsym[kHtSig0Rel]` is all zeros, the HT-SIG symbols weren't received correctly.

- [ ] **Step 1: Add probe before rotation detection**

At line 2564, before `detect_htsig_rotation`:
```cpp
fprintf(stderr, "[HT_SIG_PROBE] kHtSig0Rel=%d kHtSig1Rel=%d\n", kHtSig0Rel, kHtSig1Rel);
fprintf(stderr, "[HT_SIG_PROBE] d_early_eqsym_valid[3]=%d d_early_eqsym_valid[4]=%d\n",
        d_early_eqsym_valid[kHtSig0Rel], d_early_eqsym_valid[kHtSig1Rel]);
fprintf(stderr, "[HT_SIG_PROBE] HT-SIG0[0:8]=");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.3f+%.3fi ", d_early_eqsym[kHtSig0Rel][i].real(), d_early_eqsym[kHtSig0Rel][i].imag());
}
fprintf(stderr, "\n");
fprintf(stderr, "[HT_SIG_PROBE] HT-SIG0 pilots[48:52]=");
for (int i = 48; i < 52; i++) {
    fprintf(stderr, "%.3f+%.3fi ", d_early_eqsym[kHtSig0Rel][i].real(), d_early_eqsym[kHtSig0Rel][i].imag());
}
fprintf(stderr, "\n");
fprintf(stderr, "[HT_SIG_PROBE] Hhdr52[0:8]=");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.3f+%.3fi ", Hhdr52[i].real(), Hhdr52[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: Build and run**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -3
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep "HT_SIG_PROBE"
```

**Expected:** HT-SIG0 values should be non-zero complex numbers (magnitude ~1 in ideal loopback).

**If all zeros:** Hhdr52 (channel estimate) is zero or HT-SIG symbols weren't extracted. Go to Task 4.

---

## Task 3: Debug QBPSK Rotation Detection

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — verify rotation detection accuracy

Two rotation detection methods exist. Need to verify they agree and the correct rotation is tried.

- [ ] **Step 1: Add detailed rotation detection debug**

At line 2565, after `detect_htsig_rotation` and `vote_qbpsk_rotation`:
```cpp
fprintf(stderr, "[ROTATION] pilot_detected=%d energy_voted=%d start_rot=%d\n",
        detected_rot, energy_rot, start_rot);
fprintf(stderr, "[ROTATION] Trying all 4 rotations + 2 inversions (8 combos)\n");
```

- [ ] **Step 2: Add debug in decode_htsig_from_rotated**

At line 1486-1494, the existing probe prints `rx52_a` and `H52`. Also print the equalized symbol before QBPSK bit extraction:
```cpp
fprintf(stderr, "[DECODE_HT] rot=%d inv_a=%d inv_b=%d\n", rot, invert_a, invert_b);
// Print equalized symbols (after dividing by H, before QBPSK demap)
fprintf(stderr, "[DECODE_HT] EQ[0:8]=");
for (int i = 0; i < 8; i++) {
    gr_complex eq = (std::abs(H52[i]) > 0.1f) ? rx52_a[i] / H52[i] : gr_complex(0,0);
    fprintf(stderr, "%.3f+%.3fi ", eq.real(), eq.imag());
}
fprintf(stderr, "\n");
fprintf(stderr, "[DECODE_HT] QBPSK bits[0:12]=");
for (int i = 0; i < 12; i++) {
    gr_complex eq = (std::abs(H52[i]) > 0.1f) ? rx52_a[i] / H52[i] : gr_complex(0,0);
    fprintf(stderr, "%d", (eq.imag() >= 0.0f) ? 0 : 1);
}
fprintf(stderr, "\n");
```

- [ ] **Step 3: Run and analyze**

```bash
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep -E "ROTATION|DECODE_HT"
```

**What to look for:**
1. Is `detected_rot` matching `energy_rot`?
2. Is the correct rotation being tried first?
3. After rotation compensation (`rot_htsig0`), does the EQ show bits on imaginary axis?
4. Are Viterbi input bits mostly 0/1 errors (wrong bits) or is Viterbi failing entirely?

---

## Task 4: Verify Channel Estimate Hhdr52 is Non-Zero

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — verify Hhdr52 at line 2466

The channel estimate `Hhdr52` from L-LTF is used to equalize HT-SIG. If H=0, all EQ symbols become zero.

- [ ] **Step 1: Check Hhdr52 computation**

Around line 2466:
```cpp
estimate_header_channel_from_lltf52(d_early_eqsym[kLltf0Rel],
                                   d_early_eqsym[kLltf1Rel],
                                   Hhdr52);
fprintf(stderr, "[CHAN_EST] Hhdr52[0:8]=");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.3f+%.3fi ", Hhdr52[i].real(), Hhdr52[i].imag());
}
fprintf(stderr, "\n");
fprintf(stderr, "[CHAN_EST] |Hhdr52[0:8]|=");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.3f ", std::abs(Hhdr52[i]));
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: Build and run**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -3
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep "CHAN_EST"
```

**If |H| ≈ 0:** L-LTF channel estimation failed. Check:
- `d_early_eqsym[kLltf0Rel]` and `d_early_eqsym[kLltf1Rel]` are non-zero
- `estimate_header_channel_from_lltf52` function correctness
- L-LTF reference values match TX L-LTF sequence

---

## Task 5: Debug Viterbi Input for HT-SIG

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — verify deinterleaver and Viterbi input

Viterbi decode (96→48 bits) must have correct input. The deinterleaver reverses the 802.11 permutation.

- [ ] **Step 1: Check deinterleaver formula**

At line 1545-1555:
```cpp
// HT-SIG Deinterleaving: undo the 802.11 permutation
// Forward interleaver: j = 3*(k%16) + k/16
// Inverse (deinterleaver): j = 16*(k%3) + k/3
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    deintl48_a[k] = eqbits48_a[j] & 0x1;
}
```

**Verify:** For k=0: j=0. For k=1: j=16. For k=2: j=32. For k=3: j=1. This is the correct inverse.

- [ ] **Step 2: Print Viterbi input bits**

At line 1562-1572, existing probe prints `enc96` bits. Also print `eqbits48_a` (before deinterleave):
```cpp
fprintf(stderr, "[DEINTL] eqbits48_a[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", eqbits48_a[i]);
fprintf(stderr, "\n");
```

- [ ] **Step 3: Run and analyze**

```bash
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep -E "DEINTL|VITERBI_IN|VITERBI_OUT|VITERBI_HT"
```

**What to check:**
1. Viterbi input (`enc96`) should have ~50% ones (random-looking)
2. If `enc96` is all zeros or all ones, the deinterleaver or bit extraction is broken
3. Viterbi output (`dec48`) should be non-zero
4. If `VITERBI_HT_SIG decode failed`, Viterbi itself is failing (check polynomials)

---

## Task 6: Debug HT-SIG CRC Failure

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` — verify CRC computation

HT-SIG has an 8-bit CRC over bits 0-33 (MCS + CBW + Length + Reserved + Aggregation + STBC + AdvCoding + SGI + NumHTLTF).

- [ ] **Step 1: Print decoded HT-SIG bits before CRC check**

At line 1592-1620:
```cpp
fprintf(stderr, "[HT_SIG_BITS] MCS=%d CBW=%d Length=%d\n", mcs, bw40, psdu_length);
// Print all 48 decoded bits
fprintf(stderr, "[HT_SIG_BITS] dec48[0:48]=");
for (int i = 0; i < 48; i++) {
    fprintf(stderr, "%d", decoded_bits[i] & 1);
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: Find HT-SIG CRC computation**

The CRC is computed in `ht_sig_crc8` (defined in signal_field_impl.cc on TX side). RX should recompute CRC over received bits and compare.

At line 1629-1643:
```cpp
// CRC check
uint8_t computed_crc = ht_sig_crc8(decoded_bits, 34);  // CRC over first 34 bits
uint8_t received_crc = 0;
for (int i = 0; i < 8; i++) {
    received_crc |= ((decoded_bits[34 + i] & 1) << i);
}
fprintf(stderr, "[HT_SIG_CRC] computed=0x%02X received=0x%02X %s\n",
        computed_crc, received_crc, computed_crc == received_crc ? "MATCH" : "MISMATCH");
```

- [ ] **Step 3: Build and run**

```bash
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep "HT_SIG_CRC"
```

**If CRC mismatches:** Either:
1. Viterbi output is wrong (bit errors)
2. CRC computation differs between TX and RX
3. Bit ordering is reversed

**Compare with TX reference:**
```bash
# Get TX HT-SIG bits
timeout 10 python examples/dump_tx_htsig_bits.py 2>&1 | head -20
# Compare with RX HT-SIG bits from debug output
```

---

## Task 7: Verify TX HT-SIG Bits Match RX Decoded Bits

**Files:**
- Create: `examples/dump_tx_htsig_bits.py` — print TX HT-SIG bits for comparison

- [ ] **Step 1: Create TX HT-SIG bit dumper**

```python
#!/usr/bin/env python3
"""Dump TX HT-SIG bits for comparison with RX decoded bits."""
import sys
import struct
import pmt
sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

# HT-SIG fields for MCS=0, length=38
# MCS=0: bits 0-6 = 0x00
# CBW=0: bit 7 = 0
# Length=38: bits 8-23 = 0x0026 (LSB first)
# Reserved: bits 24-26 = 0
# Aggregation=0: bit 27 = 0
# STBC=0: bits 28-29 = 0
# AdvCoding=0: bit 30 = 0
# SGI=0: bit 31 = 0
# NumHTLTF=0: bits 32-33 = 0
# CRC8 over bits 0-33
# Tail: bits 42-47 = 0

print("Expected TX HT-SIG bits (48 bits after Viterbi):")
print("MCS=0, CBW=20MHz, Length=38, CRC=??, Tail=0")
```

- [ ] **Step 2: Compare TX vs RX bits**

From debug output, get `dec48` from RX and compare with known TX HT-SIG content.

---

## Task 8: Fix and Verify

Based on findings from Tasks 1-7, fix the root cause:

### Common Issues and Fixes

**Issue A: Hhdr52 is zero**
- Fix L-LTF channel estimation
- Check `kLltf48TX` reference values match TX

**Issue B: Rotation detection wrong**
- `detect_htsig_rotation` uses pilots but HT-SIG pilots aren't known preamble
- Use `vote_qbpsk_rotation` (energy ratio) as primary detector
- Energy ratio > 1.0 means QBPSK rotation present (HT-SIG)

**Issue C: Viterbi decode fails**
- Check convolutional polynomial: 133_171 (octal) on both TX and RX
- Check Viterbi traceback depth

**Issue D: CRC mismatch**
- TX and RX CRC computation must use identical polynomials and bit ordering
- HT-SIG CRC: G(x) = x^8 + x^2 + x + 1, init=0xFF, no final inversion

- [ ] **Step 1: Apply identified fix**

- [ ] **Step 2: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -5
```

- [ ] **Step 3: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep -E "d_have_ht|HT_SIG|htsig|CRC"
```

Expected: `d_have_ht_header=1` and HT-SIG CRC passing.

---

## Key Constants Reference

### HT-SIG Bit Layout (48 decoded bits)

| Bits | Field | Length |
|------|-------|--------|
| 0-6 | MCS | 7 |
| 7 | CBW | 1 |
| 8-23 | HT-Length | 16 |
| 24-26 | Reserved | 3 |
| 27 | Aggregation | 1 |
| 28-29 | STBC | 2 |
| 30 | Adv Coding | 1 |
| 31 | SGI | 1 |
| 32-33 | Num HT-LTF | 2 |
| 34-41 | CRC8 | 8 |
| 42-47 | Tail | 6 |

### QBPSK Rotation Codes

| Code | Rotation | Complex Factor | Undo (conj) |
|------|----------|----------------|-------------|
| 0 | 0° | 1+0j | 1+0j |
| 1 | +90° | 0+1j | 0-1j |
| 2 | -90° | 0-1j | 0+1j |
| 3 | 180° | -1+0j | -1+0j |

### Pilot Subcarrier Indices (in 52-element array)

- Pilot 0: index 48 → subcarrier -21
- Pilot 1: index 49 → subcarrier -7
- Pilot 2: index 50 → subcarrier +7
- Pilot 3: index 51 → subcarrier +21

### Deinterleaver Formula

```cpp
// Forward: j = 3*(k%16) + k/16
// Inverse: j = 16*(k%3) + k/3
// For k=0..47, j maps input position to output position
```

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate conda
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Run loopback test with constellation
timeout 30 python examples/wifi_constellation_eqsyms.py 2>&1 | grep "PATTERN"

# Alternative loopback test
timeout 30 python examples/test_loopback_constellation.py 2>&1 | grep "PATTERN"
```

## Test File Reference

The primary test file is `examples/wifi_constellation_eqsyms.py` (NOT `test_constellation_real.py` which doesn't exist).
