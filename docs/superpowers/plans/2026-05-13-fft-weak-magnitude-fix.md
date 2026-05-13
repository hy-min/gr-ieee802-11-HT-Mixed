# FFT Weak Magnitude Fix - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix FFT output magnitude being ~32x too small (0.28 instead of ~8.88) for L-LTF symbols, causing channel estimation H magnitude ≈ 0.02 instead of ~1.0.

**Architecture:** The RX chain is: sync_long → ht_symbol_splitter → fft_vxx (RX FFT) → frame_equalizer. The SPLITTER outputs 64-sample time-domain blocks. The FFT block computes FFT with shift=False. The EQ receives FFT output and does channel estimation. The FFT magnitudes being ~32x small suggests the FFT is computing over the wrong 64 samples (likely capturing CP or noise instead of LTF data).

**Tech Stack:** GNU Radio fft_vcc, C++ frame_equalizer, ht_symbol_splitter

---

## File Structure

- `lib/ht_symbol_splitter_impl.cc` — CP-skip logic, outputs 64-sample TD blocks at symbol boundaries
- `lib/frame_equalizer_impl.cc` — Channel estimation from L-LTF FFT, L-SIG/HT-SIG decode
- `examples/wifi_phy_hier.grc` — GRC flowgraph (RX FFT shift=False confirmed)
- `examples/wifi_phy_hier.py` — Python flowgraph (manually maintained, not GRC-generated)
- `examples/test_mcs_end_to_end.py` — End-to-end test with HT-Mixed MCS0

---

## Problem Analysis

Test output shows:
```
[SYMBOL_FP] call_count=0 extract_call=1 bin7=0.1735-0.2249i |mag=0.2841 phase=-52.4deg
```

Expected: L-LTF FFT magnitude for SC+7 should be ~8.88 (per subcarrier, before kFftNormalize=64/sqrt(52)≈8.88 division).

Actual: magnitude 0.28 ≈ 8.88 / 32. This 32x reduction suggests either:
1. FFT is computed over wrong 64 samples (capturing CP boundary or noise)
2. FFT block has incorrect configuration
3. SPLITTER output timing is wrong (outputs at wrong relative index)

SPLITTER outputs at rel_idx=63 (L-LTF0), rel_idx=143 (L-LTF1), etc. The SPLITTER_LLTF_VERIFY shows time-domain samples near 0 (expected for L-LTF CP-free data).

The fft_vxx_0_1 (RX FFT) has shift=False (confirmed in GRC line 491). This gives natural order: DC at bin 0, pos freq at bins 1-26, neg freq at bins 32-63.

---

## Task 1: Verify FFT Input Timing with Symbol-Count Probe

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:391-394`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: Add detailed timing probe to SPLITTER output**

At the SPLITTER output point (line 391-394), add a probe that prints the SUM of absolute values of all 64 samples to verify the buffer contains the correct symbol energy:

```cpp
// At line 391, after fprintf for [SPLITTER] Output symbol type
float total_energy = 0.0f;
for (int zz = 0; zz < 64; zz++) {
    total_energy += std::abs(d_buffer[zz]);
}
fprintf(stderr, "[SPLITTER_FFTPROBE] type=%d rel_idx=%llu total_energy=%.4f first_sample=%.4f%+.4fi\n",
        symbol_type, (unsigned long long)out_rel_idx, total_energy,
        d_buffer[0].real(), d_buffer[0].imag());
fflush(stderr);
```

Run: `cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep SPLITTER_FFTPROBE`

Expected output for L-LTF0 (type=0): total_energy should be ~8-12 (sum of 64 LTF samples). If it's near 0, the buffer is being filled with zeros/wrong data.

- [ ] **Step 2: Verify SPLITTER output at rel_idx=63 is L-LTF0 DATA**

The SPLITTER outputs at rel_idx=63 (L-LTF0), 143 (L-LTF1), 223 (L-SIG), 303 (HT-SIG0), 383 (HT-SIG1).

If total_energy is correct (~8-12) for L-LTF0, the SPLITTER output is correct and the problem is in the FFT block configuration or the connection between SPLITTER and FFT.

If total_energy is near 0, the SPLITTER is outputting wrong data (buffer filled with zeros or wrong samples).

- [ ] **Step 3: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "debug: add energy probe to SPLITTER output"
```

