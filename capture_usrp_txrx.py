#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 145c: USRP capture with TX enabled but NO wifi_phy_rx in capture flowgraph.

This avoids the realtime RX chain blocking the USRP source, producing
complete captures suitable for file-replay validation.

TX: msg_strobe -> mac -> wifi_phy_tx -> uhd_sink
RX capture: uhd_source -> multiply_const -> file_sink
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


class TxRxCapture(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self)

        # ===== TX =====
        self.msg_strobe = blocks.message_strobe(pmt.intern('x' * args.len), int(args.interval))
        self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
        self.encoding_stripper = EncodingStripper()
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.uhd_sink = uhd.usrp_sink(
            "addr=192.168.10.2,send_buff_size=1048576",
            uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)))
        self.uhd_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_sink.set_gain(args.tx_gain, 0)
        self.uhd_sink.set_subdev_spec("A:0", 0)
        self.uhd_sink.set_antenna("TX/RX", 0)
        self.uhd_sink.set_clock_source("internal")
        self.uhd_sink.set_time_source("internal")

        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

        # ===== RX (capture only, no wifi_phy_rx) =====
        self.uhd_src = uhd.usrp_source(
            "addr=192.168.10.2,recv_buff_size=1048576",
            uhd.stream_args(cpu_format="fc32", channels=[0]))
        self.uhd_src.set_subdev_spec(args.rx_subdev, 0)
        self.uhd_src.set_antenna("RX2", 0)
        self.uhd_src.set_gain(args.rx_gain, 0)
        self.uhd_src.set_center_freq(args.freq * 1e6, 0)
        self.uhd_src.set_bandwidth(args.rate * 1e6, 0)
        self.uhd_src.set_samp_rate(args.rate * 1e6)
        self.uhd_src.set_clock_source("internal")
        self.uhd_src.set_time_source("internal")

        self.gain = blocks.multiply_const_cc(args.rx_scale)
        self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.capture, False)

        self.connect((self.uhd_src, 0), (self.gain, 0))
        self.connect((self.gain, 0), (self.file_sink, 0))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5250)
    p.add_argument('--rate', type=float, default=20)
    p.add_argument('--tx-gain', type=float, default=0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='A:0')
    p.add_argument('--rx-scale', type=float, default=40.0)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--duration', type=float, default=10)
    p.add_argument('--capture', type=str, required=True)
    args = p.parse_args()

    tb = TxRxCapture(args)
    tb.start()
    time.sleep(args.duration)
    tb.stop()
    tb.wait()

    size = os.path.getsize(args.capture) if os.path.exists(args.capture) else 0
    nsamp = size // 8
    dur = nsamp / (args.rate * 1e6)
    print(f'[CAPTURE] {args.capture}: {size} bytes, {nsamp} samples, {dur:.3f}s')


if __name__ == '__main__':
    main()
