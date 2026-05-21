# HT-SIG Parse Failure Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix HT-SIG parse failure where `ht_parse_condition=1` is reached but L-SIG decode consistently fails, causing `parse failed: lsig=2 htsig=3/4`.

**Architecture:** The HT-SIG parse block is reached (counter=4, all flags valid) but L-SIG decode returns false both with and without bit inversion. The decoded L-SIG bits are completely wrong (`010011000010011100000000` instead of expected `110100000011000001000000`). The root cause appears to be FFT window timing misalignment causing incorrect LTF-based channel estimates.

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python, GDB

---

## Current State

### Problem Description
- HT-SIG parse block IS reached (`ht_parse_condition=1`)
- L-SIG decode called twice (inv_lsig=0 and inv_lsig=1), both fail
- Decoded bits: `010011000010011100000000` (completely wrong)
- Expected bits: `110100000011000001000000` (rate=0x0D, length=48)
- HT-SIG decode never succeeds (lsig=2 means L-SIG decode failed twice)

### Files Involved
- `lib/sync_long.cc` - Frame detection and timing
- `lib/ht_symbol_splitter_impl.cc` - CP removal and symbol output
- `lib/frame_equalizer_impl.cc` - L-SIG/HT-SIG decoding
- `lib/equalizer/ls.cc` - Least-squares channel estimation

### Current Values
- `d_frame_start = 192` (forced in sync_long)
- `kCorrectOutputPositions = {63, 143, 223, 303, 383, 463, 543}` (for d_frame_start=192)
- Deinterleaver formula: `j = 16 * (k % 3) + k / 3` (corrected)

---

## Task 1: Verify LTF Symbol Extraction at Correct Positions

**Files:**
- Test: `examples/test_loopback_noqt.py`
- Debug: `lib/frame_equalizer_impl.cc` - add position verification

- [ ] **Step 1: Add debug output for actual rel_idx values at extract time**

In `frame_equalizer_impl.cc`, find where `EXTRACT_HT_SIG` debug is printed and add rel_idx context:

```cpp
fprintf(stderr, "[EXTRACT_HT_SIG] internal_counter=%d, type=%s, rel_idx=%llu\n",
        internal_counter, type_str, (unsigned long long)rel_idx);
```

- [ ] **Step 2: Run test and capture extract positions**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 120 python examples/test_loopback_noqt.py 2>&1 | grep "EXTRACT_HT_SIG"
```

Expected output shows rel_idx for each symbol type:
```
EXTRACT_HT_SIG] internal_counter=0, type=L-LTF0, rel_idx=?
[EXTRACT_HT_SIG] internal_counter=1, type=L-LTF1, rel_idx=?
[EXTRACT_HT_SIG] internal_counter=2, type=L-SIG, rel_idx=?
[EXTRACT_HT_SIG] internal_counter=3, type=HT-SIG0, rel_idx=?
[EXTRACT_HT_SIG] internal_counter=4, type=HT-SIG1, rel_idx=?
```

- [ ] **Step 3: Verify rel_idx matches expected positions**

For d_frame_start=192, expected rel_idx at extract:
- L-LTF0: input 176-239, but wait - we need to trace where frame_equalizer gets its input from

---

## Task 2: Trace sync_long to ht_symbol_splitter Data Flow

**Files:**
- Debug: `lib/sync_long.cc` - add rel_idx debug in COPY loop
- Debug: `lib/ht_symbol_splitter_impl.cc` - add rel_idx debug in output

- [ ] **Step 1: Add debug in sync_long COPY loop**

In `sync_long.cc`, in the COPY case, add output for first few samples:

```cpp
if (d_offset < 10 || d_offset == d_frame_start) {
    fprintf(stderr, "[SYNC_LONG_COPY] d_offset=%d, d_frame_start=%d, rel=%d\n",
            d_offset, d_frame_start, d_offset - d_frame_start);
}
```

- [ ] **Step 2: Add debug in ht_symbol_splitter output**

In `ht_symbol_splitter_impl.cc`, in the output section:

```cpp
fprintf(stderr, "[HT_SPLITTER_OUT] produced=%d, rel_idx=%llu, output_pos=%llu\n",
        produced, (unsigned long long)rel_idx, (unsigned long long)(produced/64));
```

- [ ] **Step 3: Run and trace the data flow**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
LD_LIBRARY_PATH=... timeout 120 python examples/test_loopback_noqt.py 2>&1 | grep -E "SYNC_LONG_COPY|HT_SPLITTER_OUT" | head -30
```

Expected: sync_long outputs start at d_frame_start=192, first output corresponds to rel_idx=0 (L-LTF0 DATA start)

---

## Task 3: Verify FFT Window Timing with Known-Good Signal

**Files:**
- Test: `examples/test_loopback_noqt.py` - already loops TX to RX

- [ ] **Step 1: Add print for d_frame_start value used**

In `sync_long.cc`, when wifi_start tag is written:

```cpp
fprintf(stderr, "[SYNC_LONG] wifi_start tag written: nitems_written=%llu, d_frame_start=%d\n",
        (unsigned long long)nitems_written(0), d_frame_start);
```

- [ ] **Step 2: Verify sync_long output mapping**

