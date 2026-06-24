# Phase 37 — HT-SIG Viterbi Synthetic Tolerance Test (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a synthetic test harness (`examples/test_htsig_viterbi_synthetic.py`) that mirrors `test_lsig_viterbi_synthetic.py` for HT-SIG, then run 4 impairment layers (clean, +CFO, +AWGN, optional +SFO) to determine whether the HT-SIG viterbi decoder in `frame_equalizer_impl.cc::decode_htsig_from_rotated()` is bugged, or whether the viterbi is fine and the equalizer is the bottleneck.

**Architecture:** Re-implement the full HT-SIG TX→RX pipeline in NumPy (BCC encode, interleave per Table 18-6, BPSK+QBPSK modulate, insert pilots, inject impairments, hard-decision, deinterleave, viterbi decode, CRC8 check). No Python binding needed. The harness calls a Python viterbi (re-implementation of the C++ `viterbi_decode_133_171`) so failures point at the viterbi algorithm or upstream pipeline, not at binding plumbing.

**Tech Stack:** Python 3 + NumPy (existing in conda env `gnuradio`). No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-06-24-phase37-htsig-viterbi-synthetic-design.md` (commit 8670c53).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `examples/test_htsig_viterbi_synthetic.py` | CREATE (~450 lines) | Full HT-SIG TX→RX NumPy harness + 4 layer sweep |
| `docs/superpowers/notes/2026-06-24-phase37-verdict.md` | CREATE | Per-layer verdict + recommendation |
| `memory/project_p37_htsig_viterbi_synthetic.md` | CREATE | Phase 37 memory |
| `MEMORY.md` | MODIFY | Add test command + finding |

No C++ changes. No binding changes. Loopback regression unaffected.

---

## Task 1: Read template + extract HT-SIG constants

**Files:**
- Read: `test_lsig_viterbi_synthetic.py` (root, 171 lines)
- Read: `lib/frame_equalizer_impl.cc:248-263` (pilot polarity)
- Read: `lib/frame_equalizer_impl.cc:975-995` (L-SIG interleaver — for reference)
- Read: `lib/frame_equalizer_impl.cc:2155-2171` (HT-SIG deinterleaver formula)
- Read: `lib/frame_equalizer_impl.cc:2217-2296` (HT-SIG bit field layout)
- Read: `lib/frame_equalizer_impl.cc:1085-1131` (CRC8 polynomial)

- [ ] **Step 1.1: Confirm we have access to the constants we need**

Read these five locations and extract:
- `kHtPilotPolarity127[127]` from line 248-256 (verbatim, 127 ints)
- HT-SIG deinterleave formula `j = 3*(k%16) + k/16` from line 2157
- HT-SIG bit field positions from line 2224-2253:
  - bits 0-6: MCS (7 bits, LSB-first)
  - bit 7: bw40 (must be 0)
  - bits 8-23: psdu_length (16 bits, LSB-first)
  - bits 24-26: rsv (must all be 0)
  - bit 27: aggregation
  - bits 28-29: stbc
  - bit 30: adv_coding (0=BCC, 1=LDPC)
  - bit 31: short_gi
  - bits 32-33: num_ht_ltf
  - bits 34-41: CRC8 (8 bits, LSB-first)
  - bits 42-47: tail (must all be 0)
- CRC8 spec: polynomial x^8 + x^2 + x + 1, init all ones, final invert, input bits[0..33] LSB-first

- [ ] **Step 1.2: Verify the L-SIG test pattern re-implements viterbi**

In `test_lsig_viterbi_synthetic.py`, note that it does NOT call a Python binding for viterbi — it relies on Python-side re-implementation. For HT-SIG, we will re-implement the viterbi in NumPy the same way. Confirm by reading lines 1-15 of `test_lsig_viterbi_synthetic.py`.

- [ ] **Step 1.3: Note `kScIndex52` mapping for pilot insertion**

`kScIndex52[48..51] = {-21, -7, 7, 21}`. When inserting pilots at bins 48-51, the SC index used for pilot polarity is `data_sym_idx % 127` where `data_sym_idx` is the OFDM symbol index. For HT-SIG0 use `data_sym_idx = 0`; for HT-SIG1 use `data_sym_idx = 1`. Pilot sign: `sign = (pilot_idx == 3) ? -p : p` (4th pilot is flipped).

- [ ] **Step 1.4: Commit no-op (skipped if no source changes)**

No code changes. Proceed to Task 2.

---

## Task 2: Scaffold test file + write first failing test for HT-SIG bit synthesis

**Files:**
- Create: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 2.1: Create empty test file with header docstring + constants**

```python
#!/home/hy/conda/envs/gnuradio/bin/python
"""
Synthetic test: full HT-SIG viterbi decode pipeline on a known signal.

Mirrors `decode_htsig_from_rotated` (lib/frame_equalizer_impl.cc:2008):
1. BPSK+QBPSK modulate HT-SIG0 + HT-SIG1 (bits on IMAG axis after +j rotation)
2. Insert pilots at SCs {-21, -7, 7, 21} with kHtPilotPolarity127 polarity
3. Apply optional impairments (CFO / AWGN / SFO)
4. Equalize (eq = rx / H, with H=I for ideal)
5. Hard-bit decision on IMAG sign
6. Optional QBPSK rotation × invert_a × invert_b (16 candidates)
7. Deinterleave per 802.11n: j = 3*(k%16) + k/16
8. Concatenate HT-SIG0 + HT-SIG1 (96 bits)
9. Viterbi decode (rate 1/2, k=7, polynomials 133/171)
10. Extract MCS / length / LDPC / CRC8 / tail

We re-implement everything in NumPy so the test runs offline without GNU Radio.
"""
import numpy as np

# 802.11n subcarrier index for the 52-bin TX order
K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)

# 802.11n HT pilot polarity sequence (127 entries, from frame_equalizer_impl.cc:248)
K_HT_PILOT_POLARITY_127 = np.array([
    1, 1, 1, 1, -1, -1, -1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1, -1, 1, 1,
    -1, 1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1, 1, 1, -1, 1, 1, -1, -1, 1, 1, 1,
    -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1, -1, -1, 1, 1, 1, 1, 1, -1, -1,
    1, 1, -1, -1, 1, -1, 1, -1, 1, 1, -1, -1, -1, 1, 1, -1, -1, -1, -1, 1,
    -1, -1, 1, -1, 1, 1, 1, 1, -1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, 1, -1, -1,
    -1, -1, -1, -1, -1
], dtype=np.int32)
```

- [ ] **Step 2.2: Write the failing test for `make_known_htsig_bits`**

Add to the file (replaces nothing, appends):

```python


