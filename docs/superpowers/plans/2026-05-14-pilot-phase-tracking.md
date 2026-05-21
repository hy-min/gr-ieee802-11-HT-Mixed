# Pilot Phase Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable CPE (Common Phase Error) estimation in frame_equalizer to compensate for residual CFO, fixing the phase rotation issue in L-SIG (73°) and HT-SIG1 (-104°).

**Architecture:** The `estimate_header_cpe_rad()` function already exists and computes pilot-based phase error. It was previously disabled for debugging. Re-enabling it applies `exp(-j*cpe)` rotation to all equalized subcarriers, correcting phase drift from residual CFO.

**Tech Stack:** C++ (GNU Radio block), ieee802-11 OOT module

---

## Files

- Modify: `lib/frame_equalizer_impl.cc:733-734` — Enable CPE estimation

---

## Task 1: Enable CPE Estimation for Header Symbols

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:733-734`

- [ ] **Step 1: Read current CPE bypass code**

Find this code at line 733:
```cpp
const float cpe = 0.0f;  // DEBUG: bypass CPE to test raw symbol
//const float cpe = estimate_header_cpe_rad(rx52, H52, is_ht_sig);
```

- [ ] **Step 2: Enable CPE estimation**

Replace with:
```cpp
//const float cpe = 0.0f;  // DEBUG: bypass CPE to test raw symbol
const float cpe = estimate_header_cpe_rad(rx52, H52, is_ht_sig);
```

- [ ] **Step 3: Build and verify compilation**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 && make -j4 2>&1 | tail -5
```

Expected: `[100%] Built target ieee802_11_python`

---

## Task 2: Verify CPE Estimation Works

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Run test and check CPE output**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "CPE|Parity check|LSIG_DECODE" | head -20
```

Expected output should show:
```
[EQ_HEADER] CPE estimate: NON-ZERO_VALUE rad, rot=...
```

Where `NON-ZERO_VALUE` is approximately the phase error (e.g., -1.2 rad ≈ -69°).

- [ ] **Step 2: Check L-SIG parity**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "Parity check" | head -5
```

Expected: `Parity check passed` (or no "Parity check failed" message)

---

## Task 3: Verify Phase Correction Across All Header Symbols

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: Check L-SIG and HT-SIG phases**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "EQ_FULL|type=2|type=4" | head -20
```

Expected: Equalized symbols should have phases near 0° or 180° (BPSK), not random angles like 73° or -104°.

- [ ] **Step 2: Check end-to-end decode**

Run:
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "HT-SIG|Decode" | head -10
```

Expected: HT-SIG parse should succeed (have_ht=1).

---

## Task 4: Commit Changes

**Files:**
- Modify: `lib/frame_equalizer_impl.cc`

- [ ] **Step 1: Review changes**

Run:
```bash
cd /home/hy/gr-ieee802-11 && git diff lib/frame_equalizer_impl.cc | head -30
```

- [ ] **Step 2: Stage and commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix(frame_equalizer): enable CPE estimation for residual CFO correction

Root cause: sync_long's fine CFO correction was disabled, causing
phase rotation that accumulated over time (L-SIG: 73°, HT-SIG1: -104°).

The estimate_header_cpe_rad() function already existed and correctly
computes pilot-based phase error from 4 pilot subcarriers (SC -21,-7,+7,+21).
It was previously bypassed (cpe=0) for debugging.

Fix: Enable CPE estimation so equalized symbols are rotated by exp(-j*cpe)
to compensate for residual CFO.

Changes:
- Enable estimate_header_cpe_rad() in equalize_header52_to_eq48_and_bits()
- L-SIG and HT-SIG phases should now be near 0°/180° (BPSK)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Verification Checklist

- [ ] Build succeeds without warnings
- [ ] `[EQ_HEADER] CPE estimate` shows non-zero phase error
- [ ] L-SIG parity check passes (no "Parity check failed")
- [ ] Equalized L-SIG/HT-SIG phases are near 0°/180° (BPSK) or ±45°/±135° (QBPSK)
- [ ] HT-SIG parse succeeds
- [ ] End-to-end MCS0 test completes without NAK or errors

---

## Appendix: How CPE Estimation Works

```
1. Extract 4 pilot subcarriers from FFT output:
   pilots[i] = rx52[48 + i]  // indices 48, 49, 50, 51

2. Equalize pilots (remove channel effect):
   pilot_eq[i] = rx52[48 + i] / H52[48 + i]

3. Compute expected pilot (known from standard):
   L-SIG: {1, 1, 1, -1} (real)
   HT-SIG: {j, j, j, -j} (imaginary, due to QBPSK rotation)

4. Accumulate phase error:
   acc += pilot_eq[i] * conj(expected[i])
   cpe = arg(acc)

5. Apply rotation to all subcarriers:
   eq[j] = rx[j] / H[j] * exp(-j * cpe)
```
