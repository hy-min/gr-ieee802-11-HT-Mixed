# SPLITTER Starvation Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix SPLITTER's out-of-bounds read by adding strict input balance checking - the state machine must never read beyond what ninput_items[0] guarantees.

**Architecture:** Add per-symbol input requirement checks in the SPLITTER's main work loop. Before processing each OFDM symbol (CP skip + 64-point FFT window), verify that enough input items remain. If insufficient, consume what we've processed and return immediately, letting GNU Radio's scheduler wake SPLITTER again when more data is available.

**Tech Stack:** C++ (GNU Radio blocks), ieee802-11 OOT module

---

## Background: The Bug

GNU Radio's circular buffer creates a view of upstream's output. When sync_long produces 385 items but SPLITTER requests 448, positions 385-447 contain stale/zero data. SPLITTER's while loop blindly continues past valid data into garbage because it doesn't track how many items it has actually consumed vs. how many it needs.

The fix: **starvation protection** - check remaining items before each symbol's processing.

---

## Files

- Modify: `lib/ht_symbol_splitter_impl.cc:227-608` (general_work function)

---

## Task 1: Add `d_items_consumed_this_call` tracking

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:227`

- [ ] **Step 1: Add `d_items_consumed_this_call` variable at start of general_work**

Find this code around line 227:
```cpp
int i = 0;
// CRITICAL SAFETY CHECK: If we don't have enough items for even one symbol, return 0.
if (ninput_items[0] < d_symbol_size) {
    fprintf(stderr, "[SPLITTER_SAFETY] ninput_items[0]=%d < d_symbol_size=%d, returning 0\n",
            ninput_items[0], d_symbol_size);
    return 0;
}
while (i < ninput_items[0]) {
```

Add a local variable to track items consumed in the current call:
```cpp
int i = 0;
int items_consumed_this_call = 0;  // Track consumed for starvation protection

// CRITICAL SAFETY CHECK: If we don't have enough items for even one symbol, return 0.
if (ninput_items[0] < d_symbol_size) {
    fprintf(stderr, "[SPLITTER_SAFETY] ninput_items[0]=%d < d_symbol_size=%d, returning 0\n",
            ninput_items[0], d_symbol_size);
    return 0;
}
while (i < ninput_items[0]) {
```

- [ ] **Step 2: Update `items_consumed_this_call` at end of loop**

Find line 592-593:
```cpp
        i++;
        consumed++;
    }
```

Change to:
```cpp
        items_consumed_this_call++;
        i++;
        consumed++;
    }
```

- [ ] **Step 3: Build and verify compilation**

Run: `cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5`
Expected: No errors, `[100%] Built target ieee802_11_python`

---

## Task 2: Add per-symbol input requirement calculation

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:246-273` (inside the main while loop, after frame_started check)

- [ ] **Step 1: Add items_needed calculation before processing each symbol**

Find this code around line 246-268:
```cpp
        // Only process after frame start is known
        if (frame_started) {
            bool should_buffer = false;
```

Replace with:
```cpp
        // Only process after frame start is known
        if (frame_started) {
            // Calculate how many items we need to complete the current symbol
            // HT-Mixed 20MHz: each OFDM symbol is 80 samples (16 CP + 64 Data)
            // We need to know if we have enough remaining items to finish current symbol
            int remaining_items = ninput_items[0] - i;
            int items_needed_for_current_symbol = 80;  // 16 CP + 64 Data

            // STARVATION PROTECTION: If not enough items remain to complete current symbol,
            // consume what we've processed and return. GNU Radio scheduler will wake us
            // again when more data is available.
            if (remaining_items < items_needed_for_current_symbol) {
                fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, returning early\n",
                        remaining_items, items_needed_for_current_symbol);
                d_items_processed += items_consumed_this_call;
                consume(0, items_consumed_this_call);
                return produced;
            }

            bool should_buffer = false;
```

- [ ] **Step 2: Build and verify compilation**

Run: `cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5`
Expected: No errors

---

## Task 3: Handle early return properly at end of work function

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:596-607` (end of general_work)

- [ ] **Step 1: Update the end-of-function consumption logic**

Find this code around line 596-607:
```cpp
    d_items_processed += consumed;

    // PROBE: Print consumption at end of work
    static uint64_t last_consumed = 0;
    if (consumed > 0 || last_consumed > 0) {
        fprintf(stderr, "[SPLITTER_CONSUME] consumed=%d produced=%d d_items_processed=%llu\n",
                consumed, produced, (unsigned long long)d_items_processed);
        last_consumed = consumed;
    }

    consume(0, consumed);
    return produced;
}
```

This code path is for normal completion (not early return). The early return in Task 2 already handles consume and return. The normal path uses `consumed` which equals `items_consumed_this_call` when the loop completes normally.

The existing code is correct - no change needed here. The starvation protection early return is a pre-exit that handles the case where the loop would read garbage.

- [ ] **Step 2: Clean up the SPLITTER_CIRCULAR fix that was previously added (if still present)**

Check if there's a SPLITTER_CIRCULAR section still in the code:
```bash
grep -n "SPLITTER_CIRCULAR" /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