def make_known_htsig_bits(mcs=0, length=100, sgi=0, aggregation=0, ldpc=0,
                          num_ht_ltf=1, stbc=0, rsv_zero=True, tail_zero=True):
    """Build the 48-bit HT-SIG field per IEEE 802.11-2016 Section 18.3.5.3.

    Layout (LSB-first within each field):
      [0..6]   MCS (7 bits)
      [7]      bw40 (must be 0 for 20 MHz)
      [8..23]  PSDU length (16 bits)
      [24..26] reserved (must be 0)
      [27]     aggregation
      [28..29] STBC (2 bits)
      [30]     adv_coding (0=BCC, 1=LDPC)
      [31]     short GI
      [32..33] num_ht_ltf
      [34..41] CRC8 over bits 0..33
      [42..47] tail (must be 0)
    """
    bits = np.zeros(48, dtype=np.uint8)
    # MCS at bits 0..6
    for i in range(7):
        bits[i] = (mcs >> i) & 1
    # bw40 at bit 7 — leave 0 (20 MHz)
    # psdu_length at bits 8..23
    for i in range(16):
        bits[8 + i] = (length >> i) & 1
    # rsv at bits 24..26 — leave 0
    bits[27] = 1 if aggregation else 0
    for i in range(2):
        bits[28 + i] = (stbc >> i) & 1
    bits[30] = 1 if ldpc else 0
    bits[31] = 1 if sgi else 0
    for i in range(2):
        bits[32 + i] = (num_ht_ltf >> i) & 1
    # Compute CRC8 over bits 0..33
    bits[34:42] = _ht_crc8_compute(bits[0:34])
    # tail bits 42..47 stay 0
    return bits


def _ht_crc8_compute(bits0_33):
    """CRC8 per IEEE 802.11-2016 Section 18.3.5.3.5: polynomial x^8+x^2+x+1,
    init all ones, final invert, input bits[0..33] LSB-first.
    Returns 8-bit array, LSB-first."""
    c = [1, 1, 1, 1, 1, 1, 1, 1]
    for i in range(34):
        m = bits0_33[i] & 1
        c0, c1, c2, c3, c4, c5, c6, c7 = c
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    out = np.zeros(8, dtype=np.uint8)
    for j in range(8):
        out[j] = (c[j] ^ 1) & 1
    return out


def test_make_known_htsig_bits_case_a():
    """Case A: len=100, MCS=0, SGI=0, BCC, no agg, no LDPC."""
    bits = make_known_htsig_bits(mcs=0, length=100, sgi=0, ldpc=0)
    # Extract MCS
    mcs = sum(int(bits[i]) << i for i in range(7))
    assert mcs == 0, f"MCS mismatch: got {mcs}"
    # Extract length
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert length == 100, f"Length mismatch: got {length}"
    # bw40 must be 0
    assert bits[7] == 0, f"bw40 must be 0, got {bits[7]}"
    # rsv must be 0
    assert bits[24] == 0 and bits[25] == 0 and bits[26] == 0
    # sgi=0, ldpc=0, agg=0
    assert bits[27] == 0, "agg"
    assert bits[30] == 0, "ldpc"
    assert bits[31] == 0, "sgi"
    # num_ht_ltf=1 (default)
    num_ltf = (int(bits[32]) << 0) | (int(bits[33]) << 1)
    assert num_ltf == 1, f"num_ht_ltf mismatch: got {num_ltf}"
    # tail bits must be 0
    assert all(bits[42:48] == 0), f"tail bits not zero: {bits[42:48]}"
    print(f"[PASS] test_make_known_htsig_bits_case_a: bits[0..7]={bits[:8].tolist()}, "
          f"length_field={length}, mcs={mcs}, crc[0..3]={bits[34:38].tolist()}")


def test_make_known_htsig_bits_case_b():
    """Case B: len=1000, MCS=7, SGI=1, agg=1, BCC."""
    bits = make_known_htsig_bits(mcs=7, length=1000, sgi=1, aggregation=1)
    mcs = sum(int(bits[i]) << i for i in range(7))
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert mcs == 7, f"MCS mismatch: got {mcs}"
    assert length == 1000, f"Length mismatch: got {length}"
    assert bits[27] == 1, "agg"
    assert bits[31] == 1, "sgi"
    assert all(bits[42:48] == 0), "tail must be 0"
    print(f"[PASS] test_make_known_htsig_bits_case_b: mcs={mcs} length={length} "
          f"agg={bits[27]} sgi={bits[31]} crc[0..3]={bits[34:38].tolist()}")


def test_make_known_htsig_bits_case_c():
    """Case C: len=10, MCS=0, LDPC=1 (boundary)."""
    bits = make_known_htsig_bits(mcs=0, length=10, ldpc=1)
    mcs = sum(int(bits[i]) << i for i in range(7))
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert mcs == 0, f"MCS mismatch: got {mcs}"
    assert length == 10, f"Length mismatch: got {length}"
    assert bits[30] == 1, "ldpc must be 1"
    assert all(bits[42:48] == 0), "tail must be 0"
    print(f"[PASS] test_make_known_htsig_bits_case_c: mcs={mcs} length={length} "
          f"ldpc={bits[30]} crc[0..3]={bits[34:38].tolist()}")


if __name__ == "__main__":
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    print("\nHT-SIG bit synthesis tests passed (3/3).")
```

- [ ] **Step 2.3: Run, verify FAIL (no CRC function yet — but tests will FAIL because `_ht_crc8_compute` is undefined)**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected: `NameError: name '_ht_crc8_compute' is not defined`. The 3 tests fail because CRC isn't computed yet.

- [ ] **Step 2.4: Commit failing baseline**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T1.2): scaffold htsig bit synthesis test (failing baseline)"
```

---

## Task 3: Verify HT-SIG bit synthesis (CRC now works, tests PASS)

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py` (no actual edit needed — `_ht_crc8_compute` was already defined in Task 2)

- [ ] **Step 3.1: Re-run the test, verify PASS**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected output:
```
[PASS] test_make_known_htsig_bits_case_a: bits[0..7]=[...] length_field=100 mcs=0 crc[0..3]=[...]
[PASS] test_make_known_htsig_bits_case_b: mcs=7 length=1000 agg=1 sgi=1 crc[0..3]=[...]
[PASS] test_make_known_htsig_bits_case_c: mcs=0 length=10 ldpc=1 crc[0..3]=[...]

HT-SIG bit synthesis tests passed (3/3).
```

- [ ] **Step 3.2: Verify CRC bits are non-trivial by hand**

For Case A (mcs=0, length=100), the first 34 bits are mostly zero (only the length field has bits set). The CRC should produce a non-zero byte. Sanity check: print `bits[34:42]` and confirm it's not all zeros. (Already printed by the test as `crc[0..3]`.)

- [ ] **Step 3.3: Commit (no changes)**

```bash
git status  # should be clean
```

(No commit needed if no changes since Task 2.4.)

---

## Task 4: Implement BCC encoder + interleaver + QBPSK modulator + pilot insertion

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 4.1: Append encoder + modulator + pilot helpers**

```python