sync_long COPY loop: `out[o] = in_delayed[i]` where `rel = d_offset - d_frame_start`

When d_offset=192 (d_frame_start), rel=0. This means:
- sync_long output[0] = input[192]
- input[192] should be L-LTF0 DATA start

- [ ] **Step 3: Verify ht_symbol_splitter input mapping**

ht_symbol_splitter receives sync_long output. If wifi_start tag.value = d_frame_start = 192:
- The tag.offset is where wifi_start appears in the stream
- d_frame_start_abs should be set to align rel_idx=0 with L-LTF0 DATA

---

## Task 4: Fix LTF Phase Alignment

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - CP removal logic
- Modify: `lib/sync_long.cc` - d_frame_start value

Based on Task 2 and 3 results, determine the correct fix:

### If d_frame_start=176 Works Better:
- kCorrectOutputPositions should be {79, 159, 239, 319, 399, 479, 559}
- Because LTF0 CP=0-15, DATA=16-79, so DATA ends at 79

### If d_frame_start=192 Works Better:
- kCorrectOutputPositions = {63, 143, 223, 303, 383, 463, 543} (current)
- Need to investigate why LTF phases still show "opposite signs"

- [ ] **Step 1: Implement the determined fix**

- [ ] **Step 2: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
LD_LIBRARY_PATH=... timeout 120 python examples/test_loopback_noqt.py 2>&1 | grep -E "LTF0_vs_LTF1|Opposite"
```

Expected: No "Opposite signs" warnings, or LTF phases within ~30° of each other

---

## Task 5: Verify L-SIG Decoding After Phase Fix

**Files:**
- Test: `examples/test_loopback_noqt.py`

- [ ] **Step 1: Run test and check L-SIG decode**

```bash
LD_LIBRARY_PATH=... timeout 120 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG_DECODE|decoded_bits"
```

Expected output:
```
[LSIG_DECODE] decoded_bits[0:24]=110100000011000001000000
[LSIG_DECODE] Expected for rate 0x0D: bits[0:18]=110100000011000001
```

- [ ] **Step 2: Check parity and rate fields**

- Rate field bits[0:3] should be `1101` = 0x0D
- Length bits[4:15] should be `000000110000` = 48
- Parity bit[18] should give even parity over bits 0-17

---

## Task 6: Verify HT-SIG Decoding

**Files:**
- Test: `examples/test_loopback_noqt.py`

- [ ] **Step 1: Check HT-SIG parse success**

```bash
LD_LIBRARY_PATH=... timeout 120 python examples/test_loopback_noqt.py 2>&1 | grep -E "HT-SIG.*parse|CRC"
```

Expected: `[EQ][HT-SIG] parse success` or similar

- [ ] **Step 2: Verify HT-SIG CRC matches TX**

TX HT-SIG CRC: `0x41` (from TX debug output)
RX HT-SIG CRC should match

---

## Task 7: Clean Up Debug Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - remove/add back debug
- Modify: `lib/ht_symbol_splitter_impl.cc` - remove/add back debug
- Modify: `lib/sync_long.cc` - remove/add back debug

- [ ] **Step 1: Count remaining debug statements**

```bash
grep -c "fprintf.*stderr" lib/frame_equalizer_impl.cc
grep -c "fprintf.*stderr" lib/ht_symbol_splitter_impl.cc
grep -c "fprintf.*stderr" lib/sync_long.cc
```

- [ ] **Step 2: Keep essential debug, comment out the rest**

Keep for future debugging:
- `[SYNC_LONG] d_frame_start` (1 location)
- `[HT_SPLITTER] wifi_start detected` (1 location)
- LTF phase comparison (if still needed)

- [ ] **Step 3: Rebuild and verify test runs cleanly**

---

## Task 8: Commit Working Changes

**Files:**
- Commit: All modified files with working fixes

- [ ] **Step 1: Review changes**

```bash
git diff --stat HEAD
```

- [ ] **Step 2: Commit with descriptive message**

```bash
git add lib/sync_long.cc lib/ht_symbol_splitter_impl.cc lib/frame_equalizer_impl.cc
git commit -m "$(cat <<'EOF'
fix: Correct FFT window alignment for L-SIG/HT-SIG decoding

- sync_long: Force d_frame_start=192 for proper preamble alignment
- ht_symbol_splitter: Output at kCorrectOutputPositions={63,143,223,...}
- frame_equalizer: Fixed deinterleaver formula k/3 (was k/16)
- Result: L-SIG and HT-SIG decode successfully

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

# L-SIG specific
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "LSIG_DECODE|decoded_bits"

# LTF phase
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "LTF0_vs_LTF1|Opposite"

# HT-SIG parse
LD_LIBRARY_PATH=... timeout 120 python ... 2>&1 | grep -E "HT-SIG.*parse|CRC"
```

## Success Criteria

1. LTF0/LTF1 phase difference < 30° (no "Opposite signs")
2. L-SIG decoded bits match TX: `110100000011000001`
3. L-SIG parity check passes
4. HT-SIG parse succeeds
5. HT-SIG CRC matches TX: `0x41`
6. FCS PASS for MCS 0-7
