# kLltf48TX Reference Sequence Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add debug probe to print kLltf48TX reference sequence values, verify they match IEEE 802.11n standard.

**Architecture:** Insert debug fprintf statements in `estimate_header_channel_from_lltf52()` to print kLltf48TX[0..11] before channel estimation loop.

**Tech Stack:** C++ (GNU Radio block), ieee802-11 OOT module

---

## File Structure

- Modify: `lib/frame_equalizer_impl.cc:617-621` — Add reference sequence debug probe

---

## Task 1: Add kLltf48TX Reference Sequence Probe

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:617-621`

- [ ] **Step 1: Find insertion point**

Find this code around line 617-621:
```cpp
    // Channel estimation using LTF0 only (avoid averaging opposite signs)
    gr_complex H52_from_ltf0[52] = {gr_complex(0,0)};
    gr_complex H52_from_ltf1[52] = {gr_complex(0,0)};

    // Compute H from LTF0
    for (int i = 0; i < 48; i++) {
```

- [ ] **Step 2: Insert debug probe before the for loop**

Replace with:
```cpp
    // Channel estimation using LTF0 only (avoid averaging opposite signs)
    gr_complex H52_from_ltf0[52] = {gr_complex(0,0)};
    gr_complex H52_from_ltf1[52] = {gr_complex(0,0)};

    // DEBUG: Print kLltf48TX reference sequence for verification
    // IEEE 802.11n标准 L-LTF 序列（部分）
    fprintf(stderr, "\n[KLTX_REF_CHECK] kLltf48TX[i] for i=0..11:\n");
    const char* expected_kltx = "+1,+1,-1,-1,+1,-1,+1,-1,+1,+1,+1,+1";  // 标准值
    fprintf(stderr, "  Expected (IEEE 802.11n): %s\n", expected_kltx);
    fprintf(stderr, "  Actual kLltf48TX:  ");
    for (int i = 0; i < 12; i++) {
        fprintf(stderr, "%+.0f ", kLltf48TX[i].real());
    }
    fprintf(stderr, "\n");

    // Also print kHeader48Sc[i] to show which SC each index corresponds to
    fprintf(stderr, "  kHeader48Sc:     ");
    for (int i = 0; i < 12; i++) {
        fprintf(stderr, "%+3d ", kHeader48Sc[i]);
    }
    fprintf(stderr, "\n");
    fflush(stderr);

    // Compute H from LTF0
    for (int i = 0; i < 48; i++) {
```

- [ ] **Step 3: Build and verify compilation**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5
```

Expected: `[100%] Built target ieee802_11_python`

---

## Task 2: Run Test and Capture Output

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and capture kltx_ref_check output**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -A5 "KLTX_REF_CHECK"
```

Expected output:
```
[KLTX_REF_CHECK] kLltf48TX[i] for i=0..11:
  Expected (IEEE 802.11n): +1,+1,-1,-1,+1,-1,+1,-1,+1,+1,+1,+1
  Actual kLltf48TX:  +1 +1 -1 -1 +1 -1 +1 -1 +1 +1 +1 +1
  kHeader48Sc:      -26 -25 -24 -23 -22 -20 -19 -18 -17 -16 -15 -14
```

- [ ] **Step 2: Verify if values match**

If kLltf48TX values match Expected:
- The reference sequence is correct
- Problem is elsewhere (FFT window alignment, symbol ordering, etc.)

If kLltf48TX values DON'T match:
- kLltf48TX array has incorrect values
- Need to fix `lib/ieee80211_constants.h`

- [ ] **Step 3: Commit changes**

```bash
cd /home/hy/gr-ieee802-11
git add lib/frame_equalizer_impl.cc
git commit -m "debug: add kLltf48TX reference sequence probe

Add debug fprintf to print kLltf48TX[0..11] values before
channel estimation loop to verify reference sequence matches
IEEE 802.11n standard.

This helps diagnose H phase non-linearity across subcarriers
(SC7=-78.6°, SC14=+113.9°, SC21=-32.2°) which indicates
possible reference sequence bin shift.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Verification Checklist

- [ ] Build succeeds without warnings
- [ ] `[KLTX_REF_CHECK]` appears in test output
- [ ] kLltf48TX values printed and compared to expected
- [ ] Values either match (reference correct) or don't match (needs fix)
- [ ] Decision path documented for next steps
