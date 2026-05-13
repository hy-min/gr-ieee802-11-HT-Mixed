# Fix SPLITTER Tag Propagation - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix SPLITTER to only propagate legitimate wifi_start tags, preventing spurious frame-takeover in downstream EQ

**Architecture:** Close GNU Radio's default Tag Propagation Policy (TPP_DONT), then manually forward only legitimate wifi_start tags after SPLITTER validates them.

**Tech Stack:** GNU Radio C++ (ht_symbol_splitter_impl.cc)

---

## Problem Summary

```
[SPLITTER_TAG] Ignoring wifi_start during preamble: d_items_processed=448
[EQ][TAG] wifi_start at offset=3 freq_offset=181.000000
[EQ][FLOW] frame-takeover abs=3 allow=1
```

**Root Cause:**
1. sync_long detects a spurious correlation peak at position 181 (HT-SIG0 region)
2. SPLITTER correctly identifies this wifi_start as invalid (preamble still in progress)
3. **BUT** SPLITTER still propagates the tag downstream via GNU Radio's default TPP_ALL_TO_ALL policy
4. EQ sees wifi_start and executes frame-takeover, resetting its state machine
5. HT-SIG1 never gets decoded

**Fix:** SPLITTER must act as a "Tag Firewall" - validate wifi_start tags before propagation.

---

## File Structure

- Modify: `lib/ht_symbol_splitter_impl.cc`

---

## Task 1: Disable Default Tag Propagation

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

**步骤:**

- [ ] **Step 1: Find constructor in ht_symbol_splitter_impl.cc**

Read the file to find where the block is constructed.

- [ ] **Step 2: Add set_tag_propagation_policy(TPP_DONT) in constructor**

Find where `gr::block(name, ...)` or similar constructor is called. Add after the constructor initialization list:

```cpp
// Disable automatic tag propagation - we manually control which tags are forwarded
set_tag_propagation_policy(TPP_DONT);
```

This disables GNU Radio's default behavior of automatically forwarding all input tags to output.

---

## Task 2: Remove Automatic Tag Forwarding

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Find and remove automatic tag propagation**

In `work()` or `general_work()`, find where input tags are automatically being forwarded. Look for patterns like:
```cpp
// GNU Radio may auto-propagate tags - we need to disable this
```

Or check if there's a `add_item_tag` call that propagates wifi_start unconditionally.

- [ ] **Step 2: Remove unconditional wifi_start propagation**

Find the code that currently does:
```cpp
// Propagate wifi_start tag to output for downstream blocks
add_item_tag(0, nitems_written(0), pmt::string_to_symbol("wifi_start"),
             pmt::from_double(d_frame_start_abs),
             pmt::string_to_symbol(name()));
```

Replace with conditional propagation - only forward if the tag was actually accepted:

```cpp
// Only propagate wifi_start if SPLITTER accepted it (not ignored)
if (d_frame_start_known) {
    // Only forward if this is a legitimate wifi_start (not during preamble ignore)
    // The d_frame_start_abs was already set when we accepted the wifi_start
    add_item_tag(0, nitems_written(0), pmt::string_to_symbol("wifi_start"),
                 pmt::from_double(d_frame_start_abs),
                 pmt::string_to_symbol(name()));
}
```

**Key insight:** Only propagate wifi_start when SPLITTER **accepted** it (went through the `else` branch at line ~188), not when it **ignored** it (line ~186).

---

## Task 3: Track Whether wifi_start Was Accepted

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Add a flag to track if current wifi_start was accepted**

In the class definition (header), add:
```cpp
bool d_wifi_start_accepted;  // true if last wifi_start was accepted, false if ignored
```

Initialize in constructor:
```cpp
d_wifi_start_accepted(false),
```

- [ ] **Step 2: Set the flag when wifi_start is accepted vs ignored**

In the wifi_start handling code (~lines 182-197):
- When ignoring: `d_wifi_start_accepted = false;`
- When accepting: `d_wifi_start_accepted = true;`

- [ ] **Step 3: Only propagate if d_wifi_start_accepted is true**

Replace the unconditional `add_item_tag` with:
```cpp
// Only propagate wifi_start if SPLITTER accepted it
if (d_wifi_start_accepted) {
    add_item_tag(0, nitems_written(0), pmt::string_to_symbol("wifi_start"),
                 pmt::from_double(d_frame_start_abs),
                 pmt::string_to_symbol(name()));
    d_wifi_start_accepted = false;  // Reset after propagation
}
```

---

## Task 4: Build and Test

**步骤:**

- [ ] **Step 1: Build**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -10
```

Expected: Build succeeds

- [ ] **Step 2: Run test**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(wifi_start|frame-takeover|htsig|Received)" | head -20
```

Expected:
- No `frame-takeover` in EQ
- HT-SIG0 and HT-SIG1 both detected
- `have_ht=1` (HT frame detected)

- [ ] **Step 3: Check full test result**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | tail -10
```

Expected: MCS0 received successfully

---

## Task 5: Commit

- [ ] **Step 1: Verify changes**

```bash
cd /home/hy/gr-ieee802-11 && git diff lib/ht_symbol_splitter_impl.cc
```

Expected: Only the tag propagation policy change

- [ ] **Step 2: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): disable auto tag propagation, manually control wifi_start

Root cause: SPLITTER correctly ignored spurious wifi_start during preamble
(d_items_processed=448 < 500) but still propagated it to downstream EQ,
causing frame-takeover and state machine reset.

Fix: Close GNU Radio's default TPP_ALL_TO_ALL policy with TPP_DONT,
then manually forward wifi_start only when SPLITTER actually accepts it.
This makes SPLITTER act as a Tag Firewall for the RX chain."
```

---

## Verification Checklist

- [ ] Build succeeds without errors
- [ ] No `frame-takeover` in EQ logs
- [ ] HT-SIG0 and HT-SIG1 both detected (have_ht=1)
- [ ] L-SIG rate field correctly decoded
- [ ] HT-SIG CRC passes
- [ ] End-to-end packet reception works for MCS0
