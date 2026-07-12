#!/home/hy/conda/envs/gnuradio/bin/python
"""
p142 file replay: validate Phase 142 Wiener前移 on captured same-board IQ.
Replaces USRP source with repeating file source; keeps rest of flowgraph identical.
"""
import argparse
import os
import sys
import time

# Same env defaults as test_usrp_minimal_loopback.py internal_run
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier


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


def internal_run(args):
    if args.wiener_on:
        os.environ['IEEE80211_WIENER_H52'] = '1'
        os.environ['IEEE80211_WIENER_FIFO_N'] = str(args.wiener_fifo_n)
    if args.wiener_log:
        os.environ['IEEE80211_WIENER_LOG'] = '1'
    if args.phase139_on:
        os.environ['IEEE80211_H52_2WAY_DEFAULT'] = '1'
    if args.htsig_h_reestimate:
        os.environ['IEEE80211_HTSIG_H_REESTIMATE'] = '1'
    if args.htsig_eq_diag:
        os.environ['IEEE80211_HTSIG_EQ_DIAG'] = '1'
    if args.htsig_h_average:
        os.environ['IEEE80211_HTSIG_H_AVERAGE'] = '1'
    if args.htsig_h_average_safe:
        os.environ['IEEE80211_HTSIG_H_AVERAGE_SAFE'] = '1'
    if args.htsig_fine_rot:
        os.environ['IEEE80211_HTSIG_FINE_ROT'] = '1'
    if args.htsig_pilot_cpe:
        os.environ['IEEE80211_HTSIG_PILOT_CPE'] = '1'

    class ReplayFlowgraph(gr.top_block):
        def __init__(self, args):
            gr.top_block.__init__(self, "p142 Wiener前移 file replay")
            self.args = args

            self.wifi_phy_rx = wifi_phy_hier(
                bandwidth=10e6,
                chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2,
                frequency=5.89e9,
                sensitivity=0.01
            )

            self.file_src = blocks.file_source(gr.sizeof_gr_complex, args.input, repeat=True)
            self.rx_gain_block = blocks.multiply_const_cc(args.rx_scale)
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)
            self.msg_debug_rx = blocks.message_debug()
            self.fcs = FcsLogger()

            self.connect((self.file_src, 0), (self.rx_gain_block, 0))
            self.connect((self.rx_gain_block, 0), (self.wifi_phy_rx, 0))
            self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))

    tb = ReplayFlowgraph(args)
    tb.start()
    print(f"[REPLAY] Running for {args.duration}s on {args.input}")
    start = time.time()
    try:
        while time.time() - start < args.duration:
            elapsed = time.time() - start
            recv = tb.msg_debug_rx.num_messages()
            print(f"\r[REPLAY] Elapsed: {elapsed:.1f}s | Recv: {recv} | FCS_OK={tb.fcs.ok}",
                  end='', flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    print()
    tb.stop()
    tb.wait()
    print(f"[REPLAY] ===== RESULTS =====")
    print(f"[REPLAY] Recv: {tb.msg_debug_rx.num_messages()}")
    print(f"[REPLAY] FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}")


def main():
    parser = argparse.ArgumentParser(description='p142 file replay for Wiener前移 validation')
    parser.add_argument('--input', type=str, required=True, help='Input .fc32 file')
    parser.add_argument('--duration', type=float, default=30, help='Replay duration seconds')
    parser.add_argument('--rx-scale', type=float, default=40.0, help='RX software gain')
    parser.add_argument('--phase139-on', action='store_true', help='Enable 2-way H52 default')
    parser.add_argument('--wiener-on', action='store_true', help='Enable Wiener H52')
    parser.add_argument('--wiener-log', action='store_true', help='Enable Wiener log')
    parser.add_argument('--wiener-fifo-n', type=int, default=4, help='Wiener FIFO depth')
    parser.add_argument('--htsig-h-reestimate', action='store_true', help='Enable HT-SIG pilot H re-estimate (Phase 39)')
    parser.add_argument('--htsig-eq-diag', action='store_true', help='Enable HTSIG_EQ_DIAG dump')
    parser.add_argument('--htsig-h-average', action='store_true', help='Enable HT-SIG H averaging (Phase 118b)')
    parser.add_argument('--htsig-h-average-safe', action='store_true', help='Enable H_AVERAGE safe filter')
    parser.add_argument('--htsig-fine-rot', action='store_true', help='Enable HT-SIG fine rotation candidate search (Phase 95)')
    parser.add_argument('--htsig-pilot-cpe', action='store_true', help='Enable HT-SIG pilot-aided CPE (Phase 95+)')
    args = parser.parse_args()
    sys.exit(internal_run(args))


if __name__ == '__main__':
    main()
