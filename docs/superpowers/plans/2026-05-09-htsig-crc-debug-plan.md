# HT-SIG Parse Condition Debug Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix HT-SIG CRC mismatch by understanding why the HT-SIG parse condition is never satisfied.

**Architecture:** The issue is not CRC computation itself - both TX (gr-htsig) and RX (frame_equalizer) use the same algorithm. The problem is that Viterbi output is wrong, meaning upstream data is corrupted.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, gr-htsig

---

## Problem Statement

From the debug output:
```
[EQ][DEBUG] Checking HT-SIG parse condition: d_sym_idx=0, kHtSig1Rel=4, d_have_ht_header=0
[EQ][GATE_DETAIL] d_sym_idx=0, kHtSig1Rel=4, d_have_ht_header=0
... (repeated for d_sym_idx=0..8)
```

The HT-SIG parse condition at `frame_equalizer_impl.cc:2448-2456` is:
```cpp
const bool ht_parse_condition =
    !d_have_ht_header &&
    d_internal_symbol_counter >= kHtSig1Rel &&
    d_early_eqsym_valid[kLltf0Rel] &&
    d_early_eqsym_valid[kLltf1Rel] &&
    d_early_eqsym_valid[kLSigRel] &&
    d_early_eqsym_valid[kHtSig0Rel] &&
    d_early_eqsym_valid[kHtSig1Rel];
```

This condition is NEVER satisfied. Possible reasons:
1. `d_internal_symbol_counter` never reaches `kHtSig1Rel` (4)
2. Some `d_early_eqsym_valid[]` flag is never true
3. The condition check itself has a bug

---

## Task 1: Add Valid Flags Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:2436-2443`

- [ ] **Step 1: Add debug output for all valid flags**

At line 2436, modify the `[EQ][GATE_DETAIL]` block to also print `d_internal_symbol_counter`:

```cpp
std::printf("[EQ][GATE_DETAIL] d_sym_idx=%d, d_internal_counter=%d, kHtSig1Rel=%d, d_have_ht_header=%d\n",
           d_sym_idx, d_internal_symbol_counter, kHtSig1Rel, d_have_ht_header ? 1 : 0);
std::printf("[EQ][GATE_DETAIL] valid flags: lltf0=%d lltf1=%d lsig=%d htsig0=%d htsig1=%d\n",
           d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
           d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
           d_early_eqsym_valid[kLSigRel] ? 1 : 0,
           d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
           d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
std::printf("[EQ][GATE_DETAIL] ht_parse_condition = %d (need: !have_ht=%d counter>=4=%d lltf0=%d lltf1=%d lsig=%d htsig0=%d htsig1=%d)\n",
           (!d_have_ht_header) && (d_internal_symbol_counter >= kHtSig1Rel) &&
           d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLltf1Rel] &&
           d_early_eqsym_valid[kLSigRel] && d_early_eqsym_valid[kHtSig0Rel] &&
           d_early_eqsym_valid[kHtSig1Rel],
           !d_have_ht_header,
           d_internal_symbol_counter >= kHtSig1Rel,
           d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
           d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
           d_early_eqsym_valid[kLSigRel] ? 1 : 0,
           d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
           d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
```

- [ ] **Step 2: Rebuild ieee802_11**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 3: Run test and capture output**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "GATE_DETAIL|EXTRACT"
```

Expected output should show which valid flag is failing.

---

## Task 2: Verify d_internal_symbol_counter Progression

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:2263-2265`

- [ ] **Step 1: Add debug at valid flag set**

At line 2263, when `d_early_eqsym_valid[d_internal_symbol_counter] = true` is set, also print the value:

```cpp
d_early_eqsym_valid[d_internal_symbol_counter] = true;
std::printf("[EQ][VALID_SET] internal_counter=%d, valid=1, type=%s\n",
            d_internal_symbol_counter,
            d_internal_symbol_counter == kLltf0Rel ? "L-LTF0" :
            d_internal_symbol_counter == kLltf1Rel ? "L-LTF1" :
            d_internal_symbol_counter == kLSigRel ? "L-SIG" :
            d_internal_symbol_counter == kHtSig0Rel ? "HT-SIG0" :
            d_internal_symbol_counter == kHtSig1Rel ? "HT-SIG1" : "OTHER");
```

- [ ] **Step 2: Add debug at counter increment**

