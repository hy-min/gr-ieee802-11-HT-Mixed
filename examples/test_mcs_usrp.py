#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP X310 Hardware Test for 802.11n HT-Mixed Mode
TX: Radio 0 (UBX TX/RX port)
RX: Radio 1 (UBX RX2 port)
Features: Real-time constellation display, spectrum monitor, MCS switching
"""

import argparse
import os
import sys
import time
import pmt
import numpy as np

# Disable GNU Radio RPC to avoid segfault
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks, uhd
from gnuradio import qtgui
from gnuradio.fft import window
from PyQt5 import Qt, sip, QtWidgets
import ieee802_11

# Import wifi_phy_hier
sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier

# MCS mapping tables
GUI_MCS_NAMES = [
    'BPSK 1/2 (MCS0)', 'BPSK 3/4',
    'QPSK 1/2 (MCS1)', 'QPSK 3/4 (MCS2)',
    '16QAM 1/2 (MCS3)', '16QAM 3/4 (MCS4)',
    '64QAM 2/3 (MCS5)', '64QAM 3/4 (MCS6)',
    '64QAM 5/6 (MCS7)',
]

GUI_MCS_VALUES = [
    ieee802_11.BPSK_1_2, ieee802_11.BPSK_3_4,
    ieee802_11.QPSK_1_2, ieee802_11.QPSK_3_4,
    ieee802_11.QAM16_1_2, ieee802_11.QAM16_3_4,
    ieee802_11.QAM64_2_3, ieee802_11.QAM64_3_4,
    ieee802_11.QAM64_5_6,
]

# Constellation display ranges per MCS (key = standard HT-MCS 0-7)
CONSTELLATION_RANGES = {
    0: (-1.5, 1.5),
    1: (-1.5, 1.5),
    2: (-1.5, 1.5),
    3: (-3.0, 3.0),
    4: (-3.0, 3.0),
    5: (-7.0, 7.0),
    6: (-7.0, 7.0),
    7: (-7.0, 7.0),
}

RX_MCS_NAMES = {
    0: 'BPSK 1/2 (MCS0)',
    1: 'QPSK 1/2 (MCS1)',
    2: 'QPSK 3/4 (MCS2)',
    3: '16QAM 1/2 (MCS3)',
    4: '16QAM 3/4 (MCS4)',
    5: '64QAM 2/3 (MCS5)',
    6: '64QAM 3/4 (MCS6)',
    7: '64QAM 5/6 (MCS7)',
}


class encoding_stripper(gr.basic_block):
    """Remove encoding/mcs tags from PDU meta so mapper uses set_encoding()."""

    def __init__(self):
        gr.basic_block.__init__(
            self,
            name="encoding_stripper",
            in_sig=None,
            out_sig=None
        )
        self.message_port_register_in(pmt.intern("pdu"))
        self.message_port_register_out(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp("encoding"))
        meta = pmt.dict_delete(meta, pmt.mp("mcs"))
        self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))


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
        mcs = pmt.to_long(pmt.dict_ref(meta, pmt.mp('mcs'), pmt.from_long(-1)))
        if mcs != self.last_mcs and mcs >= 0:
            self.last_mcs = mcs
            self.callback(mcs)


class MCSEndToEndUSRP(gr.top_block, Qt.QWidget):
    """USRP-based 802.11n test with GUI."""

    def __init__(self, args):
        gr.top_block.__init__(self, "USRP 802.11n Test")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("USRP 802.11n HT-Mixed Test")
        self.resize(1200, 800)
        self.args = args

        # Store current parameters
        self.center_freq = args.freq * 1e6
        self.tx_gain = args.tx_gain
        self.rx_gain = args.rx_gain

        # ===== GUI Layout =====
        self._setup_gui()

        # ===== GNU Radio Blocks =====
        self._setup_gr_blocks()

        # ===== Connections =====
        self._make_connections()

    def _setup_gui(self):
        """Setup PyQt5 GUI layout."""
        self.top_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_layout)

        # Control panel
        self.control_layout = Qt.QHBoxLayout()
        self.top_layout.addLayout(self.control_layout)

        # MCS chooser
        self.mcs_label = Qt.QLabel("TX MCS:")
        self.control_layout.addWidget(self.mcs_label)

        self.mcs_combo = Qt.QComboBox()
        self.mcs_combo.addItems(GUI_MCS_NAMES)
        self.mcs_combo.currentIndexChanged.connect(self.set_mcs)
        self.control_layout.addWidget(self.mcs_combo)

        # LDPC toggle
        self.ldpc_check = Qt.QCheckBox("LDPC")
        self.ldpc_check.setToolTip("Enable LDPC coding (unchecked = BCC)")
        self.ldpc_check.stateChanged.connect(self.set_use_ldpc)
        self.control_layout.addWidget(self.ldpc_check)

        # TX Gain (0-31.5 dB for UBX)
        self.tx_gain_label = Qt.QLabel("TX Gain:")
        self.control_layout.addWidget(self.tx_gain_label)
        self.tx_gain_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.tx_gain_slider.setRange(0, 31)
        self.tx_gain_slider.setValue(int(self.tx_gain))
        self.tx_gain_slider.valueChanged.connect(self.set_tx_gain)
        self.control_layout.addWidget(self.tx_gain_slider)
        self.tx_gain_value = Qt.QLabel(f"{int(self.tx_gain)} dB")
        self.control_layout.addWidget(self.tx_gain_value)

        # RX Gain (0-37.5 dB for UBX)
        self.rx_gain_label = Qt.QLabel("RX Gain:")
        self.control_layout.addWidget(self.rx_gain_label)
        self.rx_gain_slider = Qt.QSlider(Qt.Qt.Horizontal)
        self.rx_gain_slider.setRange(0, 37)
        self.rx_gain_slider.setValue(int(self.rx_gain))
        self.rx_gain_slider.valueChanged.connect(self.set_rx_gain)
        self.control_layout.addWidget(self.rx_gain_slider)
        self.rx_gain_value = Qt.QLabel(f"{int(self.rx_gain)} dB")
        self.control_layout.addWidget(self.rx_gain_value)

        # Frequency
        self.freq_label = Qt.QLabel("Freq (MHz):")
        self.control_layout.addWidget(self.freq_label)
        self.freq_input = Qt.QLineEdit(str(int(self.args.freq)))
        self.freq_input.setMaximumWidth(80)
        self.control_layout.addWidget(self.freq_input)
        self.freq_btn = Qt.QPushButton("Set")
        self.freq_btn.clicked.connect(self.set_center_freq_from_gui)
        self.control_layout.addWidget(self.freq_btn)

        # Status labels
        self.rx_mcs_label = Qt.QLabel("RX MCS: --")
        self.control_layout.addWidget(self.rx_mcs_label)
        self.sent_label = Qt.QLabel("Sent: 0")
        self.control_layout.addWidget(self.sent_label)
        self.recv_label = Qt.QLabel("Recv: 0")
        self.control_layout.addWidget(self.recv_label)
        self.status_label = Qt.QLabel("Status: Ready")
        self.control_layout.addWidget(self.status_label)

        self.control_layout.addStretch(1)

        # Plots layout
        self.plots_layout = Qt.QHBoxLayout()
        self.top_layout.addLayout(self.plots_layout)

        # Status update timer
        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)

    def _setup_gr_blocks(self):
        """Setup all GNU Radio blocks."""
        # TX WiFi PHY
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=GUI_MCS_VALUES[0],
            frequency=5.89e9,
            sensitivity=0.01
        )
        self.wifi_phy_tx.set_use_ldpc(self.args.ldpc)

        # RX WiFi PHY (separate instance)
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=GUI_MCS_VALUES[0],
            frequency=5.89e9,
            sensitivity=0.01
        )

        # Message strobe
        self.msg_strobe = blocks.message_strobe(
            pmt.intern("x" * 10), self.args.interval
        )

        # MAC layer
        self.mac = ieee802_11.mac(
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        )

        # Message debug
        self.msg_debug_mac = blocks.message_debug(True, gr.log_levels.info)
        self.msg_debug_rx = blocks.message_debug(True, gr.log_levels.info)

        # Encoding stripper
        self.encoding_stripper = encoding_stripper()

        # MCS detector
        self.mcs_detect = mcs_detector(self.update_constellation_range)

        # ===== USRP TX (Radio 0) =====
        self.uhd_usrp_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=range(1),
            ),
        )
        self.uhd_usrp_sink.set_samp_rate(self.args.rate * 1e6)
        self.uhd_usrp_sink.set_center_freq(self.center_freq, 0)
        self.uhd_usrp_sink.set_gain(self.tx_gain, 0)
        self.uhd_usrp_sink.set_antenna("TX/RX", 0)
        self.uhd_usrp_sink.set_subdev_spec("A:0", 0)

        # ===== USRP RX (Radio 1) =====
        self.uhd_usrp_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=range(1),
            ),
        )
        self.uhd_usrp_source.set_samp_rate(self.args.rate * 1e6)
        self.uhd_usrp_source.set_center_freq(self.center_freq, 0)
        self.uhd_usrp_source.set_gain(self.rx_gain, 0)
        self.uhd_usrp_source.set_antenna("RX2", 0)
        self.uhd_usrp_source.set_subdev_spec("B:0", 0)
        self.uhd_usrp_source.set_bandwidth(self.args.rate * 1e6, 0)

        # ===== Constellation Display =====
        self.pdu_to_stream = blocks.pdu_to_tagged_stream(
            gr.types.complex_t, 'packet_len'
        )

        self.constellation_sink = qtgui.const_sink_c(
            480, "Constellation", 1, None
        )
        self.constellation_sink.set_update_time(0.10)
        self.constellation_sink.set_x_axis(-2, 2)
        self.constellation_sink.set_y_axis(-2, 2)

        constellation_widget = sip.wrapinstance(
            self.constellation_sink.qwidget(), QtWidgets.QWidget
        )
        self.plots_layout.addWidget(constellation_widget)

        # ===== Spectrum Display =====
        self.freq_sink = qtgui.freq_sink_c(
            1024,
            window.WIN_BLACKMAN_hARRIS,
            self.center_freq,
            self.args.rate * 1e6,
            "RX Spectrum",
            1,
            None,
        )
        self.freq_sink.set_update_time(0.10)
        self.freq_sink.set_y_axis(-140, 10)

        freq_widget = sip.wrapinstance(
            self.freq_sink.qwidget(), QtWidgets.QWidget
        )
        self.plots_layout.addWidget(freq_widget)

    def _make_connections(self):
        """Connect all GNU Radio blocks."""
        # TX Message connections
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))

        # RX Message connections
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
        self.msg_connect(
            (self.wifi_phy_rx, 'constellation'), (self.pdu_to_stream, 'pdus')
        )
        self.msg_connect(
            (self.wifi_phy_rx, 'constellation'), (self.mcs_detect, 'pdu')
        )

        # TX stream: wifi_phy_tx -> uhd_sink
        self.connect((self.wifi_phy_tx, 0), (self.uhd_usrp_sink, 0))

        # RX stream: uhd_source -> wifi_phy_rx
        self.connect((self.uhd_usrp_source, 0), (self.wifi_phy_rx, 0))

        # Spectrum: branch from uhd_source to freq_sink
        self.connect((self.uhd_usrp_source, 0), (self.freq_sink, 0))

        # Constellation: pdu_to_stream -> constellation_sink
        self.connect((self.pdu_to_stream, 0), (self.constellation_sink, 0))

    def set_mcs(self, index):
        """Callback for MCS combo box."""
        encoding = GUI_MCS_VALUES[index]
        self.wifi_phy_tx.set_encoding(encoding)
        print(f"[MCS] TX set to {GUI_MCS_NAMES[index]} (encoding={encoding})")

    def set_use_ldpc(self, state):
        """Callback for LDPC checkbox."""
        enabled = (state == Qt.Qt.Checked)
        self.wifi_phy_tx.set_use_ldpc(enabled)
        print(f"[LDPC] {'Enabled' if enabled else 'Disabled'}")

    def set_tx_gain(self, value):
        """Callback for TX gain slider."""
        self.tx_gain = float(value)
        self.tx_gain_value.setText(f"{value} dB")
        self.uhd_usrp_sink.set_gain(self.tx_gain, 0)
        print(f"[GAIN] TX gain set to {self.tx_gain} dB")

    def set_rx_gain(self, value):
        """Callback for RX gain slider."""
        self.rx_gain = float(value)
        self.rx_gain_value.setText(f"{value} dB")
        self.uhd_usrp_source.set_gain(self.rx_gain, 0)
        print(f"[GAIN] RX gain set to {self.rx_gain} dB")

    def set_center_freq_from_gui(self):
        """Callback for frequency set button."""
        try:
            freq_mhz = float(self.freq_input.text())
            self.center_freq = freq_mhz * 1e6
            self.uhd_usrp_sink.set_center_freq(self.center_freq, 0)
            self.uhd_usrp_source.set_center_freq(self.center_freq, 0)
            self.freq_sink.set_frequency_range(
                self.center_freq, self.args.rate * 1e6
            )
            print(f"[FREQ] Center frequency set to {freq_mhz} MHz")
        except ValueError:
            print("[FREQ] Invalid frequency input")

    def update_constellation_range(self, mcs):
        """Adjust constellation display range based on detected MCS."""
        xmin, xmax = CONSTELLATION_RANGES.get(mcs, (-2, 2))
        self.constellation_sink.set_x_axis(xmin, xmax)
        self.constellation_sink.set_y_axis(xmin, xmax)
        self.rx_mcs_label.setText(
            f"RX MCS: {RX_MCS_NAMES.get(mcs, 'Unknown')}"
        )
        print(
            f"[CONSTELLATION] Auto-adapted to MCS {mcs}: range [{xmin}, {xmax}]"
        )

    def update_status(self):
        """Update status bar labels."""
        sent = self.msg_debug_mac.num_messages()
        recv = self.msg_debug_rx.num_messages()
        self.sent_label.setText(f"Sent: {sent}")
        self.recv_label.setText(f"Recv: {recv}")

    def closeEvent(self, event):
        self.stop()
        self.wait()
        event.accept()


def main():
    parser = argparse.ArgumentParser(
        description='USRP X310 802.11n HT-Mixed Mode Test with GUI'
    )
    parser.add_argument(
        '--freq', type=float, default=2437,
        help='Center frequency in MHz (default: 2437 = Wi-Fi Ch 6)'
    )
    parser.add_argument(
        '--tx-gain', type=float, default=10,
        help='TX gain in dB (default: 10)'
    )
    parser.add_argument(
        '--rx-gain', type=float, default=20,
        help='RX gain in dB (default: 20)'
    )
    parser.add_argument(
        '--mcs', type=int, default=0, choices=range(9),
        help='Initial MCS mode (0-8, default: 0 = BPSK 1/2)'
    )
    parser.add_argument(
        '--ldpc', action='store_true',
        help='Enable LDPC coding (default: BCC)'
    )
    parser.add_argument(
        '--rate', type=float, default=20,
        help='Sample rate in MHz (default: 20)'
    )
    parser.add_argument(
        '--interval', type=int, default=1000,
        help='Frame interval in ms (default: 1000)'
    )
    args = parser.parse_args()

    qapp = Qt.QApplication(sys.argv)
    tb = MCSEndToEndUSRP(args)

    # Set initial MCS from command line
    if args.mcs < len(GUI_MCS_VALUES):
        tb.mcs_combo.setCurrentIndex(args.mcs)
        tb.wifi_phy_tx.set_encoding(GUI_MCS_VALUES[args.mcs])

    tb.show()
    tb.start()
    qapp.exec_()


if __name__ == '__main__':
    main()
