# L-SIG/HT-SIG FFT Alignment Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix L-SIG/HT-SIG decoding failure caused by FFT window timing misalignment resulting in LTF0/LTF1 channel estimates with ~180° phase differences.

**Architecture:** The issue is in the RX chain's FFT window alignment. When sync_long detects the frame at d_frame_start=X and outputs from that point, ht_symbol_splitter should output FFT blocks at correct OFDM symbol boundaries. The kCorrectOutputPositions array defines where FFT outputs should occur. Mismatches between d_frame_start and kCorrectOutputPositions cause FFT windows to capture wrong sample positions, resulting in incorrect LTF phase relationships.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, GDB

---

## Current State

### Commit History (branch: ht-mixed-mode-fcs-fix)
- `e892be9`: fix: Remove forced d_frame_start values and clean up debug output
- Previous commits attempted forced d_frame_start=176/192 which made issues worse

### Problem Description
- LTF0/LTF1 channel estimates show ~180° phase differences ("Opposite signs")
- This causes LSIG and HT-SIG decoding to fail
- Decoded bits are completely wrong (e.g., `010011000010011100000000` instead of `110100000011000001`)

### Key Files and Their Roles

| File | Role | Current State |
|------|------|---------------|
| `lib/sync_long.cc` | Detects frame start, outputs from d_frame_start | Natural detection (no forced value) |
| `lib/ht_symbol_splitter_impl.cc` | Removes CP, outputs FFT blocks at symbol boundaries | kCorrectOutputPositions={63,143,223,303,383,463,543} |
| `lib/frame_equalizer_impl.cc` | Channel estimation, L-SIG/HT-SIG decode | 176 debug statements (needs cleanup) |
| `lib/equalizer/ls.cc` | Least-squares channel estimation | May be correct |

### Data Flow Understanding

```
TX Signal:
  L-STF: 0-159
  L-LTF0: 160-239 (CP=160-175, DATA=176-239)
  L-LTF1: 240-319 (CP=240-255, DATA=256-319)
  L-SIG: 320-399 (CP=320-335, DATA=336-399)
  HT-SIG0: 400-479
  HT-SIG1: 480-559
  HT-STF: 560-639
  HT-DATA: 640+

sync_long COPY loop:
  - Outputs from input position d_frame_start onwards (1:1 mapping)
  - sync_long output[0] = sync_long input[d_frame_start]
  - wifi_start tag.value = d_frame_start

ht_symbol_splitter:
  - Receives sync_long output
  - d_frame_start_abs = tag_abs_pos (position in stream)
  - rel_idx = current_idx - d_frame_start_abs
  - Buffers 64 samples, outputs when buffer full AND at correct boundary
  - should_output_at(rel_idx) checks kCorrectOutputPositions
```

---

## Task 1: Verify sync_long Output Position Mapping

**Files:**
- Debug: `lib/sync_long.cc`

- [ ] **Step 1: Add debug to verify sync_long output start position**

In sync_long.cc COPY loop, verify what input position corresponds to output[0]:

```cpp
if (o == 0) {
    fprintf(stderr, "[SYNC_LONG_COPY] OUTPUT START: d_frame_start=%d, first input sampled\n", d_frame_start);
}
```

- [ ] **Step 2: Run and verify**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep "SYNC_LONG_COPY.*OUTPUT START"
```

Expected: d_frame_start should be around 176 (L-LTF0 DATA start in original input)

---

## Task 2: Verify ht_symbol_splitter CP Removal Logic

**Files:**
- Debug: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Add debug to trace buffer filling and output**

In ht_symbol_splitter_impl.cc, add:

```cpp
// Debug: track buffer state
if (d_buffer_count == 0) {
    fprintf(stderr, "[HT_SPLITTER] BUFFER EMPTY at current_idx=%llu, rel_idx=%llu\n",
            (unsigned long long)current_idx, (unsigned long long)rel_idx);
}
if (d_buffer_count == 32) {
    fprintf(stderr, "[HT_SPLITTER] BUFFER HALF at rel_idx=%llu\n", (unsigned long long)rel_idx);
}
```

- [ ] **Step 2: Run and check buffer fills at expected rel_idx values**

```bash
LD_LIBRARY_PATH=... timeout 60 python ... 2>&1 | grep -E "HT_SPLITTER.*BUFFER|HT_SPLITTER_OUT"
```

Expected: Buffer should fill during CP and empty at DATA boundaries (rel_idx=63,143,223,...)

---

## Task 3: Verify FFT Window Alignment with Known-Good Signal

**Files:**
- Test: `examples/test_loopback_noqt.py` (TX→RX loopback)

- [ ] **Step 1: Verify LTF phases at channel estimator input**

Add debug in `lib/equalizer/ls.cc` to print raw LTF FFT output:

```cpp
fprintf(stderr, "[LTF_RAW] LTF0[%d]=%.4f∠%.1f  LTF1[%d]=%.4f∠%.1f\n",
        i, abs(ltf0[i]), arg(ltf0[i])*180/3.14159,
        i, abs(ltf1[i]), arg(ltf1[i])*180/3.14159);
