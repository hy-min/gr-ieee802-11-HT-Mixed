# HT-SIG Parsing Success Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Achieve HT-SIG CRC passing in pure loopback simulation (SNR=30dB, no channel impairment).

**Architecture:** The receiver chain is: sync_long → ht_symbol_splitter → fft_vcc → frame_equalizer → decode_mac. The SPLITTER must correctly output HT-SIG OFDM symbols (64 samples each) so that frame_equalizer can perform channel estimation from LTF and decode HT-SIG bits via QBPSK demapping.

**Tech Stack:** GNU Radio 3.10, C++ (SPLITTER, frame_equalizer), Python (test_mcs_end_to_end.py)

---

## File Map

| File | Role |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | SPLITTER block — CP skipping, symbol boundary detection, FFT output |
| `lib/frame_equalizer_impl.cc` | Channel estimation, HT-SIG decoding, QBPSK demapping |
| `test_mcs_end_to_end.py` | End-to-end MCS test (TX→RX loopback) |
| `docs/superpowers/specs/2026-05-16-splitter-starvation-fix-design.md` | Design doc for SPLITTER fix |

---

## Diagnostic Summary

**Current state (before this plan):**
- SPLITTER outputs: LTF0 (rel_idx=63), LTF1 (rel_idx=143), L-SIG (rel_idx=223) ✓
- SPLITTER does NOT output: HT-SIG0, HT-SIG1, HT-STF ✗
- SPLITTER starves on 2nd work call at rel_idx=605, remaining=39 < 80
- HT-SIG never reaches frame_equalizer → HT-SIG CRC never attempted

**Root cause:** SPLITTER starvation logic triggers when `d_buffer_count == 0 && remaining < 80 && in_data_region`, causing early return before HT-SIG symbols are reached.

---

## Task 1: Fix SPLITTER Starvation Logic

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (starvation check and output logic)

### The Real Root Cause

Changing `d_buffer_count == 0` to `d_buffer_count > 0` is necessary but NOT sufficient. Here's why:

When we ARE mid-buffer (`d_buffer_count > 0`) and `remaining < 80`, the starvation check returns early **without outputting the partial buffer**. The buffer only outputs when `d_buffer_count == d_fft_size (64)`, so we lose any partially-filled buffer.

Additionally, the `should_buffer` flag is evaluated AFTER the starvation check, making the `in_data_region` check unreliable (it uses hardcoded rel_idx ranges, not the actual should_buffer state).

### The Correct Fix

The fix has two parts:

**Part A:** When `d_buffer_count > 0` AND we can't complete the symbol, we must output the partial buffer before returning.

**Part B:** Move `should_buffer` calculation BEFORE the starvation check so the check is accurate.

### Implementation Steps

- [ ] **Step 1: Read the current starvation block (lines ~281-302)**

The current code (after previous fix attempt) looks like:
```cpp
bool in_data_region = (rel_idx < 64) || ...;
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    if (in_data_region) {
        fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, returning early\n",
                remaining_items, items_needed_for_current_symbol);
        d_items_processed += items_consumed_this_call;
        d_buffer_filled = false;
        d_buffer_count = 0;
        consume(0, items_consumed_this_call);
        return produced;
    }
}
bool should_buffer = false;
// ... should_buffer calculation happens AFTER starvation check ...
```

- [ ] **Step 2: Restructure - calculate should_buffer FIRST**

Move the entire `should_buffer` calculation block (lines ~304-459) to BEFORE the starvation check at line ~286. This ensures the starvation check uses the actual current should_buffer state, not a hardcoded approximation.

- [ ] **Step 3: Fix starvation to output partial buffer before returning**

Change the starvation block to:
```cpp
// If we can't get enough items to complete the current symbol AND we have partial data,
// output what we have and then continue consuming without buffering.
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    // Output the partial buffer (if we have any data)
    if (d_buffer_count > 0) {
        // Copy partial buffer to output
        for (int j = 0; j < d_buffer_count; j++) {
            out[produced++] = d_buffer[j];
        }
        d_buffer_count = 0;
        d_buffer_filled = false;
    }
    // Continue: consume remaining items without buffering, let next call handle them
    d_items_processed += items_consumed_this_call;
    consume(0, items_consumed_this_call);
    return produced;
}
```

