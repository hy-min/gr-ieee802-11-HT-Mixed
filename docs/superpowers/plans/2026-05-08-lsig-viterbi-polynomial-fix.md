# L-SIG Viterbi Polynomial Mismatch Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Fix the convolutional encoder / Viterbi decoder polynomial mismatch causing L-SIG parity check failure in HT-mixed mode

**Architecture:** The TX convolutional encoder in `utils.cc` uses octal polynomials `0133` (0x5B) and `0171` (0x79), but the RX Viterbi decoder in `frame_equalizer_impl.cc` expects hex {0x6D, 0x4F}. These MUST match for correct decoding.

**Tech Stack:** GNU Radio, IEEE 802.11, C++

---

## Problem Statement

TX and RX use different convolutional polynomials:

| Component | Location | Polynomials |
|-----------|----------|-------------|
| TX encoder | `lib/utils.cc:161-162` | octal 0133 (0x5B), 0171 (0x79) |
| RX decoder | `lib/frame_equalizer_impl.cc:811-812` | 0x6D, 0x4F |

This mismatch causes Viterbi decoding to produce wrong bits, leading to L-SIG parity check failure.

---

## Verification

### Step 1: Confirm polynomial mismatch

```bash
grep -n "ones.*0133\|ones.*0171" /home/hy/gr-ieee802-11/lib/utils.cc
grep -n "0x6d\|0x4f" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

Expected output should show the mismatch.

### Step 2: Check IEEE 802.11 standard polynomials

Standard convolutional encoder polynomials for 802.11:
- g0 = 0x4D (octal 0115) - but this implementation uses 0x5B
- g1 = 0x6F (octal 0157) - but this implementation uses 0x79

Wait - let me recalculate:
- 0133 octal = 91 decimal = 0x5B
- 0171 octal = 121 decimal = 0x79
- 0x6D = 109 decimal
- 0x4F = 79 decimal

The standard polynomials for 802.11 BPSK 1/2 are:
- g0 = 0x4D (octal 0115)
- g1 = 0x6F (octal 0157)

But this codebase uses:
- TX: 0x5B (0133 octal), 0x79 (0171 octal)
- RX: 0x6D, 0x4F

These are swapped and different! The TX uses {0x5B, 0x79} while RX uses {0x6D, 0x4F}.

---

## Task 1: Fix RX Viterbi Decoder Polynomials

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:811-812`

The Viterbi decoder at line 811-812 currently uses:
```cpp
const uint8_t o0 = ones8_local(reg & 0133) & 0x1;  // WRONG: should match TX
const uint8_t o1 = ones8_local(reg & 0171) & 0x1;  // WRONG: should match TX
```

Should be changed to match TX polynomials 0x5B and 0x79:
```cpp
const uint8_t o0 = ones8_local(reg & 0155) & 0x1;  // 0x5B = 0133 octal
const uint8_t o1 = ones8_local(reg & 0143) & 0x1;  // 0x79 = 0171 octal
```

Wait - let me recalculate octal for 0x5B and 0x79:
- 0x5B = 91 = 0133 octal ✓
- 0x79 = 121 = 0171 octal ✓

So the octal values 0133 and 0171 are CORRECT for TX.

For RX, change:
```cpp
const uint8_t o0 = ones8_local(reg & 0133) & 0x1;  // octal for 0x5B
const uint8_t o1 = ones8_local(reg & 0171) & 0x1;  // octal for 0x79
```

But the hex values 0x6D and 0x4F are WRONG. Let me verify:
- 0x6D = 109 decimal = 0155 octal
- 0x4F = 79 decimal = 0117 octal

So the RX needs to use 0155 (for 0x6D) and 0117 (for 0x4F)... but those don't match TX!

Actually wait - let me re-check. The issue is:
- TX uses 0133 octal = 0x5B = 91 decimal
- RX uses 0x6D = 109 decimal

These are DIFFERENT polynomials. The RX should use the SAME as TX.

If TX uses 0133 octal (0x5B), RX should use 0133 octal too!

But the current RX code shows:
```cpp
const uint8_t o0 = ones8_local(reg & 0133) & 0x1;
const uint8_t o1 = ones8_local(reg & 0171) & 0x1;
```

This USES octal 0133 and 0171! So they DO match!

Let me re-read the actual code more carefully...

```cpp
// RX decoder (frame_equalizer_impl.cc:811-812)
const uint8_t o0 = ones8_local(reg & 0133) & 0x1;
const uint8_t o1 = ones8_local(reg & 0171) & 0x1;

// TX encoder (utils.cc:161-162)
out[i * 2]     = ones(state & 0133) % 2;
out[i * 2 + 1] = ones(state & 0171) % 2;
```

Both use 0133 and 0171! So they SHOULD match...

But wait - the grep showed 0x6d and 0x4f in viterbi_decoder_x86.cc:
```
/home/hy/gr-ieee802-11/lib/viterbi_decoder/viterbi_decoder_x86.cc:35: *   g0 = 0x6d
/home/hy/gr-ieee802-11/lib/viterbi_decoder/viterbi_decoder_x86.cc:36: *   g1 = 0x4f
```

These are COMMENTS. The actual code uses 0133 octal.

So the polynomials actually DO match between TX and RX. The issue must be elsewhere.

---

## Task 2: Verify Deinterleaver Matches Interleaver

**Files:**
- Interleaver: `lib/utils.cc:213-248` - `interleave()` function
- Deinterleaver: `lib/frame_equalizer_impl.cc:763-771` - `deinterleave_bpsk_48()` function

