#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 146: front-end vs downstream bisection of the RX-chain throughput wall.

perf is blocked (perf_event_paranoid=4), so this isolates the bottleneck by
measuring throughput of incremental sub-chains on REAL USRP IQ:

  depth 0: src -> head -> null                                  (baseline: disk read)
  depth 1: ... -> sync_short_fused -> sync_short -> sync_long -> null   (front-end)

If depth 1 sustains >>20 MHz, the front-end (sync_*) is NOT the bottleneck and
the wall is downstream (splitter/FFT/frame_equalizer). If depth 1 is also slow,
the front-end itself is the wall. Buffers match wifi_phy_hier (1M).
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks, fft
from gnuradio.fft import window
import ieee802_11

BUF = 1000000
MAX_SYMBOLS = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)


class Bisect(gr.top_block):
    def __init__(self, nsamp, iq_file, depth, with_idle_hier=False, with_ofdm_cp=False):
        gr.top_block.__init__(self, "Bisect")
        self.src = blocks.file_source(gr.sizeof_gr_complex, iq_file, False)
        self.head = blocks.head(gr.sizeof_gr_complex, nsamp)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.vnull = blocks.null_sink(gr.sizeof_gr_complex * 64)  # vector sink for FFT out
        self.connect((self.src, 0), (self.head, 0))

        # Optionally instantiate a FULL wifi_phy_hier (TX+RX) idle in the SAME
        # flowgraph, to test whether the mere presence of the hier's TX-path blocks
        # (and scheduler interaction) throttles an independent fast RX chain.
        if with_idle_hier:
            sys.path.insert(0, '/home/hy/gr-ieee802-11')
            from wifi_phy_hier import wifi_phy_hier
            self.idle_src = blocks.null_source(gr.sizeof_gr_complex)
            self.idle_hier = wifi_phy_hier(bandwidth=10e6, chan_est=ieee802_11.LS,
                                           encoding=ieee802_11.BPSK_1_2,
                                           frequency=5.89e9, sensitivity=0.01)
            self.idle_null = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect((self.idle_src, 0), (self.idle_hier, 0))
            self.connect((self.idle_hier, 0), (self.idle_null, 0))

        # Optionally instantiate a single idle ofdm_cyclic_prefixer (the prime
        # suspect: 2.25M-sample min_output_buffer + waits for a packet_len tag that
        # never arrives). Tests whether THIS block alone stalls the flowgraph.
        if with_ofdm_cp:
            from gnuradio import digital
            self.cp_src = blocks.null_source(gr.sizeof_gr_complex * 64)  # 64-vector input
            self.cp = digital.ofdm_cyclic_prefixer(64, 64 + 16, 2, "packet_len")
            self.cp.set_min_output_buffer(MAX_SYMBOLS * 52 * 8 * 10)
            self.cp_null = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect((self.cp_src, 0), (self.cp, 0))
            self.connect((self.cp, 0), (self.cp_null, 0))

        if depth == 0:
            self.connect((self.head, 0), (self.null, 0))
            return

        # front-end (depth >= 1)
        self.ssf = ieee802_11.sync_short_fused(0.01, 3.0, 1024)
        self.ssf.set_min_output_buffer(BUF)
        self.ss = ieee802_11.sync_short(0.01, 2, True, True)
        self.ss.set_min_output_buffer(BUF)
        self.dly = blocks.delay(gr.sizeof_gr_complex, 320)
        self.dly.set_min_output_buffer(BUF)
        self.sl = ieee802_11.sync_long(320, True, True)
        self.sl.set_min_output_buffer(BUF)
        self.connect((self.head, 0), (self.ssf, 0))
        self.connect((self.ssf, 0), (self.ss, 0))
        self.connect((self.ssf, 1), (self.ss, 1))
        self.connect((self.ssf, 2), (self.ss, 2))
        self.connect((self.ss, 0), (self.dly, 0))
        self.connect((self.dly, 0), (self.sl, 1))
        self.connect((self.ss, 0), (self.sl, 0))
        if depth == 1:
            self.connect((self.sl, 0), (self.null, 0))
            return

        # + splitter (depth >= 2)
        self.split = ieee802_11.ht_symbol_splitter(64, 80, 16)
        self.split.set_min_output_buffer(MAX_SYMBOLS * 64 * 8)
        self.connect((self.sl, 0), (self.split, 0))
        if depth == 2:
            self.connect((self.split, 0), (self.null, 0))
            return

        # + stream_to_vector + FFT (depth >= 3)
        self.s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, 64)
        self.s2v.set_min_output_buffer(BUF)
        self.fftblk = fft.fft_vcc(64, True, window.rectangular(64), False, 1)
        self.fftblk.set_min_output_buffer(MAX_SYMBOLS * 64 * 8)
        self.connect((self.split, 0), (self.s2v, 0))
        self.connect((self.s2v, 0), (self.fftblk, 0))
        if depth == 3:
            self.connect((self.fftblk, 0), (self.vnull, 0))
            return

        # + frame_equalizer (depth >= 4)
        self.feq = ieee802_11.frame_equalizer(ieee802_11.LS, 5.89e9, 10e6, False, False)
        self.feq.set_min_output_buffer(MAX_SYMBOLS * 52 * 8)
        self.feq.set_output_multiple(52)
        self.connect((self.fftblk, 0), (self.feq, 0))
        if depth == 4:
            self.connect((self.feq, 0), (self.null, 0))
            return

        # + decode_mac (depth >= 5) — full RX chain endpoint (matches wifi_phy_hier)
        self.dmac = ieee802_11.decode_mac(True, True)
        self.dmac.set_min_output_buffer(MAX_SYMBOLS * 52 * 8)
        self.msgdbg = blocks.message_debug()
        self.connect((self.feq, 0), (self.dmac, 0))
        self.msg_connect((self.dmac, 'out'), (self.msgdbg, 'store'))



def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', required=True)
    p.add_argument('--samples', type=int, default=20_000_000)
    p.add_argument('--depth', type=int, default=1)
    p.add_argument('--with-idle-hier', action='store_true',
                   help='instantiate a full idle wifi_phy_hier in the same flowgraph')
    p.add_argument('--with-ofdm-cp', action='store_true',
                   help='instantiate a single idle ofdm_cyclic_prefixer (prime suspect)')
    args = p.parse_args()

    tb = Bisect(args.samples, args.file, args.depth, args.with_idle_hier, args.with_ofdm_cp)
    print(f"[BISECT] depth={args.depth} samples={args.samples} ...", flush=True)
    t0 = time.time()
    tb.start()
    tb.wait()
    dt = time.time() - t0
    mhz = args.samples / dt / 1e6
    extra = ""
    if args.depth >= 5 and hasattr(tb, 'msgdbg'):
        extra = f"  decoded_msgs={tb.msgdbg.num_messages()}"
    print(f"[BISECT] depth={args.depth}: {args.samples} samples in {dt:.2f}s = {mhz:.3f} MHz{extra}",
          flush=True)


if __name__ == '__main__':
    main()
