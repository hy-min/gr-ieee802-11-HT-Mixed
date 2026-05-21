# L-SIG Rate Field Decode Fix - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix L-SIG decoding to correctly decode rate field 0x0D (currently decodes to 0x01)

**Architecture:** The issue is likely in the deinterleaver formula - the current `j = 16*(k%3) + (k/3)%16` may not correctly inverse the TX interleave formula `k = 16*(i%3) + (i/3)%16`. Need to verify the deinterleave formula is correct.

**Tech Stack:** GNU Radio blocks (frame_equalizer), C++ debug probes, Python test

---

## Current Problem

- **TX sends:** rate field 0x0D (binary 1101)
- **RX decodes:** rate field 0x01 (binary 0001) → lsig_enc=6 instead of lsig_enc=0
- **Parity check passes** for non-inverted bits, but wrong rate field decoded
- HT-SIG decode also fails

## File Structure

- `lib/frame_equalizer_impl.cc` — L-SIG/HT-SIG decode logic, deinterleaver
- `lib/signal_field_impl.cc` — TX L-SIG generation (rate field 0x0D hardcoded)
- `lib/utils.cc` — interleave/deinterleave functions
- `examples/test_mcs_end_to_end.py` — end-to-end test

---

## Task 1: Verify TX sends correct rate field 0x0D

**Files:**
- Read: `lib/signal_field_impl.cc:generate_l_sig_header()`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: Check TX L-SIG generation**

In `generate_l_sig_header()` (line 107):
```cpp
// HT-mixed: L-SIG RATE fixed 6 Mbps => 0xD (1101)
const int rate_field = 0x0D;
```

This confirms TX sends 0x0D. No change needed.

- [ ] **Step 2: Run test to confirm TX debug output**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(SIGNAL_FORMATTER|TX.*rate|rate=0x)" | head -20
```

Expected: signal_field debug output showing encoding and rate.

---

## Task 2: Verify interleave/deinterleave formulas are true inverses

**Files:**
- Read: `lib/frame_equalizer_impl.cc:deinterleave_bpsk_48()`
- Read: `lib/utils.cc:interleave()`
- Test: Write standalone C++ test or analyze manually

- [ ] **Step 1: Read the interleave function in utils.cc**

```cpp
// Forward interleave (TX side):
// i = n_row * (k mod n_col) + (k / n_col)
// For L-SIG BPSK: n_row=3, n_col=16
// i = 3 * (k mod 16) + (k / 16)
```

- [ ] **Step 2: Read the deinterleave function in frame_equalizer_impl.cc**

```cpp
// Current deinterleave (RX side):
// j = 16 * (k % 3) + (k / 3) % 16
```

- [ ] **Step 3: Verify if formulas are true inverses**

For two functions to be true inverses:
- interleave: k → i where i = 3*(k%16) + k/16
- deinterleave: i → k where k = 16*(i%3) + i/16

Test: For k=0,1,2,3, 16,17:
- interleave(k=0) should give i=0
- interleave(k=1) should give i=3
- interleave(k=2) should give i=6
- interleave(k=3) should give i=9
- interleave(k=16) should give i=1
- interleave(k=17) should give i=4

Then deinterleave(i) should return k.

**If the formulas ARE true inverses:** The bug is elsewhere (equalization, bit extraction, Viterbi).

**If the formulas are NOT true inverses:** Fix the deinterleave formula.

- [ ] **Step 4: Check the deinterleaver formula by manual trace**

Using interleave formula i = 3*(k%16) + k/16:
- k=0: i=0
- k=1: i=3
- k=2: i=6
- k=3: i=9
- ...
- k=16: i=1

Using current deinterleave formula j = 16*(k%3) + k/16:
- k=0: j=0 ✓ (should map back to i=0)
- k=1: j=16 (but interleave(1)=3, so this doesn't inverse correctly!)
- k=16: j=1 (but interleave(16)=1, so k=16 and k=? both map to j=1)

**This confirms the deinterleave formula is WRONG.** The correct inverse of i = 3*(k%16) + k/16 is NOT j = 16*(k%3) + k/16.

- [ ] **Step 5: Derive the correct deinterleave formula**

Given i = 3*(k%16) + k/16, solve for k:
- Let a = k%16 (0 to 15)
- Let b = k/16 (0 to 2)
- i = 3*a + b

For a given i:
- b = i%3
- a = (i - b)/3 = i/3 (integer division)

So k = 16*a + b = 16*(i/3) + (i%3)

**Correct deinterleave formula:** `k = 16*(i/3) + (i%3)`

Or renaming variables for RX code context: `j = 16*(i/3) + (i%3)`

---

## Task 3: Fix the deinterleave formula

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:deinterleave_bpsk_48()` line ~748

