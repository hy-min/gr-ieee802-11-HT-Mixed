#!/usr/bin/env python
"""
USRP TDD 测试 — 匹配 PHY 10MHz 带宽的采样率
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

parser = argparse.ArgumentParser(description="USRP TDD rate-match loopback")
parser.add_argument('--rate', type=float, default=10e6,
                    help='USRP sample rate (Hz). Default 10e6 to match Phase 53-58 standard config.')
parser.add_argument('--duration', type=float, default=35.0,
                    help='Test duration in seconds. Default 35.')
parser.add_argument('--freq', type=float, default=5.89e9,
                    help='Center frequency (Hz). Default 5.89e9.')
parser.add_argument('--tx-gain', type=float, default=20,
                    help='TX gain (dB). Default 20 per project standard.')
args = parser.parse_args()

from gnuradio import gr, blocks, uhd
import pmt

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier
import ieee802_11


class fcs_counter(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="fcs_counter", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)
        self.ok = 0
        self.fail = 0

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        crc_ok = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        if crc_ok:
            self.ok += 1
        else:
            self.fail += 1

    def report(self):
        return f"OK={self.ok} FAIL={self.fail}"


class encoding_stripper(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="stripper", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.message_port_register_out(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp("encoding"))
        meta = pmt.dict_delete(meta, pmt.mp("mcs"))
        self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))


class TDDTestTop(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "USRP TDD Rate Match")

        center_freq = args.freq
        samp_rate = args.rate
        tx_gain = args.tx_gain
        rx_gain = 31
        phy_bw = min(args.rate, 20e6)

        # TX PHY
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=phy_bw, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01
        )

        # RX PHY
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=phy_bw, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01
        )

        self.msg_strobe = blocks.message_strobe(pmt.intern("x" * 20), 500)
        self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
        self.stripper = encoding_stripper()
        self.fcs = fcs_counter()
        self.sent_debug = blocks.message_debug(True, gr.log_levels.info)

        # USRP TX (Radio#0 TX/RX)
        self.uhd_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_sink.set_samp_rate(samp_rate)
        self.uhd_sink.set_center_freq(center_freq, 0)
        self.uhd_sink.set_gain(tx_gain, 0)
        self.uhd_sink.set_antenna("TX/RX", 0)
        self.uhd_sink.set_subdev_spec("A:0", 0)

        # USRP RX (Radio#1 RX2) — FDD cross-board
        self.uhd_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_source.set_samp_rate(samp_rate)
        self.uhd_source.set_center_freq(center_freq, 0)
        self.uhd_source.set_gain(rx_gain, 0)
        self.uhd_source.set_antenna("RX2", 0)
        self.uhd_source.set_subdev_spec("B:0", 0)
        self.uhd_source.set_bandwidth(samp_rate, 0)

        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(5000000)

        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.stripper, 'pdu'))
        self.msg_connect((self.stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.msg_connect((self.mac, 'phy out'), (self.sent_debug, 'store'))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))

        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

        self.connect((self.uhd_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))

        print(f"\n[INIT] USRP sample rate: {samp_rate/1e6} MHz (matching PHY bandwidth)")
        print(f"[INIT] Radio#0 TX/RX + RX2, TDD mode")


def main():
    tb = TDDTestTop()
    print(f"\n=== Running {args.duration:g}s ===")
    tb.start()
    for i in range(int(round(args.duration))):
        time.sleep(1)
        sent = tb.sent_debug.num_messages()
        print(f"[{i+1:2d}s] Sent={sent}  FCS {tb.fcs.report()}")
    tb.stop()
    tb.wait()
    print(f"\n=== Final: FCS {tb.fcs.report()} ===")


if __name__ == '__main__':
    main()