# ============================================================
# BCC encoder: rate 1/2, K=7, polynomials 133 (octal) and 171 (octal)
# Mirrors `viterbi_decode_133_171` companion in C++.
# ============================================================
def bcc_encode_24(bits24):
    """Encode 24 input bits into 48 coded bits using K=7 rate 1/2 BCC.

    Polynomials: G0 = 133 (octal) = 1011011, G1 = 171 (octal) = 1111001
    Trellis starts in state 0 and is forced to state 0 at the end via
    6 tail bits (input forcing). Caller is responsible for ensuring the
    input already contains the 6 tail bits at positions 18..23 (already
    the case in our 48-bit HT-SIG layout — bits 42..47 are tail, which
    are 6 zeros, so the last 6 input bits to BCC are also zeros).
    """
    assert len(bits24) == 24
    # Convert octal polynomials to bit masks
    g0 = 0o133  # = 91
    g1 = 0o171  # = 121
    state = 0   # K=7 shift register, 6-bit state
    out = np.zeros(48, dtype=np.uint8)
    for t in range(24):
        # Shift in current input bit as MSB
        reg = ((state << 1) | int(bits24[t])) & 0x7F
        o0 = bin(reg & g0).count("1") & 1
        o1 = bin(reg & g1).count("1") & 1
        out[2 * t] = o0
        out[2 * t + 1] = o1
        state = reg & 0x3F  # keep last 6 bits
    return out


# ============================================================
# HT-SIG interleaver per IEEE 802.11n Table 18-6 (depth-2, BPSK)
# Forward: j = 3*(k%16) + k/16  (k in 0..47)
# Deinterleaver uses the same formula (since 2nd permutation is identity for BPSK).
# ============================================================
def htsig_interleave(bits48):
    """Apply the 802.11n HT-SIG interleaver permutation."""
    assert len(bits48) == 48
    out = np.zeros(48, dtype=np.uint8)
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[j] = bits48[k] & 1
    return out


def htsig_deinterleave(bits48):
    """Inverse of htsig_interleave. Since the permutation is its own
    inverse for this particular j mapping, we apply the same formula."""
    return htsig_interleave(bits48)


# ============================================================
# BPSK + QBPSK modulation: bits 0 -> -j, bits 1 -> +j
# (mirrors line 2054 in frame_equalizer_impl.cc: bit on IMAG axis)
# ============================================================
def bpsk_qbpsk_modulate(coded_bits):
    """Map coded bits to complex symbols with QBPSK rotation.

    bit 0 -> -j (imag < 0)
    bit 1 -> +j (imag >= 0)
    Returns 48-element complex64 array.
    """
    assert len(coded_bits) == 48
    syms = np.empty(48, dtype=np.complex64)
    for i in range(48):
        syms[i] = 1j if coded_bits[i] else -1j
    return syms


# ============================================================
# Pilot insertion at SCs {-21, -7, 7, 21} (bins 48..51 in 52-order)
# Pilot sign: kHtPilotPolarity127[data_sym_idx % 127], flipped for pilot_idx==3
# Pilots are on IMAG axis (QBPSK): pilot_value = sign * j
# ============================================================
def insert_ht_pilots(data48_syms, data_sym_idx):
    """Insert 4 pilots into a 52-SC array. data48_syms[i] for i in 0..47.
    Pilots occupy bins 48..51 (SC indices -21, -7, +7, +21)."""
    assert len(data48_syms) == 48
    p = int(K_HT_PILOT_POLARITY_127[data_sym_idx % 127])
    out = np.empty(52, dtype=np.complex64)
    out[:48] = data48_syms
    # Pilot polarity: pilots 0,1,2 use +p, pilot 3 uses -p
    out[48] = 1j * p       # SC -21
    out[49] = 1j * p       # SC -7
    out[50] = 1j * p       # SC +7
    out[51] = -1j * p      # SC +21
    return out
```

- [ ] **Step 4.2: Run existing tests, confirm no regression**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected: 3/3 PASS (synthesis tests only — encoder/modulator not yet exercised by tests).

- [ ] **Step 4.3: Commit**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T2): BCC encoder + HT-SIG interleaver + QBPSK mod + pilot insertion"
```

---

## Task 5: Implement NumPy viterbi (hard-decision, K=7, rate 1/2, polynomials 133/171)

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 5.1: Append viterbi decoder**

```python


# ============================================================
# Viterbi decoder (hard-decision, K=7, rate 1/2, polynomials 133/171)
# Mirrors `viterbi_decode_133_171` in lib/frame_equalizer_impl.cc:997.
# ============================================================
def viterbi_decode_133_171(rx_bits):
    """Decode rx_bits (length N, even) using K=7 rate 1/2 viterbi.
    Returns (decoded_bits, best_metric).
    Best metric is the path metric of the best path ending in state 0.
    """
    assert len(rx_bits) % 2 == 0
    n_steps = len(rx_bits) // 2
    INF = 10**9
    metric_prev = np.full(64, INF, dtype=np.int64)
    metric_prev[0] = 0
    prev_state = np.full((n_steps + 1, 64), -1, dtype=np.int32)
    prev_bit = np.zeros((n_steps + 1, 64), dtype=np.uint8)

    for t in range(n_steps):
        metric_curr = np.full(64, INF, dtype=np.int64)
        r0 = int(rx_bits[2 * t])
        r1 = int(rx_bits[2 * t + 1])
        for s in range(64):
            mp = metric_prev[s]
            if mp >= INF:
                continue
            for b in (0, 1):
                reg = ((s << 1) | b) & 0x7F
                o0 = bin(reg & 0o133).count("1") & 1
                o1 = bin(reg & 0o171).count("1") & 1
                ns = reg & 0x3F
                bm = (o0 != r0) + (o1 != r1)
                mc = mp + bm
                if mc < metric_curr[ns]:
                    metric_curr[ns] = mc
                    prev_state[t + 1, ns] = s
                    prev_bit[t + 1, ns] = b
        metric_prev = metric_curr

    best_state = 0
    best_metric = int(metric_prev[best_state])
    if best_metric >= INF:
        # Search all states for lowest
        idx = int(np.argmin(metric_prev))
        best_state = idx
        best_metric = int(metric_prev[idx])
        if best_metric >= INF:
            return None, INF

    decoded = np.zeros(n_steps, dtype=np.uint8)
    cur = best_state
    for t in range(n_steps, 0, -1):
        decoded[t - 1] = prev_bit[t, cur]
        cur = int(prev_state[t, cur])
        if cur < 0 and t > 1:
            return None, INF
    return decoded, best_metric
```

- [ ] **Step 5.2: Append a unit test for the viterbi with a known encode→decode round-trip**

```python


def test_viterbi_encode_decode_roundtrip():
    """BCC encode 24 random bits, then viterbi decode, expect to recover
    the input. This is the viterbi's correctness sanity check."""
    rng = np.random.default_rng(seed=12345)
    bits24 = rng.integers(0, 2, 24, dtype=np.uint8)
    coded48 = bcc_encode_24(bits24)
    decoded24, metric = viterbi_decode_133_171(coded48)
    assert decoded24 is not None, "viterbi failed to converge"
    assert np.array_equal(decoded24, bits24), \
        f"viterbi mismatch: got {decoded24}, expected {bits24}, metric={metric}"
    print(f"[PASS] test_viterbi_encode_decode_roundtrip: metric={metric}, "
          f"len={len(bits24)}, all-match=True")
```