- [ ] **Step 1: Fix the deinterleave_bpsk_48 function**

Replace:
```cpp
const int j = 16 * (k % 3) + (k / 3) % 16;  // WRONG
```

With:
```cpp
const int j = 16 * (k / 3) + (k % 3);  // Correct inverse of i = 3*(k%16) + k/16
```

Or equivalently:
```cpp
const int j = 16 * (k / 3) + (k % 3);  // Correct: i = 3*(k%16) + k/16 → k = 16*(i/3) + (i%3)
```

- [ ] **Step 2: Verify the fix manually**

For k=0: j = 16*(0/3) + (0%3) = 16*0 + 0 = 0 ✓
For k=1: j = 16*(1/3) + (1%3) = 16*0 + 1 = 1
For k=2: j = 16*(2/3) + (2%3) = 16*0 + 2 = 2
For k=3: j = 16*(3/3) + (3%3) = 16*1 + 0 = 16
For k=4: j = 16*(4/3) + (4%3) = 16*1 + 1 = 17
...
For k=16: j = 16*(16/3) + (16%3) = 16*5 + 1 = 81? No wait, that's wrong.

Actually let me recalculate. If the deinterleave formula is supposed to inverse the interleave:
- interleave takes original position i and computes k = 3*(i%16) + i/16
- deinterleave takes k and should return i

So if interleave(i=0) = k=0, then deinterleave(k=0) should = 0.
If interleave(i=1) = k=3, then deinterleave(k=3) should = 1.

Using j = 16*(k/3) + (k%3):
- j = 16*(0/3) + (0%3) = 0 ✓
- j = 16*(3/3) + (3%3) = 16 + 0 = 16 (but we want 1!)
- j = 16*(1/3) + (1%3) = 0 + 1 = 1 ✓

So the issue is that k=3 maps to j=16 but we want j=1. The formula is still wrong.

Let me think again. The interleave formula is:
k = 3*(i%16) + i/16

For i=0: k=0
i=1: k=3
i=2: k=6
...
i=16: k=1
i=17: k=4
...

So the mapping is:
i → k:
0 → 0
1 → 3
2 → 6
3 → 9
...
15 → 45
16 → 1
17 → 4
18 → 7
...

The inverse (deinterleave) should map:
k → i:
0 → 0
1 → 16
2 → 32
3 → 1
4 → 17
5 → 18
...

So the deinterleave formula should be:
i = 16*(k%3) + k/16

For k=0: i = 16*0 + 0 = 0 ✓
For k=1: i = 16*1 + 0 = 16 ✓
For k=2: i = 16*2 + 0 = 32 ✓
For k=3: i = 16*0 + 3 = 3 (but we want 1!) ✗

Hmm, k=3 should give i=1, but the formula gives i=3. The issue is that the interleave mapping is not a simple permutation where each k maps back to a unique i.

Let me look at the interleave pattern more carefully:
- i=0,1,2 → k=0,3,6 (k%3=0, k/16=0)
- i=3,4,5 → k=9,12,15 (k%3=0, k/16=0 or 1...)

Actually for k=9: k%3=0, k/16=0
k=12: k%3=0, k/16=0
k=15: k%3=0, k/16=0

But i=3 → k=9, i=4 → k=12, i=5 → k=15.

So for k in {0,3,6,9,12,15} (where k%3=0), i = k/3? No:
- k=0 → i=0 (but k/3=0, ok)
- k=3 → i=1 (but k/3=1, not ok)
- k=6 → i=2 (but k/3=2, not ok)

The formula is not i = k/3 for k%3=0.

Let me use the derived formula: k = 3*(i%16) + i/16

For a given k, we need to find i such that:
k = 3*(i%16) + i/16

Let i = 16*q + r, where q = i/16 (0,1,2) and r = i%16 (0-15).

Then k = 3*(r) + q, where q = 0,1,2 and r = 0-15.

For k=0: q=0, r=0 → i=16*0+0=0 ✓
For k=1: q=0, r=?? 3*r + 0 = 1 → no integer solution for r!
For k=2: q=0, r=?? 3*r + 0 = 2 → no integer solution!

Wait, this means k=1 and k=2 are not valid outputs of the interleave formula? Let me check:

k = 3*(i%16) + i/16

For i=0: k = 3*0 + 0 = 0 ✓
i=1: k = 3*1 + 0 = 3 ✓
i=2: k = 3*2 + 0 = 6 ✓
i=3: k = 3*3 + 0 = 9 ✓
...
i=15: k = 3*15 + 0 = 45 ✓
i=16: k = 3*0 + 1 = 1 ✓
i=17: k = 3*1 + 1 = 4 ✓
i=18: k = 3*2 + 1 = 7 ✓
...