The interleaver uses:
```cpp
// For n_cbps == 48:
n_col = 16;
n_row = 3 * ofdm.n_bpsc;  // n_bpsc for BPSK = 1, so n_row = 3

for (int k = 0; k < n_cbps; k++) {
    const int i = n_row * (k % n_col) + (k / n_col);  // i = 3*(k%16) + k/16
    const int j = s * (i / s) + ((i + n_cbps - ((n_col * i) / n_cbps)) % s);
    // where s = max(n_bpsc/2, 1) = max(1/2, 1) = 1
    // so j = i % 1 + ... = 0? No wait...
}
```

The deinterleaver uses:
```cpp
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

Let me verify these are inverses:
- Deinterleave: out[k] = in[j] where j = 16*(k%3) + k/3
- Interleave: out[j] = in[k] where j = s*(i/s) + ...

For BPSK (n_bpsc=1), s=1:
- Interleave: j = 1*(i/1) + ((i + 48 - ((16*i)/48)) % 1) = i + 0 = i
- Wait that's not right...

Let me re-examine. For n_cbps=48, n_col=16, n_row=3, s=1:
- i = 3*(k%16) + k/16  [this maps k to i]
- j = 1*(i/1) + ((i + 48 - ((16*i)/48)) % 1) = i + 0 = i  [since % 1 = 0]

So j = i = 3*(k%16) + k/16

And deinterleave: j = 16*(k%3) + k/3

These are NOT obviously inverses. Let me check by example:
- k=0: interleave i=0, j=0; deinterleave j=0
- k=1: interleave i=3, j=3; deinterleave j=16
- k=3: interleave i=16, j=16; deinterleave j=1

Hmm, the deinterleave formula seems wrong. Let me look more carefully...

Actually, the deinterleave formula `j = 16 * (k % 3) + k / 3` produces:
- k=0: j=0
- k=1: j=16
- k=2: j=32
- k=3: j=1
- k=4: j=17
- k=5: j=33
- k=16: j=3
- k=17: j=19

This doesn't look like the inverse of the interleave.

But wait - the interleave formula when s=1 simplifies differently. Let me trace through more carefully.

For the interleave with s=1, n_col=16, n_row=3, n_cbps=48, n_bpsc=1:
```
i = n_row * (k % n_col) + (k / n_col) = 3*(k%16) + k/16
j = s * (i / s) + ((i + n_cbps - ((n_col * i) / n_cbps)) % s)
  = 1 * (i / 1) + ((i + 48 - ((16 * i) / 48)) % 1)
  = i + ((i + 48 - ((16*i)/48)) % 1)
  = i + 0   [since x % 1 == 0 for any x]
  = i
```

So j = i = 3*(k%16) + k/16.

The deinterleave formula gives j = 16*(k%3) + k/3.

For these to be inverses, if interleave maps input[k] to output[j], then deinterleave should map output[j] back to input[k].

If j = 3*(k%16) + k/16, then the inverse would need to solve for k given j.

Let me check a few values:
- k=0: j=0
- k=1: j=3
- k=2: j=6
- k=3: j=9
- k=4: j=12
- k=5: j=15
- k=6: j=16+1=17
- k=7: j=16+4=20
- k=8: j=16+7=23
- k=9: j=16+10=26
- k=10: j=16+13=29
- k=11: j=16+16=32
- k=12: j=3*12+1=37
- k=13: j=3*13+4=43
- k=14: j=3*14+7=49 (wait that's > 48!)
- k=15: j=3*15+10=55 (wait that's > 48!)

Wait, k=14 gives j=49 which is > 48? That's a problem!

Actually, let me re-check. For n_cbps=48, valid j values are 0-47. So maybe I'm misunderstanding.

Actually, the interleave formula should produce j values in range 0-47. Let me check:
- k=0: j=0
- k=1: j=3*(1%16) + 1/16 = 3*1 + 0 = 3
- k=2: j=3*2 + 0 = 6
- ...
- k=15: j=3*15 + 0 = 45
- k=16: j=3*(16%16) + 16/16 = 0 + 1 = 1
- k=17: j=3*1 + 17/16 = 3 + 1 = 4
- k=31: j=3*15 + 31/16 = 45 + 1 = 46
- k=32: j=3*0 + 32/16 = 0 + 2 = 2
- k=47: j=3*15 + 47/16 = 45 + 2 = 47
- k=48: j=3*(48%16) + 48/16 = 3*0 + 3 = 3 (but k only goes to 47!)

OK so j ranges 0-47 correctly.

Now for deinterleave j = 16*(k%3) + k/3:
- k=0: j=16*0 + 0 = 0
- k=1: j=16*1 + 0 = 16
- k=2: j=16*2 + 0 = 32
- k=3: j=16*0 + 1 = 1
- k=4: j=16*1 + 1 = 17
- k=5: j=16*2 + 1 = 33
- k=6: j=16*0 + 2 = 2
- k=7: j=16*1 + 2 = 18
- ...
- k=45: j=16*0 + 15 = 15
- k=46: j=16*1 + 15 = 31
- k=47: j=16*2 + 15 = 47

So deinterleave j values range 0-47 too, good.

Now if interleave(input[k]) = output[j] where j = 3*(k%16) + k/16,
then deinterleave should recover: input[k] = deinterleave(output[j]) where j = 16*(k%3) + k/3.

Are these true inverses? Let me verify with an example:
- input[1] -> interleave -> output[j=3] -> deinterleave[3] = input[1]?

For k=1: j = 3*(1%16) + 1/16 = 3*1 + 0 = 3
For deinterleave at position 3: we need to find k' such that j' = 16*(k'%3) + k'/3 = 3
Try k'=3: j' = 16*0 + 3/3 = 0 + 1 = 1 ≠ 3
Try k'=6: j' = 16*0 + 6/3 = 0 + 2 = 2 ≠ 3
Try k'=9: j' = 16*0 + 9/3 = 0 + 3 = 3 ✓

So deinterleave[3] = output[k'=9] = input[1]?

Wait, that means deinterleave[3] gives input[9], not input[1]. That can't be right...

I think there's a bug in my analysis or in the deinterleave formula. Let me look at the actual 802.11 spec.

Actually, let me just run the test and see if L-SIG decoding works. The polynomials match (both use octal 0133/0171), so the issue might be elsewhere.

---

## Task 3: Run Test to Confirm Current State

**Files:**
- Build: `lib/frame_equalizer_impl.cc`
- Test: `examples/test_constellation_real.py`

- [ ] **Step 1: Build the project**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -20
```

