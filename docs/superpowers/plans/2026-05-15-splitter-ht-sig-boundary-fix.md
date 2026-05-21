# HT Symbol Splitter HT-SIG Boundary Fix - Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify that the SPLITTER correctly outputs all 6 preamble FFT symbols (LTF0, LTF1, L-SIG, HT-SIG0, HT-SIG1, HT-STF) and that HT-SIG decoding succeeds.

**Architecture:** The SPLITTER fix corrects HT-SIG/HT-STF boundary offsets (were 16 samples off) and adjusts starvation protection to only require 64 samples when starting a new symbol buffer instead of 80.

**Tech Stack:** GNU Radio 3.10, Python, C++, IEEE 802.11n HT-Mixed

---

## Verification Architecture

The test runs a loopback: TX generates HT-Mixed frames → wireless channel (simulated with AWGN) → RX processes through SPLITTER → FFT → Equalizer → MAC decoding.

**Expected SPLITTER output:**
```
type=0 rel_idx=63   → LTF0     (64 samples, FFT boundary)
type=0 rel_idx=143   → LTF1     (64 samples, FFT boundary)
type=2 rel_idx=223   → L-SIG    (64 samples, FFT boundary)
type=3 rel_idx=303   → HT-SIG0  (64 samples, FFT boundary) ← was 287 before fix
type=4 rel_idx=383   → HT-SIG1  (64 samples, FFT boundary) ← was 447 before fix
type=5 rel_idx=463   → HT-STF   (64 samples, FFT boundary)
```

**Success criteria:**
- SPLITTER outputs 6 FFT symbols (not 3)
- HT-SIG CRC passes
- L-SIG parity check passes
- Received message count > 0

---

## File Structure

**Modified files:**
- `lib/ht_symbol_splitter_impl.cc` - Fixed boundary offsets and starvation logic
- `examples/wifi_phy_hier.grc` - Not used (manual Python editing only)
- `wifi_phy_hier.py` - TX/RX hierarchical block

**Build:**
- Build directory: `/home/hy/gr-ieee802-11/build`
- Library: `build/lib/libgnuradio-ieee802_11.so`

---

## Task 1: Build the Project

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: Verify the source file is modified**

Run: `grep -n "rel_idx < 240\|rel_idx < 304\|rel_idx < 320\|rel_idx < 384" lib/ht_symbol_splitter_impl.cc`
Expected output:
```
...rel_idx < 240) {  // HT-SIG0 CP: 224-239
...rel_idx < 304) {  // HT-SIG0 DATA: 240-303
...rel_idx < 320) {  // HT-SIG1 CP: 304-319
...rel_idx < 384) {  // HT-SIG1 DATA: 320-383
```

- [ ] **Step 2: Build the project**

Run: `cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -20`
Expected: `[100%] Built target ieee802_11_python`

---

## Task 2: Create a Minimal Loopback Test

**Files:**
- Create: `/home/hy/gr-ieee802-11/examples/test_loopback_noqt.py`

- [ ] **Step 1: Create test script**

```python
#!/usr/bin/env python3
"""Minimal loopback test for HT-Mixed SPLITTER verification"""
import os
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

import numpy as np
from gnuradio import blocks, gr
import ieee802_11

def test_loopback():
    tb = gr.top_block()

    # TX: Generate HT-Mixed frames
    tx = ieee802_11.wifi_phy_hier(encoding=ieee802_11.BPSK_1_2)

    # Create source: dummy vector source for PSDU data
    # 13 bytes for MCS0 HT-Mixed header, 100 bytes dummy data
    src_data = [0x41] * (13 + 100)  # Some non-zero data
    src = blocks.vector_source_b(src_data, repeat=True)

    # RX: Process through SPLITTER
    rx = ieee802_11.wifi_phy_hier()

    # Message sinks to capture decoded output
    rx_sink = blocks.message_sink(gr.sizeof_gr_complex, blocks.null_sink(1).to_basic_block(), False)

    # Connect TX -> Channel (AWGN) -> RX
    # For minimal test: just connect TX to RX directly
    # In real test: add noise and channel effects
    head = blocks.head(gr.sizeof_gr_complex, 10000)  # Limit samples

    tb.connect(src, tx)
    tb.connect(tx, head)
    tb.connect(head, rx)

    print("Starting loopback test...")
    tb.start()
    import time
    time.sleep(2.0)
    tb.stop()
    tb.wait()

    print("Test complete")
    return True

if __name__ == '__main__':
    test_loopback()
```

