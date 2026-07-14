#!/home/hy/conda/envs/gnuradio/bin/python
"""Minimal RX-only realtime test: UHD source -> wifi_phy_rx.

Bypasses rx_buffer, rx_gain_block, rx_buffer2 from test_usrp_minimal_loopback.py
to match the file replay chain as closely as possible.
"""
import argparse
import os
import sys

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
                print(f'[FCS_OK]')
            else:
                self.fail += 1
                print(f'[FCS_FAIL]')


class RxOnlyFlowgraph(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self)

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

        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01)

        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)
        self.fcs = FcsLogger()

        self.connect((self.uhd_src, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--address', default='addr=192.168.10.2')
    p.add_argument('--freq', type=float, default=5250)
    p.add_argument('--rate', type=float, default=20)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='A:0')
    p.add_argument('--duration', type=float, default=10)
    args = p.parse_args()

    tb = RxOnlyFlowgraph(args)
    tb.start()
    import time
    time.sleep(args.duration)
    tb.stop()
    tb.wait()
    print(f'[RESULT] FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}')


if __name__ == '__main__':
    main()
