# Sync Long HT-Mixed Mode Detection Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix sync_long to correctly detect HT-mixed mode frames instead of detecting as "Legacy (QBPSK failed)"

**Architecture:** The sync_long block detects HT-mixed mode by looking at QBPSK constellation rotation. If it fails to detect QBPSK rotation, it classifies the frame as "Legacy".

**Tech Stack:** GNU Radio, IEEE 802.11, C++, Python

---

## Problem Statement

After fixing HT-LTF position constants and array bounds:
- d_H is now non-zero (HT-LTF channel estimation works)
- But sync_long detects frame as "Legacy (QBPSK failed)"
- This prevents HT-SIG decode from being attempted
- `d_have_ht_header` stays at 0

```
Detected Legacy frame (QBPSK failed)
decode_lsig_direct_from_header52 returns FALSE for inv_lsig=0 and inv_lsig=1
```

---

## Task 1: Understand Sync Long QBPSK Detection

**Files:**
- Debug: `lib/sync_long_impl.cc` - QBPSK rotation detection

- [ ] **Step 1: Find QBPSK rotation detection code**

Search for "QBPSK" or "QBPSK_FAILED" in sync_long_impl.cc

- [ ] **Step 2: Understand the detection logic**

The sync block detects HT-mixed mode by:
1. Looking for QBPSK constellation rotation in L-SIG
2. If rotation detected → HT-mixed mode
3. If no rotation → Legacy mode

- [ ] **Step 3: Check threshold for detection**

---

## Task 2: Trace QBPSK Detection Failure

**Files:**
- Debug: `lib/sync_long_impl.cc` - add QBPSK debug

- [ ] **Step 1: Add debug output for QBPSK detection**

```cpp
fprintf(stderr, "[SYNC][QBPSK] Checking QBPSK rotation at offset=%d\n", offset);
fprintf(stderr, "[SYNC][QBPSK] Constellation rotation=%.4f angle=%.4f\n",
        rotation_mag, rotation_angle);
```

- [ ] **Step 2: Run and check QBPSK values**

Run: `timeout 30 python examples/test_constellation_real.py 2>&1 | grep "QBPSK"`

Expected: Rotation should be ~±π/4 for QBPSK

---

## Task 3: Identify Root Cause

**Possible causes:**

1. **QBPSK threshold too high**: Rotation is detected but below threshold
2. **L-SIG FFT output is wrong**: Constellation is not rotated correctly
3. **Pilot sign error**: HT-SIG pilots have wrong signs causing rotation
4. **TX/RX mismatch**: L-SIG is not QBPSK modulated in TX

- [ ] **Step 1: Check L-SIG constellation**

In wifi_constellation.py, L-SIG should be QBPSK (rotation of ±π/4)

- [ ] **Step 2: Verify TX L-SIG is QBPSK**

TX generates L-SIG with QBPSK modulation

- [ ] **Step 3: Identify root cause**

---

## Task 4: Fix Identified Issue

Based on Task 3 findings, fix the root cause:

**If threshold issue:**
- Lower the QBPSK detection threshold

**If TX/RX mismatch:**
- Fix L-SIG generation or detection

---

## Task 5: Verify Fix

- [ ] **Step 1: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 2: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep -E "(d_have_ht|HT_SIG|QBPSK)"
```

Expected:
- Detected as "HT-mixed" not "Legacy"
- d_have_ht_header=1
- HT-SIG CRC pass

---

## Key Files Reference

### Sync Long
- `lib/sync_long_impl.cc` - sync detection
- `lib/sync_long_impl.h` - class definition

### Expected QBPSK Rotation
- QBPSK constellation points: (1,1), (-1,1), (-1,-1), (1,-1)
- Angle: ±π/4 or ±3π/4 (45° or 135°)
- If constellation is flat (angle ~0 or π/2), it's regular BPSK, not QBPSK

### Debug Commands
```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep "PATTERN"
```