- [ ] **Step 4: Also fix d_buffer_count == 0 case**

For `d_buffer_count == 0`, just continue consuming items without triggering starvation. The condition should be:
```cpp
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
    // d_buffer_count == 0 means we haven't started buffering. Just consume items
    // and let GNU Radio call us again with more. Don't return early.
    // DO NOTHING - fall through to normal processing
}
```

- [ ] **Step 5: Build**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
```

- [ ] **Step 6: Test**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_WORK|SPLITTER_FFTPROBE|SPLITTER_STARVATION"
```

**Expected:** No SPLITTER_STARVATION. HT-SIG0 at rel_idx=303, HT-SIG1 at rel_idx=383.

- [ ] **Step 7: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc && git commit -m "$(cat <<'EOF'
fix(splitter): output partial buffer on starvation, restructure should_buffer

Two issues fixed:
1. When d_buffer_count>0 and remaining<80, previously returned without
   outputting partial buffer. Now outputs partial buffer before returning.
2. should_buffer calculation was after starvation check, making the check
   use stale/hardcoded region判断. Now calculated first for accuracy.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1b: Fix d_frame_start_abs Offset Bug

**DISCOVERED ISSUE:** Even after the starvation fix, HT-SIG boundaries are not reached because `d_frame_start_abs` is set to the wrong value during preamble.

**Root Cause:** The `wifi_start` tag carries `d_frame_start=160` (position of L-LTF0 CP start in the full frame). The SPLITTER sets `d_frame_start_abs = d_frame_start = 160`. But this creates a mismatch: sync_long outputs starting at input 160, but the SPLITTER's rel_idx calculations assume sync_long starts at 0.

**Impact:** rel_idx values are off by ~16 samples, causing HT-SIG boundaries (rel_idx=303, 383) to never be reached.

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

### The Fix

The `wifi_start` tag has `d_frame_start=160` which is the position of L-LTF0 CP in the input buffer. But the SPLITTER should treat this as the starting reference. The question is: what should `d_frame_start_abs` actually be?

