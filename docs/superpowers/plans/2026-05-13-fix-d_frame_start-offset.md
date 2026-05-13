# Fix sync_long d_frame_start Offset - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix d_frame_start offset in sync_long to correctly identify L-LTF0 DATA start position

**Architecture:** The sync_long block uses delay-and-correlate to find HT-mode L-LTF peak pairs. The correlation peak is found at L-LTF1 position, then d_frame_start is computed as `lower_peak + 2`. Due to a 1-sample offset error, d_frame_start=177 instead of 176, causing FFT window misalignment and ICI.

**Tech Stack:** GNU Radio C++ block (sync_long), Python test harness

---

## Root Cause Summary

| Parameter | Current | Expected | Delta |
|-----------|---------|----------|-------|
| d_frame_start | 177 | 176 | +1 |
| FFT window | samples 177-240 | samples 176-239 | shifted by 1 |
| FFT magnitude | ~5.1 | ~8.88 | -42% |
| H magnitude | ~0.57 | ~1.0 | -43% |
| Equalized symbol magnitude | ~15-35 | ~1.0 | 15-35x too large |

---

## File Structure

- `lib/sync_long.cc` — d_frame_start computation
- `lib/ht_symbol_splitter_impl.cc` — receives d_frame_start via wifi_start tag
- `lib/frame_equalizer_impl.cc` — channel estimation using H
- `test_mcs_end_to_end.py` — end-to-end test

---

## Task 1: Verify d_frame_start = 177 is wrong, should be 176

**Files:**
- Read: `lib/sync_long.cc` lines 340-360 (HT-mode peak selection)
- Read: `lib/sync_long.cc` lines 400-450 (d_frame_start computation)

- [ ] **Step 1: Read sync_long d_frame_start computation**

In HT mode, d_frame_start is computed as:
```cpp
d_frame_start = best_ht_lower_peak + 2;
```

The "best_ht_lower_peak" is the lower index of the HT LTF peak pair with diff ≈ 80.

- [ ] **Step 2: Understand the offset logic**

The correlation detects two identical L-LTF sequences separated by 80 samples (64 LTF + 16 CP).
If the upper peak is at position `i` and lower peak at position `k`, then `diff = i - k = 80`.
The L-LTF0 DATA starts at `k + 2 = lower_peak + 2`.

For d_frame_start = 176 to be correct:
- L-LTF0 DATA must start at input sample 176
- lower_peak must be 174 (so 174 + 2 = 176)

- [ ] **Step 3: Run test to see current d_frame_start**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(d_frame_start|HT-mode-plateau SELECTED)" | head -10
```

Expected: d_frame_start=177, best_lower_peak=175

- [ ] **Step 4: Verify the offset formula**

The correlation peak occurs at the END of L-LTF1 (the second identical sequence).
If lower_peak = 175 corresponds to the START of L-LTF1, then:
- L-LTF1 starts at 175
- L-LTF1 ends at 175 + 64 - 1 = 238
- L-LTF0 starts at 175 - 80 = 95 (but wait, this is wrong)

Actually let me reconsider. The two LTF sequences are:
- LTF0: samples 160-223 (CP at 160-175, DATA at 176-223)
- LTF1: samples 240-303 (CP at 240-255, DATA at 256-303)

The delay-and-correlate finds the correlation between LTF0 and LTF1. If the second peak (lower_peak) is at position 175 in the input, that would be in the LTF0 DATA portion, not LTF1.

Wait, I need to re-examine. The sync_long's d_offset starts at SYNC_LENGTH=320 and correlates with L-LTF sequence of length 160 (two 80-sample LTF blocks). The correlation peak found at lower_peak = 175 means...

Actually, let me just verify empirically. If d_frame_start=176 works (sym_idx 0 = L-LTF0 DATA), and d_frame_start=177 causes the FFT window to shift by 1, then the fix is to subtract 1 from d_frame_start.

Try: d_frame_start = best_ht_lower_peak + 1 instead of + 2.

---

## Task 2: Implement the fix - change +2 to +1

**Files:**
- Modify: `lib/sync_long.cc` line 349

- [ ] **Step 1: Edit sync_long.cc line 349**

Replace:
```cpp
d_frame_start = best_ht_lower_peak + 2;
```

With:
```cpp
d_frame_start = best_ht_lower_peak + 1;
```

- [ ] **Step 2: Also fix legacy mode line 425**

Replace:
```cpp
d_frame_start = best_leg_lower_peak + 2;
```

With:
```cpp
d_frame_start = best_leg_lower_peak + 1;
```

- [ ] **Step 3: Also fix fallback line 439**

Replace:
```cpp
d_frame_start = peak_pos + 2;
```

With:
```cpp
d_frame_start = peak_pos + 1;
```

- [ ] **Step 4: Build**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -10
```

Expected: Build succeeds

---

## Task 3: Test the fix

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check d_frame_start**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(d_frame_start|HT-mode-plateau SELECTED)" | head -10
```

Expected: d_frame_start=176, best_lower_peak=175 (if +1 fix is correct)

- [ ] **Step 2: Check FFT magnitude**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "RAW_FFT_PROBE" | head -3
```

Expected: lltf0 magnitude ≈ 8.88 (was ~5.1 before)

- [ ] **Step 3: Check channel estimate H magnitude**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "CHAN_EST.*n=0.*d_H" | head -3
```

Expected: H magnitude ≈ 1.0 (was ~0.57 before)

- [ ] **Step 4: Check L-SIG decode**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LSIG_DECODE|rate.*0x0D|lsig_enc)" | head -10
```

Expected: lsig_enc=0 (rate field 0x0D correctly decoded)

---

## Task 4: Verify HT-SIG decode

- [ ] **Step 1: Check HT-SIG parse**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(HT-SIG|parse|Received)" | head -10
```

Expected: HT-SIG parses successfully without "parse failed"

- [ ] **Step 2: Check end-to-end packet**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(Received|PASS|FAIL)" | head -5
```

Expected: Packet received correctly

---

## Task 5: Commit

- [ ] **Step 1: Verify all changes**

```bash
cd /home/hy/gr-ieee802-11 && git diff lib/sync_long.cc
```

Expected: Only the three +2 → +1 changes

- [ ] **Step 2: Commit**

```bash
git add lib/sync_long.cc
git commit -m "fix: adjust d_frame_start offset from +2 to +1

Root cause: d_frame_start=177 caused FFT window to capture
samples 177-240 instead of 176-239, resulting in 42%
FFT magnitude loss and broken channel estimation.

Change best_ht_lower_peak/peak_pos offset from +2 to +1
to correctly align FFT window with L-LTF0 DATA start.

Fixes: H magnitude ~0.57→1.0, equalized symbol ~15→1.0"
```