- [ ] **Step 2: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep -E "(LSIG_DECODE|Parity|d_have_ht)"
```

Expected output should show L-SIG decode attempts and parity check results.

---

## Task 4: Add Debug Output for Viterbi Input/Output

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:1210-1297` - decode_lsig_direct_from_header52

Add debug output to see what bits are being decoded:

- [ ] **Step 1: Add debug output before Viterbi decode**

```cpp
fprintf(stderr, "[LSIG_DECODE] eqbits48[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", eqbits48[i]);
fprintf(stderr, "\n");
fprintf(stderr, "[LSIG_DECODE] eqbits48[24:48]=");
for (int i = 24; i < 48; i++) fprintf(stderr, "%d", eqbits48[i]);
fprintf(stderr, "\n");

fprintf(stderr, "[LSIG_DECODE] deintl48[0:24]=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", deintl48[i]);
fprintf(stderr, "\n");
fprintf(stderr, "[LSIG_DECODE] deintl48[24:48]=");
for (int i = 24; i < 48; i++) fprintf(stderr, "%d", deintl48[i]);
fprintf(stderr, "\n");
```

- [ ] **Step 2: Add debug output after Viterbi decode**

```cpp
fprintf(stderr, "[LSIG_DECODE] dec24=");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", dec24[i] & 1);
fprintf(stderr, "\n");
```

- [ ] **Step 3: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc) 2>&1 | tail -5
timeout 30 python examples/test_constellation_real.py 2>&1 | grep "LSIG_DECODE"
```

---

## Task 5: Compare TX L-SIG bits with RX decoded bits

The TX sends L-SIG with known bits. We need to verify the RX is receiving correctly.

**Files:**
- TX debug: `examples/wifi_constellation.py` - should print TX L-SIG bits
- RX debug: Added in Task 4

- [ ] **Step 1: Check TX L-SIG output**

```bash
timeout 30 python examples/wifi_constellation.py 2>&1 | grep -E "(TX.*LSIG|LSIG.*TX)" | head -5
```

- [ ] **Step 2: Compare with RX decoded bits**

The TX L-SIG should be:
```
L-SIG Rate: 1101 (0x0D = 13 = 6 Mbps BPSK 1/2)
L-SIG Length: 000011000001 (12 bits)
L-SIG Parity: matches bit 17
L-SIG Tail: 000000
```

So raw 24 bits should look like: `1101 000011000001 P TTTTTT`

Where P is parity and TTTTTT are tail bits (0).

---

## Task 6: Verify deinterleaver formula is correct inverse

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:763-771` - verify deinterleave_bpsk_48

The deinterleave formula might be wrong. Let me check the standard 802.11 deinterleaver formula:

For BPSK (n_bpsc=1), the deinterleaver should be:
```cpp
// 802.11-2012 Equation 17-27
// j = n_row * (k mod n_col) + floor(k / n_col)
// But this is the interleave... deinterleave is:
// k = n_col * j + floor(j / n_row)  [for first half, j < n_cbps/2]
```

Actually, I need to derive the inverse properly.

Interleaver for 48 carriers:
- n_col = 16, n_row = 3, n_bpsc = 1
- i = n_row * (k mod n_col) + floor(k / n_col) = 3*(k mod 16) + floor(k/16)
- j = s * floor(i / s) + (i + k) mod s where s = max(n_bpsc/2, 1) = 1
- Since s=1, j = i + (i + k) mod 1 = i + 0 = i

So interleave: j = 3*(k mod 16) + floor(k/16)

Deinterleave should solve for k:
- j = 3*(k mod 16) + floor(k/16)
- Let a = k mod 16, b = floor(k/16), so k = 16*b + a, where 0<=a<16, 0<=b<3
- j = 3*a + b
- b = j mod 3
- a = (j - b) / 3 = (j - (j mod 3)) / 3
- k = 16 * a + b = 16 * ((j - (j mod 3)) / 3) + (j mod 3)

So deinterleave: k = 16 * floor(j/3) + (j mod 3)

Let me check if this matches the current formula j = 16*(k%3) + k/3:
- Current: j = 16*(k%3) + k/3
- Correct: j = 16*floor(k/3) + (k%3)

These are THE SAME! Since:
- k%3 = k - 3*floor(k/3)
- floor(k/3) = (k - (k%3))/3

So j = 16*(k%3) + k/3 = 16*(k%3) + floor(k/3) - WRONG!

Wait: k/3 with integer division is floor(k/3). So:
- j = 16*(k%3) + k/3 = 16*(k%3) + floor(k/3)

But the correct formula is:
- j = 16*floor(k/3) + (k%3)

These are SWAPPED! The current code has the row and column indices swapped!

Actually wait, let me re-examine. In the interleave:
- k is the input index (0-47)
- j is the output index (0-47)

In deinterleave:
- We have j (output index from interleave)
- We want to recover k (input index)

Interleave: j = 3*(k mod 16) + floor(k/16)

For k in 0-47:
- k=0: j=0
- k=1: j=3
- k=2: j=6
- ...
- k=15: j=45
- k=16: j=1
- k=17: j=4
- ...
- k=31: j=46
- k=32: j=2
- ...
- k=47: j=47

So j = [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46, 2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35, 38, 41, 44, 47]