```

- [ ] **Step 2: Check phase relationship**

LTF0 and LTF1 should have SAME phase (within noise) since they go through the same channel.
Phase difference > 90° indicates FFT window misalignment.

---

## Task 4: Calculate Correct kCorrectOutputPositions

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

Based on Task 1 and 2 results, calculate correct output positions:

### If sync_long outputs from input position X:
- L-LTF0 DATA: input X to X+63 (64 samples)
- kCorrectOutputPositions[0] = X + 63 (LTF0 DATA end)
- L-LTF1 DATA: input X+80 to X+143 (assuming 80-sample symbols)
- kCorrectOutputPositions[1] = X + 143

### Current assumption:
- d_frame_start ≈ 176 (LTF0 DATA start)
- kCorrectOutputPositions = {63, 143, 223, 303, 383, 463, 543}
- This implies rel_idx = current_idx - d_frame_start_abs
- And d_frame_start_abs = tag_abs_pos = 0 (for first frame)

### Issue:
If sync_long output[0] = input[176] (LTF0 DATA start), then:
- rel_idx 0-63 = LTF0 DATA (correct)
- But when buffering, we need to know where CP ends and DATA begins

---

## Task 5: Fix kCorrectOutputPositions Based on Analysis

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Update kCorrectOutputPositions array**

Based on analysis from Task 4, update the array:

```cpp
static const uint64_t kCorrectOutputPositions[] = {
    XX,   // LTF0 DATA end
    XX,  // LTF1 DATA end
    XX,  // L-SIG DATA end
    XX,  // HT-SIG0 DATA end
    XX,  // HT-SIG1 DATA end
    XX,  // HT-STF DATA end
    XX,  // HT-DATA(0) end
};
```

- [ ] **Step 2: Update HT-DATA base offset if needed**

```cpp
// Update HT-DATA base offset
if (rel_idx >= YY) {  // YY is new base
    return ((rel_idx - YY) % 80) == 79;
}
```

---

## Task 6: Verify LTF Phase Fix

**Files:**
- Test: `examples/test_loopback_noqt.py`

- [ ] **Step 1: Run test and check LTF phases**

```bash
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "LTF0_vs_LTF1|Opposite"
```

Expected: No "Opposite signs" warnings, phase differences < 30°

---

## Task 7: Verify L-SIG Decoding

**Files:**
- Test: `examples/test_loopback_noqt.py`

- [ ] **Step 1: Run test and check L-SIG decode**

```bash
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "LSIG_DECODE.*SUCCESS|decoded_bits"
```

Expected:
```
[LSIG_DECODE] decoded_bits[0:24]=110100000011000001000000
[LSIG_DECODE] Expected for rate 0x0D: bits[0:18]=110100000011000001
```

---

## Task 8: Verify HT-SIG Decoding

**Files:**
- Test: `examples/test_loopback_noqt.py`

- [ ] **Step 1: Run test and check HT-SIG CRC**

```bash
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "HT-SIG.*CRC|PASS|FAIL"
```

Expected: HT-SIG CRC matches TX (0x41)

---

## Task 9: Clean Up Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - remove 176 debug statements
- Modify: `lib/ht_symbol_splitter_impl.cc` - remove temporary debug
- Modify: `lib/sync_long.cc` - remove temporary debug

- [ ] **Step 1: Count and categorize debug statements**

```bash
grep -c "fprintf.*stderr" lib/frame_equalizer_impl.cc
grep -c "fprintf.*stderr" lib/ht_symbol_splitter_impl.cc
grep -c "fprintf.*stderr" lib/sync_long.cc
```

- [ ] **Step 2: Keep essential debug, comment out rest**

Essential to keep:
- `[SYNC_LONG] d_frame_start`
- `[HT_SPLITTER] wifi_start detected`

---

## Task 10: Commit Working Changes

**Files:**
- Commit: All modified files

- [ ] **Step 1: Review changes**

```bash
git diff --stat HEAD
```

- [ ] **Step 2: Commit**

```bash
git add lib/sync_long.cc lib/ht_symbol_splitter_impl.cc lib/frame_equalizer_impl.cc
git commit -m "$(cat <<'EOF'
fix: Correct FFT window alignment for L-SIG/HT-SIG decoding

- sync_long: Natural d_frame_start detection
- ht_symbol_splitter: kCorrectOutputPositions aligned with d_frame_start
- Result: LTF0/LTF1 phases match, L-SIG/HT-SIG decode correctly

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Debug Commands Summary

```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Activate
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# Full debug
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 120 python examples/test_loopback_noqt.py 2>&1

# LTF phase check
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "LTF0_vs_LTF1|Opposite"

# L-SIG check
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep "LSIG_DECODE.*SUCCESS"

# HT-SIG check
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "HT-SIG.*CRC|0x41"
```

## Success Criteria

1. LTF0/LTF1 phase difference < 30° (no "Opposite signs")
2. L-SIG decoded bits match TX: `110100000011000001`
3. L-SIG parity check passes
4. HT-SIG CRC matches TX: `0x41`
5. FCS PASS for MCS 0-7
6. Debug output < 20 fprintf statements per file

---

## Key Insight: The Critical Relationship

The kCorrectOutputPositions must match where the FFT windows actually land based on d_frame_start:

```
If sync_long output starts at input[d_frame_start]:
  → ht_symbol_splitter rel_idx = current_idx - tag_abs_pos
  → When rel_idx = kCorrectOutputPositions[i], output FFT block

If d_frame_start = 176:
  → LTF0 DATA is at rel_idx 0-63
  → kCorrectOutputPositions[0] = 63 (LTF0 DATA end)

If d_frame_start varies (natural detection):
  → kCorrectOutputPositions must be recalculated dynamically OR
  → sync_long must consistently output from a fixed d_frame_start
```

The issue with "natural detection" is d_frame_start varies (51, 63, 84, 94...), so fixed kCorrectOutputPositions may not align. Consider forcing d_frame_start to a consistent value like 176 that matches the known preamble structure.
