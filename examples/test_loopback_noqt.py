#!/usr/bin/env python3
"""
HT-Mixed Loopback Test (no Qt)

Tests the SPLITTER block within wifi_phy_hier by feeding it
a synthetic HT-Mixed preamble signal.

The SPLITTER outputs [SPLITTER_FFTPROBE] messages to stderr when it processes
the 6 preamble FFT symbols (L-LTF0, L-LTF1, L-SIG, HT-SIG0, HT-SIG1, HT-STF).
"""
import sys
import os

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'

from gnuradio import blocks
from gnuradio import gr
from gnuradio import analog
from gnuradio import fft
from gnuradio.fft import window
import ieee802_11
import wifi_phy_hier
import pmt
import numpy as np
import time


def generate_ht_mixed_preamble():
    """Generate a synthetic HT-Mixed preamble for testing.

    HT-Mixed preamble structure (20MHz, 64-FFT):
    - L-STF: 2x (16 CP + 64 DATA) = 2x80 = 160 samples
    - L-LTF: 2x (16 CP + 64 DATA) = 2x80 = 160 samples
    - L-SIG: 1x (16 CP + 64 DATA) = 80 samples
    - HT-SIG: 2x (16 CP + 64 DATA) = 2x80 = 160 samples
    - HT-STF: 1x (16 CP + 64 DATA) = 80 samples

    Total preamble: 640 samples
    """
    print("[TEST] Generating synthetic HT-Mixed preamble...", file=sys.stderr)

    # Parameters
    fft_size = 64
    cp_size = 16
    symbol_size = 80  # CP + FFT

    # Generate simple test tones for each symbol
    # Using different frequencies for each symbol to distinguish them
    samples_per_symbol = symbol_size
    num_symbols = 8  # L-STF x2, L-LTF x2, L-SIG, HT-SIG x2, HT-STF

    preamble = np.zeros(num_symbols * samples_per_symbol, dtype=np.complex64)

    for i in range(num_symbols):
        # Generate a simple tone with some phase offset per symbol
        freq = 0.1 * (i + 1)  # Different frequency per symbol
        phase = i * 0.5
        t = np.arange(samples_per_symbol)
        # Complex sinusoid with CP
        symbol = np.exp(1j * 2 * np.pi * freq * t + phase).astype(np.complex64)
        preamble[i * samples_per_symbol:(i + 1) * samples_per_symbol] = symbol

    print(f"[TEST] Generated {len(preamble)} preamble samples", file=sys.stderr)
    return preamble


def main():
    print("=" * 60, file=sys.stderr)
    print("HT-Mixed Loopback Test", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Testing SPLITTER block debug output", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    # Generate synthetic preamble
    preamble = generate_ht_mixed_preamble()

    # Write preamble to file
    preamble_file = '/tmp/ht_preamble.dat'
    preamble.astype(np.complex64).tofile(preamble_file)
    print(f"[TEST] Wrote preamble to {preamble_file}", file=sys.stderr)

    class rx_test(gr.top_block):
        def __init__(self, filename):
            gr.top_block.__init__(self, "RX Test")

            # File source with the preamble
            self.source = blocks.file_source(gr.sizeof_gr_complex*1, filename, False)

            # WiFi PHY hierarchical block
            self.wifi_phy = wifi_phy_hier.wifi_phy_hier(
                bandwidth=10e6,
                chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2,
                frequency=5.89e9,
                sensitivity=0.56
            )

            # Throttle
            self.throttle = blocks.throttle(gr.sizeof_gr_complex*1, 10e6)

            # Null sink
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex*1)

            # Connect: source -> throttle -> wifi_phy -> null_sink
            self.connect((self.source, 0), (self.wifi_phy, 0))
            self.connect((self.wifi_phy, 0), (self.null_sink, 0))

    # Run the test
    print("[TEST] Starting RX flowgraph...", file=sys.stderr)
    print("[TEST] Watch for [SPLITTER_FFTPROBE] in stderr", file=sys.stderr)
    sys.stderr.flush()

    tb = rx_test(preamble_file)
    tb.start()

    time.sleep(2)

    tb.stop()
    tb.wait()

    print("-" * 60, file=sys.stderr)
    print("Test complete.", file=sys.stderr)
    print("[TEST] Note: SPLITTER_FFTPROBE requires actual WiFi frames", file=sys.stderr)
    print("[TEST] The synthetic preamble may not trigger full SPLITTER output", file=sys.stderr)

    return 0


if __name__ == '__main__':
    sys.exit(main())