Now deinterleave formula j = 16*(k%3) + k/3:
- k=0: j=16*0 + 0 = 0 ✓
- k=1: j=16*1 + 0 = 16 ✗ (should be 3)
- k=2: j=16*2 + 0 = 32 ✗ (should be 6)
- k=3: j=16*0 + 1 = 1 ✗ (should be 9)

So the deinterleave formula is WRONG!

The correct deinterleave should be:
k = 16*floor(j/3) + (j%3)

Let me verify:
- j=0: k = 16*0 + 0 = 0 ✓
- j=3: k = 16*1 + 0 = 16 ✗ (should be 1)
- j=6: k = 16*2 + 0 = 32 ✗ (should be 2)

Hmm, still not right...

Let me derive more carefully.

Interleave: j = 3*a + b where a = k mod 16, b = floor(k/16), and 0<=a<16, 0<=b<3

So j = 3*a + b, where a in [0,15], b in [0,2]

Given j, we can compute:
b = j mod 3
a = (j - b) / 3 = floor(j/3)

Since k = 16*a + b = 16*floor(j/3) + (j mod 3)

So deinterleave should be: k = 16*floor(j/3) + (j%3)

Let me verify with the interleave output:
- j=0: k = 16*0 + 0 = 0 ✓
- j=1: k = 16*0 + 1 = 1 ✓
- j=2: k = 16*0 + 2 = 2 ✓
- j=3: k = 16*1 + 0 = 16 ✗ (should be 1)
- j=4: k = 16*1 + 1 = 17 ✗ (should be 4)

Wait, j=3 in interleave output corresponds to k=1 (input[1] -> output[3]).
But deinterleave[3] = 16*floor(3/3) + (3%3) = 16*1 + 0 = 16!

So the formula is still wrong. Let me re-derive.

From interleave: j = 3*(k mod 16) + floor(k/16)

Let k = 16*q + r where q = floor(k/16) in 0..2 and r = k mod 16 in 0..15.

Then j = 3*r + q.

Given j, we want to find k = 16*q + r.

From j = 3*r + q:
- q = j mod 3
- r = (j - q) / 3 = floor(j/3)

So k = 16*floor(j/3) + (j mod 3)

For j=0: k = 16*0 + 0 = 0 ✓
For j=1: k = 16*0 + 1 = 1 ✓
For j=2: k = 16*0 + 2 = 2 ✓
For j=3: k = 16*1 + 0 = 16 ✗ (should be 1 according to interleave)

Wait, interleave shows input[1] -> output[3].
But my deinterleave formula gives deinterleave[3] = 16!

Let me re-check interleave: j = 3*(k%16) + k/16
For k=1: j = 3*(1%16) + 1/16 = 3*1 + 0 = 3 ✓

So interleave[1] = 3.

For deinterleave to be correct, deinterleave[3] should equal 1.

But k = 16*floor(3/3) + (3%3) = 16*1 + 0 = 16 ≠ 1!

So there's a bug in my understanding. Let me re-check.

Actually wait, in C++ integer division:
- k/16 for k=1 is 0
- k%16 for k=1 is 1

So j = 3*1 + 0 = 3.

Now for deinterleave:
Given j=3, we want to find k such that j = 3*(k%16) + k/16.

Let k = 16*q + r, with 0<=q<=2, 0<=r<=15.

Then j = 3*r + q = 3*1 + 0 = 3 when q=0, r=1.

So k = 16*0 + 1 = 1.

But if we use the formula k = 16*floor(j/3) + (j%3):
- floor(3/3) = 1
- 3%3 = 0
- k = 16*1 + 0 = 16

That's wrong! The correct inverse would be to solve the diophantine equation.

Let me re-think. The interleave mapping is:
- Input index k (0-47) -> Output index j (0-47)
- j = 3*(k mod 16) + floor(k/16)

This is a permutation. Let's enumerate the pairs:
k -> j:
0 -> 0 (q=0,r=0)
1 -> 3 (q=0,r=1)
2 -> 6 (q=0,r=2)
3 -> 9 (q=0,r=3)
...
15 -> 45 (q=0,r=15)
16 -> 1 (q=1,r=0)
17 -> 4 (q=1,r=1)
...
31 -> 46 (q=1,r=15)
32 -> 2 (q=2,r=0)
33 -> 5 (q=2,r=1)
...
47 -> 47 (q=2,r=15)

So the mapping is:
- Inputs 0-15 map to j = 3*r (r=0-15) = 0, 3, 6, 9, ..., 45
- Inputs 16-31 map to j = 3*r + 1 (r=0-15) = 1, 4, 7, ..., 46
- Inputs 32-47 map to j = 3*r + 2 (r=0-15) = 2, 5, 8, ..., 47

So the deinterleave should be:
- For j in {0, 3, 6, 9, ..., 45}: k = j/3 (since j = 3*r + 0)
- For j in {1, 4, 7, ..., 46}: k = 16 + (j-1)/3
- For j in {2, 5, 8, ..., 47}: k = 32 + (j-2)/3

Or more compactly:
- q = j mod 3
- r = (j - q) / 3
- k = 16*q + r

This is NOT the same as 16*floor(j/3) + (j%3)!

Let me verify:
- j=0: q=0, r=0, k=16*0+0=0 ✓
- j=1: q=1, r=0, k=16*1+0=16 ✗ (should be 16? Wait let me check interleave[16])
  - interleave[16] = 3*(16%16) + 16/16 = 0 + 1 = 1 ✓
  - So deinterleave[1] should be 16. k=16 ✓
- j=2: q=2, r=0, k=16*2+0=32 ✓ (interleave[32]=2)
- j=3: q=0, r=1, k=16*0+1=1 ✓ (interleave[1]=3)
- j=4: q=1, r=1, k=16*1+1=17 ✓ (interleave[17]=4)

So the correct deinterleave formula is:
k = 16 * (j % 3) + (j / 3)