**Option A:** Set `d_frame_start_abs = 0` during preamble (ignore the tag's position, use 0 as reference)
**Option B:** Set `d_frame_start_abs = d_frame_start` but adjust all rel_idx boundaries accordingly
**Option C:** Set `d_frame_start_abs = d_frame_start` and use the CORRECT preamble boundaries that match rel_idx

### Investigation Steps

- [ ] **Step 1: Add debug probe to print d_frame_start_abs at each work call**

Add at the start of general_work():
```cpp
fprintf(stderr, "[SPLITTER_DBG] call=%d d_frame_start_abs=%lld d_frame_start_known=%d\n",
        this_call, (long long)d_frame_start_abs, d_frame_start_known);
```

- [ ] **Step 2: Analyze the correct relationship**

From the preamble structure:
- L-LTF0 DATA starts at input 176 (CP is 160-175)
- sync_long outputs from input 160 (L-LTF0 CP start)
- If d_frame_start_abs = 160, then rel_idx = input - 160
- L-LTF0 DATA: input 176-239 → rel_idx = 16-79 (but should be 0-63!)
- HT-SIG0 DATA: input 384-447 → rel_idx = 224-287 (but should be 240-303!)

**This confirms the 16-sample offset.** If d_frame_start_abs=160, LTF0 DATA appears at rel_idx 16-79 instead of 0-63.

- [ ] **Step 3: Determine the correct d_frame_start_abs**

If sync_long outputs starting at input 160, and we want rel_idx=0 to correspond to L-LTF0 DATA (input 176), then:
- d_frame_start_abs should be 176 (so rel_idx = input - 176)
- But the wifi_start tag carries d_frame_start=160

OR: If we want rel_idx=0 = input 160, then all boundary rel_idx values need to shift by 16.

The correct approach is to set `d_frame_start_abs = 176` (L-LTF0 DATA start) and adjust the boundaries in the should_buffer calculation to match.

**Correct boundaries with d_frame_start_abs=176:**
- L-LTF0 DATA: input 176-239 → rel_idx 0-63 ✓
- L-LTF1 DATA: input 256-319 → rel_idx 80-143 ✓
- L-SIG DATA: input 336-399 → rel_idx 160-223 ✓
- HT-SIG0 DATA: input 416-479 → rel_idx 240-303 ✓
- HT-SIG1 DATA: input 496-559 → rel_idx 320-383 ✓

- [ ] **Step 4: Find where d_frame_start_abs is set during preamble and change it**

In `lib/ht_symbol_splitter_impl.cc`, find where `d_frame_start_abs = d_frame_start` is set during preamble handling. Change it to `d_frame_start_abs = d_frame_start + 16` (to account for L-LTF0 CP being 16 samples before DATA start).

**Note:** `d_frame_start` from sync_long is the position of L-LTF0 CP start (input 160). L-LTF0 DATA starts at input 176. So `d_frame_start_abs = d_frame_start + 16 = 176`.

OR: Simply set `d_frame_start_abs = 176` during preamble and remove the `+ d_frame_start` part.

- [ ] **Step 5: Build and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
```

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_WORK|SPLITTER_FFTPROBE|SPLITTER_STARVATION|SPLITTER_DBG"
```

**Expected:**
- d_frame_start_abs should be 176 (not 0 or 160)
- HT-SIG0 at rel_idx=303
- HT-SIG1 at rel_idx=383

- [ ] **Step 6: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc && git commit -m "$(cat <<'EOF'
fix(splitter): set d_frame_start_abs=176 during preamble

sync_long outputs from input 160 (L-LTF0 CP start).
d_frame_start from tag = 160.
L-LTF0 DATA starts at input 176.
Set d_frame_start_abs = d_frame_start + 16 = 176
so that rel_idx=0 corresponds to L-LTF0 DATA start.

Also removed debug SPLITTER_STARVATION output after confirming fix.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Fix sync_long output_multiple and SPLITTER alignment

**DISCOVERED ISSUE:** The SPLITTER is only called 2 times total, receiving 448 items in call 0. This is insufficient to reach HT-SIG boundaries (rel_idx 303, 383). sync_long's output_multiple(640) was already set but sync_long's production is limited by input (ninput=448), not by output_multiple.

**Root Cause Chain:**
1. sync_long produces 448 items in first COPY call (limited by ninput, not output_multiple)
2. SPLITTER receives 448 items, outputs LTF0+LTF1+L-SIG+partial (225 items), starves
3. SPLITTER is only called 2 times total - insufficient for full preamble (560 items needed)
4. HT-SIG boundaries (rel_idx 303, 383) are never reached

**Files:**
- Modify: `lib/sync_long.cc` (increase output_multiple to request more from upstream)
- Modify: `lib/ht_symbol_splitter_impl.cc` (output logic)

**Try increasing sync_long output_multiple further:**

```cpp
// In sync_long.cc line 59:
set_output_multiple(1440);  // 18 symbols × 80 = 1440, much larger
```

This makes sync_long request 1440 items from its inputs in COPY state, potentially pulling more items through the chain.

- [ ] **Step 1: Change sync_long output_multiple**

In `lib/sync_long.cc` line 59, change:
```cpp
set_output_multiple(512);
```
to:
```cpp
set_output_multiple(640);  // 8 symbols × 80 = 640, enough for full preamble + some margin
```

This ensures sync_long always outputs multiples of 640 items when in COPY state.

- [ ] **Step 2: Build and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
```

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_OUT|SPLITTER_WORK|SPLITTER_FFTPROBE|SYNC_LONG_PRODUCE|htsig|GATE" | head -40
```

**Expected:**
- sync_long outputs 640+ items in first COPY call
- SPLITTER outputs HT-SIG0 at rel_idx=303, HT-SIG1 at rel_idx=383
- SPLITTER_FFTPROBE shows type=3 and type=4 symbols
- No SPLITTER_OUT_NONBOUND (all outputs should be at boundary)

- [ ] **Step 3: Check HT-SIG parsing**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "HT-SIG.*parse|parse failed|RX_CRC|CHAN_EST" | head -20
```

**Expected:** HT-SIG CRC passes or at least the rotation detection and bit extraction are correct.

- [ ] **Step 4: Commit**

```bash
git add lib/sync_long.cc && git commit -m "$(cat <<'EOF'
fix(sync_long): increase output_multiple to 640 for HT-Mixed preamble

HT-Mixed preamble needs 560 samples (7 symbols × 80) for the full
preamble. Previous output_multiple=512 caused only 448 items to be
output in first call, causing SPLITTER to receive misaligned
non-boundary FFT blocks and miss HT-SIG symbols.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Debug HT-SIG CRC (if symbols arrive but CRC still fails)

If HT-SIG symbols reach the equalizer but CRC still fails, debug the decode chain.

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`
- Debug: `test_mcs_end_to_end.py` (add print statements if needed)

### Sub-Task 3A: Verify Channel Estimation

- [ ] **Step 1: Check H estimate for HT-SIG region**

Look at `frame_equalizer_impl.cc` `estimate_header_channel_from_lltf52()` function. The channel H should be computed from averaging LTF0 and LTF1 FFT outputs. Verify that:
- H magnitude is reasonable (near 1.0 for loopback)
- H phase is linear across subcarriers (no 180° jumps)

### Sub-Task 3B: Verify HT-SIG QBPSK Demapping

- [ ] **Step 1: Check equalized HT-SIG bits**

Look at `frame_equalizer_impl.cc` `equalize_and_decode_htsig()` or equivalent. HT-SIG uses QBPSK (45° rotation per bit). Verify:
- The constellation demapping is using correct QBPSK reference
- Phase rotation applied before demapping matches IEEE 802.11n spec

### Sub-Task 3C: Verify HT-SIG CRC

- [ ] **Step 1: Check CRC-8 computation**

IEEE 802.11n HT-SIG uses CRC-8 (polynomial 0x1F). Check that the CRC computation in `frame_equalizer_impl.cc` matches the spec. If CRC fails even with correct bits, the CRC implementation is wrong.

### Sub-Task 3D: Add probe at equalizer input

- [ ] **Step 1: Add temporary probe in frame_equalizer**

Add `fprintf` to print the first few FFT bins of HT-SIG symbols before equalization, to verify the FFT data arriving at the equalizer is correct.

---

## Task 4: End-to-End Verification

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run full MCS test**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | tail -30
```

- [ ] **Step 2: Verify success criteria**

| Criterion | Expected |
|-----------|----------|
| SPLITTER outputs HT-SIG0 | Yes (type=3 at rel_idx=303) |
| SPLITTER outputs HT-SIG1 | Yes (type=3 at rel_idx=383) |
| frame_equalizer receives HT-SIG | Yes (htsig0=1, htsig1=1 in GATE) |
| HT-SIG CRC | Pass (parity check OK) |
| MCS 0 test | Pass (1/1 received) |

- [ ] **Step 3: Final commit**

```bash
git add -A && git commit -m "$(cat <<'EOF'
test: verify HT-SIG parsing success in loopback

- SPLITTER now outputs HT-SIG symbols correctly
- Channel estimation from LTF working
- HT-SIG CRC passes

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Troubleshooting Guide

### If SPLITTER still starves after Task 1 fix

**Symptom:** `SPLITTER_STARVATION` still appears.

**Check:** The `in_data_region` calculation at line ~286. The HT-SIG DATA region is `rel_idx 240-303`. If `in_data_region` is incorrectly computed, starvation may still fire in a non-DATA region.

**Fix:** Add debug print of `in_data_region` for each item to verify boundary calculations.

### If HT-SIG arrives but CRC fails

**Symptom:** HT-SIG symbols reach equalizer but `parse failed` or CRC error.

**Check in order:**
1. Channel estimate H — run `equalize_header_signal()` and verify H magnitude ≈ 1.0 and phase is linear
2. HT-SIG bit extraction — verify the 48 HT-SIG bits are correctly mapped from FFT bins
3. QBPSK demapping — verify rotation and demapping reference constellation
4. CRC-8 implementation — verify polynomial and bit ordering

### If LTF0/LTF1 phase issue reappears

**Symptom:** HT-SIG bits are inverted (QBPSK points in wrong quadrant).

**Check:** The LTF phase relationship documented in `docs/superpowers/specs/2026-05-14-ltf-phase-inversion-diagnostic.md`. If LTF0 vs LTF1 still show ~180° phase difference, apply the channel estimation workaround (use LTF0 only or apply π phase correction to LTF1).
