#!/usr/bin/env python3
"""
WiFi Loopback with Real-time Constellation Display and MCS Switch

Loopback test with:
- Real-time constellation display after frame_equalizer
- TX MCS switching via GUI chooser
- RX auto-adaptation of constellation display range based on detected MCS
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

from PyQt5 import Qt, sip, QtWidgets
from gnuradio import blocks
from gnuradio import gr
from gnuradio import analog
from gnuradio import qtgui
from gnuradio import channels
from gnuradio.filter import pfb
import ieee802_11
import wifi_phy_hier
import pmt
import numpy as np
import foo


class mcs_detector(gr.basic_block):
    """Detect MCS from constellation PDU meta and trigger callback."""

    def __init__(self, callback):
        gr.basic_block.__init__(
            self,
            name="mcs_detector",
            in_sig=None,
            out_sig=None
        )
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)
        self.callback = callback
        self.last_mcs = -1

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        mcs = pmt.to_long(pmt.dict_ref(meta, pmt.mp('mcs'), pmt.from_long(0)))
        if mcs != self.last_mcs:
            self.last_mcs = mcs
            self.callback(mcs)


class wifi_loopback_constellation(gr.top_block, Qt.QWidget):
    def __init__(self):
        gr.top_block.__init__(self, "WiFi Loopback + Constellation")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("WiFi Loopback + Constellation Display")
        self.resize(800, 600)

        # ===== GUI Layout =====
        self.top_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_layout)

        # Control panel
        self.control_layout = Qt.QHBoxLayout()
        self.top_layout.addLayout(self.control_layout)

        # MCS mapping
        self.mcs_names = [
            'BPSK 1/2 (MCS0)', 'BPSK 3/4',
            'QPSK 1/2 (MCS1)', 'QPSK 3/4 (MCS2)',
            '16QAM 1/2 (MCS3)', '16QAM 3/4 (MCS4)',
            '64QAM 2/3 (MCS5)', '64QAM 3/4 (MCS6)',
            '64QAM 5/6 (MCS7)',
        ]
        self.mcs_values = [
            ieee802_11.BPSK_1_2, ieee802_11.BPSK_3_4,
            ieee802_11.QPSK_1_2, ieee802_11.QPSK_3_4,
            ieee802_11.QAM16_1_2, ieee802_11.QAM16_3_4,
            ieee802_11.QAM64_2_3, ieee802_11.QAM64_3_4,
            ieee802_11.QAM64_5_6,
        ]

        # MCS chooser
        self.mcs_label = Qt.QLabel("TX MCS:")
        self.control_layout.addWidget(self.mcs_label)

        self.mcs_combo = Qt.QComboBox()
        self.mcs_combo.addItems(self.mcs_names)
        self.mcs_combo.currentIndexChanged.connect(self.set_mcs)
        self.control_layout.addWidget(self.mcs_combo)

        # SNR slider
        self.snr_label = Qt.QLabel("SNR (dB):")
        self.control_layout.addWidget(self.snr_label)

        self.snr_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.snr_slider.setRange(0, 40)
        self.snr_slider.setValue(30)
        self.snr_slider.valueChanged.connect(self.set_snr)
        self.control_layout.addWidget(self.snr_slider)

        self.snr_value_label = Qt.QLabel("30 dB")
        self.control_layout.addWidget(self.snr_value_label)

        # Detected MCS label
        self.rx_mcs_label = Qt.QLabel("RX MCS: --")
        self.control_layout.addWidget(self.rx_mcs_label)

        self.control_layout.addStretch(1)

        # ===== GNU Radio Blocks =====

        # WiFi PHY
        self.wifi_phy = wifi_phy_hier.wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01
        )

        # Message strobe: send dummy packets periodically
        # mac app_in expects a string message (PMT symbol), not a PDU
        self.msg_strobe = blocks.message_strobe(pmt.intern("x" * 10), 1000)

        # MAC layer
        self.mac = ieee802_11.mac(
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        )

        # Packet pad (pad_front=500 matches project convention)
        self.packet_pad = foo.packet_pad2(False, False, 0.001, 500, 0)
        self.packet_pad.set_min_output_buffer(960000)

        # Multiply const (SNR scaling)
        self.snr = 30.0
        self.multiply_const = blocks.multiply_const_cc(1.0)

        # Channel model
        noise_voltage = 10**(-self.snr / 20.0)
        self.channel = channels.channel_model(
            noise_voltage=noise_voltage,
            frequency_offset=0.0,
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False
        )

        # Resampler (compensate epsilon)
        self.resampler = pfb.arb_resampler_ccf(
            1.0, taps=None, flt_size=32, atten=100
        )
        self.resampler.declare_sample_delay(0)

        # PDU to tagged stream for constellation
        self.pdu_to_stream = blocks.pdu_to_tagged_stream(
            gr.types.complex_t, 'packet_len'
        )

        # Constellation sink
        self.constellation_sink = qtgui.const_sink_c(480, "", 1, None)
        self.constellation_sink.set_update_time(0.10)
        self.constellation_sink.set_x_axis(-2, 2)
        self.constellation_sink.set_y_axis(-2, 2)

        # Add constellation widget to layout
        constellation_widget = sip.wrapinstance(
            self.constellation_sink.qwidget(), QtWidgets.QWidget
        )
        self.top_layout.addWidget(constellation_widget)

        # MCS detector
        self.mcs_detect = mcs_detector(self.update_constellation_range)

        # ===== Connections =====

        # Message connections
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.wifi_phy, 'mac_in'))
        self.msg_connect((self.wifi_phy, 'constellation'), (self.pdu_to_stream, 'pdus'))
        self.msg_connect((self.wifi_phy, 'constellation'), (self.mcs_detect, 'pdu'))

        # Stream connections (loopback)
        self.connect((self.wifi_phy, 0), (self.packet_pad, 0))
        self.connect((self.packet_pad, 0), (self.multiply_const, 0))
        self.connect((self.multiply_const, 0), (self.channel, 0))
        self.connect((self.channel, 0), (self.resampler, 0))
        self.connect((self.resampler, 0), (self.wifi_phy, 0))

        # Constellation stream
        self.connect((self.pdu_to_stream, 0), (self.constellation_sink, 0))

    def set_mcs(self, index):
        encoding = self.mcs_values[index]
        self.wifi_phy.set_encoding(encoding)
        print(f"[MCS] TX set to {self.mcs_names[index]} (encoding={encoding})")

    def set_snr(self, value):
        self.snr = float(value)
        self.snr_value_label.setText(f"{value} dB")
        noise_voltage = 10**(-self.snr / 20.0)
        self.channel.set_noise_voltage(noise_voltage)

    def update_constellation_range(self, mcs):
        """Adjust constellation display range based on detected MCS."""
        ranges = {
            0: (-1.5, 1.5),   # BPSK
            1: (-1.5, 1.5),   # BPSK 3/4
            2: (-1.5, 1.5),   # QPSK
            3: (-1.5, 1.5),   # QPSK 3/4
            4: (-3.0, 3.0),   # 16QAM
            5: (-3.0, 3.0),   # 16QAM 3/4
            6: (-7.0, 7.0),   # 64QAM
            7: (-7.0, 7.0),   # 64QAM 3/4
            8: (-7.0, 7.0),   # 64QAM 5/6
        }
        xmin, xmax = ranges.get(mcs, (-2, 2))
        self.constellation_sink.set_x_axis(xmin, xmax)
        self.constellation_sink.set_y_axis(xmin, xmax)
        self.rx_mcs_label.setText(f"RX MCS: {self.mcs_names[mcs]}")
        print(f"[CONSTELLATION] Auto-adapted to MCS {mcs}: range [{xmin}, {xmax}]")

    def closeEvent(self, event):
        self.stop()
        self.wait()
        event.accept()


def main():
    qapp = Qt.QApplication(sys.argv)
    tb = wifi_loopback_constellation()
    tb.start()
    tb.show()
    qapp.exec_()


if __name__ == '__main__':
    main()
