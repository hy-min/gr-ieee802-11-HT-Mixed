# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: WiFi PHY Hier
# GNU Radio version: 3.10.11.0

import os
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'

from gnuradio import blocks
from gnuradio import gr
from gnuradio import digital
from gnuradio import fft
from gnuradio.fft import window
from gnuradio.filter import firdes
import sys
import signal
import ieee802_11
import threading
import mixed_mode_carrier_allocator
import numpy as np
import pmt





class wifi_phy_hier(gr.hier_block2):
    def __init__(self, bandwidth=10e6, chan_est=ieee802_11.LS, encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.56, use_ldpc=False, sync_length=320):
        gr.hier_block2.__init__(
            self, "WiFi PHY Hier",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )
        self.message_port_register_hier_in("mac_in")
        self.message_port_register_hier_out("carrier")
        self.message_port_register_hier_out("mac_out")
        self.message_port_register_hier_out("constellation")

        ##################################################
        # Parameters
        ##################################################
        self.bandwidth = bandwidth
        self.chan_est = chan_est
        self.encoding = encoding
        self.frequency = frequency
        self.sensitivity = sensitivity
        self.use_ldpc = use_ldpc

        # Phase 71: RX FFT window selection. Default OFF (rectangular) preserves
        # current behavior. Set IEEE80211_RX_FFT_WINDOW=hann to apply Hann window
        # before RX FFT, reducing spectral leakage from -13 dB to -31 dB sidelobes.
        # Other options: blackmanharris, hamming, blackman (GNU Radio fft.window).
        rx_window_type = os.environ.get('IEEE80211_RX_FFT_WINDOW', 'rectangular').lower()
        valid_windows = ('rectangular', 'hann', 'hamming', 'blackman', 'blackmanharris',
                         'kaiser', 'bartlett', 'flattop', 'nuttall', 'welch')
        if rx_window_type not in valid_windows:
            print(f"[WARN] Unknown RX FFT window '{rx_window_type}', falling back to rectangular", file=sys.stderr)
            rx_window_type = 'rectangular'
        self._rx_window_type = rx_window_type

        ##################################################
        # Variables
        ##################################################
        self.window_size = window_size = 48
        # Phase 14 (2026-06-15): sync_length is now a constructor parameter.
        # Default 320 (preserves software loopback 9/9 regression baseline).
        # For USRP tests, override to 1 to fix sync_long scheduler deadlock
        # (sync_long's 2-input-port + set_output_multiple(512) + 320-sample
        # delay prime causes scheduler to never call general_work on USRP
        # continuous streaming input). Verified: sync_length=1 unlocks
        # sync_long (93 calls in 10s) but breaks loopback algorithm. Use
        # the proper fix (sync_long.cc set_output_multiple(64)) to get both.
        self.sync_length = sync_length = sync_length
        self.max_symbols = max_symbols = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)
        self.header_formatter = header_formatter = ieee802_11.signal_field()

        ##################################################
        # Blocks
        ##################################################

        # Phase 89 T3: MIN_PLATEAU env var override (default 2, opt-in 16 for L-STF plateau)
        MIN_PLATEAU = int(os.environ.get('IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE', '2'))
        self.sync_short = ieee802_11.sync_short(sensitivity, MIN_PLATEAU, True, True)
        # Phase 58 Task 5: increased to 1M samples (8 MB) for USRP burst absorption
        self.sync_short.set_min_output_buffer(1000000)
        self.sync_long = ieee802_11.sync_long(sync_length, True, True)
        # Phase 58 Task 5: increased to 1M samples (8 MB) for USRP burst absorption
        self.sync_long.set_min_output_buffer(1000000)
        self.ieee802_11_mapper_0 = ieee802_11.mapper(encoding, False)
        self.ieee802_11_mapper_0.set_use_ldpc(use_ldpc)
        self.ieee802_11_frame_equalizer_0 = ieee802_11.frame_equalizer(chan_est, frequency, bandwidth, False, False)
        self.ieee802_11_frame_equalizer_0.set_min_output_buffer((max_symbols * 52 * 8))
        self.ieee802_11_frame_equalizer_0.set_output_multiple(52)
        self.ieee802_11_decode_mac_0 = ieee802_11.decode_mac(True, True)
        self.ieee802_11_decode_mac_0.set_min_output_buffer((max_symbols * 52 * 8))
        self.ieee802_11_chunks_to_symbols_xx_0 = ieee802_11.chunks_to_symbols()
        self.ieee802_11_chunks_to_symbols_xx_0.set_min_output_buffer((max_symbols * 52 * 8))
        self.ht_symbol_splitter_0 = ieee802_11.ht_symbol_splitter(64, 80, 16)
        self.ht_symbol_splitter_0.set_min_output_buffer((max_symbols * 64 * 8))
        # ht_header_tagged generates L-SIG + HT-SIG header from encoding
        self.ht_header_tagged_0 = ieee802_11.ht_header_tagged(13, True, 'psdu_len', 'encoding', 'packet_len')
        # RX FFT: shift=False for natural order (matches kHeader48Bin in frame_equalizer)
        # Phase 71: use env-var-selected window (default rectangular = no change)
        if self._rx_window_type == 'rectangular':
            rx_window = window.rectangular(64)
        elif self._rx_window_type == 'hann':
            rx_window = window.hann(64)
        elif self._rx_window_type == 'hamming':
            rx_window = window.hamming(64)
        elif self._rx_window_type == 'blackman':
            rx_window = window.blackman(64)
        elif self._rx_window_type == 'blackmanharris':
            rx_window = window.blackmanharris(64)
        elif self._rx_window_type == 'kaiser':
            rx_window = window.kaiser(64, 14.0)  # beta=14 ~80 dB sidelobes
        elif self._rx_window_type == 'bartlett':
            rx_window = window.bartlett(64)
        elif self._rx_window_type == 'flattop':
            rx_window = window.flattop(64)
        elif self._rx_window_type == 'nuttall':
            rx_window = window.nuttall(64)
        elif self._rx_window_type == 'welch':
            rx_window = window.welch(64)
        self.fft_vxx_0_1 = fft.fft_vcc(64, True, rx_window, False, 1)
        self.fft_vxx_0_1.set_min_output_buffer((max_symbols * 64 * 8))
        # TX IFFT: shift=False, window normalizes by 1/sqrt(52)
        self.fft_vxx_0_0 = fft.fft_vcc(64, False, tuple([1/52**.5] * 64), False, 1)
        self.fft_vxx_0_0.set_min_output_buffer((max_symbols * 52 * 8 * 10))
        self.digital_ofdm_cyclic_prefixer_0_0 = digital.ofdm_cyclic_prefixer(
            64,
            64 + 16,
            2,
            "packet_len")
        self.digital_ofdm_cyclic_prefixer_0_0.set_min_output_buffer((max_symbols * 52 * 8 * 10))
        # mixed_mode_carrier_allocator handles:
        # - Dynamic subcarrier switching: 48 for header, 52 for HT-DATA
        # - QBPSK rotation for HT-SIG internally
        # - Sync words (L-STF, L-LTF)
        self.mixed_mode_carrier_allocator_0 = mixed_mode_carrier_allocator.mixed_mode_carrier_allocator(
            tag_key="packet_len",
            sync_words=None
        )
        self.mixed_mode_carrier_allocator_0.set_min_output_buffer((max_symbols * 52 * 8))
        # Insert HT-STF and HT-LTF training symbols after 7 preamble + header symbols.
        # Must be placed between carrier allocator and OFDM CP so the CP sees
        # the updated packet_len tag (insert_ht_training adds N_TRAIN=2).
        self.insert_ht_training_0 = ieee802_11.insert_ht_training("packet_len")

        # Phase 169: TX band-edge pre-emphasis (opt-in, default OFF).
        # Phase 161 root-caused terminal decode failures to band-edge subcarrier
        # fade: failing frames have min|H|=13.7 vs 28.7, argmin always at
        # SC -28/-27 (20 MHz filter rolloff edge). ZF equalization 1/H amplifies
        # thermal noise at those SCs -> bit errors. All 165 prior phases attacked
        # the RX side; the TX is ours and has ~20 dB headroom (--tx-scale 0.1),
        # so boost the edge SCs at TX to raise their received |H| directly.
        # The RX H estimate absorbs the boost automatically (H just gets bigger).
        # Gain map (64-point FFT bins; SC k -> bin k if k>=0 else 64+k):
        #   SC +26/+27/+28 -> bins 26,27,28; SC -26/-27/-28 -> bins 38,37,36
        # Enable: IEEE80211_TX_PREEMPHASIS=1; gain: IEEE80211_TX_PREEMPHASIS_GAIN
        # (default 2.0). Inserted between insert_ht_training and the IFFT so
        # preamble, header, training, and data symbols all get the same map.
        self.tx_preemphasis_enabled = os.environ.get('IEEE80211_TX_PREEMPHASIS', '') == '1'
        if self.tx_preemphasis_enabled:
            pe_gain = float(os.environ.get('IEEE80211_TX_PREEMPHASIS_GAIN', '2.0'))
            pe_gains = [1.0] * 64
            for b in (26, 27, 28, 36, 37, 38):
                pe_gains[b] = pe_gain
            self.blocks_multiply_const_vxx_pe = blocks.multiply_const_vcc(
                [complex(g, 0) for g in pe_gains])
            sys.stderr.write(
                "[PHY_P169] TX band-edge pre-emphasis ENABLED: gain=%.1f on "
                "bins 26-28/36-38 (SC ±26..±28)\n" % pe_gain)
        # Header path: BPSK for L-SIG/HT-SIG
        self.digital_chunks_to_symbols_xx_0 = digital.chunks_to_symbols_bc([-1, 1], 1)
        self.digital_chunks_to_symbols_xx_0.set_min_output_buffer((max_symbols * 48 * 8 * 2))
        # 2 inputs: header (0) + data (1)
        self.blocks_tagged_stream_mux_0 = blocks.tagged_stream_mux(gr.sizeof_gr_complex*1, "packet_len", 2)
        self.blocks_tagged_stream_mux_0.set_min_output_buffer((max_symbols * 52 * 8))
        self.blocks_stream_to_vector_0 = blocks.stream_to_vector(gr.sizeof_gr_complex*1, 64)
        # Phase 58 Task 5: increased to 1M samples (8 MB) for USRP burst absorption
        self.blocks_stream_to_vector_0.set_min_output_buffer(1000000)
        self.sync_short_fused_0 = ieee802_11.sync_short_fused(sensitivity, 3.0, 1024)
        # Phase 58 Task 5: increased to 1M samples (8 MB) for USRP burst absorption
        self.sync_short_fused_0.set_min_output_buffer(1000000)
        self.blocks_delay_0 = blocks.delay(gr.sizeof_gr_complex*1, sync_length)
        # Phase 58 Task 5: increased to 1M samples (8 MB) for USRP burst absorption
        self.blocks_delay_0.set_min_output_buffer(1000000)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.ieee802_11_decode_mac_0, 'out'), (self, 'mac_out'))
        self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'carrier'))
        self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'constellation'))
        self.msg_connect((self, 'mac_in'), (self.ieee802_11_mapper_0, 'in'))
        self.connect((self, 0), (self.sync_short_fused_0, 0))
        self.connect((self.sync_short_fused_0, 0), (self.sync_short, 0))
        self.connect((self.sync_short_fused_0, 1), (self.sync_short, 1))
        self.connect((self.sync_short_fused_0, 2), (self.sync_short, 2))
        self.connect((self.blocks_delay_0, 0), (self.sync_long, 1))
        self.connect((self.blocks_stream_to_vector_0, 0), (self.fft_vxx_0_1, 0))
        # Header path: mapper → ht_header_tagged → chunks_to_symbols → mux input 0
        self.connect((self.ieee802_11_mapper_0, 0), (self.ht_header_tagged_0, 0))
        self.connect((self.ht_header_tagged_0, 0), (self.digital_chunks_to_symbols_xx_0, 0))
        self.connect((self.digital_chunks_to_symbols_xx_0, 0), (self.blocks_tagged_stream_mux_0, 0))
        # Data path: mapper → chunks_to_symbols → mux input 1
        self.connect((self.ieee802_11_chunks_to_symbols_xx_0, 0), (self.blocks_tagged_stream_mux_0, 1))
        # Combined: mux → mixed_mode_carrier_allocator → FFT → CP → output
        self.connect((self.blocks_tagged_stream_mux_0, 0), (self.mixed_mode_carrier_allocator_0, 0))
        # Insert HT-STF + HT-LTF between carrier allocator and OFDM CP (via IFFT)
        self.connect((self.mixed_mode_carrier_allocator_0, 0),
                     (self.insert_ht_training_0, 0))
        # Phase 169: route through pre-emphasis multiplier when enabled
        if self.tx_preemphasis_enabled:
            self.connect((self.insert_ht_training_0, 0),
                         (self.blocks_multiply_const_vxx_pe, 0))
            self.connect((self.blocks_multiply_const_vxx_pe, 0),
                         (self.fft_vxx_0_0, 0))
        else:
            self.connect((self.insert_ht_training_0, 0),
                         (self.fft_vxx_0_0, 0))
        self.connect((self.fft_vxx_0_0, 0), (self.digital_ofdm_cyclic_prefixer_0_0, 0))
        self.connect((self.digital_ofdm_cyclic_prefixer_0_0, 0), (self, 0))
        self.connect((self.fft_vxx_0_1, 0), (self.ieee802_11_frame_equalizer_0, 0))
        self.connect((self.ht_symbol_splitter_0, 0), (self.blocks_stream_to_vector_0, 0))
        self.connect((self.ieee802_11_frame_equalizer_0, 0), (self.ieee802_11_decode_mac_0, 0))
        self.connect((self.ieee802_11_mapper_0, 0), (self.ieee802_11_chunks_to_symbols_xx_0, 0))
        self.connect((self.sync_long, 0), (self.ht_symbol_splitter_0, 0))
        self.connect((self.sync_short, 0), (self.blocks_delay_0, 0))
        self.connect((self.sync_short, 0), (self.sync_long, 0))


    def get_bandwidth(self):
        return self.bandwidth

    def set_bandwidth(self, bandwidth):
        self.bandwidth = bandwidth
        self.ieee802_11_frame_equalizer_0.set_bandwidth(self.bandwidth)

    def get_chan_est(self):
        return self.chan_est

    def set_chan_est(self, chan_est):
        self.chan_est = chan_est
        self.ieee802_11_frame_equalizer_0.set_algorithm(self.chan_est)

    def get_encoding(self):
        return self.encoding

    def set_encoding(self, encoding):
        self.encoding = encoding
        self.ieee802_11_mapper_0.set_encoding(self.encoding)

    def get_use_ldpc(self):
        return self.use_ldpc

    def set_use_ldpc(self, use_ldpc):
        self.use_ldpc = use_ldpc
        self.ieee802_11_mapper_0.set_use_ldpc(self.use_ldpc)

    def get_frequency(self):
        return self.frequency

    def set_frequency(self, frequency):
        self.frequency = frequency
        self.ieee802_11_frame_equalizer_0.set_frequency(self.frequency)

    def get_sensitivity(self):
        return self.sensitivity

    def set_sensitivity(self, sensitivity):
        self.sensitivity = sensitivity

    def get_window_size(self):
        return self.window_size

    def set_window_size(self, window_size):
        self.window_size = window_size
        self.blocks_moving_average_xx_0.set_length_and_scale(self.window_size, 1)
        self.blocks_moving_average_xx_1.set_length_and_scale((self.window_size  + 16), 1)

    def get_sync_length(self):
        return self.sync_length

    def set_sync_length(self, sync_length):
        self.sync_length = sync_length
        self.blocks_delay_0.set_dly(int(self.sync_length))

    def get_max_symbols(self):
        return self.max_symbols

    def set_max_symbols(self, max_symbols):
        self.max_symbols = max_symbols

    def get_header_formatter(self):
        return self.header_formatter

    def set_header_formatter(self, header_formatter):
        self.header_formatter = header_formatter
