# SPLITTER Tag Timing Fix - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix SPLITTER's wifi_start tag handling to prevent `d_frame_start_abs` from being corrupted mid-preamble, which was causing L-SIG FFT output to have stale zeros at the last sample position.

**Architecture:** The SPLITTER block receives wifi_start tags from sync_long during preamble processing. The bug: `d_frame_start_abs` was being overwritten mid-preamble, causing rel_idx misalignment for buffered symbols. Fix: Keep `d_frame_start_abs = 0` during preamble (since input[0] already corresponds to L-LTF0 DATA), while still propagating wifi_start to downstream blocks.

**Tech Stack:** C++ (GNU Radio blocks), ieee802-11 OOT module, GRC flowgraph

---

## Problem Evidence

From test output before fix:
```
[SPLITTER_FFTPROBE] type=2 rel_idx=223 td_energy=42.9026 first=0.5218-0.9934i last=0.0000+0.0000i buf_filled=0
```

The `last=0.0000+0.0000i` indicates the buffer's last sample was stale/zero. After the tag-timing fix:
```
[SPLITTER_FFTPROBE] type=2 rel_idx=223 td_energy=63.7996 first=0.3098+1.0411i last=-0.1303-0.7468i buf_filled=0
```

`last` is now non-zero, confirming the fix works for L-SIG.

---

## File Structure

- Modify: `lib/ht_symbol_splitter_impl.cc:185-230` — Fix wifi_start tag handling during preamble
- Test: `test_mcs_end_to_end.py` — Verify L-SIG FFT output has non-zero last sample
- Test: `test_mcs_end_to_end.py` — Verify HT-SIG1 FFT output exists
- Test: `test_mcs_end_to_end.py` — Verify L-SIG parity check passes

---

## Task 1: Verify SPLITTER tag timing fix is present

**Files:**
- Check: `lib/ht_symbol_splitter_impl.cc:185-230`

- [ ] **Step 1: Read current tag handling code**

Find the section handling wifi_start tags in general_work(). Look for code that sets `d_frame_start_abs`.

Run:
```bash
sed -n '185,230p' /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

Expected output should include:
- `bool is_in_preamble = (d_items_processed < 500);`
- `if (is_in_preamble)` block that sets `d_frame_start_known = true`
- `d_wifi_start_accepted = true;` (propagate tag even during preamble)

- [ ] **Step 2: If fix is missing, add it**

If the preamble-aware tag handling is NOT present, find this code around line 180-195:
```cpp
// Check if this is a NEW frame
bool is_new_frame = d_frame_start_known && (d_items_processed >= 500);

