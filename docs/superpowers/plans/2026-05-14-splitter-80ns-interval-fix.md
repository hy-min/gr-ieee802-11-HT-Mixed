# SPLITTER 80ns Interval Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix SPLITTER's L-SIG boundary from `rel_idx >= 159` to `rel_idx >= 160` to ensure 64-sample FFT window and 80-sample symbol intervals.

**Architecture:** Change the `should_buffer` condition for L-SIG DATA in ht_symbol_splitter_impl.cc state machine. This is a 1-line fix that corrects an off-by-one error causing 65-sample windows instead of 64.

**Tech Stack:** C++ (GNU Radio block), ieee802-11 OOT module

---

## File Structure

- Modify: `lib/ht_symbol_splitter_impl.cc:405` — Fix L-SIG boundary condition

---

## Task 1: Fix L-SIG Boundary Condition

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:405`

- [ ] **Step 1: Find the code to change**

Find this code around line 405:
```cpp
            } else if (rel_idx < 224) {
                // Stage 2: L-SIG (rel_idx 144-159 CP, 160-223 DATA)
                should_buffer = (rel_idx >= 159);
```

- [ ] **Step 2: Fix the boundary condition**

Replace `rel_idx >= 159` with `rel_idx >= 160`:
```cpp
            } else if (rel_idx < 224) {
                // Stage 2: L-SIG (rel_idx 144-159 CP, 160-223 DATA)
                should_buffer = (rel_idx >= 160);
```

**Why this fix:**
- `rel_idx >= 159` included rel_idx 159 (the last CP sample) in DATA
- This created a 65-sample window (159-223 = 65 points)
- FFT requires exactly 64 samples
- Fixing to `rel_idx >= 160` gives exactly 64 samples (160-223 = 64 points)

- [ ] **Step 3: Build and verify compilation**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5
```

Expected: `[100%] Built target ieee802_11_python`

- [ ] **Step 4: Commit changes**

```bash
cd /home/hy/gr-ieee802-11
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): fix L-SIG boundary from 159 to 160

Off-by-one error caused 65-sample FFT window instead of 64.
Previous: should_buffer = (rel_idx >= 159) included CP sample 159
Fixed: should_buffer = (rel_idx >= 160) gives exactly 64 samples

This ensures strict 80-sample intervals between preamble symbols:
- LTF1 end: 143, L-SIG start: 160 → interval = 80 ✓

With proper intervals, H phase slope will be consistent across
all symbols, allowing rx/H to cancel perfectly in equalizer.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Run Test and Verify

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check for L-SIG parity check**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(Parity check|HT-SIG|L-SIG)" | head -20
```

Expected after fix:
- `Parity check passed` (no "Parity check failed")
- HT-SIG parse succeeds

- [ ] **Step 2: Verify H phase slope is consistent**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "H_PHASE_CHECK" | head -5
```

Expected: H phase should show consistent slope (not chaotic jumping)

- [ ] **Step 3: Document results**

If L-SIG parity passes:
- The 80ns interval fix worked
- H phase slope is now consistent across subcarriers

If L-SIG parity still fails:
- There may be additional issues beyond this boundary
- Further investigation needed into FFT window alignment

---

## Verification Checklist

- [ ] Build succeeds without warnings
- [ ] L-SIG boundary changed from 159 to 160
- [ ] L-SIG parity check passes
- [ ] HT-SIG parse succeeds
- [ ] End-to-end MCS0 test completes