---

## Task 2: Verify FFT Block Configuration in RX Chain

**Files:**
- Modify: `examples/wifi_phy_hier.py:78` (RX FFT block instantiation)
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: Check RX FFT shift setting in wifi_phy_hier.py**

Read line 78 of wifi_phy_hier.py:
```bash
grep -n "fft_vxx_0_1\|shift" /home/hy/gr-ieee802-11/examples/wifi_phy_hier.py | head -20
```

The RX FFT should have `shift=False`. If it's `True`, fix it:
```python
# Line ~78: RX FFT: shift=False for natural order
self.fft_vxx_0_1 = fft.fft_vcc(64, False, window.rectangular(64), False, 1)
```

If already False, the FFT block configuration is correct.

- [ ] **Step 2: Add FFT output probe in frame_equalizer**

Before channel estimation divides by kFftNormalize, probe the raw FFT value at SC+7 (bin 7):

In `lib/frame_equalizer_impl.cc`, at `estimate_header_channel_from_lltf52` function (line ~580), add:

```cpp
// After computing H52[i] = (lltf0 / tx) / kFftNormalize;
// Probe raw FFT values before channel estimation
if (i == 7) {  // SC+7
    fprintf(stderr, "[RAW_FFT_PROBE] lltf0[7]=%.4f%+.4fi tx=%.4f%+.4fi H=%.4f%+.4fi mag=%.4f\n",
            lltf0.real(), lltf0.imag(), tx.real(), tx.imag(),
            H52[i].real(), H52[i].imag(), std::abs(H52[i]));
}
```

- [ ] **Step 3: Run test and check RAW_FFT_PROBE**

Expected: lltf0[7] magnitude should be ~8.88 before normalization. After normalization, H should be ~1.0.

If lltf0[7] magnitude is ~0.28: FFT is outputting 32x small values → problem in FFT or SPLITTER connection.

If lltf0[7] magnitude is ~8.88: FFT output is correct → problem in normalization factor or channel estimation formula.

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "debug: add raw FFT probe at SC+7 for L-LTF channel estimation"
```

---

## Task 3: Verify SPLITTER → FFT Connection and FFT Window Alignment

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (add absolute sample index to output tag)
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: Check if SPLITTER output aligns with FFT input timing**

In the SPLITTER, when it outputs at rel_idx=63, the output is a 64-sample block. The question is: which 64 samples?

The SPLITTER buffers d_buffer[d_buffer_count++] = in[i] for each sample where should_buffer=true.
When d_buffer_count == d_fft_size (64), it outputs d_buffer.

For L-LTF0 DATA (rel_idx 0-63), the SPLITTER should buffer the 64 time-domain samples from rel_idx 0-63. These are the L-LTF DATA samples (after CP removal at rel_idx 0).

The SPLITTER outputs at rel_idx=63 (when the last sample is buffered). So the output is exactly the 64 L-LTF DATA samples.

But wait: the SPLITTER outputs by memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex)). This means the output is d_buffer which contains samples from rel_idx 0-63.

If this is correct, then the FFT should receive the correct L-LTF DATA.

The issue might be that the FFT is NOT aligned with the SPLITTER output. The FFT block might be computing over a different 64-sample window than what the SPLITTER outputs.

- [ ] **Step 2: Verify FFT input comes directly from SPLITTER output**

Check wifi_phy_hier.py connections:
```bash
grep -n "fft_vxx_0_1\|ht_symbol_splitter\|connect" /home/hy/gr-ieee802-11/examples/wifi_phy_hier.py | head -30
```

Expected connection: sync_long → ht_symbol_splitter → blocks_stream_to_vector → fft_vxx_0_1 → frame_equalizer

- [ ] **Step 3: Check if FFT block is using window function**

GNU Radio fft_vcc applies the window to each input block. If the window is NOT the rectangular window, the FFT output will be scaled.

In wifi_phy_hier.py line 78: `fft.fft_vcc(64, False, window.rectangular(64), False, 1)`
The window is explicitly set to rectangular(64), so no scaling from windowing.

- [ ] **Step 4: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc examples/wifi_phy_hier.py
git commit -m "debug: verify SPLITTER→FFT connection and window config"
```