if (!is_new_frame && d_frame_start_known) {
    // ignore
} else {
    d_frame_start_abs = (int64_t)d_frame_start;
```

Replace with:
```cpp
// FIX: Ignore wifi_start if d_items_processed < 500 (still in preamble).
// However, we still need to set d_frame_start_known=true so that buffering is enabled.
bool is_in_preamble = (d_items_processed < 500);

if (is_in_preamble) {
    fprintf(stderr, "[SPLITTER_TAG] Ignoring wifi_start during preamble: d_items_processed=%llu d_frame_start=%llu\n",
            (unsigned long long)d_items_processed, (unsigned long long)d_frame_start);
    // CRITICAL: Still enable buffering by setting d_frame_start_known=true
    // if this is the first wifi_start we received
    if (!d_frame_start_known) {
        d_frame_start_known = true;
        // d_frame_start_abs stays at initial value (0) during preamble
    }
    // CRITICAL: Still propagate wifi_start so downstream (FFT/equalizer) knows
    // where the frame starts. Use d_frame_start_abs=0 since that's our
    // internal coordinate during preamble.
    d_wifi_start_accepted = true;  // Propagate wifi_start!
} else {
    d_frame_start_abs = (int64_t)d_frame_start;
    fprintf(stderr, "[SPLITTER_TAG] d_frame_start=%llu -> d_frame_start_abs=%lld\n",
            (unsigned long long)d_frame_start, (long long)d_frame_start_abs);
    d_frame_start_known = true;
    d_wifi_start_accepted = true;
    // Reset state for new frame
    d_buffer_count = 0;
    d_items_processed = 0;
}
```

- [ ] **Step 3: Build and verify compilation**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -3
```

Expected: `[100%] Built target ieee802_11_python`

---

## Task 2: Verify L-SIG FFT output has non-zero last sample

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check L-SIG FFT probe**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE.*type=2"
```

Expected output should show:
```
[SPLITTER_FFTPROBE] type=2 rel_idx=223 ... last=NON_ZERO_VALUE
```

Where `NON_ZERO_VALUE` is any complex number except `0.0000+0.0000i`.

- [ ] **Step 2: If last is still zero, investigate further**

If `last=0.0000+0.0000i`, check the SPLITTER_WORK calls:
```bash
grep "SPLITTER_WORK" /tmp/test_output.txt
```

Look for:
- `call=0 ninput_items[0]=448 start_abs_idx=0 d_frame_start_abs=0` (first call)
- `d_frame_start_abs` should stay at 0 throughout preamble

---

## Task 3: Add SPLITTER starvation protection fix (Option A)

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:254-268`

- [ ] **Step 1: Read current starvation protection**

Find the starvation protection code around line 254-268:
```bash
sed -n '254,268p' /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

- [ ] **Step 2: Verify starvation check includes `d_buffer_count == 0`**

The current starvation protection should be:
```cpp
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
```

This ensures we only return early if we're NOT in the middle of buffering a symbol.

- [ ] **Step 3: If missing, add the `d_buffer_count == 0` check**

If the condition does NOT include `&& d_buffer_count == 0`, find:
```cpp
if (remaining_items < items_needed_for_current_symbol) {
```

Replace with:
```cpp
// STARVATION PROTECTION: If not enough items remain to complete current symbol,
// AND we're not in the middle of buffering a symbol (d_buffer_count == 0),
// consume what we've processed and return.
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
```

- [ ] **Step 4: Build**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -3
```

---

## Task 4: Verify HT-SIG1 FFT output exists

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check for HT-SIG1 (type=4)**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE.*type=4"
```

Expected output should show:
```
[SPLITTER_FFTPROBE] type=4 rel_idx=383 ...
```

- [ ] **Step 2: If HT-SIG1 missing, check for starvation**

Run:
```bash
grep "SPLITTER_STARVATION" /tmp/test_output.txt
```

If starvation triggers before HT-SIG1 is output, we need to ensure SPLITTER continues processing through the full preamble.

---

## Task 5: Verify L-SIG parity check passes

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check for parity check**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LSIG_DECODE|Parity check passed)"
```

Expected: `Parity check passed` (or no "Parity check failed" message)

- [ ] **Step 2: If parity still fails, investigate channel estimation**

Check LS equalizer output:
```bash
grep "LS_EQ" /tmp/test_output.txt | head -20
```

Look for `d_H` values being non-zero.

---

## Task 6: Clean up debug probes (optional)

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Identify debug probes to keep**

Keep only essential probes:
- `[SPLITTER_WORK]` — shows call count and buffer state
- `[SPLITTER_STARVATION]` — shows when starvation triggers
- `[SPLITTER_TAG]` — shows wifi_start handling
- `[SPLITTER_FFTPROBE]` — shows FFT output for debugging

- [ ] **Step 2: Remove excessive debug output**

Remove or comment out:
- `[DEBUG_BCKT]` probes
- `[DEBUG_FILL]` probes
- `[DEBUG_SPLITTER_REL]` probes

---

## Task 7: Commit changes

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Review changes**

Run:
```bash
cd /home/hy/gr-ieee802-11 && git diff lib/ht_symbol_splitter_impl.cc | head -80
```

- [ ] **Step 2: Stage and commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): correct wifi_start tag handling during preamble

Root cause: d_frame_start_abs was being overwritten when wifi_start tag
arrived mid-preamble, causing rel_idx misalignment for buffered OFDM symbols.
This resulted in L-SIG FFT output having stale zeros at the last sample
position (d_buffer[63] = 0).

Fix: During preamble processing (d_items_processed < 500), keep
d_frame_start_abs at its initial value (0) since the SPLITTER's input[0]
already corresponds to L-LTF0 DATA start. We still enable buffering by
setting d_frame_start_known=true and propagate wifi_start to downstream
blocks so they know the frame start position.

Also added d_buffer_count == 0 check to starvation protection to prevent
early return when we're in the middle of buffering a symbol.

Changes:
- Preamble-aware wifi_start tag handling: ignore tag value but enable buffering
- Propagate wifi_start tag even during preamble (with d_frame_start_abs=0)
- Starvation protection only triggers when not buffering (d_buffer_count == 0)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] Build succeeds without warnings
- [ ] L-SIG FFT probe shows non-zero `last` value (was `0.0000+0.0000i`, should be non-zero)
- [ ] HT-SIG1 FFT probe (type=4 @ rel_idx=383) appears in output
- [ ] SPLITTER_STARVATION appears at most once (if at all)
- [ ] L-SIG parity check passes (no "Parity check failed" message)
- [ ] End-to-end MCS0 test completes without NAK or errors

---

## Appendix: Key SPLITTER State Variables

| Variable | Purpose |
|----------|---------|
| `d_frame_start_abs` | Absolute position reference for rel_idx calculation |
| `d_frame_start_known` | Whether wifi_start tag has been received |
| `d_items_processed` | Total items consumed across all work() calls |
| `d_buffer_count` | Current fill level of d_buffer (0-64) |
| `d_buffer_filled` | True if buffer filled at non-boundary position |

## Appendix: SPLITTER Coordinate System

```
sync_long output[0] = sync_long input[d_frame_start=176] = L-LTF0 DATA start
ht_symbol_splitter input[0] = sync_long output[0] = L-LTF0 DATA start

Therefore: d_frame_start_abs should be 0 (not 176) for correct rel_idx
```
