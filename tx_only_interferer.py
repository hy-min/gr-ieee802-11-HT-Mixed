#!/usr/bin/env python3
"""
tx_only_interferer.py — 单箱 A 板连续发射（干扰源，无 RX）。

用途：Co-SR 干扰实验——作为干扰 AP，以可变功率连续发帧，
观察受害链路（另一进程 box2 TX -> box1 B RX）的 FCS_OK 变化。

用法：
  python3 tx_only_interferer.py --addr 192.168.10.2 --freq 5890 \
      --tx-gain 31.5 --tx-scale 1.0 --interval 5 --len 38
"""
import os
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
import argparse
from gnuradio import gr, blocks, uhd
import pmt
import sys

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from wifi_phy_hier import wifi_phy_hier
import ieee802_11

_MCS_TO_ENC = {0: ieee802_11.BPSK_1_2, 1: ieee802_11.QPSK_1_2,
               2: ieee802_11.QPSK_3_4, 3: ieee802_11.QAM16_1_2,
               4: ieee802_11.QAM16_3_4, 5: ieee802_11.QAM64_2_3,
               6: ieee802_11.QAM64_3_4, 7: ieee802_11.QAM64_5_6}


class EncodingStripper(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name='enc_strip', in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern('pdu'))
        self.message_port_register_out(pmt.intern('pdu'))
        self.set_msg_handler(pmt.intern('pdu'), self.handle)

    def handle(self, msg):
        meta = pmt.dict_delete(pmt.car(msg), pmt.mp('encoding'))
        meta = pmt.dict_delete(meta, pmt.mp('mcs'))
        self.message_port_pub(pmt.intern('pdu'), pmt.cons(meta, pmt.cdr(msg)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--addr', default='192.168.10.2')
    p.add_argument('--subdev', default='A:0')
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--rate', type=float, default=20)
    p.add_argument('--tx-gain', type=float, default=0)
    p.add_argument('--tx-scale', type=float, default=1.0)
    p.add_argument('--interval', type=float, default=5, help='ms，越小干扰越密')
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--mcs', type=int, default=0)
    args = p.parse_args()

    tb = gr.top_block('tx_only_interferer')
    strobe = blocks.message_strobe(pmt.intern('x' * args.len), int(args.interval))
    # 干扰帧用与受害 harness 不同的 MAC（受害=0x23/0x42/0xff），使受害 RX
    # 将其判为异帧(ourmac=0)——只当纯干扰，不污染受害 FCS_OK 计数。
    mac = ieee802_11.mac([0x11]*6, [0x22]*6, [0x33]*6)
    strip = EncodingStripper()
    tx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=_MCS_TO_ENC[args.mcs], frequency=5.89e9,
                       sensitivity=0.01)
    null_src = blocks.null_source(gr.sizeof_gr_complex)
    att = blocks.multiply_const_cc(args.tx_scale)
    sink = uhd.usrp_sink(f"addr={args.addr},send_buff_size=1048576",
                         uhd.stream_args(cpu_format="fc32", otw_format="sc16",
                                         channels=range(1)), "")
    sink.set_samp_rate(args.rate * 1e6)
    sink.set_center_freq(args.freq * 1e6, 0)
    sink.set_gain(args.tx_gain, 0)
    sink.set_subdev_spec(args.subdev, 0)
    sink.set_antenna("TX/RX", 0)
    sink.set_clock_source("internal")
    sink.set_time_source("internal")

    tb.msg_connect((strobe, 'strobe'), (mac, 'app in'))
    tb.msg_connect((mac, 'phy out'), (strip, 'pdu'))
    tb.msg_connect((strip, 'pdu'), (tx, 'mac_in'))
    tb.connect((null_src, 0), (tx, 0))
    tb.connect((tx, 0), (att, 0))
    tb.connect((att, 0), (sink, 0))
    tb.start()
    import time
    print(f"[INTERFERER] tx on {args.addr} {args.subdev} freq={args.freq} "
          f"gain={args.tx_gain} scale={args.tx_scale} interval={args.interval}ms",
          flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    tb.stop(); tb.wait()


if __name__ == '__main__':
    main()
