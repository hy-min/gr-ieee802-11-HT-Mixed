# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: WiFi PHY Hier
# GNU Radio version: 3.10.11.0

from gnuradio import blocks
from gnuradio import digital
from gnuradio import fft
from gnuradio.fft import window
from gnuradio import gr
from gnuradio.filter import firdes
import sys
import signal
import ieee802_11
import threading
import numpy as np
import pmt







class wifi_phy_hier(gr.hier_block2):
    def __init__(self, bandwidth=10e6, chan_est=ieee802_11.LS, encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.56):
        gr.hier_block2.__init__(
            self, "WiFi PHY Hier",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )
        self.message_port_register_hier_in("mac_in")
        self.message_port_register_hier_out("carrier")
        self.message_port_register_hier_out("mac_out")

        ##################################################
        # Parameters
        ##################################################
        self.bandwidth = bandwidth
        self.chan_est = chan_est
        self.encoding = encoding
        self.frequency = frequency
        self.sensitivity = sensitivity

        ##################################################
        # Variables
        ##################################################
        self.window_size = window_size = 48
        self.sync_length = sync_length = 320
        self.max_symbols = max_symbols = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)
        self.header_formatter = header_formatter = ieee802_11.signal_field()

        ##################################################
        # Blocks
        ##################################################

        self.sync_short = ieee802_11.sync_short(sensitivity, 2, False, False)
        self.sync_long = ieee802_11.sync_long(sync_length, False, False)
        self.ieee802_11_mapper_0 = ieee802_11.mapper(encoding, False)
        self.ieee802_11_frame_equalizer_0 = ieee802_11.frame_equalizer(chan_est, frequency, bandwidth, False, False)
        self.ieee802_11_decode_mac_0 = ieee802_11.decode_mac(False, False)
        self.ieee802_11_chunks_to_symbols_xx_0 = ieee802_11.chunks_to_symbols()
        self.ieee802_11_chunks_to_symbols_xx_0.set_min_output_buffer((max_symbols * 48 * 8))
        self.ht_symbol_splitter_0 = ieee802_11.ht_symbol_splitter(64, 80, 16)
        self.fft_vxx_0_1 = fft.fft_vcc(64, True, window.rectangular(64), False, 1)
        self.fft_vxx_0_0 = fft.fft_vcc(64, False, tuple([1/52**.5] * 64), False, 1)
        self.fft_vxx_0_0.set_min_output_buffer((max_symbols * 48 * 8 * 10))
        self.digital_packet_headergenerator_bb_0 = digital.packet_headergenerator_bb(header_formatter.formatter(), "packet_len")
        self.digital_ofdm_cyclic_prefixer_0_0 = digital.ofdm_cyclic_prefixer(
            64,
            64 + 16,
            2,
            "packet_len")
        self.digital_ofdm_cyclic_prefixer_0_0.set_min_output_buffer((max_symbols * 48 * 8 * 10))
        self.digital_ofdm_carrier_allocator_cvc_0_0_0 = digital.ofdm_carrier_allocator_cvc( 64, (list(range(-26, -21)) + list(range(-20, -7)) + list(range(-6, 0)) + list(range(1, 7)) + list(range(8, 21)) +list( range(22, 27)),), ((-21, -7, 7, 21), ), ((1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (1, 1, 1, -1), (1, 1, 1, -1), (1, 1, 1, -1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, -1, 1)), ((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (-1.4719601443879746-1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, (1.4719601443879746+1.4719601443879746j), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (0, 0j, 0, 0j, 0, 0j, -1, 1j, -1, 1j, -1, 1j, -1, -1j, 1, 1j, 1, -1j, -1, 1j, 1, 1j, 1, 1j, 1, 1j, -1, (-0-1j), 1, -1j, -1, 1j, 0, -1j, 1, (-0-1j), 1, -1j, 1, 1j, -1, -1j, 1, (-0-1j), -1, 1j, 1, 1j, 1, 1j, 1, 1j, -1, -1j, 1, 1j, 1, -1j, -1, 0j, 0, 0j, 0, 0j), (0, 0, 0, 0, 0, 0, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0, 0)), "packet_len", True)
        self.digital_ofdm_carrier_allocator_cvc_0_0_0.set_min_output_buffer((max_symbols * 48 * 8))
        self.digital_chunks_to_symbols_xx_0 = digital.chunks_to_symbols_bc([-1, 1], 1)
        self.digital_chunks_to_symbols_xx_0.set_min_output_buffer((max_symbols * 48 * 8 * 2))
        self.blocks_tagged_stream_mux_0 = blocks.tagged_stream_mux(gr.sizeof_gr_complex*1, "packet_len", 1)
        self.blocks_tagged_stream_mux_0.set_min_output_buffer((max_symbols * 48 * 8))
        self.ht_sig_rotator_0 = ht_sig_rotator()
        self.blocks_stream_to_vector_0 = blocks.stream_to_vector(gr.sizeof_gr_complex*1, 64)
        self.blocks_multiply_xx_0 = blocks.multiply_vcc(1)
        self.blocks_moving_average_xx_1 = blocks.moving_average_ff((window_size  + 16), 1, 4000, 1)
        self.blocks_moving_average_xx_0 = blocks.moving_average_cc(window_size, 1, 4000, 1)
        self.blocks_divide_xx_0 = blocks.divide_ff(1)
        self.blocks_delay_0_0 = blocks.delay(gr.sizeof_gr_complex*1, 16)
        self.blocks_delay_0 = blocks.delay(gr.sizeof_gr_complex*1, sync_length)
        self.blocks_conjugate_cc_0 = blocks.conjugate_cc()
        self.blocks_complex_to_mag_squared_0 = blocks.complex_to_mag_squared(1)
        self.blocks_complex_to_mag_0 = blocks.complex_to_mag(1)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.ieee802_11_decode_mac_0, 'out'), (self, 'mac_out'))
        self.msg_connect((self.ieee802_11_frame_equalizer_0, 'symbols'), (self, 'carrier'))
        self.msg_connect((self, 'mac_in'), (self.ieee802_11_mapper_0, 'in'))
        self.connect((self.blocks_complex_to_mag_0, 0), (self.blocks_divide_xx_0, 0))
        self.connect((self.blocks_complex_to_mag_squared_0, 0), (self.blocks_moving_average_xx_1, 0))
        self.connect((self.blocks_conjugate_cc_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.blocks_delay_0, 0), (self.sync_long, 1))
        self.connect((self.blocks_delay_0_0, 0), (self.blocks_conjugate_cc_0, 0))
        self.connect((self.blocks_delay_0_0, 0), (self.sync_short, 0))
        self.connect((self.blocks_divide_xx_0, 0), (self.sync_short, 2))
        self.connect((self.blocks_moving_average_xx_0, 0), (self.blocks_complex_to_mag_0, 0))
        self.connect((self.blocks_moving_average_xx_0, 0), (self.sync_short, 1))
        self.connect((self.blocks_moving_average_xx_1, 0), (self.blocks_divide_xx_0, 1))
        self.connect((self.blocks_multiply_xx_0, 0), (self.blocks_moving_average_xx_0, 0))
        self.connect((self.blocks_stream_to_vector_0, 0), (self.fft_vxx_0_1, 0))
        self.connect((self.blocks_tagged_stream_mux_0, 0), (self.digital_ofdm_carrier_allocator_cvc_0_0_0, 0))
        self.connect((self.digital_chunks_to_symbols_xx_0, 0), (self.ht_sig_rotator_0, 0))
        self.connect((self.ht_sig_rotator_0, 0), (self.blocks_tagged_stream_mux_0, 0))
        self.connect((self.digital_ofdm_carrier_allocator_cvc_0_0_0, 0), (self.fft_vxx_0_0, 0))
        self.connect((self.digital_ofdm_cyclic_prefixer_0_0, 0), (self, 0))
        self.connect((self.digital_packet_headergenerator_bb_0, 0), (self.digital_chunks_to_symbols_xx_0, 0))
        self.connect((self.fft_vxx_0_0, 0), (self.digital_ofdm_cyclic_prefixer_0_0, 0))
        self.connect((self.fft_vxx_0_1, 0), (self.ieee802_11_frame_equalizer_0, 0))
        self.connect((self.ht_symbol_splitter_0, 0), (self.blocks_stream_to_vector_0, 0))
        self.connect((self.ieee802_11_chunks_to_symbols_xx_0, 0), (self.blocks_tagged_stream_mux_0, 1))
        self.connect((self.ieee802_11_frame_equalizer_0, 0), (self.ieee802_11_decode_mac_0, 0))
        self.connect((self.ieee802_11_mapper_0, 0), (self.digital_packet_headergenerator_bb_0, 0))
        self.connect((self.ieee802_11_mapper_0, 0), (self.ieee802_11_chunks_to_symbols_xx_0, 0))
        self.connect((self, 0), (self.blocks_complex_to_mag_squared_0, 0))
        self.connect((self, 0), (self.blocks_delay_0_0, 0))
        self.connect((self, 0), (self.blocks_multiply_xx_0, 0))
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


# ============================================================================
# HT-SIG Rotator Block
# Rotates HT-SIG complex symbols by j (QBPSK rotation) while leaving L-SIG unchanged
# Inserted between chunks_to_symbols_bc and tagged_stream_mux
# ============================================================================
import numpy as np
from gnuradio import gr
import pmt

class ht_sig_rotator(gr.sync_block):
    """
    HT-SIG Rotator for 802.11n HT-Mixed mode TX

    L-SIG: first 48 complex symbols (indices 0-47) → pass through unchanged (BPSK on real axis)
    HT-SIG: next 96 complex symbols (indices 48-143) → multiply by 1j (QBPSK on imaginary axis)
    """

    def __init__(self):
        gr.sync_block.__init__(
            self,
            name="HT-SIG Rotator",
            in_sig=[np.complex64],
            out_sig=[np.complex64]
        )
        self.header_pos = 0  # position within header stream (0-143)
        print('[HT-SIG-ROTATOR] Created', flush=True)

    def work(self, input_items, output_items):
        inp = input_items[0]
        out = output_items[0]
        n_in = len(inp)
        n_out = len(out)
        n = min(n_in, n_out)

        produced = 0
        for i in range(n):
            # Check for packet_len tag at this input position
            tags = self.get_tags_in_window(0, i, i + 1)
            for tag in tags:
                if pmt.eq(tag.key, pmt.intern("packet_len")):
                    self.header_pos = 0
                    break

            # Apply rotation based on header_pos BEFORE incrementing
            if self.header_pos < 48:
                # L-SIG: pass through unchanged (BPSK ±1 on real axis)
                out[i] = inp[i]
            elif self.header_pos < 144:
                # HT-SIG: rotate by j (QBPSK ±j on imaginary axis)
                out[i] = inp[i] * 1j
            else:
                # Beyond header: pass through unchanged
                out[i] = inp[i]

            self.header_pos += 1
            produced += 1

        return produced