But the current code has:
j = 16 * (k % 3) + (k / 3)

So the formulas are swapped! The current code is computing j from k, but it's using the wrong formula (should be the transpose).

Wait, let me re-read the current deinterleave code:
```cpp
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

This says: given input index k, compute j, then out[k] = in[j].

So out[k] = in[16*(k%3) + k/3].

For this to be the inverse of interleave (which is in[k] -> out[j] where j=3*(k%16)+k/16), we need:
out[j] = in[k] iff k = deinterleave(j).

The current code computes out[k] = in[j], so it's applying the "deinterleave" formula to get j from k, then reading from in[j].

Wait, this is confusing. Let me think about it differently.

The interleaver is: output[j] = input[i] where j = 3*(i%16) + i/16.
So to deinterleave: output[i] = input[j] where j = 3*(i%16) + i/16.

But that's the SAME formula! That can't be right...

Actually no, the interleave function takes input bytes and produces output bytes. The deinterleave should undo that.

If interleave maps input byte at position i to output byte at position j = f(i), then deinterleave should map output byte at position j back to input byte at position i = f^{-1}(j).

The current deinterleave code computes j = 16*(k%3) + k/3 and sets out[k] = in[j].

So for each output position k, it reads from input position j = 16*(k%3) + k/3.

If this is correct, then in[j] = original_input[k].

So the mapping from input index j to output index k is: k = g(j) where k = 16*(j%3) + j/3.

This means the deinterleave formula should compute g(j), not f(k).

Let me verify:
- interleave: output[j=3] = input[i=1] (because j=3*(1%16)+1/16=3)
- deinterleave: output[k=1] = input[j=3] (because j=16*(1%3)+1/3=16*1+0=16? NO!)

Wait, k=1 gives j = 16*(1%3) + 1/3 = 16*1 + 0 = 16, not 3!

So deinterleave[1] reads from in[16], not in[3]. That's WRONG!

The correct deinterleave for position 1 should read from in[3] because interleave[1] = 3.

So we need: given output position k=1, find j such that interleave(k) = j.
Actually no. We have output of interleave at position j. We want to get back the original input at position k.

If interleave[1] = 3, then when we deinterleave, position 3 should give us back 1.

So given k=3 (the output index from interleave), we want j such that... no.

Let me start fresh:

interleave: output[j] = input[k], where j = 3*(k%16) + k/16.

So:
- input[0] -> output[0]
- input[1] -> output[3]
- input[2] -> output[6]
- ...
- input[16] -> output[1]
- ...

Now deinterleave should undo this:
- output[0] (which is input[0]) -> input[0]
- output[3] (which is input[1]) -> input[1]
- output[1] (which is input[16]) -> input[16]

So deinterleave[k] should give the original input index that was mapped to output position k.

For deinterleave:
- k=0: should read from in[0] (because output[0]=input[0])
- k=3: should read from in[1] (because output[3]=input[1])
- k=1: should read from in[16] (because output[1]=input[16])

Given k (output position), we want to find the original input index i such that f(i) = k.

f(i) = 3*(i%16) + i/16.

For k=0: i=0 works (3*0 + 0 = 0).
For k=3: i=1 works (3*1 + 0 = 3).
For k=1: i=16 works (3*0 + 1 = 1).

So given k, we need to find i such that i = f^{-1}(k).

From earlier derivation:
i = 16*(k%3) + (k/3)  [with integer division]

Check:
- k=0: i = 16*0 + 0 = 0 ✓
- k=3: i = 16*0 + 1 = 1 ✓ (but k=3 gives k%3=0, k/3=0? Wait 3/3=1 with integer division!)

Let me compute:
- k=3: k%3 = 3%3 = 0, k/3 = 3/3 = 1. i = 16*0 + 1 = 1 ✓
- k=1: k%3 = 1%3 = 1, k/3 = 1/3 = 0. i = 16*1 + 0 = 16 ✓

So the correct deinterleave formula is:
i = 16 * (k % 3) + (k / 3)

And the code should be:
```cpp
for (int k = 0; k < 48; k++) {
    const int i = 16 * (k % 3) + (k / 3);
    out48[k] = in48[i] & 0x1;
}
```

But the current code has:
```cpp
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

This uses j instead of i, but the formula is the same (j = 16*(k%3) + k/3 = 16*(k%3) + (k/3)).

So out[k] = in[16*(k%3) + k/3].

But we just derived that in should be indexed by i = 16*(k%3) + k/3!

So the formula is correct... but wait, let me re-verify with the actual values:

Current code: out[k] = in[16*(k%3) + k/3]

For k=3:
- 16*(3%3) + 3/3 = 16*0 + 1 = 1
- out[3] = in[1]

But we said deinterleave[3] should read from in[1] (because interleave[1] = 3).
So out[3] = in[1] ✓

For k=1:
- 16*(1%3) + 1/3 = 16*1 + 0 = 16
- out[1] = in[16]

But we said deinterleave[1] should read from in[16] (because interleave[16] = 1).
So out[1] = in[16] ✓

Wait, that seems correct!

Let me re-verify for k=0:
- 16*(0%3) + 0/3 = 16*0 + 0 = 0
- out[0] = in[0] ✓

And for k=16:
- 16*(16%3) + 16/3 = 16*1 + 5 = 21
- out[16] = in[21]

But interleave[21] = 3*(21%16) + 21/16 = 3*5 + 1 = 16 ✓
So deinterleave[16] should read from in[21] which is what the formula gives!

So the deinterleave formula IS correct: out[k] = in[16*(k%3) + k/3].

So what else could be wrong?

Let me think about the whole chain again:

TX:
1. Input bits (24 bits for L-SIG)
2. Convolutional encoding: 24 -> 48 bits (rate 1/2)
3. Interleaving: 48 bits with permutation