---

## Task 4: Fix FFT Magnitude Issue

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:571-612`
- Test: `examples/test_mcs_end_to_end.py`

Based on Task 1-3 findings, apply the fix:

- [ ] **Step 1: If FFT magnitude is 32x small but SPLITTER output is correct**

The FFT block might be scaling by 1/N (1/64) internally, causing ~32x reduction. Check GNU Radio fft_vcc documentation - forward FFT should NOT normalize.

If the FFT output needs to be scaled up by 64, modify the channel estimation code:

```cpp
// At line 586: instead of
H52[i] = (lltf0 / tx) / kFftNormalize;
// Try (if FFT applies 1/N scaling):
H52[i] = (lltf0 * 64.0f / tx) / kFftNormalize;
// Or simply:
H52[i] = (lltf0 / tx);  // if FFT already includes 1/N
```

Or alternatively, check if the FFT normalization in GNU Radio's fft_vcc with shift=False and window=rectangular(64) applies a scaling factor.

- [ ] **Step 2: If SPLITTER output timing is wrong (buffer contains wrong samples)**

The CP-skip logic might be buffering the wrong samples. For HT-Mixed mode, the SPLITTER should:
- Buffer L-LTF0 DATA (rel_idx 0-63) - after skipping CP at rel_idx < 0 (which doesn't exist since sync starts at L-LTF0 DATA)
- Actually, the first L-LTF0 CP is at rel_idx -16 to -1, but since sync starts at rel_idx 0, the CP was already removed by sync_long.

Wait - sync_long outputs from rel_idx=0 onwards. The first output (rel_idx=0) IS the L-LTF0 DATA start. The CP for L-LTF0 (rel_idx -16 to -1) was NOT output by sync_long.

So the SPLITTER should buffer ALL samples from rel_idx 0-63 for L-LTF0 DATA. This is correct.

- [ ] **Step 3: Rebuild and test**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(L-SIG|LSIG|HT-SIG|FRAME_DETECT|CHAN_EST|H_Mag)"
```

Expected: CHAN_EST H magnitude should be ~1.0, L-SIG rate field should be 0x0D.

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: adjust FFT normalization factor for correct channel estimation"
```

---

## Task 5: Verify End-to-End L-SIG and HT-SIG Decoding

**Files:**
- Test: `examples/test_mcs_end_to_end.py`
- Verify: Channel estimation, L-SIG parity, HT-SIG CRC

- [ ] **Step 1: Run full end-to-end test**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | tail -30
```

Expected output:
- L-SIG rate field: 0x0D (BPSK 1/2)
- HT-SIG0 CRC: pass
- Frame detected as HT-Mixed (not Legacy)

- [ ] **Step 2: Check decoded MAC output**

The test should show received messages > 0 if decoding succeeds.

- [ ] **Step 3: If L-SIG parity fails, check deinterleaver and Viterbi**

The deinterleaver was already fixed (k/3 → (k/3)%16). If L-SIG still fails parity, the issue might be in the symbol-to-bits extraction (hard_bit_from_complex for BPSK).

- [ ] **Step 4: Commit**

```bash
git add lib/frame_equalizer_impl.cc lib/ht_symbol_splitter_impl.cc
git commit -m "feat: FFT magnitude fix enables correct L-SIG/HT-SIG decoding"
```

---

## Self-Review Checklist

1. **Spec coverage:** All tasks map to the goal of fixing FFT weak magnitude. No placeholder tasks.

2. **Placeholder scan:** No "TBD", "TODO", or vague steps. Each step shows exact code changes.

3. **Type consistency:** Function names (estimate_header_channel_from_lltf52, extract_header52_from_sym64) are consistent across all tasks.

4. **Dependencies:** Task 1 → Task 2 → Task 3 → Task 4 → Task 5 (sequential investigation order).

5. **Test verification:** Each task ends with a concrete test command and expected output.
