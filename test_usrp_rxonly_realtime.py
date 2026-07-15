#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 146 FIX: RX-only realtime test (no idle TX-path ofdm_cyclic_prefixer).

Root cause (Phase 146): test_usrp_minimal_loopback.py uses wifi_phy_hier (a FULL
transceiver) for RX. Its IDLE TX path contains a tag-starved ofdm_cyclic_prefixer
(waits for a packet_len tag that never arrives) which STALLS the whole GNU Radio
flowgraph ~5000x, truncating the capture and starving the RX decode chain.

This script replaces wifi_phy_rx (full hier) with an RX-ONLY decode chain wired
manually from the same blocks (proven to run 207-263 MHz AND decode frames in
p146_bisect.py). TX still uses wifi_phy_hier (works: its idle RX path has no
ofdm_cyclic_prefixer). Expected: complete realtime capture + realtime FCS_OK.

Usage:
  python test_usrp_rxonly_realtime.py --freq 5250 --tx-gain 0 --duration 10 [--capture /tmp/x.fc32]
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

# Phase 145c winning decoder config + Phase 89 sync_short + Phase 18/34
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_HDR_COMP_DISABLE', '1')
os.environ.setdefault('IEEE80211_H52_2WAY_DEFAULT', '0')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks, uhd, fft, digital
from gnuradio.fft import window
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier

MAX_SYMBOLS = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)  # 541, matches wifi_phy_hier
BUF = 1000000


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
            print("*** FCS OK ***", flush=True)
        else:
            self.fail += 1


class RxOnlyRealtime(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "RX-only Realtime")

        # ===== TX (wifi_phy_hier; its idle RX path has NO ofdm_cyclic_prefixer) =====
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

        # ===== RX source =====
        self.uhd_src = uhd.usrp_source(
            "addr=192.168.10.2",
            uhd.stream_args(cpu_format="fc32", otw_format="sc16",
                            args=uhd.device_addr("recv_buff_size=16777216,num_recv_frames=256"),
                            channels=[0]))
        self.uhd_src.set_subdev_spec(args.rx_subdev, 0)
        self.uhd_src.set_antenna("RX2", 0)
        self.uhd_src.set_gain(args.rx_gain, 0)
        self.uhd_src.set_center_freq(args.freq * 1e6, 0)
        self.uhd_src.set_bandwidth(args.rate * 1e6, 0)
        self.uhd_src.set_samp_rate(args.rate * 1e6)
        self.uhd_src.set_clock_source("internal")
        self.uhd_src.set_time_source("internal")

        self.gain = blocks.multiply_const_cc(args.rx_scale)

        # ===== RX-ONLY decode chain (manual wiring = p146_bisect depth 5) =====
        # NO idle TX path, NO ofdm_cyclic_prefixer -> no scheduler stall.
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

        # RX wiring: source -> gain -> decode chain
        self.connect((self.uhd_src, 0), (self.gain, 0))
        self.connect((self.gain, 0), (self.sync_short_fused, 0))
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

        # optional capture tap (shares gain output with decode chain — but decode
        # chain is now fast, so no backpressure stall)
        if args.capture:
            self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.capture, False)
            self.connect((self.gain, 0), (self.file_sink, 0))

        print(f"[RXONLY] freq={args.freq}MHz rate={args.rate} tx_gain={args.tx_gain} "
              f"rx_gain={args.rx_gain} rx_subdev={args.rx_subdev} capture={args.capture or 'off'}",
              flush=True)


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
    p.add_argument('--warmup', type=float, default=30)
    p.add_argument('--capture', type=str, default='')
    args = p.parse_args()

    tb = RxOnlyRealtime(args)
    tb.start()
    print(f"[RXONLY] warmup {args.warmup}s ...", flush=True)
    time.sleep(args.warmup)
    print(f"[RXONLY] running {args.duration}s ...", flush=True)
    t0 = time.time()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(1.0)
            el = time.time() - t0
            print(f"\r[RXONLY] t={el:.1f}s Recv={tb.msg_debug_rx.num_messages()} "
                  f"FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}", end='', flush=True)
    except KeyboardInterrupt:
        pass
    print()
    tb.stop()
    tb.wait()
    print(f"\n[RXONLY] ===== RESULTS =====")
    print(f"[RXONLY] Recv={tb.msg_debug_rx.num_messages()} "
          f"FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}")
    if args.capture and os.path.exists(args.capture):
        size = os.path.getsize(args.capture)
        print(f"[RXONLY] capture: {args.capture} = {size} bytes = {size/8/ (args.rate*1e6):.2f}s "
              f"({'COMPLETE' if size/8 >= 0.9*args.duration*args.rate*1e6 else 'TRUNCATED'})")


if __name__ == '__main__':
    main()