- [ ] **Step 2: Run test script**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1`
Expected: See `[SPLITTER_FFTPROBE]` output for 6 symbols

---

## Task 3: Verify SPLITTER Outputs All 6 FFT Symbols

**Files:**
- Test: `lib/ht_symbol_splitter_impl.cc` (debug probe already present)

- [ ] **Step 1: Run loopback and capture SPLITTER debug output**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1 | grep SPLITTER_FFTPROBE`
Expected output:
```
SPLITTER_FFTPROBE type=0 rel_idx=63 ...   → LTF0
SPLITTER_FFTPROBE type=0 rel_idx=143 ...  → LTF1
SPLITTER_FFTPROBE type=2 rel_idx=223 ...  → L-SIG
SPLITTER_FFTPROBE type=3 rel_idx=303 ...  → HT-SIG0  ← should be 303, not 287
SPLITTER_FFTPROBE type=4 rel_idx=383 ...  → HT-SIG1  ← should be 383, not 447
SPLITTER_FFTPROBE type=5 rel_idx=463 ...  → HT-STF
```

- [ ] **Step 2: Verify no HT-SIG1 boundary at rel_idx=447**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1 | grep "rel_idx=447"`
Expected: No output (old bug should not appear)

- [ ] **Step 3: Verify no STARVATION triggered after L-SIG**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1 | grep SPLITTER_STARVATION`
Expected: Should NOT see starvation after L-SIG FFT (rel_idx > 223)

---

## Task 4: Verify HT-SIG Decoding Success

**Files:**
- Test: `lib/frame_equalizer_impl.cc` (HT-SIG decoding)

- [ ] **Step 1: Check HT-SIG CRC pass**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1 | grep -E "HT-SIG|ht_sig|CRC"`
Expected: Should see "HT-SIG CRC passed" or similar

- [ ] **Step 2: Check received message count**

Run: `cd /home/hy/gr-ieee802-11/build && LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_loopback_noqt.py 2>&1 | grep -i "received\|message"`
Expected: `Received N messages` with N > 0

---

## Task 5: Commit Changes

- [ ] **Step 1: Verify all changes**

Run: `cd /home/hy/gr-ieee802-11 && git diff lib/ht_symbol_splitter_impl.cc | head -100`
Expected: See the boundary and starvation fixes

- [ ] **Step 2: Stage and commit**

Run: `cd /home/hy/gr-ieee802-11 && git add lib/ht_symbol_splitter_impl.cc && git commit -m "$(cat <<'EOF'
fix(splitter): correct HT-SIG/HT-STF boundary offsets

- Shift HT-SIG0 DATA boundary from 287→303
- Shift HT-SIG1 boundary from 447→383 (was incorrectly 447)
- Fix HT-SIG0 CP from 240→224, HT-SIG0 DATA from 304→240
- Fix HT-SIG1 CP from 368→304, HT-SIG1 DATA from 384→320
- Fix HT-STF CP from unreachable→384
- Adjust starvation protection: only require 64 samples when
  starting new symbol buffer (not 80 for full CP+DATA)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"`
Expected: Commit created successfully

---

## Self-Review Checklist

1. **Spec coverage:** All 6 FFT symbols should output at correct boundaries. HT-SIG CRC should pass.
2. **Placeholder scan:** No "TBD", "TODO" in the plan. All steps have concrete expected outputs.
3. **Type consistency:** rel_idx values match the boundary definitions in the comments.

---

## Execution Options

**Which approach?**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints
