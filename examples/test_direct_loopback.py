#!/usr/bin/env python
"""
直连 loopback：TX PHY 输出直接连到 RX PHY 输入
验证 PHY 本身工作正常

--mcs N (0-7): HT MCS sweep gate (default: MCS 0 / BPSK_1_2, unchanged).
Mapping follows lib/mapper_impl.cc mcs_to_encoding.
"""
import os, sys, time, argparse
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks
import pmt

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier
import ieee802_11

_MCS_TO_ENC = {0: ieee802_11.BPSK_1_2, 1: ieee802_11.QPSK_1_2,
               2: ieee802_11.QPSK_3_4, 3: ieee802_11.QAM16_1_2,
               4: ieee802_11.QAM16_3_4, 5: ieee802_11.QAM64_2_3,
               6: ieee802_11.QAM64_3_4, 7: ieee802_11.QAM64_5_6}

_ap = argparse.ArgumentParser()
_ap.add_argument('--mcs', type=int, default=0, choices=range(0, 8))
_ap.add_argument('--cfo', type=float, default=0.0,
                help='normalized CFO (cycles/sample) via channel_model; 0=off')
_ap.add_argument('--noise', type=float, default=0.0,
                help='AWGN noise voltage via channel_model; 0=off')
_ap.add_argument('--len', type=int, default=None,
                help="payload bytes (default None = 'test'*5, regression unchanged)")
_args = _ap.parse_args()
_enc = _MCS_TO_ENC[_args.mcs]
print(f"[LOOPBACK] mcs={_args.mcs} encoding_enum={int(_enc)} "
      f"cfo={_args.cfo} noise={_args.noise} len={_args.len}")


def _padded_len(mcs, payload_len, max_real):
    ndbps = {0: 26, 1: 52, 2: 78, 3: 104, 4: 156, 5: 208, 6: 234, 7: 260}[mcs]
    psdu = payload_len + 28
    for delta in range(0, 65):
        p = psdu + delta
        r = 16 + 8 * p + 6
        n_sym = (r + ndbps - 1) // ndbps
        if r - (n_sym - 1) * ndbps <= max_real:
            return p - 28
    return payload_len


_PAD_MAX = os.environ.get('IEEE80211_TX_PAD_ALIGN')
if _PAD_MAX:
    _args.len = _padded_len(_args.mcs, _args.len or 0, int(_PAD_MAX))
    print(f"[LOOPBACK] pad-align -> payload {_args.len}")


class FcsLogger(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="fcs", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)
        self.ok = 0
        self.fail = 0

    def handle(self, msg):
        meta = pmt.car(msg)
        crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        if crc:
            self.ok += 1
            print("*** FCS OK ***")
        else:
            self.fail += 1


class Stripper(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="stripper", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.message_port_register_out(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)

    def handle(self, msg):
        meta, data = pmt.car(msg), pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp("encoding"))
        meta = pmt.dict_delete(meta, pmt.mp("mcs"))
        self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))


tb = gr.top_block("direct_loopback")

phy_tx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=_enc, frequency=5.89e9, sensitivity=0.01)
phy_rx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=_enc, frequency=5.89e9, sensitivity=0.01)

strobe = blocks.message_strobe(pmt.intern("test" * 5 if _args.len is None else "x" * _args.len), 500)
mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
stripper = Stripper()
fcs = FcsLogger()

null_src = blocks.null_source(gr.sizeof_gr_complex)
null_sink = blocks.null_sink(gr.sizeof_gr_complex)

# TX chain
tb.msg_connect((strobe, 'strobe'), (mac, 'app in'))
tb.msg_connect((mac, 'phy out'), (stripper, 'pdu'))
tb.msg_connect((stripper, 'pdu'), (phy_tx, 'mac_in'))
tb.connect((null_src, 0), (phy_tx, 0))

# Direct: TX output → RX input (optionally through a channel impairment model)
if _args.cfo != 0.0 or _args.noise != 0.0:
    from gnuradio import channels
    chan = channels.channel_model(_args.noise, _args.cfo, 1.0, [1.0], 0, False)
    tb.connect((phy_tx, 0), (chan, 0), (phy_rx, 0))
else:
    tb.connect((phy_tx, 0), (phy_rx, 0))
tb.connect((phy_rx, 0), (null_sink, 0))
tb.msg_connect((phy_rx, 'mac_out'), (fcs, 'pdu'))

print("=" * 50)
print("Direct loopback: TX PHY → RX PHY")
print("=" * 50)

tb.start()
for i in range(10):
    time.sleep(1)
    sys.stdout.write(f"[{i+1:2d}s] FCS OK={fcs.ok} FAIL={fcs.fail}\n")
    sys.stdout.flush()

tb.stop()
tb.wait()
print(f"\nFinal: OK={fcs.ok} FAIL={fcs.fail}")
