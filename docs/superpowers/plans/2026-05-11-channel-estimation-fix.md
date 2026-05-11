# Channel Estimation Normalization Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix channel estimation so d_H magnitude is ~1.0 instead of ~8.3

**Architecture:** Normalize channel estimate in `frame_equalizer_impl.cc` by dividing by FFT gain factor (64/sqrt(52) ≈ 8.88)

---

## Context

**Problem:** Channel estimation produces d_H magnitude ~8.3 instead of ~1.0 due to FFT/IFFT scaling mismatch.

**Chain of failure:**
1. TX IFFT applies 1/sqrt(52) scaling
2. RX FFT applies no normalization
3. FFT gain = 64
4. Effective ratio = 64/sqrt(52) ≈ 8.88
5. Channel estimate H = RX/TX gives H ≈ 8.3 instead of 1.0
6. Equalized symbols have wrong phase → wrong bits → L-SIG parity fail

**Fix approach:** Normalize the channel estimate by the FFT gain factor so H ≈ 1.0

---

## Task 1: Identify Channel Estimation Code

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`

- [ ] **Step 1: Find where channel estimate is computed**

```bash
grep -n "d_H\|channel.*est\|H\s*=" lib/frame_equalizer_impl.cc | head -30
```

Look for the code that computes `H = rx / tx` or similar.

- [ ] **Step 2: Find the `equalize_header52_to_bits48` function**

This function is called for L-SIG equalization. Find where d_H is computed.

- [ ] **Step 3: Find where kLltf48TX is defined**

This is the TX reference for LTF channel estimation.

---

## Task 2: Implement Normalization Fix

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`

- [ ] **Step 1: Add normalization constant**

At the top of `equalize_header52_to_bits48` function or as a static constant:

```cpp
// FFT/IFFT normalization factor
// TX IFFT: 1/sqrt(52), RX FFT: 1 (no normalization)
// Effective gain: 64/sqrt(52) ≈ 8.88
static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);
```

- [ ] **Step 2: Apply normalization to channel estimate**

Find where `H = rx / tx` is computed and divide by kFftNormalize:

```cpp
// Original: H[i] = rx / tx;
// Fixed:
H[i] = (rx / tx) / kFftNormalize;
```

Or equivalently multiply TX reference by kFftNormalize before division.

- [ ] **Step 3: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -5
```

---

## Task 3: Verify Fix

**Files:**
- Test: Run test_loopback_final.py

- [ ] **Step 1: Run test and check d_H magnitude**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
timeout 15 python /home/hy/gr-ieee802-11/examples/test_loopback_final.py 2>&1 | \
grep "n=0: d_H\[6-10\]" | head -3
```

**Expected:** d_H magnitude should be ~1.0 (not ~8.3)

- [ ] **Step 2: Check L-SIG parity**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
timeout 15 python /home/hy/gr-ieee802-11/examples/test_loopback_final.py 2>&1 | \
grep "LSIG_DECODE.*parity" | head -5
```

**Expected:** parity check should PASS

- [ ] **Step 3: Check HT-SIG CRC**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
timeout 15 python /home/hy/gr-ieee802-11/examples/test_loopback_final.py 2>&1 | \
grep "HT-SIG.*CRC\|htsig.*crc" | head -5
```

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.cc && git commit -m "fix: Normalize channel estimate by FFT gain factor"
```

---

## Verification Criteria

**Success when:**
- d_H magnitude ≈ 1.0 (not 8.3)
- L-SIG parity check PASS
- HT-SIG CRC matches TX (0x41)
