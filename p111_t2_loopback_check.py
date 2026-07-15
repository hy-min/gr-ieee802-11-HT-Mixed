#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 111 T2: loopback regression check.

Generates a clean HT-Mixed frame, runs it through wifi_phy_hier, counts
FCS_OK. Baseline test to ensure Kalman implementation does not break
the loopback path.
"""
import os, sys, time

# Standard baseline env (CLAUDE.md 2026-07-04)
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

from gnuradio import gr, blocks
import pmt
import ieee802_11
from wifi_phy_hier import wifi_phy_hier


class FcsCounter(gr.basic_block):
    def __init__(self, name):
        gr.basic_block.__init__(self, name=name, in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)
        self.fcs_ok = 0
        self.fcs_fail = 0
        self.frames = 0

    def handle(self, msg):
        meta = pmt.car(msg)
        try:
            crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        except Exception:
            crc = 0
        self.frames += 1
        if crc == 1:
            self.fcs_ok += 1
        else:
            self.fcs_fail += 1


def main():
    kalman = os.environ.get('IEEE80211_H52_KALMAN_TRACK', '0') == '1'
    print(f"[P111_T2_LOOPBACK] Kalman={kalman}")
    phy_tx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS)
    phy_rx = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS)
    tb = gr.top_block()
    vec2stream = blocks.vector_to_stream(gr.sizeof_gr_complex, 64)
    stream2vec = blocks.stream_to_vector(gr.sizeof_gr_complex, 64)
    throttle = blocks.throttle(gr.sizeof_gr_complex, 20e6)
    fcs_tx = FcsCounter("fcs_tx")
    fcs_rx = FcsCounter("fcs_rx")
    tb.connect((phy_tx, 0), (vec2stream, 0))
    tb.connect((vec2stream, 0), (throttle, 0))
    tb.connect((throttle, 0), (stream2vec, 0))
    tb.connect((stream2vec, 0), (phy_rx, 0))
    tb.msg_connect((phy_tx, 'phy_out'), (phy_rx, 'phy_in'))
    tb.msg_connect((phy_rx, 'rx_pdu'), (fcs_rx, 'pdu'))
    tb.msg_connect((phy_tx, 'tx_pdu'), (fcs_tx, 'pdu'))
    tb.start()
    print("[P111_T2_LOOPBACK] Sending 5 frames...")
    pdu = pmt.make_dict()
    for i in range(5):
        payload = bytes([0xAA, 0xBB, 0xCC, 0xDD] * 5)  # 20-byte payload
        pmt_data = pmt.init_u8vector(len(payload), list(payload))
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("duration"), pmt.from_long(100))
        meta = pmt.dict_add(meta, pmt.intern("encoding"), pmt.from_long(0))
        pdu = pmt.cons(meta, pmt_data)
        phy_tx.phy_in(pmt.cons(pmt.PMT_NIL, pdu))
        time.sleep(0.1)
    time.sleep(1.0)
    tb.stop()
    tb.wait()
    print(f"[P111_T2_LOOPBACK] Result: frames={fcs_rx.frames} FCS_OK={fcs_rx.fcs_ok} FCS_FAIL={fcs_rx.fcs_fail}")


if __name__ == '__main__':
    main()