RX:
1. Equalized constellation bits (48 bits)
2. Deinterleaving: 48 bits (inverse permutation)
3. Viterbi decoding: 48 -> 24 bits

For this to work, deinterleave must be the exact inverse of interleave.

Let me trace through with concrete values:

TX input: [b0, b1, b2, ..., b23]
After conv encoding: [e0, e1, e2, ..., e47] where e[2i], e[2i+1] are the two encoded bits for input b[i]

After interleaving: [E0, E1, ..., E47] where Ek = e[interleave_index(k)]

RX:
Equalized bits: [R0, R1, ..., R47] = [E0, E1, ..., E47] if channel is perfect

After deinterleaving: [r0, r1, ..., r47] where r[k] = R[interleave_index(k)]

After Viterbi: [d0, d1, ..., d23]

For this to equal the original [b0, b1, ..., b23], we need:
deinterleave = inverse of interleave

And Viterbi must correctly decode the convolutional code.

So if everything else is correct, the issue might be in:
1. The Viterbi decoder itself
2. The equalized bits are wrong (channel estimation issue)
3. The deinterleaver is not the exact inverse

Let me check if there's an off-by-one or similar issue.

Actually, wait. Let me re-read the interleave code:

```cpp
for (int k = 0; k < n_cbps; k++) {
    const int i = n_row * (k % n_col) + (k / n_col);
    const int j =
        s * (i / s) +
        ((i + n_cbps - ((n_col * i) / n_cbps)) % s);
    out_sym[j] = in_sym[k];
}
```

Note: out_sym[j] = in_sym[k], so j is the output index and k is the input index.

So in[k] goes to out[j].

For BPSK with n_cbps=48, n_col=16, n_row=3, s=1:
- i = 3 * (k % 16) + (k / 16)
- j = 1 * (i / 1) + ((i + 48 - ((16 * i) / 48)) % 1) = i + 0 = i

So j = i = 3 * (k % 16) + (k / 16).

So in[k] goes to out[3*(k%16) + k/16].