If found (shouldn't be - we reverted it earlier), remove the `effective_ninput` logic since it's no longer needed with starvation protection.

---

## Task 4: Remove debug probes that interfere with normal operation

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Identify and remove excessive debug probes**

The file currently has many `fprintf(stderr, ...)` probes that fire on every call. For production, we should remove or comment out these probes:

Key probes to REMOVE (they cause log spam and slow down processing):
- Line ~253-256: `[SPLITTER_START]` probe
- Line ~261-268: `[SPLITTER_IN_AMP]` probe (fires on 11 positions × 20 times)
- Line ~271-277: `[SPLITTER_AMPLITUDE]` probe
- Line ~278-283: `[SPLITTER_IDX_xxx]` probe
- Line ~395-411: `[SPLITTER_RESET_CHECK]` and `[SPLITTER_LSIG_ABS]` probes
- Line ~440-448: `[SPLITTER_REL]` probe
- Line ~454-478: `[SPLITTER_LTF0_PROBE]` and `[SPLITTER_LTF1_PROBE]` probes
- Line ~481-487: `[SPLITTER_LSIG_BUF]` probe
- Line ~493-501: `[SPLITTER_LTF1_END]` boundary probe
- Line ~542-543: `[SPLITTER]` output probe
- Line ~556-562: `[SPLITTER_FFTPROBE]` - keep this one, it's useful
- Line ~565-572: `[SPLITTER_DUMP]` probe
- Line ~574-579: `[SPLITTER_LLTF_VERIFY]` probe
- Line ~601-604: `[SPLITTER_CONSUME]` probe

Keep only:
- `[SPLITTER_WORK]` (line ~88)
- `[SPLITTER_FFTPROBE]` (line ~556-562)
- `[SPLITTER_STARVATION]` (newly added)
- `[SPLITTER_SAFETY]` (line ~231-233)

- [ ] **Step 2: Build and run test**

Run: `cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -3`
Then run test: `cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(STARVATION|SPLITTER_WORK|LSIG_DECODE|HT-SIG)" | head -20`

Expected: SPLITTER_STARVATION should appear at least once (when 385 limit triggers early return), then SPLITTER_WORK call=1 should show fresh data

---

## Task 5: Verify end-to-end L-SIG and HT-SIG decoding

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run full end-to-end test**

Run: `cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(parity|HT-SIG)" | head -10`

Current (broken) expected output:
```
[LSIG_DECODE] Parity check failed! parity_sum=1
[EQ][HT-SIG] parse failed: lsig=2 htsig=3/4
```

After fix expected output:
```
[LSIG_DECODE] Parity check passed!
```

- [ ] **Step 2: If parity check still fails, investigate**

Possible causes:
1. Starvation protection is too aggressive - items_needed=80 is too high for preamble symbols
2. Buffer alignment still wrong
3. Channel estimation issue

Check `[SPLITTER_FFTPROBE]` output for L-SIG (type=2):
```
[SPLITTER_FFTPROBE] type=2 rel_idx=223 td_energy=X peak_mag=X first=VALID last=VALID
```
Both first and last should be non-zero.

---

## Task 6: Commit changes

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Review changes**

Run: `cd /home/hy/gr-ieee802-11 && git diff lib/ht_symbol_splitter_impl.cc | head -100`

- [ ] **Step 2: Stage and commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): add starvation protection to prevent OOB read

When sync_long produces fewer items than SPLITTER requests (385 vs 448),
the circular buffer contains stale zeros at positions 385-447.
Previously SPLITTER's state machine greedily read past valid data
into garbage, corrupting L-SIG and HT-SIG FFT output.

Fix: Add per-symbol input requirement check in main work loop.
Before processing each OFDM symbol, verify that enough items
remain (80 = 16 CP + 64 Data). If insufficient, consume what
we've processed and return early, letting GNU Radio's scheduler
wake SPLITTER again when more data is available.

This is GNU Radio's standard pattern for handling asynchronous
producer/consumer rates between blocks.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] Build succeeds without warnings
- [ ] `SPLITTER_STARVATION` appears in logs (proves protection triggers)
- [ ] `SPLITTER_WORK` shows call=0 consumes partial, call=1 consumes fresh
- [ ] L-SIG parity check passes (rate=0xD, length=45, parity=1)
- [ ] HT-SIG parse succeeds (have_ht=1)
- [ ] End-to-end MCS0 test completes without NAK or errors