- [ ] **Step 5.3: Wire it into main() temporarily**

Add `test_viterbi_encode_decode_roundtrip()` to the `if __name__ == "__main__":` block BEFORE the other 3 tests:

```python
if __name__ == "__main__":
    test_viterbi_encode_decode_roundtrip()
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    print("\nHT-SIG bit synthesis tests passed (3/3).")
```

- [ ] **Step 5.4: Run, verify PASS**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected:
```
[PASS] test_viterbi_encode_decode_roundtrip: metric=0, len=24, all-match=True
[PASS] test_make_known_htsig_bits_case_a: ...
[PASS] test_make_known_htsig_bits_case_b: ...
[PASS] test_make_known_htsig_bits_case_c: ...

HT-SIG bit synthesis tests passed (3/3).
```

(Metric should be 0 because the channel is ideal.)

- [ ] **Step 5.5: Commit**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T3): NumPy viterbi K=7 133/171 + round-trip test"
```

---

## Task 6: Wire Layer 1 (clean) — full HT-SIG TX→RX pipeline, expect 3/3 PASS

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 6.1: Append full pipeline + Layer 1 driver**

```python


# ============================================================
# Apply QBPSK rotation candidates (4 rotations × inv_a × inv_b = 16 candidates).
# QBPSK detection: E_Q > E_I rotates by 90° (-90°, 0°, +90°, 180°).
# For an ideal test signal with known rotation=0, candidates with the wrong
# rotation or wrong inversions will fail parity/CRC/tail checks.
# ============================================================
def slice_with_qbpsk_candidate(eq48_a, eq48_b, rot_idx, inv_a, inv_b):
    """Apply QBPSK rotation + invert flags, return 96 hard-decision bits.

    rot_idx in {0, 1, 2, 3} mapping:
      0: rotate by -90°  (mult by j)
      1: rotate by 0°    (no rotation)
      2: rotate by +90°  (mult by -j)
      3: rotate by 180°  (mult by -1)
    Then bit decision on IMAG axis: bit = (imag >= 0).
    """
    rot_phases = {0: 1j, 1: 1.0 + 0j, 2: -1j, 3: -1.0 + 0j}
    rot = rot_phases[rot_idx]
    eq48_a_r = eq48_a * rot
    eq48_b_r = eq48_b * rot
    bits_a = (eq48_a_r.imag >= 0).astype(np.uint8)
    bits_b = (eq48_b_r.imag >= 0).astype(np.uint8)
    if inv_a:
        bits_a ^= 1
    if inv_b:
        bits_b ^= 1
    return bits_a, bits_b


def decode_htsig_attempt(eq48_a, eq48_b):
    """Try all 16 QBPSK rotation candidates. Return (best_decoded48, info_dict)
    or (None, info_dict) if all fail.

    info_dict contains: best_metric, chosen_rot, chosen_inv_a, chosen_inv_b,
    crc_ok (bool), fail_reason.
    """
    best = None
    best_metric = 10**9
    best_info = {"crc_ok": False, "fail_reason": "no_candidate"}
    for rot_idx in range(4):
        for inv_a in (False, True):
            for inv_b in (False, True):
                bits_a, bits_b = slice_with_qbpsk_candidate(
                    eq48_a, eq48_b, rot_idx, inv_a, inv_b)
                # Deinterleave each half
                deint_a = htsig_deinterleave(bits_a)
                deint_b = htsig_deinterleave(bits_b)
                # Concatenate 96 bits
                enc96 = np.concatenate([deint_a, deint_b])
                # Viterbi decode
                dec48, metric = viterbi_decode_133_171(enc96)
                if dec48 is None or len(dec48) != 48:
                    continue
                # Validate tail + CRC
                tail_ok = np.all(dec48[42:48] == 0)
                crc_calc = _ht_crc8_compute(dec48[0:34])
                crc_match = np.array_equal(crc_calc, dec48[34:42])
                # bw40 and rsv must be 0
                field_ok = (dec48[7] == 0 and np.all(dec48[24:27] == 0))
                if tail_ok and crc_match and field_ok and metric < best_metric:
                    best = dec48
                    best_metric = metric
                    best_info = {
                        "crc_ok": True,
                        "rot_idx": rot_idx,
                        "inv_a": inv_a,
                        "inv_b": inv_b,
                        "metric": metric,
                        "fail_reason": "OK",
                    }
    if best is None:
        return None, best_info
    return best, best_info


# ============================================================
# Layer 1: clean (no impairment). Ideal channel H = identity.
# Synthesize → modulate → decode → expect CRC OK + correct fields.
# ============================================================
def synth_and_decode_layer1(case_name, **case_kwargs):
    """One Layer 1 test case. Returns dict with crc_ok, mcs, length, etc."""
    bits48_tx = make_known_htsig_bits(**case_kwargs)
    # Split: bits 0..23 -> HT-SIG0, bits 24..47 -> HT-SIG1 (per 802.11n).
    # But our layout is: bits 0..23 = HT-SIG0, bits 24..47 = HT-SIG1.
    # Actually HT-SIG0 is 24 bits and HT-SIG1 is 24 bits, but our 48-bit
    # field is laid out as MCS/length/.../crc/tail all 48 bits. The split
    # is: bits 0..23 go to symbol 0, bits 24..47 to symbol 1.
    bits0 = bits48_tx[0:24]
    bits1 = bits48_tx[24:48]
    # BCC encode each half
    coded0 = bcc_encode_24(bits0)
    coded1 = bcc_encode_24(bits1)
    # Interleave each half
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    # QBPSK modulate
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    # Insert pilots (data_sym_idx 0 for HT-SIG0, 1 for HT-SIG1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)
    # Equalize: H = identity (ideal channel), so eq = rx directly.
    # Also need to drop the pilot SCs to get back to 48-element arrays.
    eq48_a = sc52_0[0:48]
    eq48_b = sc52_1[0:48]
    # Run the full decoder over 16 candidates
    dec48, info = decode_htsig_attempt(eq48_a, eq48_b)
    info["expected_mcs"] = case_kwargs.get("mcs", 0)
    info["expected_length"] = case_kwargs.get("length", 100)
    info["expected_agg"] = case_kwargs.get("aggregation", 0)
    info["expected_sgi"] = case_kwargs.get("sgi", 0)
    info["expected_ldpc"] = case_kwargs.get("ldpc", 0)
    if dec48 is not None:
        info["got_mcs"] = sum(int(dec48[i]) << i for i in range(7))
        info["got_length"] = sum(int(dec48[8 + i]) << i for i in range(16))
        info["got_agg"] = int(dec48[27])
        info["got_sgi"] = int(dec48[31])
        info["got_ldpc"] = int(dec48[30])
        info["bit_match"] = bool(np.array_equal(dec48, bits48_tx))
    return info