Now the deinterleave code:
```cpp
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

So out[k] = in[j] where j = 16*(k%3) + k/3.

For this to be the inverse, if in[k] goes to out[j] in interleave, then in interleave's out[j] should have value in[k], and deinterleave's out[k] should read from in[j] where out[j] = in[k] in the interleave context.

Actually, I'm getting confused with the variable names. Let me rename:

Interleave: output[j] = input[i] where j = 3*(i%16) + i/16.
Deinterleave: output[i] = input[j] where j = 3*(i%16) + i/16.

Wait, they're the SAME formula? That can't be right...

No wait. In interleave, j = f(i). In deinterleave, we want i = f^{-1}(j).

The deinterleave code has: output[k] = input[j] where j = 16*(k%3) + k/3.

For this to be i = f^{-1}(j), we need f^{-1}(j) = 16*(j%3) + j/3.

We derived earlier that f(i) = 3*(i%16) + i/16.

So f^{-1}(j) should satisfy f(f^{-1}(j)) = j.

Let me verify if i = 16*(j%3) + j/3 satisfies f(i) = j:
- f(i) = 3*((16*(j%3) + j/3) % 16) + (16*(j%3) + j/3) / 16

This is getting complicated. Let me just test with the values we know:

For j=3 (which is f(1)):
- i = 16*(3%3) + 3/3 = 16*0 + 1 = 1
- f(i) = f(1) = 3*(1%16) + 1/16 = 3*1 + 0 = 3 = j ✓

For j=1 (which is f(16)):
- i = 16*(1%3) + 1/3 = 16*1 + 0 = 16
- f(i) = f(16) = 3*(16%16) + 16/16 = 3*0 + 1 = 1 = j ✓

So YES, the deinterleave formula i = 16*(j%3) + j/3 is indeed the inverse of interleave i = 3*(k%16) + k/16!

And since deinterleave code uses j = 16*(k%3) + k/3, and then out[k] = in[j], this means out[k] = in[16*(k%3) + k/3] = in[i] where i is the original input index.

So the deinterleave formula is correct!

So if polynomials match and deinterleaver is correct, what else could cause L-SIG parity failure?

Let me think about this more carefully. The parity check is on the decoded 24 bits after Viterbi. The parity bit is bit 17 (0-indexed) and should equal the XOR of bits 0-16.

If the Viterbi decoder is producing wrong bits, but the polynomials match, then either:
1. The Viterbi algorithm itself has a bug
2. The input to Viterbi is wrong (bits flipped by channel)
3. The metric computation is wrong

Actually, let me check the Viterbi decoder more carefully. The Viterbi algorithm finds the most likely sequence given the received bits.

In the RX, we're computing:
```cpp
const uint8_t r0 = rx_bits[2 * t] & 0x1;
const uint8_t r1 = rx_bits[2 * t + 1] & 0x1;
```

So for each step t, we read two bits: r0 and r1.

The branch metric is:
```cpp
const int bm = ((o0 != r0) ? 1 : 0) + ((o1 != r1) ? 1 : 0);
```

This counts the number of bit errors between the expected output (o0, o1) and received bits (r0, r1).

Now, the issue is: what are r0 and r1? They are the deinterleaved, equalized, sliced bits.

If the channel is perfect (loopback), then r0 and r1 should be exactly the transmitted encoded bits.

But wait - there's a phase rotation issue! The L-SIG uses QBPSK rotation, and if the phase rotation is not corrected properly, the constellation points might be rotated.

Actually, the parity check is on the decoded bits AFTER Viterbi. The Viterbi decoder works on +1/-1 (or 0/1) bits, not complex symbols. So phase rotation shouldn't affect bit decisions as long as the slicer makes correct decisions.

But if the phase rotation causes the slicer to flip bits, then Viterbi would decode wrong bits.

Actually, let me think about the BPSK mapping:
- Bit 0 -> constellation point at angle 0 (e.g., +1)
- Bit 1 -> constellation point at angle π (e.g., -1)

If there's a phase rotation of θ, then:
- Bit 0 might be at +e^(jθ) instead of +1
- Bit 1 might be at -e^(jθ) instead of -1

When we slice based on real part:
- If cos(θ) > 0, bit 0 is correctly identified
- If cos(θ) < 0, bit 0 is misidentified as bit 1

So if there's any phase rotation that makes cos(θ) < 0, bits will be flipped.

But BPSK only cares about the sign of the real part. If the rotation is exactly ±90° (cos=0), then both bits would have equal magnitude and be unrecoverable.

The issue is likely that the phase correction isn't being applied correctly to the L-SIG.

Let me check the frame_equalizer to see how phase correction works for L-SIG.

Actually, looking at the memory note: "两团清晰可分但有虚部散射" - the constellation shows two clusters that are separable but with imaginary axis scattering. This suggests phase rotation is present.

For L-SIG (regular BPSK, not QBPSK), the constellation should be at 0° and 180° on the real axis. If there's phase rotation, the clusters would be on a diagonal.

But the issue is that sync_long is detecting "Legacy (QBPSK failed)" which means it's not detecting HT-mixed mode at all. This is a separate issue from the deinterleaver.

Wait, let me re-read the summary:
"Detected Legacy frame (QBPSK failed)"
"decode_lsig_direct_from_header52 returns FALSE for inv_lsig=0 and inv_lsig=1"

So sync_long detects the frame as Legacy (non-HT), and when trying to decode L-SIG directly, it fails parity check.

The issue is that sync_long is not detecting HT-mixed mode correctly. It's looking for QBPSK rotation in L-SIG to detect HT-mixed mode.

But in HT-mixed mode, L-SIG should NOT have QBPSK rotation - that's the whole point. L-SIG is regular BPSK. The QBPSK rotation is applied to HT-SIG.

Wait, let me re-read the problem:

In HT-mixed mode:
- L-SIG is regular BPSK (no rotation)
- HT-SIG is QBPSK (45° rotation)

The detection mechanism:
- If QBPSK rotation detected in L-SIG position -> HT-mixed mode
- If no rotation -> Legacy mode

But that seems backwards! In HT-mixed mode, L-SIG should NOT be rotated. Only HT-SIG is rotated.

So if the detector looks for QBPSK rotation in L-SIG to detect HT-mixed, it would NOT find it, and would detect Legacy mode!

That seems like a bug in the detection logic. Let me check the sync_long code to understand the detection mechanism.

Actually, wait. The standard says HT-mixed mode uses QBPSK for the signal field (which includes L-SIG and HT-SIG). But that doesn't mean L-SIG itself is rotated - the entire signal field might use QBPSK modulation.

Let me check the 802.11 spec more carefully.

Actually, looking at the memory file:
```
Detected Legacy frame (QBPSK failed)
decode_lsig_direct_from_header52 returns FALSE for inv_lsig=0 and inv_lsig=1
```

So both inv_lsig=0 and inv_lsig=1 give FALSE. This means:
- Try decoding with no bit inversion: FAIL
- Try decoding with bit inversion: FAIL

So the Viterbi decoder is failing to decode correctly. This could be because:
1. The bits coming into Viterbi are wrong (deinterleaver issue)
2. The Viterbi decoder has a bug
3. The channel is causing too many errors

But it's a loopback test, so channel should be perfect.

Let me focus on the simpler issue: the deinterleaver formula.

I already verified that the deinterleaver formula out[k] = in[16*(k%3) + k/3] is the correct inverse of interleave. So that's not the bug.

Let me check if the interleaver and deinterleaver are being used with consistent parameters.

Actually, wait. Let me re-read the interleave code:

```cpp
for (int k = 0; k < n_cbps; k++) {
    const int i = n_row * (k % n_col) + (k / n_col);
    const int j =
        s * (i / s) +
        ((i + n_cbps - ((n_col * i) / n_cbps)) % s);
    out_sym[j] = in_sym[k];
}
```

This computes i first, then j from i.

For BPSK with n_cbps=48, n_col=16, n_row=3, s=1:
- i = 3*(k%16) + k/16
- j = 1*(i/1) + ((i + 48 - ((16*i)/48)) % 1) = i + 0 = i

So j = i = 3*(k%16) + k/16.

And out[j] = in[k].

So in[k] -> out[3*(k%16) + k/16].

Now for deinterleave:
```cpp
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

So out[k] = in[16*(k%3) + k/3].