So k only takes values: 0,1,3,4,6,7,9,10,12,13,15,16,18,19,21,22,24,25,27,28,30,31,33,34,36,37,39,40,42,43,45,46,1,4,7,10...

Wait, k=2 is not in the list! k=1 is from i=16, but k=2 would need i where 3*r + q = 2 with q=0,1,2 and r=0-15. For q=0: 3*r=2 → no integer r. For q=1: 3*r=1 → no integer r. For q=2: 3*r=0 → r=0, giving i=16*2+0=32 and k=3*0+2=2. So k=2 does appear but from i=32, not i=1!

So the mapping is:
i → k:
0 → 0
1 → 3
2 → 6
...
15 → 45
16 → 1
17 → 4
18 → 7
...
31 → 46
32 → 2
33 → 5
...

The inverse should be:
k → i:
0 → 0
1 → 16
2 → 32
3 → 1
4 → 17
5 → 33
6 → 2
7 → 18
8 → 34
9 → 3
...

For k=1, we get i=16. For k=2, we get i=32. For k=3, we get i=1. For k=4, we get i=17.

Looking at the pattern: i = 16*(k%3) + (k/3) when k%3 gives the row and k/3 gives the column offset.

Testing this: k=1 gives 16*1 + 0 = 16 ✓, k=2 gives 16*2 + 0 = 32 ✓, k=3 gives 16*0 + 1 = 1 ✓, k=4 gives 16*1 + 1 = 17 ✓, k=5 gives 16*2 + 1 = 33 ✓, k=6 gives 16*0 + 2 = 2 ✓.

So the deinterleave formula j = 16*(k%3) + (k/3) correctly reverses the interleave operation. The current code uses j = 16*(k%3) + (k/3)%16, but the modulo on the second term is unnecessary and changes the result—for k=17 it gives 16*2 + 1 = 33, not 21.

Let me verify the modulo issue: (17/3)%16 = 5%16 = 5, so the formula produces 16*2 + 5 = 37, which doesn't match the correct deinterleaved position of 17. The fix is removing that modulo operation entirely.

But checking the correct formula against actual test output reveals a deeper problem—the deinterleaved bits aren't producing the expected L-SIG values, which suggests the issue originates upstream in the equalization process itself, not in the deinterleaver formula.

Looking at the EQ_HEADER output, the equalized L-SIG symbols have substantial imaginary components (±8 to ±19), which shouldn't happen for standard BPSK where equalized symbols should be purely real. This indicates the channel estimation or equalization itself is flawed, not just the bit extraction downstream.

The channel estimates in Hhdr52 show strong magnitudes (0.5-1.6), so the problem lies in how equalization is being applied. The equalize_header52_to_eq48_and_bits function performs equalization as eq = rx / H, which should yield the transmitted symbol, but the imaginary components persisting suggest either the division isn't working correctly or the channel estimates themselves are corrupted.

Even accounting for BPSK constellation rotation, the large imaginary components point to something more fundamental—possibly an issue with which channel estimate is being used for HT-SIG versus L-SIG, since HT-SIG uses QBPSK with phase rotations that might not be properly compensated by the current Hhdr52 calculation.

The 180° rotation ambiguity in L-SIG is handled by trying both inverted and non-inverted versions, but the real problem is that `detect_htsig_rotation` is being applied to HT-SIG when it should be accounting for the QBPSK rotation differently. If the rotation detection isn't working correctly, the HT-SIG symbols won't align properly even with the phase compensation applied.

I'm seeing the rate field decode to 0x01 instead of 0x0D, which suggests the issue might stem from a wrong bit position in the deinterleaver rather than the rotation handling. Rather than continue debugging the rotation logic, I should focus on verifying the deinterleaver formula is correct and ensuring the L-SIG decoding pipeline produces the right rate field value.

Looking at the current deinterleave implementation, the formula `j = 16 * (k % 3) + (k / 3) % 16` has an incorrect modulo operation on the second term—it should be `(k / 3)` without the `% 16` to properly map the interleaver output back to the original bit positions.

Using the test case where the interleaver produces [0,16,32,1,17,33,2,18,...], the correct deinterleave should reverse this to [0,1,2,3,4,5,6,7,...]. The current formula gives j(17) = 16*2 + 5 = 37 instead of 17, which is clearly wrong. Removing the spurious `% 16` from the second term corrects this to j = 16 * (k % 3) + (k / 3), which properly inverts the interleaving permutation.
