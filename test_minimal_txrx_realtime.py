#!/home/hy/conda/envs/gnuradio/bin/python
"""Minimal TX+RX realtime test matching file replay chain as closely as possible.

TX: msg_strobe -> mac -> wifi_phy_tx -> uhd_sink
RX: uhd_source -> multiply_const -> wifi_phy_rx -> null_sink

No rx_buffer/rx_buffer2. Only gain block (like file replay's absence of buffers).
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr, blocks, uhd
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier


class FcsLogger(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name='fcs_logger', in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern('pdu'))
        self.set_msg_handler(pmt.intern('pdu'), self.handle)
        self.ok = 0
        self.fail = 0

    def handle(self, msg):
        meta = pmt.car(msg)
        if pmt.is_dict(meta):
            crc = pmt.dict_ref(meta, pmt.intern('crc'), pmt.PMT_NIL)
            if crc == pmt.PMT_T:
                self.ok += 1
                print('[FCS_OK]')
            else:
                self.fail += 1
                print('[FCS_FAIL]')


class EncodingStripper(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name='encoding_stripper', in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern('pdu'))
        self.message_port_register_out(pmt.intern('pdu'))
        self.set_msg_handler(pmt.intern('pdu'), self.handle_pdu)

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp('encoding'))
        meta = pmt.dict_delete(meta, pmt.mp('mcs'))
        self.message_port_pub(pmt.intern('pdu'), pmt.cons(meta, data))


class MinimalTxRx(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self)

        # ===== TX =====
        self.msg_strobe = blocks.message_strobe(pmt.intern('x' * args.len), args.interval)
        self.mac = ieee802_11.mac(
            [0x23] * 6, [0x42] * 6, [0xff] * 6)
        self.encoding_stripper = EncodingStripper()
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01)
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.uhd_sink = uhd.usrp_sink(
            ",".join((args.address, "")),
            uhd.stream_args(cpu_format="fc32", channels=[0]))
        self.uhd_sink.set_subdev_spec("A:0", 0)
        self.uhd_sink.set_antenna("TX/RX", 0)
        self.uhd_sink.set_gain(args.tx_gain, 0)
        self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_sink.set_bandwidth(args.rate * 1e6, 0)
        self.uhd_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_sink.set_clock_source("internal")
        self.uhd_sink.set_time_source("internal")

        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

        # ===== RX =====
        self.uhd_src = uhd.usrp_source(
            ",".join((args.address, "")),
            uhd.stream_args(cpu_format="fc32", channels=[0]))
        self.uhd_src.set_subdev_spec(args.rx_subdev, 0)
        self.uhd_src.set_antenna("RX2", 0)
        self.uhd_src.set_gain(args.rx_gain, 0)
        self.uhd_src.set_center_freq(args.freq * 1e6, 0)
        self.uhd_src.set_bandwidth(args.rate * 1e6, 0)
        self.uhd_src.set_samp_rate(args.rate * 1e6)
        self.uhd_src.set_clock_source("internal")
        self.uhd_src.set_time_source("internal")

        self.rx_gain = blocks.multiply_const_cc(args.rx_scale)
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01)
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)
        self.fcs = FcsLogger()

        self.connect((self.uhd_src, 0), (self.rx_gain, 0))
        self.connect((self.rx_gain, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--address', default='addr=192.168.10.2')
    p.add_argument('--freq', type=float, default=5250)
    p.add_argument('--rate', type=float, default=20)
    p.add_argument('--tx-gain', type=float, default=0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='A:0')
    p.add_argument('--rx-scale', type=float, default=40.0)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--duration', type=float, default=10)
    args = p.parse_args()

    tb = MinimalTxRx(args)
    tb.start()
    time.sleep(args.duration)
    tb.stop()
    tb.wait()
    print(f'[RESULT] FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}')


if __name__ == '__main__':
    main()
