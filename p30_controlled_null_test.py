#!/usr/bin/env python
"""
Phase 30: Controlled null-SC injection test.

Approach:
  1. Generate a clean 802.11 frame via the TX chain
  2. Inject controlled null subcarriers (|H[k]| -> 0) at specific SCs
  3. Run through RX chain with IEEE80211_H52_DUMP=1
  4. Verify whether dropping null SCs reduces avg_snr_lsig pathology

If avg_snr_lsig stays near 1.0 after null injection (and the existing code
already drops nulls), then null SCs are NOT the cause of the 90%
pathological frames seen in USRP runs.
"""
import os, sys, time
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

from gnuradio import gr, blocks
import pmt
from wifi_phy_hier import wifi_phy_hier
import ieee802_11

# ========== Generate base frame ==========
print("STEP 1: Generating base 802.11 frame...")
tb1 = gr.top_block("gen")
phy_tx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
src = blocks.message_strobe(pmt.intern("test_payload"), 100)
file_sink = blocks.file_sink(gr.sizeof_gr_complex, "/tmp/p30_base.c32", False)
file_sink.set_unbuffered(True)
null_src = blocks.null_source(gr.sizeof_gr_complex)
tb1.msg_connect((src, 'strobe'), (mac, 'app in'))
tb1.msg_connect((mac, 'phy out'), (phy_tx, 'mac_in'))
tb1.connect((null_src, 0), (phy_tx, 0))
tb1.connect((phy_tx, 0), (file_sink, 0))
tb1.start()
time.sleep(0.5)
tb1.stop()
tb1.wait()

samples = np.fromfile("/tmp/p30_base.c32", dtype=np.complex64)
print(f"  Base frame: {len(samples)} samples")

# ========== Inject nulls at specific subcarriers ==========
# Find the L-LTF region (correlate to find the start of frame)
# For simplicity, we just inject frequency-domain nulls globally
# This will create a controlled channel with null SCs

# Build per-SC channel: 64-point FFT nulls at specific bins
def apply_null_channel(iq, null_bins, h52_file="/tmp/p30_h52_injected.txt"):
    """
    Zero out specific subcarriers in the signal (simulating |H|=0 at those SCs).
    """
    out = iq.copy()
    # Use overlap-save style: process 64-sample blocks
    n = len(out)
    # Just FFT the whole signal, zero out bins, IFFT back
    # Note: this is a rough simulation; for more accurate per-SC drop test,
    # the equalizer's H52_DUMP path will measure |H| per SC.
    # We rely on the RX chain's H estimation being honest about what's null.
    return out


# ========== Run RX on a clean (no null) signal first to get baseline ==========
print("\nSTEP 2: RX on CLEAN signal (baseline)...")
tb2 = gr.top_block("rx_clean")
file_src = blocks.file_source(gr.sizeof_gr_complex, "/tmp/p30_base.c32", True)
phy_rx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
null_sink = blocks.null_sink(gr.sizeof_gr_complex)
tb2.connect((file_src, 0), (phy_rx, 0))
tb2.connect((phy_rx, 0), (null_sink, 0))
tb2.start()
time.sleep(2)
tb2.stop()
tb2.wait()
print("  Clean RX done")

# ========== Now inject 1 null SC at index 7, 21, 43, 57 (pilot positions) ==========
# This is the most aggressive null: at pilot SCs which are critical for CPE
print("\nSTEP 3: Inject null at SC 7 (pilot)...")
samples_null_7 = samples.copy()
# Process in 64-sample blocks, FFT, zero out bin 7
N_BLOCK = 64
out = samples_null_7.copy()
for i in range(0, len(out) - N_BLOCK, N_BLOCK):
    block = out[i:i+N_BLOCK].copy()
    F = np.fft.fft(block)
    F[7] = 0  # Null SC 7
    out[i:i+N_BLOCK] = np.fft.ifft(F).astype(np.complex64)
out.tofile("/tmp/p30_null_sc7.c32")
print(f"  Saved null-SC7 file: {len(out)} samples")

tb3 = gr.top_block("rx_null7")
file_src3 = blocks.file_source(gr.sizeof_gr_complex, "/tmp/p30_null_sc7.c32", True)
phy_rx3 = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                        encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
null_sink3 = blocks.null_sink(gr.sizeof_gr_complex)
tb3.connect((file_src3, 0), (phy_rx3, 0))
tb3.connect((phy_rx3, 0), (null_sink3, 0))
tb3.start()
time.sleep(2)
tb3.stop()
tb3.wait()
print("  Null-7 RX done")

# ========== Inject null at data SC 11 (mid) ==========
print("\nSTEP 4: Inject null at SC 11 (data)...")
out = samples.copy()
for i in range(0, len(out) - N_BLOCK, N_BLOCK):
    block = out[i:i+N_BLOCK].copy()
    F = np.fft.fft(block)
    F[11] = 0  # Null data SC 11
    out[i:i+N_BLOCK] = np.fft.ifft(F).astype(np.complex64)
out.tofile("/tmp/p30_null_sc11.c32")
print(f"  Saved null-SC11 file")

tb4 = gr.top_block("rx_null11")
file_src4 = blocks.file_source(gr.sizeof_gr_complex, "/tmp/p30_null_sc11.c32", True)
phy_rx4 = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                        encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
null_sink4 = blocks.null_sink(gr.sizeof_gr_complex)
tb4.connect((file_src4, 0), (phy_rx4, 0))
tb4.connect((phy_rx4, 0), (null_sink4, 0))
tb4.start()
time.sleep(2)
tb4.stop()
tb4.wait()
print("  Null-11 RX done")

print("\nDONE — check stdout for H52_DUMP/avg_snr_lsig data")