def test_layer1_clean():
    """Layer 1: clean (no impairment). 3/3 must PASS."""
    cases = [
        ("A", {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}),
        ("B", {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1}),
        ("C", {"mcs": 0, "length": 10, "ldpc": 1}),
    ]
    passed = 0
    for name, kwargs in cases:
        info = synth_and_decode_layer1(name, **kwargs)
        if info.get("crc_ok") and info.get("bit_match"):
            print(f"[PASS] Layer1/{name}: CRC OK, metric={info.get('metric')}, "
                  f"mcs={info['got_mcs']}, length={info['got_length']}, "
                  f"agg={info['got_agg']}, sgi={info['got_sgi']}, ldpc={info['got_ldpc']}")
            passed += 1
        else:
            print(f"[FAIL] Layer1/{name}: {info}")
    assert passed == 3, f"Layer 1: expected 3/3 PASS, got {passed}/3"
    print(f"[PASS] Layer 1 clean: {passed}/3")
```

- [ ] **Step 6.2: Wire Layer 1 into main()**

Replace the `if __name__ == "__main__":` block with:

```python
if __name__ == "__main__":
    test_viterbi_encode_decode_roundtrip()
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    test_layer1_clean()
    print("\nPhase 37 Layer 1 (clean) tests passed.")
```

- [ ] **Step 6.3: Run, expect Layer 1 to PASS 3/3**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected:
```
[PASS] test_viterbi_encode_decode_roundtrip: metric=0, len=24, all-match=True
[PASS] test_make_known_htsig_bits_case_a: ...
[PASS] test_make_known_htsig_bits_case_b: ...
[PASS] test_make_known_htsig_bits_case_c: ...
[PASS] Layer1/A: CRC OK, metric=0, mcs=0, length=100, agg=0, sgi=0, ldpc=0
[PASS] Layer1/B: CRC OK, metric=0, mcs=7, length=1000, agg=1, sgi=1, ldpc=0
[PASS] Layer1/C: CRC OK, metric=0, mcs=0, length=10, agg=0, sgi=0, ldpc=1
[PASS] Layer 1 clean: 3/3

Phase 37 Layer 1 (clean) tests passed.
```

If any case fails: the viterbi, encoder, interleaver, or QBPSK modulator has a bug. Diagnose by printing `dec48` vs `bits48_tx` and `info`.

- [ ] **Step 6.4: Commit Layer 1 baseline**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T4): Layer 1 clean (3/3 PASS) — HT-SIG viterbi decoder is correct on ideal input"
```

---

## Task 7: Add Layer 2 (+CFO sweep) — quantify phase drift tolerance

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 7.1: Append CFO impairment helper + Layer 2 driver**

```python


# ============================================================
# Carrier Frequency Offset (CFO) impairment.
# Apply in time-domain: each SC sees a phase rotation exp(j*2π*f_cfo*t/N)
# where t is the OFDM symbol time. We approximate by multiplying each SC
# by exp(j*2π*f_cfo*counter*Ts/N_fft) where Ts = 4 µs (one OFDM symbol),
# N_fft = 64, and counter is 0 for HT-SIG0, 1 for HT-SIG1.
# ============================================================
def apply_cfo_per_sc(sc52, cfo_hz, sample_rate=20e6):
    """Apply CFO as a per-SC phase rotation. counter is implicit in sc52's
    context (HT-SIG0 or HT-SIG1). For simplicity here, counter=0 (HT-SIG0)
    or counter=1 (HT-SIG1)."""
    n_fft = 64
    ts = 1.0 / sample_rate  # 50 ns
    phase_per_sc = 2 * np.pi * cfo_hz * K_SC_INDEX_52.astype(np.float64) * ts
    return sc52 * np.exp(1j * phase_per_sc).astype(np.complex64)


def synth_and_decode_with_cfo(case_name, cfo_hz, **case_kwargs):
    """Same as Layer 1 but inject CFO between HT-SIG0 (counter=0) and
    HT-SIG1 (counter=1). Counter index is approximated as 'time since
    start of HT-SIG'. For Phase 37 we treat HT-SIG0 and HT-SIG1 as having
    4 µs separation and CFO as Hz offset."""
    bits48_tx = make_known_htsig_bits(**case_kwargs)
    bits0 = bits48_tx[0:24]
    bits1 = bits48_tx[24:48]
    coded0 = bcc_encode_24(bits0)
    coded1 = bcc_encode_24(bits1)
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)
    # Apply CFO: HT-SIG0 sees counter=0 phase rotation, HT-SIG1 sees counter=1.
    # For Phase 37 simplicity, we treat the CFO as a SC-dependent phase that
    # is the same for both symbols (so it's the same as a static phase ramp).
    # This is the worst case for the viterbi (a coherent rotation).
    # If we wanted to model time-varying CFO, we'd multiply HT-SIG1 by an
    # additional exp(j*2π*cfo_hz*4e-6) factor.
    if cfo_hz != 0:
        sc52_0 = apply_cfo_per_sc(sc52_0, cfo_hz)
        sc52_1 = apply_cfo_per_sc(sc52_1, cfo_hz)
        # Add time-variation: HT-SIG1 is 4 µs after HT-SIG0
        additional_phase = 2 * np.pi * cfo_hz * 4e-6
        sc52_1 = sc52_1 * np.exp(1j * additional_phase).astype(np.complex64)
    eq48_a = sc52_0[0:48]
    eq48_b = sc52_1[0:48]
    dec48, info = decode_htsig_attempt(eq48_a, eq48_b)
    info["case"] = case_name
    info["cfo_hz"] = cfo_hz
    info["expected_mcs"] = case_kwargs.get("mcs", 0)
    info["expected_length"] = case_kwargs.get("length", 100)
    if dec48 is not None:
        info["bit_match"] = bool(np.array_equal(dec48, bits48_tx))
    return info


def test_layer2_cfo():
    """Layer 2: +CFO sweep. Expect 3/3 PASS at CFO <= 1 kHz."""
    cases = [
        ("A", {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}),
        ("B", {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1}),
        ("C", {"mcs": 0, "length": 10, "ldpc": 1}),
    ]
    cfo_values = [0, 100, 500, 1000, 5000]
    results = {}  # {cfo: passed_count}
    for cfo in cfo_values:
        passed = 0
        for name, kwargs in cases:
            info = synth_and_decode_with_cfo(name, cfo, **kwargs)
            if info.get("crc_ok") and info.get("bit_match"):
                passed += 1
        results[cfo] = passed
        print(f"[INFO] Layer2/CFO={cfo}Hz: {passed}/3")
    # Pass criterion: 3/3 PASS at CFO <= 1 kHz
    assert results[0] == 3, f"Layer 2 CFO=0Hz must be 3/3, got {results[0]}/3"
    assert results[100] == 3, f"Layer 2 CFO=100Hz must be 3/3, got {results[100]}/3"
    assert results[500] == 3, f"Layer 2 CFO=500Hz must be 3/3, got {results[500]}/3"
    assert results[1000] == 3, f"Layer 2 CFO=1000Hz must be 3/3, got {results[1000]}/3"
    print(f"[PASS] Layer 2 +CFO: 3/3 PASS at CFO <= 1 kHz. "
          f"5kHz result: {results[5000]}/3")
```

- [ ] **Step 7.2: Wire Layer 2 into main()**

```python
if __name__ == "__main__":
    test_viterbi_encode_decode_roundtrip()
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    test_layer1_clean()
    test_layer2_cfo()
    print("\nPhase 37 Layer 1+2 tests passed.")
```

- [ ] **Step 7.3: Run, expect Layer 2 PASS at low CFO**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected:
```
[PASS] Layer 1 clean: 3/3
[INFO] Layer2/CFO=0Hz: 3/3
[INFO] Layer2/CFO=100Hz: 3/3
[INFO] Layer2/CFO=500Hz: 3/3
[INFO] Layer2/CFO=1000Hz: 3/3
[INFO] Layer2/CFO=5000Hz: X/3
[PASS] Layer 2 +CFO: 3/3 PASS at CFO <= 1 kHz.
```

If 1000 Hz fails: this is informative (viterbi needs some phase tracking). Record the actual threshold.

- [ ] **Step 7.4: Commit**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T5): Layer 2 +CFO sweep — quantify phase drift tolerance"
```

---

## Task 8: Add Layer 3 (+AWGN sweep) — quantify noise tolerance

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

- [ ] **Step 8.1: Append AWGN helper + Layer 3 driver**

```python


# ============================================================
# Additive White Gaussian Noise (AWGN) impairment.
# Set noise variance to achieve target SNR. SNR per SC is computed as
# signal_power / noise_power where signal_power = mean(|sym|^2) and
# noise_power is chosen so 10*log10(sig/noise) = snr_db.
# ============================================================
def apply_awgn(sc52, snr_db, rng):
    """Add complex Gaussian noise at given SNR (dB)."""
    sig_power = np.mean(np.abs(sc52) ** 2)
    noise_power = sig_power / (10 ** (snr_db / 10))
    noise = np.sqrt(noise_power / 2) * (rng.standard_normal(sc52.shape) +
                                        1j * rng.standard_normal(sc52.shape))
    return (sc52 + noise).astype(np.complex64)


def synth_and_decode_with_awgn(case_name, snr_db, **case_kwargs):
    """Same as Layer 1 but add AWGN at given SNR."""
    bits48_tx = make_known_htsig_bits(**case_kwargs)
    bits0 = bits48_tx[0:24]
    bits1 = bits48_tx[24:48]
    coded0 = bcc_encode_24(bits0)
    coded1 = bcc_encode_24(bits1)
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)
    if snr_db < np.inf:
        rng = np.random.default_rng(seed=hash((case_name, snr_db)) & 0xFFFF)
        sc52_0 = apply_awgn(sc52_0, snr_db, rng)
        sc52_1 = apply_awgn(sc52_1, snr_db, rng)
    eq48_a = sc52_0[0:48]
    eq48_b = sc52_1[0:48]
    dec48, info = decode_htsig_attempt(eq48_a, eq48_b)
    info["case"] = case_name
    info["snr_db"] = snr_db
    if dec48 is not None:
        info["bit_match"] = bool(np.array_equal(dec48, bits48_tx))
    return info


def test_layer3_awgn():
    """Layer 3: +AWGN sweep. Expect 3/3 PASS at SNR >= 10 dB."""
    cases = [
        ("A", {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}),
        ("B", {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1}),
        ("C", {"mcs": 0, "length": 10, "ldpc": 1}),
    ]
    snr_values = [20, 15, 12, 9, 6]
    results = {}  # {snr: passed_count}
    for snr in snr_values:
        passed = 0
        for name, kwargs in cases:
            info = synth_and_decode_with_awgn(name, snr, **kwargs)
            if info.get("crc_ok") and info.get("bit_match"):
                passed += 1
        results[snr] = passed
        print(f"[INFO] Layer3/SNR={snr}dB: {passed}/3")
    # Pass criterion: 3/3 PASS at SNR >= 10 dB
    assert results[20] == 3, f"Layer 3 SNR=20dB must be 3/3, got {results[20]}/3"
    assert results[15] == 3, f"Layer 3 SNR=15dB must be 3/3, got {results[15]}/3"
    assert results[12] == 3, f"Layer 3 SNR=12dB must be 3/3, got {results[12]}/3"
    print(f"[PASS] Layer 3 +AWGN: 3/3 PASS at SNR >= 12 dB. "
          f"9dB: {results[9]}/3, 6dB: {results[6]}/3")
```

- [ ] **Step 8.2: Wire Layer 3 into main()**

```python
if __name__ == "__main__":
    test_viterbi_encode_decode_roundtrip()
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    test_layer1_clean()
    test_layer2_cfo()
    test_layer3_awgn()
    print("\nPhase 37 Layer 1+2+3 tests passed.")
```

- [ ] **Step 8.3: Run, expect Layer 3 PASS at high SNR**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

Expected:
```
[PASS] Layer 1 clean: 3/3
[PASS] Layer 2 +CFO: 3/3 PASS at CFO <= 1 kHz. 5kHz result: X/3
[INFO] Layer3/SNR=20dB: 3/3
[INFO] Layer3/SNR=15dB: 3/3
[INFO] Layer3/SNR=12dB: 3/3
[INFO] Layer3/SNR=9dB: X/3
[INFO] Layer3/SNR=6dB: X/3
[PASS] Layer 3 +AWGN: 3/3 PASS at SNR >= 12 dB. 9dB: X/3, 6dB: X/3
```

If 12 dB fails: hard-decision viterbi is too aggressive; would need soft-decision LLR. Record actual threshold.

- [ ] **Step 8.4: Commit**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T6): Layer 3 +AWGN sweep — quantify noise tolerance"
```

---

## Task 9: (Optional) Add Layer 4 (+SFO sweep) — quantify per-symbol drift tolerance

**Files:**
- Modify: `examples/test_htsig_viterbi_synthetic.py`

**Skip this task if budget is tight. SFO has been REFUTED multiple times already (Phase 24-26), so this layer is informational only.**

- [ ] **Step 9.1: Append SFO helper + Layer 4 driver**

```python


# ============================================================
# Sampling Frequency Offset (SFO) impairment.
# Apply as a SC-dependent phase rotation that grows with symbol index.
# For HT-SIG0 (symbol 0): phase = 2π*sfo_ppm*K_SC_INDEX52*counter / 1e6
# For HT-SIG1 (symbol 1, 4 µs later): same with counter incremented.
# We model SFO as a per-SC phase slope of (2π * sfo_ppm * 1e-6 * sc * counter).
# ============================================================
def apply_sfo_per_sc(sc52, sfo_ppm, counter):
    """Apply SFO as a per-SC phase rotation. counter is the OFDM symbol
    index from the start of HT-SIG (0 for HT-SIG0, 1 for HT-SIG1)."""
    phase_per_sc = (2 * np.pi * sfo_ppm * 1e-6 *
                    K_SC_INDEX_52.astype(np.float64) * counter)
    return sc52 * np.exp(1j * phase_per_sc).astype(np.complex64)


def synth_and_decode_with_sfo(case_name, sfo_ppm, **case_kwargs):
    """Same as Layer 1 but inject SFO."""
    bits48_tx = make_known_htsig_bits(**case_kwargs)
    bits0 = bits48_tx[0:24]
    bits1 = bits48_tx[24:48]
    coded0 = bcc_encode_24(bits0)
    coded1 = bcc_encode_24(bits1)
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)
    if sfo_ppm != 0:
        sc52_0 = apply_sfo_per_sc(sc52_0, sfo_ppm, counter=0)
        sc52_1 = apply_sfo_per_sc(sc52_1, sfo_ppm, counter=1)
    eq48_a = sc52_0[0:48]
    eq48_b = sc52_1[0:48]
    dec48, info = decode_htsig_attempt(eq48_a, eq48_b)
    info["case"] = case_name
    info["sfo_ppm"] = sfo_ppm
    if dec48 is not None:
        info["bit_match"] = bool(np.array_equal(dec48, bits48_tx))
    return info


def test_layer4_sfo():
    """Layer 4: +SFO sweep. Expect 3/3 PASS at SFO <= 50 ppm."""
    cases = [
        ("A", {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}),
        ("B", {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1}),
        ("C", {"mcs": 0, "length": 10, "ldpc": 1}),
    ]
    sfo_values = [0, 10, 50, 100]
    results = {}
    for sfo in sfo_values:
        passed = 0
        for name, kwargs in cases:
            info = synth_and_decode_with_sfo(name, sfo, **kwargs)
            if info.get("crc_ok") and info.get("bit_match"):
                passed += 1
        results[sfo] = passed
        print(f"[INFO] Layer4/SFO={sfo}ppm: {passed}/3")
    assert results[0] == 3, f"Layer 4 SFO=0ppm must be 3/3"
    assert results[10] == 3, f"Layer 4 SFO=10ppm must be 3/3"
    print(f"[PASS] Layer 4 +SFO: 3/3 PASS at SFO <= 10 ppm. "
          f"50ppm: {results[50]}/3, 100ppm: {results[100]}/3")
```

- [ ] **Step 9.2: Wire Layer 4 into main()**

```python
if __name__ == "__main__":
    test_viterbi_encode_decode_roundtrip()
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    test_layer1_clean()
    test_layer2_cfo()
    test_layer3_awgn()
    test_layer4_sfo()  # Optional
    print("\nPhase 37 Layer 1+2+3+4 tests passed.")
```

- [ ] **Step 9.3: Run, expect Layer 4 PASS at low SFO**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

- [ ] **Step 9.4: Commit (if Layer 4 was done)**

```bash
git add examples/test_htsig_viterbi_synthetic.py
git commit -m "feat(phase37/T7): Layer 4 +SFO sweep — quantify per-symbol drift tolerance (optional)"
```

---

## Task 10: Run all layers + record results

**Files:**
- Read: `examples/test_htsig_viterbi_synthetic.py` (no modifications)

- [ ] **Step 10.1: Run full test harness, capture output**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py \
  2>&1 | tee /tmp/p37_harness.log
```

- [ ] **Step 10.2: Verify all enabled layers PASS their success criteria**

Expected aggregate output (if all layers implemented):
```
[PASS] Layer 1 clean: 3/3
[PASS] Layer 2 +CFO: 3/3 PASS at CFO <= 1 kHz. 5kHz result: X/3
[PASS] Layer 3 +AWGN: 3/3 PASS at SNR >= 12 dB. 9dB: X/3, 6dB: X/3
[PASS] Layer 4 +SFO: 3/3 PASS at SFO <= 10 ppm. 50ppm: X/3, 100ppm: X/3
```

If any layer fails its success criterion, document which threshold was actually needed.

- [ ] **Step 10.3: Verify loopback regression still passes (no C++ changes were made)**

```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py
```

Expected: `Final: OK=3 FAIL=0`.

- [ ] **Step 10.4: Save results for verdict doc**

Copy `/tmp/p37_harness.log` to `docs/superpowers/notes/2026-06-24-phase37-harness-output.log` (for reference in verdict):

```bash
mkdir -p docs/superpowers/notes
cp /tmp/p37_harness.log docs/superpowers/notes/2026-06-24-phase37-harness-output.log
git add docs/superpowers/notes/2026-06-24-phase37-harness-output.log
git commit -m "notes(phase37/T8): save harness output for verdict reference"
```

---

## Task 11: Write verdict document

**Files:**
- Create: `docs/superpowers/notes/2026-06-24-phase37-verdict.md`

- [ ] **Step 11.1: Determine the verdict from harness output**

The verdict depends on which layers passed/failed:

| Outcome | Verdict | Next step |
|---|---|---|
| Layer 1 fails (CRC not OK on clean input) | **Decoder has a bug** | Fix viterbi/CRC/deinterleaver in `lib/frame_equalizer_impl.cc` (Phase 38) |
| All layers PASS | **Decoder is correct** | Equalizer is the bottleneck. Revert to upstream investigation. |
| Layer 1 passes, Layer 2 fails at CFO < 1 kHz | **Viterbi too sensitive to phase drift** | Add CFO tracking in equalizer |
| Layer 1 passes, Layer 3 fails at SNR < 10 dB | **Viterbi needs soft info** | Add LLR-based soft-decision viterbi |
| Layer 1+2+3 pass, Layer 4 fails | **Viterbi too sensitive to per-symbol drift** | Add per-symbol CPE before HT-SIG1 (already REFUTED in Phase 36) |

- [ ] **Step 11.2: Write the verdict doc**

```markdown
# Phase 37 Verdict — HT-SIG Viterbi Synthetic Tolerance Test

**Date:** 2026-06-24
**Status:** [PASS / FAIL / TOLERANCE_LIMIT]
**Test:** `examples/test_htsig_viterbi_synthetic.py`

## Layer Results

| Layer | Impairment | Range | Result | Verdict |
|---|---|---|---|---|
| 1 | Clean | n/a | 3/3 PASS | Decoder correct on ideal input |
| 2 | +CFO | 0, 100, 500, 1000, 5000 Hz | 3/3 at ≤N Hz | [Tolerates X kHz] |
| 3 | +AWGN | 20, 15, 12, 9, 6 dB | 3/3 at ≥N dB | [Tolerates X dB SNR] |
| 4 (optional) | +SFO | 0, 10, 50, 100 ppm | 3/3 at ≤N ppm | [Tolerates X ppm] |

## Detailed Output

[FULL OUTPUT FROM /tmp/p37_harness.log]

## Verdict

[One of:]
- **Decoder bug**: Layer 1 fails. Fix path: investigate viterbi/CRC/deinterleaver.
- **Equalizer bottleneck**: All layers pass. Decoder is correct. The USRP HT-SIG
  failure is NOT a viterbi issue — return to upstream equalizer investigation.
- **Tolerance limit**: Layer 2/3/4 fails at USRP-relevant thresholds. Add
  CFO tracking / soft-decision LLR / per-symbol CPE.

## Recommendation

[Specific next step based on verdict]