Around line 2849, after `d_internal_symbol_counter++`, add:

```cpp
std::printf("[EQ][COUNTER] incrementing d_internal_symbol_counter to %d\n", d_internal_symbol_counter);
```

- [ ] **Step 3: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "VALID_SET|COUNTER"
```

Expected: Shows progression of `d_internal_symbol_counter` from 0 to at least 5.

---

## Task 3: Verify HT-SIG Symbols Are Being Extracted

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:2243`

- [ ] **Step 1: Add debug at extract_header52_from_sym64 call**

At line 2243, before `extract_header52_from_sym64`, print what's being extracted:

```cpp
std::printf("[EQ][EXTRACT] Calling extract for internal_counter=%d, type=%s\n",
            d_internal_symbol_counter,
            d_internal_symbol_counter == kLltf0Rel ? "L-LTF0" :
            d_internal_symbol_counter == kLltf1Rel ? "L-LTF1" :
            d_internal_symbol_counter == kLSigRel ? "L-SIG" :
            d_internal_symbol_counter == kHtSig0Rel ? "HT-SIG0" :
            d_internal_symbol_counter == kHtSig1Rel ? "HT-SIG1" : "OTHER");
fflush(stdout);
extract_header52_from_sym64(sym64, d_early_eqsym[d_internal_symbol_counter]);
```

- [ ] **Step 2: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "EXTRACT|VALID_SET"
```

Expected: Should see extract calls for types L-LTF0, L-LTF1, L-SIG, HT-SIG0, HT-SIG1.

---

## Task 4: Check HT-SIG Decode Input

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:1480-1510`

- [ ] **Step 1: Add debug in decode_htsig_from_rotated**

At line 1482, at the start of `decode_htsig_from_rotated`, print the input values:

```cpp
fprintf(stderr, "[DECODE_HT] CALLED: rx52_a[0]=%.3f+%.3fi rx52_b[0]=%.3f+%.3fi H52[0]=%.3f+%.3fi\n",
        rx52_a[0].real(), rx52_a[0].imag(),
        rx52_b[0].real(), rx52_b[0].imag(),
        H52[0].real(), H52[0].imag());
```

- [ ] **Step 2: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "DECODE_HT CALLED"
```

Expected: Should see `DECODE_HT CALLED` message. If not, the function is never reached.

---

## Task 5: Fix the Root Cause

Based on the debug output from Tasks 1-4, fix the identified issue.

### Possible Issues and Fixes

**Issue A: `d_internal_symbol_counter` never reaches 4**
- Fix: Check why counter doesn't increment past 3

**Issue B: Some valid flag never becomes true**
- Fix: Investigate why `d_early_eqsym_valid[rel]` is never set for that symbol

**Issue C: `d_early_eqsym[rel]` contains garbage data**
- Fix: Check `extract_header52_from_sym64` function

**Issue D: HT-SIG decode called but CRC fails**
- Fix: The issue is upstream - Viterbi input is wrong

---

## Task 6: Clean Up Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`

- [ ] **Step 1: Remove temporary debug statements**

After fixing the issue, remove all debug printf statements added in Tasks 1-4.

- [ ] **Step 2: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 3: Final test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "HT-SIG.*parsed|d_have_ht_header"
```

Expected: Should see `d_have_ht_header=1` and successful HT-SIG parse.

---

## Key Code Locations

| Location | Purpose |
|----------|---------|
| `frame_equalizer_impl.cc:2243` | `extract_header52_from_sym64` call |
| `frame_equalizer_impl.cc:2263` | `d_early_eqsym_valid[...] = true` |
| `frame_equalizer_impl.cc:2448-2456` | HT-SIG parse condition |
| `frame_equalizer_impl.cc:2608-2636` | HT-SIG decode loop (tries all rotations) |
| `frame_equalizer_impl.cc:2849` | `d_internal_symbol_counter++` |

## Key Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `kLltf0Rel` | 0 | L-LTF0 relative index |
| `kLltf1Rel` | 1 | L-LTF1 relative index |
| `kLSigRel` | 2 | L-SIG relative index |
| `kHtSig0Rel` | 3 | HT-SIG0 relative index |
| `kHtSig1Rel` | 4 | HT-SIG1 relative index |

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate environment
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Run with debug
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "GATE_DETAIL|VALID_SET|COUNTER|EXTRACT|DECODE_HT"
```
