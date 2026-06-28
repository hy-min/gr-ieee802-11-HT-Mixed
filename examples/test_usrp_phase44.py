#!/usr/bin/env python
"""
Phase 44 USRP validation — Soft-LLR viterbi for HT-SIG unblock.

Standard USRP test config (per CLAUDE.md):
  IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14
  IEEE80211_TIMING_OFFSET_APPLY=1 IEEE80211_SOFT_LLR_VITERBI=1
  test_usrp_final.py  (30 seconds; freq 5.89 GHz, tx-gain 20)

Compares Phase 41 baseline (HT_SIG_PARSE_FAIL=8/30s, FCS_OK=0) against
Phase 44 (soft-LLR). Expectation: HT_SIG_PARSE_FAIL decreases OR FCS_OK > 0.
"""
import os, sys, time
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks, uhd
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


tb = gr.top_block("usrp_phase44_soft_llr")

phy_tx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
phy_rx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                       encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)

strobe = blocks.message_strobe(pmt.intern("test_payload"), 1000)
mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
stripper = Stripper()
fcs = FcsLogger()

# USRP TX at 20MHz, 5.89 GHz, tx-gain 20 (per CLAUDE.md standard config)
sink = uhd.usrp_sink(device_addr="addr=192.168.10.2",
                     stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)))
sink.set_samp_rate(20e6)
sink.set_center_freq(5.89e9, 0)
sink.set_gain(20, 0)
sink.set_antenna("TX/RX", 0)
sink.set_subdev_spec("A:0", 0)

# USRP RX at 20MHz
source = uhd.usrp_source(device_addr="addr=192.168.10.2",
                         stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)))
source.set_samp_rate(20e6)
source.set_center_freq(5.89e9, 0)
source.set_gain(20, 0)
source.set_antenna("RX2", 0)
source.set_subdev_spec("A:0", 0)
source.set_bandwidth(20e6, 0)

buf = blocks.copy(gr.sizeof_gr_complex)
buf.set_min_output_buffer(10000000)

null_src = blocks.null_source(gr.sizeof_gr_complex)
null_sink = blocks.null_sink(gr.sizeof_gr_complex)

tb.msg_connect((strobe, 'strobe'), (mac, 'app in'))
tb.msg_connect((mac, 'phy out'), (stripper, 'pdu'))
tb.msg_connect((stripper, 'pdu'), (phy_tx, 'mac_in'))
tb.msg_connect((phy_rx, 'mac_out'), (fcs, 'pdu'))

tb.connect((null_src, 0), (phy_tx, 0))
tb.connect((phy_tx, 0), (sink, 0))
tb.connect((source, 0), (buf, 0))
tb.connect((buf, 0), (phy_rx, 0))
tb.connect((phy_rx, 0), (null_sink, 0))

print("=" * 60)
print("Phase 44 USRP Validation — Soft-LLR Viterbi")
print(f"  SOFT_LLR_VITERBI={os.environ.get('IEEE80211_SOFT_LLR_VITERBI', '0')}")
print(f"  TIMING_OFFSET_APPLY={os.environ.get('IEEE80211_TIMING_OFFSET_APPLY', '0')}")
print(f"  LSIG_RATE_FORCE={os.environ.get('IEEE80211_LSIG_RATE_FORCE', 'NOT SET')}")
print(f"  LLTF_OFFSET_CORRECT={os.environ.get('IEEE80211_LLTF_OFFSET_CORRECT', 'NOT SET')}")
print("  Freq: 5.89 GHz, tx-gain: 20, 5 GHz A:0 subdev")
print("=" * 60)

tb.start()
for i in range(30):
    time.sleep(1)
    sys.stdout.write(f"[{i+1:2d}s] FCS OK={fcs.ok} FAIL={fcs.fail}\n")
    sys.stdout.flush()

tb.stop()
tb.wait()
print(f"\nFinal: OK={fcs.ok} FAIL={fcs.fail}")
if fcs.ok > 0:
    print("SUCCESS: Phase 44 soft-LLR unblocked HT-SIG viterbi!")
elif fcs.fail == 0:
    print("No frames decoded (silent failure)")
else:
    print("Still failing — Phase 44 REFUTED (or needs more iterations)")