If we apply deinterleave to the output of interleave:
- interleave output at position p has value in[p'] where p' = 3*(p%16) + p/16
- deinterleave[position p] reads from in[position j = 16*(p%3) + p/3]

So deinterleave[position p] = interleave_input[p'] where j = 16*(p%3) + p/3 is the original input index p'.

Wait, that means deinterleave IS correctly recovering the original input! Let me verify:

If interleave maps input[k] to output[j = 3*(k%16) + k/16], then deinterleave should map output[j] back to input[k].

In deinterleave, we compute j = 16*(k%3) + k/3 for output position k, and set out[k] = in[j].

So if we want to recover input[k], we need to find j such that j = 3*(k%16) + k/16.

But in deinterleave, j is computed from k, not the other way around.

So for output position k (which corresponds to j in interleave's output), deinterleave reads from input position j = 16*(k%3) + k/3.

So deinterleave[output_position = j_interleave] = in[input_position].

That means if interleave has output[j] = in[k], then deinterleave[position j] = in[k].

So deinterleave[j] = in[k] where j = 3*(k%16) + k/16.

This means k = 16*(j%3) + j/3 (which we verified earlier).

So deinterleave is correct!

OK so I've verified:
1. Polynomials match (both use octal 0133 and 0171)
2. Deinterleaver is correct inverse of interleaver

So the bug must be elsewhere. Let me check the Viterbi decoder itself.

Actually wait - I should check if the interleaver is being called with the right parameters for L-SIG.

L-SIG uses 48 carriers and BPSK modulation. So n_cbps=48, n_bpsc=1.

In the interleave code, for n_cbps=48:
```cpp
if (n_cbps == 48 || n_cbps == 96 || n_cbps == 192 || n_cbps == 288) {
    n_col = 16;
    n_row = 3 * ofdm.n_bpsc;  // n_row = 3 * 1 = 3
}
```

And s = max(ofdm.n_bpsc / 2, 1) = max(1/2, 1) = 1.

So n_col=16, n_row=3, s=1.

And in deinterleave, it's hardcoded for 48:
```cpp
const int j = 16 * (k % 3) + k / 3;
```

This matches the parameters from interleave (n_col=16, n_row=3).

So parameters are consistent.

Hmm, let me think about this from a different angle. What if the issue is in the FFT? The summary mentions that FFT parameters were wrong and were fixed. Let me check if there's an FFT shift issue.

Actually, let me focus on the immediate task. The plan should document what to check and fix.

Let me reconsider the problem statement:
- TX sends correct L-SIG bits (verified: raw24=110100000011000001000000)
- RX decodes bits fail parity check
- Both encoder/decoder polynomials match
- Deinterleaver formula is correct

So the remaining possibilities are:
1. Viterbi decoder has a bug (unlikely if it works for HT-DATA)
2. Something else in the decoding chain is wrong

Actually wait - the memory file says HT-DATA works with ~1-2/52 errors and FCS passes. So Viterbi works for HT-DATA.

The issue is specifically with L-SIG decoding. Let me check if there's something different about how L-SIG is decoded vs HT-DATA.

Actually, I notice that L-SIG uses decode_lsig_direct_from_header52 which is a different code path than the normal data decoding.

Let me focus on writing a plan that will methodically debug this.

---

## Task 7: Verify Deinterleaver Implementation

**Files:**
- Interleave: `lib/utils.cc:213-248`
- Deinterleave: `lib/frame_equalizer_impl.cc:763-771`

- [ ] **Step 1: Write a test for interleave/deinterleave**

Create a simple test that:
1. Generates 48 random bits
2. Applies interleave
3. Applies deinterleave
4. Verifies output equals input

If this test passes, deinterleaver is correct.

- [ ] **Step 2: Run the test**

```bash
# Add to frame_equalizer_impl.cc temporarily:
fprintf(stderr, "[TEST] Interleave/deinterleave test: ");
uint8_t test_in[48], test_inter[48], test_out[48];
for (int i = 0; i < 48; i++) test_in[i] = rand() % 2;
// Apply interleave (need to extract this logic)
// Apply deinterleave
bool pass = true;
for (int i = 0; i < 48; i++) if (test_out[i] != test_in[i]) pass = false;
fprintf(stderr, pass ? "PASS\n" : "FAIL\n");
```

---

## Task 8: Verify Convolutional Encoder/Decoder Polynomials

**Files:**
- Encoder: `lib/utils.cc:154-164`
- Decoder: `lib/frame_equalizer_impl.cc:773-850`

- [ ] **Step 1: Add debug to encoder**

In `convolutional_encoding`, add:
```cpp
fprintf(stderr, "[TX_ENC] input[0:8]=");
for (int i = 0; i < 8; i++) fprintf(stderr, "%d", in[i] & 0x1);
fprintf(stderr, "\n");
fprintf(stderr, "[TX_ENC] encoded[0:16]=");
for (int i = 0; i < 16; i++) fprintf(stderr, "%d", out[i] & 0x1);
fprintf(stderr, "\n");
```

- [ ] **Step 2: Add debug to decoder**

In `viterbi_decode_133_171`, add at start:
```cpp
fprintf(stderr, "[RX_VIT] rx_bits[0:16]=");
for (int i = 0; i < 16; i++) fprintf(stderr, "%d", rx_bits[i] & 0x1);
fprintf(stderr, "\n");
```

- [ ] **Step 3: Run test and compare**

The encoded bits from TX should match the rx_bits at RX (in loopback).

---

## Task 9: Identify Root Cause and Fix

Based on Tasks 7-8, identify the root cause:

**If deinterleave test fails:**
- Fix the deinterleave formula

**If encoder/decoder bits don't match:**
- Check if there's a bit ordering issue
- Check if there's a puncturing pattern issue (though L-SIG uses BPSK 1/2 which has no puncturing)

**If bits match but parity still fails:**
- The issue is in the Viterbi decoder itself
- Check the trellis termination
- Check the path metric computation

---

## Task 10: Verify Fix

- [ ] **Step 1: Rebuild**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 2: Run test**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep -E "(LSIG_DECODE|Parity|d_have_ht)"
```

Expected: L-SIG decode succeeds, parity check passes, d_have_ht_header=1

---

## Key Files Reference

### Convolutional Encoding
- `lib/utils.cc:154-164` - `convolutional_encoding()` function
- Polynomials: octal 0133 (0x5B) and 0171 (0x79)

### Viterbi Decoding
- `lib/frame_equalizer_impl.cc:773-850` - `viterbi_decode_133_171()` function
- Polynomials: octal 0133 (0x5B) and 0171 (0x79) [should match encoder]

### Interleaving
- `lib/utils.cc:213-248` - `interleave()` function
- Parameters: n_col=16, n_row=3, s=1 for BPSK 48 carriers

### Deinterleaving
- `lib/frame_equalizer_impl.cc:763-771` - `deinterleave_bpsk_48()` function
- Formula: j = 16*(k%3) + k/3

### Debug Commands
```bash
# Build
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# Test
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
timeout 30 python examples/test_constellation_real.py 2>&1 | grep "PATTERN"
```