## Files

- Test: `examples/test_htsig_viterbi_synthetic.py`
- Harness output: `docs/superpowers/notes/2026-06-24-phase37-harness-output.log`
- Spec: `docs/superpowers/specs/2026-06-24-phase37-htsig-viterbi-synthetic-design.md`
- Plan: `docs/superpowers/plans/2026-06-24-phase37-htsig-viterbi-synthetic.md`
```

Fill in the actual values from the harness output.

- [ ] **Step 11.3: Commit verdict doc**

```bash
git add docs/superpowers/notes/2026-06-24-phase37-verdict.md
git commit -m "notes(phase37): verdict — [PASS / FAIL / TOLERANCE_LIMIT]"
```

---

## Task 12: Write memory file + update MEMORY.md

**Files:**
- Create: `memory/project_p37_htsig_viterbi_synthetic.md`
- Modify: `MEMORY.md`

- [ ] **Step 12.1: Write memory file**

```markdown
# Phase 37 HT-SIG Viterbi Synthetic Tolerance Test (2026-06-24)

**Status:** [PASS / FAIL / TOLERANCE_LIMIT]
**Test:** `examples/test_htsig_viterbi_synthetic.py`

## Goal

Determine if HT-SIG viterbi has a bug independent of equalizer.
Quantify tolerance to controlled impairments (clean, CFO, AWGN, SFO).

## Method

Re-implement full HT-SIG TX→RX pipeline in NumPy (BCC encode, Table 18-6
interleave, QBPSK modulate, pilot insertion, impairments, viterbi decode,
CRC8 check). No Python binding — mirrors `test_lsig_viterbi_synthetic.py`.

## Results

| Layer | Result |
|---|---|
| 1 (clean) | 3/3 PASS |
| 2 (+CFO) | 3/3 PASS at CFO <= X Hz |
| 3 (+AWGN) | 3/3 PASS at SNR >= X dB |
| 4 (+SFO, optional) | 3/3 PASS at SFO <= X ppm |

## Implications

- [If all PASS]: Decoder is correct. Equalizer is the bottleneck.
  Phase 36 conclusion stands. Next: investigate what impairment the
  equalizer is NOT removing that viterbi needs.
- [If Layer 1 fails]: Decoder bug. Fix in Phase 38.
- [If Layer 2/3/4 fails at threshold]: Tolerance limit. Add CFO tracking
  / soft LLR / per-symbol CPE before next phase.

## Files

- Test: `examples/test_htsig_viterbi_synthetic.py`
- Verdict: `docs/superpowers/notes/2026-06-24-phase37-verdict.md`
- Harness output: `docs/superpowers/notes/2026-06-24-phase37-harness-output.log`
- Spec: `docs/superpowers/specs/2026-06-24-phase37-htsig-viterbi-synthetic-design.md`
- Plan: `docs/superpowers/plans/2026-06-24-phase37-htsig-viterbi-synthetic.md`

## Test command

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_htsig_viterbi_synthetic.py
```

## Related memory

- [[project-p36-persc-fit-refuted]] — Phase 36 wall (per-SC fit REFUTED)
- [[project-p35-htsig-fix]] — Phase 35 partial (per-symbol MEAN REFUTED)
- [[project-p34-delta-correction]] — Phase 34 success (L-SIG unblocked)
- [[project-p33-lltf0-14sample-shift-fix]] — Phase 33 L-LTF0 root cause
- [[project-p19-htsig-viterbi]] — Phase 19 HT-SIG bottleneck
- [[project-p18-lsig-viterbi-analysis]] — Phase 18 L-SIG unblock (synthetic pattern source)
```

- [ ] **Step 12.2: Update MEMORY.md index**

Append a new line at the end of the index list in `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md` (NOT the global one):

```
- [Phase 37 HT-SIG Viterbi Synthetic](project_p37_htsig_viterbi_synthetic.md) — **2026-06-24** — HT-SIG viterbi decoder correctness test (3/3 PASS = decoder OK, equalizer is bottleneck; FAIL = Phase 38 fix).
```

- [ ] **Step 12.3: Update global MEMORY.md test command section**

Add to the "## 运行测试" section in `/home/hy/.claude/CLAUDE.md` (or the relevant instructions file):

```
- HT-SIG viterbi synthetic: `python examples/test_htsig_viterbi_synthetic.py` (3/3 PASS per layer)
```

- [ ] **Step 12.4: Commit memory updates**

```bash
git add memory/project_p37_htsig_viterbi_synthetic.md
git commit -m "memory(phase37): HT-SIG viterbi synthetic tolerance test results"
```

(The MEMORY.md index update is in the auto-memory directory and updates automatically.)

---

## Critical Files (reference)

- `lib/frame_equalizer_impl.cc:248-263` — `kHtPilotPolarity127[127]` and pilot polarity formula
- `lib/frame_equalizer_impl.cc:975-995` — `deinterleave_bpsk_48` (L-SIG, for reference; HT-SIG uses different formula)
- `lib/frame_equalizer_impl.cc:997-1082` — `viterbi_decode_133_171` (target for re-implementation)
- `lib/frame_equalizer_impl.cc:1085-1131` — `ht_sig_crc8_calc` (CRC8 polynomial reference)
- `lib/frame_equalizer_impl.cc:2008-2398` — `decode_htsig_from_rotated` (target decoder, mirrored in NumPy)
- `lib/frame_equalizer_impl.cc:2155-2171` — HT-SIG deinterleave formula `j = 3*(k%16) + k/16`
- `lib/frame_equalizer_impl.cc:2217-2253` — HT-SIG bit field positions
- `test_lsig_viterbi_synthetic.py` — template for re-implementation pattern

## Active Conventions

- **Loopback regression** must still pass: `test_direct_loopback.py` → `Final: OK=1 FAIL=0`.
- **No C++ changes** in this phase. Pure NumPy test, mirroring `test_lsig_viterbi_synthetic.py`.
- **make install** not required (no C++ builds).
- **Layer pass criteria** (from spec):
  - Layer 1: 3/3 PASS, BER=0
  - Layer 2: 3/3 PASS at CFO <= 1 kHz
  - Layer 3: 3/3 PASS at SNR >= 10 dB
  - Layer 4: 3/3 PASS at SFO <= 50 ppm (optional)

## Definition of Done

- [ ] `examples/test_htsig_viterbi_synthetic.py` exists, runs in <30s
- [ ] Layer 1 (clean): 3/3 cases CRC OK, BER = 0
- [ ] Layer 2 (+CFO): 3/3 cases CRC OK at CFO <= 1 kHz
- [ ] Layer 3 (+AWGN): 3/3 cases CRC OK at SNR >= 10 dB
- [ ] Layer 4 (+SFO): either DONE or DEFERRED with note
- [ ] Verdict file written to `docs/superpowers/notes/2026-06-24-phase37-verdict.md`
- [ ] Memory file written to `memory/project_p37_htsig_viterbi_synthetic.md`
- [ ] `MEMORY.md` updated with new test command and finding
- [ ] Loopback regression still passes