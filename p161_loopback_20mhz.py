#!/usr/bin/env python
"""Phase 161: 20 MHz clean-loopback probe for the last-symbol error spike.

USRP realtime (20 MHz) shows the last data symbol carrying ~10x the bit
errors of mid-frame symbols (sym21 mean 5.22). The 10 MHz loopback has its
own artifacts, so this probe runs the SAME frame ('x'*38 payload, 100 ms
strobe) through a direct TX->RX loopback at 20 MHz. If the last-symbol
spike reproduces on a clean 20 MHz channel, it is a pure decoder defect
(offline-iterable); if not, it is a channel/noise interaction.
"""
import os, sys, time
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks
import pmt

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier
import ieee802_11


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


tb = gr.top_block("loopback_20mhz")

phy_tx = wifi_phy_hier(bandwidth=20e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
phy_rx = wifi_phy_hier(bandwidth=20e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)

strobe = blocks.message_strobe(pmt.intern("x" * 38), 100)   # match USRP frame
mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
stripper = Stripper()
fcs = FcsLogger()

null_src = blocks.null_source(gr.sizeof_gr_complex)
null_sink = blocks.null_sink(gr.sizeof_gr_complex)

tb.msg_connect((strobe, 'strobe'), (mac, 'app in'))
tb.msg_connect((mac, 'phy out'), (stripper, 'pdu'))
tb.msg_connect((stripper, 'pdu'), (phy_tx, 'mac_in'))
tb.connect((null_src, 0), (phy_tx, 0))
tb.connect((phy_tx, 0), (phy_rx, 0))
tb.connect((phy_rx, 0), (null_sink, 0))
tb.msg_connect((phy_rx, 'mac_out'), (fcs, 'pdu'))

print("Direct loopback 20 MHz, 'x'*38 @100ms", flush=True)
tb.start()
for i in range(10):
    time.sleep(1)
    sys.stdout.write(f"[{i+1:2d}s] FCS OK={fcs.ok} FAIL={fcs.fail}\n")
    sys.stdout.flush()
tb.stop()
tb.wait()
print(f"\nFinal: OK={fcs.ok} FAIL={fcs.fail}")
