#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 148: RX-only offline replay funnel WITH a drain phase.

Identical chain to p147_replay_funnel.py, but after the head block consumes all
input we let the flowgraph KEEP RUNNING for --drain seconds before tb.stop().
This lets frames buffered deep in the chain (frame_equalizer -> decode_mac)
flush to a terminal decode outcome instead of being cut off at shutdown — the
shutdown race that made decode counts swing across identical runs.

Per-stage markers go to stderr; parse with p148_parse.py / p148_stats.py.
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_HDR_COMP_DISABLE', '1')
os.environ.setdefault('IEEE80211_H52_2WAY_DEFAULT', '0')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks, fft
from gnuradio.fft import window
import pmt
import ieee802_11

MAX_SYMBOLS = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)
BUF = 1000000


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


class RxOnlyReplay(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "RX-only Replay (drain)")
        self.file_source = blocks.file_source(gr.sizeof_gr_complex, args.iq_file, False)
        self.head = blocks.head(gr.sizeof_gr_complex, int(args.nsamp))
        self.sync_short_fused = ieee802_11.sync_short_fused(0.01, 3.0, 1024)
        self.sync_short_fused.set_min_output_buffer(BUF)
        self.sync_short = ieee802_11.sync_short(0.01, 2, True, True)
        self.sync_short.set_min_output_buffer(BUF)
        self.delay = blocks.delay(gr.sizeof_gr_complex, 320)
        self.delay.set_min_output_buffer(BUF)
        self.sync_long = ieee802_11.sync_long(320, True, True)
        self.sync_long.set_min_output_buffer(BUF)
        self.splitter = ieee802_11.ht_symbol_splitter(64, 80, 16)
        self.splitter.set_min_output_buffer(MAX_SYMBOLS * 64 * 8)
        self.s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, 64)
        self.s2v.set_min_output_buffer(BUF)
        self.fft = fft.fft_vcc(64, True, window.rectangular(64), False, 1)
        self.fft.set_min_output_buffer(MAX_SYMBOLS * 64 * 8)
        self.feq = ieee802_11.frame_equalizer(ieee802_11.LS, 5.89e9, 10e6, False, False)
        self.feq.set_min_output_buffer(MAX_SYMBOLS * 52 * 8)
        self.feq.set_output_multiple(52)
        self.dmac = ieee802_11.decode_mac(True, True)
        self.dmac.set_min_output_buffer(MAX_SYMBOLS * 52 * 8)
        self.msg_debug_rx = blocks.message_debug()
        self.fcs = FcsLogger()
        self.connect((self.file_source, 0), (self.head, 0))
        self.connect((self.head, 0), (self.sync_short_fused, 0))
        self.connect((self.sync_short_fused, 0), (self.sync_short, 0))
        self.connect((self.sync_short_fused, 1), (self.sync_short, 1))
        self.connect((self.sync_short_fused, 2), (self.sync_short, 2))
        self.connect((self.sync_short, 0), (self.delay, 0))
        self.connect((self.delay, 0), (self.sync_long, 1))
        self.connect((self.sync_short, 0), (self.sync_long, 0))
        self.connect((self.sync_long, 0), (self.splitter, 0))
        self.connect((self.splitter, 0), (self.s2v, 0))
        self.connect((self.s2v, 0), (self.fft, 0))
        self.connect((self.fft, 0), (self.feq, 0))
        self.connect((self.feq, 0), (self.dmac, 0))
        self.msg_connect((self.dmac, 'out'), (self.msg_debug_rx, 'store'))
        self.msg_connect((self.dmac, 'out'), (self.fcs, 'pdu'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--iq-file', default='/tmp/p146_rxonly_cap.fc32')
    p.add_argument('--nsamp', type=float, default=0, help='samples to process (0=whole file)')
    p.add_argument('--drain', type=float, default=10.0,
                   help='seconds to keep running after head done, to flush in-flight frames')
    p.add_argument('--max-time', type=float, default=120)
    args = p.parse_args()
    if args.nsamp <= 0:
        args.nsamp = os.path.getsize(args.iq_file) // 8
    print(f"[P148] file={args.iq_file} nsamp={int(args.nsamp)} drain={args.drain}s", flush=True)
    tb = RxOnlyReplay(args)
    tb.start()
    t0 = time.time()
    # Phase 1: wait until head has consumed all input samples
    while time.time() - t0 < args.max_time:
        if tb.head.nitems_read(0) >= args.nsamp:
            break
        time.sleep(0.5)
    # Phase 2: DRAIN — keep running so deep-pipeline frames flush through decode_mac
    t_d = time.time()
    while time.time() - t_d < args.drain:
        time.sleep(1.0)
    tb.stop()
    tb.wait()
    print(f"[P148] done Recv={tb.msg_debug_rx.num_messages()} fcs_ok={tb.fcs.ok} fcs_fail={tb.fcs.fail}",
          flush=True)


if __name__ == '__main__':
    main()
