#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 T6: INSTRUMENTED RX-only realtime test.

Extends test_usrp_rxonly_realtime.py (Phase 146 breakthrough) with:
  - TX strobe auto-start/stop -> KNOWN sent-frame count (clean denominator).
  - UHD underflow/overflow captured from stderr (no async msg port on this
    gr-uhd) -> tests the TX-underflow hypothesis directly.
  - Fixed rx_scale SWEEP (no hidden digital AGC) -> separates RX saturation
    from underflow from scheduler-lag.
  - Optional capture (default OFF) -> tests the scheduler-lag hypothesis
    (disk+I/O load slowing the decode chain -> 2-vs-21 gap).
  - Counts FCS from BOTH PDU logger AND C++ stderr [DECODE_SUCCESS]/[DECODE_FAIL].

Run a sweep; each config prints a one-line funnel. Compare offline baseline=21.
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
# Phase 159b (2026-08-05): TIMING_OFFSET_APPLY flipped 1 -> 0. The Phase 34
# retroactive delta correction (frame_equalizer_impl.cc:7466) rewrites the
# cached L-SIG/HT-SIG symbols at counter=4 using a per-frame delta estimate
# from the noise-dominated H52 phase slope; when the estimate is wrong (~50%
# of frames) it DESTROYS the otherwise clean constellation (evidence: pre-delta
# dump constellation template-match hamming 0-3 vs post-delta viterbi input
# hamming 25/48). N=8 ABAB: DS 231.9 -> 453.8 (+221.9, p<1e-4), arrival
# 240.6 -> 464.0 (~100% of sent), decode-of-arrived 97.8%. The "1.77 rad LO
# wall" was substantially this correction's artifact. C++ default was already
# OFF; loopback regression always ran delta-off.
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '0')
os.environ.setdefault('IEEE80211_HDR_COMP_DISABLE', '1')
os.environ.setdefault('IEEE80211_H52_2WAY_DEFAULT', '0')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')
# Phase 154b: sweep optimum MIN_PLATEAU=24 (broad plateau 24-32, cliff at 48).
# USRP batch: M=2 -> 59.5, M=16 -> 124.5, M=24 -> 200.0, M=32 -> 202.3,
# M=48 -> 128.7 DECODE_SUCCESS/45s. 24 chosen over 32: same mean, tighter
# std (8.5 vs 15.7), more margin from the M=48 real-frame-miss cliff.
# NOTE: 10 MHz loopback needs M<=16 (8-sample L-STF period artifact) — the
# loopback regression gate runs with default M=2 and is unaffected.
os.environ.setdefault('IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE', '24')
# Phase 159: trigger-strength margin 2.5 CONFIRMED (N=8 interleaved ABAB
# 2026-08-04, +55.8 DS p=0.0001, arrival +46.8 p=0.0002). Kills 99.6% of
# noise-trap episodes (5015->18) so sync_long's diet is real-frame-only;
# chain success 36%->51%. Real frames trigger at max_cor>=500 (2500x gate),
# traps at 0.26-0.36 (1.3-1.8x) — the 0.4-10 band is empty, so 2.5 loses no
# real frames. Env-overridable; C++ default unchanged (1.0).
os.environ.setdefault('IEEE80211_SYNC_SHORT_TRIGGER_MARGIN', '2.5')
# Phase 162 (2026-08-06): data-path soft-decision viterbi (|H|^2-weighted LLR)
# CONFIRMED (N=8 interleaved ABAB: terminal failures 5.25 -> 2.0/45s, -62%,
# paired t p=0.0047; arrival unchanged p=0.83). Attacks the Phase 161 band-edge
# SC fade tail (min|H| 13.7 vs 28.7 at SC -28/-27 -> hard-viterbi budget
# overflow). LLR = Re(eq)*|H|^2, scale-invariant (no sigma^2 estimate).
# Env-overridable; C++ default unchanged (OFF).
os.environ.setdefault('IEEE80211_DATA_SOFT_VITERBI', '1')
# Phase 165c (2026-08-11): L-SIG 4-rot candidate search (90deg step, 2 inv).
# systematic-debugging root cause: the L-SIG candidate loop defaulted to
# n_rot=1 (rot=0 only), so frames whose L-SIG constellation was rotated ~90
# degrees by instantaneous phase noise always failed hard-decision. 4-rot
# rescues them. Cable -20dB 2x300s: PDU 2981/2980 -> 2990/2983 (99.35 ->
# 99.55% stable). FINE_ROT (8x45deg) flat vs 4-rot (99.53%) — residual
# rotations are 90-degree multiples, 4-rot covers fully. Env-overridable;
# C++ default unchanged (n_rot=1).
os.environ.setdefault('IEEE80211_LSIG_VITERBI_CANDIDATE', '1')

from gnuradio import gr, blocks, uhd, fft
from gnuradio.fft import window
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier

MAX_SYMBOLS = int(5 + 1 + ((16 + 800 * 8 + 6) * 2) / 24)
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


class BurstTagger(gr.basic_block):
    """Phase 170 option A: convert packet_len tags into UHD timed-burst tags
    (tx_sob / tx_eob / tx_time). The USRP then buffers each whole packet in the
    FPGA and transmits it atomically at tx_time, eliminating the mid-packet
    underflow that tears ~0.5% of frames' preambles into silence holes.

    tx_time is scheduled with a fixed lead so the host always finishes
    delivering the packet before transmission starts.
    """
    def __init__(self, samp_rate=20e6, lead_s=0.005, period_s=0.1015):
        gr.basic_block.__init__(self, name='burst_tagger',
                                in_sig=[np.complex64], out_sig=[np.complex64])
        self.samp_rate = samp_rate
        self.lead_s = lead_s
        self.period_s = period_s
        self.next_tx = 0.05  # first burst at 50 ms (USRP time reset to 0 at start)

    def general_work(self, input_items, output_items):
        inp = input_items[0]
        out = output_items[0]
        n = min(len(inp), len(out))
        if n <= 0:
            return 0
        nread = self.nitems_read(0)
        nwritten = self.nitems_written(0)
        out[:n] = inp[:n]

        tags = self.get_tags_in_window(0, 0, n, pmt.intern("packet_len"))
        for t in tags:
            rel = int(t.offset) - nread
            if rel < 0 or rel >= n:
                continue
            plen = pmt.to_long(t.value)
            abs_start = nwritten + rel
            sec = int(self.next_tx)
            frac = self.next_tx - sec
            self.add_item_tag(0, abs_start, pmt.intern("tx_sob"), pmt.PMT_T)
            self.add_item_tag(0, abs_start, pmt.intern("tx_time"),
                              pmt.make_tuple(pmt.from_uint64(sec), pmt.from_double(frac)))
            self.add_item_tag(0, abs_start + plen - 1, pmt.intern("tx_eob"), pmt.PMT_T)
            self.next_tx += self.period_s

        self.consume(0, n)
        return n


class FcsLogger(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="fcs", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)
        self.ok = 0
        self.fail = 0
        self.lens = []

    def handle(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        size = len(pmt.u8vector_elements(data)) if pmt.is_u8vector(data) else 0
        self.lens.append(size)
        if crc:
            self.ok += 1
        else:
            self.fail += 1


class InstrumentedRxOnly(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "Instrumented RX-only")

        # ---- TX (wifi_phy_hier; idle RX path has no ofdm_cyclic_prefixer) ----
        self.msg_strobe = blocks.message_strobe(pmt.intern('x' * a.len), int(a.interval))
        self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
        self.enc_strip = EncodingStripper()
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01,
            use_ldpc=a.ldpc)
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        # Phase 170 option A: UHD tagged-stream burst mode. The hier already emits
        # packet_len length tags (used by ofdm_cyclic_prefixer). Passing
        # tsb_tag_name="packet_len" makes the USRP sink treat the input as a bursty
        # tagged stream, so UHD knows each packet's boundary and does NOT terminate
        # the burst early on a host scheduler gap (the mechanism that tore ~0.5% of
        # frames' preambles into silence holes). RX operating point unchanged.
        # Opt-in via IEEE80211_TX_BURST_TSB=1 for A/B.
        import os as _os
        _tsb = "packet_len" if _os.environ.get('IEEE80211_TX_BURST_TSB') == '1' else ""
        self.rx_file_preplay = getattr(a, 'rx_file', '')
        if self.rx_file_preplay:
            # Pure offline RX replay: no USRP at all. Route live TX hier to null.
            self.uhd_sink = None
            self.tx_null_sink0 = blocks.null_sink(gr.sizeof_gr_complex)
        else:
            self.uhd_sink = uhd.usrp_sink(
                "addr=192.168.10.2,send_buff_size=1048576",
                uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
                _tsb)
            if _tsb:
                sys.stderr.write("[P170] UHD burst mode ENABLED (tsb_tag_name=packet_len)\n")
            self.uhd_sink.set_samp_rate(a.rate * 1e6)
            self.uhd_sink.set_center_freq(a.freq * 1e6, 0)
            self.uhd_sink.set_gain(a.tx_gain, 0)
            self.uhd_sink.set_subdev_spec("A:0", 0)
            self.uhd_sink.set_antenna("TX/RX", 0)
            self.uhd_sink.set_clock_source("internal")
            self.uhd_sink.set_time_source("internal")

        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.enc_strip, 'pdu'))
        self.msg_connect((self.enc_strip, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        # Phase 165: TX software attenuation (equivalent of an external pad).
        # Bare-cable direct connect at tx-gain 0 sends ~+5 dBm into RX2, 20 dB
        # above the UBX-160 -15 dBm linearity limit -> overdrive distortion.
        # A digital tx-scale < 1 reduces the DAC output amplitude, so the RX
        # front-end stays in its linear region. rx-gain/scale calibration is
        # untouched. Default 1.0 (no change); env/arg override e.g. 0.1 (-20dB).
        self.tx_scale = getattr(a, 'tx_scale', 1.0)
        self.tx_att = blocks.multiply_const_cc(self.tx_scale)

        # Phase 170: file-based TX (steady-rate, eliminates USRP underflow stalls).
        # Root cause found in P170 dissection: the live msg_strobe->mac->hier TX
        # chain delivers packets with scheduler jitter; the continuously-clocked
        # uhd_sink underflows mid-burst ~1x/frame, and ~0.5% of the time the
        # underflow lands mid-packet -> silence hole tears the L-SIG -> RX chain
        # loss. Feeding a pre-computed steady-rate waveform removes all jitter.
        # The waveform file already has tx_scale baked in, so connect directly
        # to uhd_sink (skip tx_att).
        self.tx_file = getattr(a, 'tx_file', '')
        # Route the live hier through tx_att always (keeps ports satisfied);
        # where tx_att's output goes depends on mode.
        self.connect((self.wifi_phy_tx, 0), (self.tx_att, 0))
        if self.rx_file_preplay:
            # offline replay: no USRP. TX chain output -> null sink.
            self.connect((self.tx_att, 0), (self.tx_null_sink0, 0))
        elif self.tx_file:
            self.tx_file_source = blocks.file_source(gr.sizeof_gr_complex, self.tx_file, True)
            self.connect((self.tx_file_source, 0), (self.uhd_sink, 0))
            # live TX hier output (via tx_att) -> null sink (doesn't reach USRP)
            self.tx_null_sink = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect((self.tx_att, 0), (self.tx_null_sink, 0))
            sys.stderr.write("[P170] file-based TX ENABLED: %s (steady-rate, live chain -> null_sink)\n"
                             % self.tx_file)
        else:
            self.connect((self.tx_att, 0), (self.uhd_sink, 0))

        # Phase 170: optional TX-chain capture to localize mid-preamble stalls.
        # If the silence hole (found in RX captures of lost frames) appears in
        # this TX-side file, the GR TX chain produced it; if not, it's UHD/USRP.
        if getattr(a, 'tx_capture', ''):
            self.tx_file_sink = blocks.file_sink(gr.sizeof_gr_complex, a.tx_capture, False)
            self.connect((self.tx_att, 0), (self.tx_file_sink, 0))

        # ---- RX source ----
        # Phase 170: optional file-based RX replay (--rx-file) routes a captured
        # IQ file through the EXACT realtime RX chain (sync_short_fused -> sync_short
        # -> sync_long -> splitter -> FFT -> equalizer -> decode_mac), offline.
        # This replays a capture through the realtime decode path without the USRP,
        # isolating signal-quality issues from realtime-scheduling issues.
        self.rx_file = getattr(a, 'rx_file', '')
        if self.rx_file:
            self.rx_file_source = blocks.file_source(gr.sizeof_gr_complex, self.rx_file, False)
            sys.stderr.write("[P170] RX file replay: %s\n" % self.rx_file)
            rx_source_out = self.rx_file_source
            # P171: offline replay has no TX purpose. Stop the msg strobe so the
            # mac/mapper/TX hier stays idle (it otherwise burns a core spinning
            # null_source into the TX chain and spews TX debug logs).
            self.msg_disconnect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            sys.stderr.write("[P170] RX file replay: msg_strobe DISCONNECTED\n")
        else:
            self.uhd_src = uhd.usrp_source(
                "addr=192.168.10.2",
                uhd.stream_args(cpu_format="fc32", otw_format="sc16",
                                args=uhd.device_addr("recv_buff_size=16777216,num_recv_frames=256"),
                                channels=[0]))
            self.uhd_src.set_subdev_spec(a.rx_subdev, 0)
            self.uhd_src.set_antenna("RX2", 0)
            self.uhd_src.set_gain(a.rx_gain, 0)
            self.uhd_src.set_center_freq(a.freq * 1e6, 0)
            self.uhd_src.set_bandwidth(a.rate * 1e6, 0)
            self.uhd_src.set_samp_rate(a.rate * 1e6)
            self.uhd_src.set_clock_source("internal")
            self.uhd_src.set_time_source("internal")
            rx_source_out = self.uhd_src

        # FIXED digital scale (no AGC; sweep externally)
        self.gain = blocks.multiply_const_cc(a.rx_scale)
        if self.rx_file:
            # P171: offline replay must mimic USRP chunk granularity. The
            # realtime uhd_source delivers 59-2000-sample chunks; file_source
            # delivers ~400k-sample chunks. sync_short's adaptive threshold
            # (p90 over a 4096-sample trailing window, recomputed once per
            # call) and the sync state machines are chunk-partition-dependent
            # (P148/P151c): with huge chunks the threshold goes stale ->
            # false-detection storm (~10 starts per frame period vs 1
            # realtime) -> equalizer churns garbage -> chain CPU-saturated at
            # ~0.22x realtime -> ~4/800 decodes. Bound the chunk size at the
            # first RX-chain buffer (gain's output feeds sync_short_fused).
            self.gain.set_min_output_buffer(4096)

        # ---- RX-ONLY decode chain ----
        self.sync_short_fused = ieee802_11.sync_short_fused(0.01, 3.0, 1024)
        self.sync_short_fused.set_min_output_buffer(BUF)
        # MIN_PLATEAU: Phase 154b default 24 (sweep optimum); env override
        # matches wifi_phy_hier.py convention (Phase 89). Real L-STF is
        # 160 samples at 20 MHz (10x16) -> boxcar plateau ~128; noise
        # excursions are capped at ~16 (boxcar window), so 24 sits between.
        min_plateau = int(os.environ.get('IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE', '24'))
        self.sync_short = ieee802_11.sync_short(0.01, min_plateau, True, True)
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

        self.connect((rx_source_out, 0), (self.gain, 0))
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

        if a.capture:
            self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, a.capture, False)
            self.connect((self.gain, 0), (self.file_sink, 0))


def measure_window(tb, scale, a):
    """Set rx_scale at runtime (no flowgraph rebuild) and measure one window."""
    tb.gain.set_k(scale)
    pdu0 = tb.msg_debug_rx.num_messages()
    ok0, fail0 = tb.fcs.ok, tb.fcs.fail
    time.sleep(a.run)
    pdu1 = tb.msg_debug_rx.num_messages()
    ok1, fail1 = tb.fcs.ok, tb.fcs.fail
    est_sent = int(round(a.run * 1000.0 / a.interval))
    print(f"[RESULT] scale={scale} rx_gain={a.rx_gain} cap={'ON' if a.capture else 'off'} "
          f"window={a.run}s est_sent~{est_sent} PDU={pdu1-pdu0} "
          f"FCS_OK={ok1-ok0} FCS_FAIL={fail1-fail0}", flush=True)
    return ok1 - ok0, fail1 - fail0, pdu1 - pdu0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5250)
    p.add_argument('--rate', type=float, default=20)
    p.add_argument('--tx-gain', type=float, default=0)
    p.add_argument('--tx-scale', type=float, default=1.0, help='TX digital attenuation (1.0 = none; 0.1 = -20dB)')
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='A:0')
    p.add_argument('--rx-scale', type=float, default=40.0, help='initial digital scale (sweep overrides via set_k)')
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=10)
    p.add_argument('--capture', type=str, default='')
    p.add_argument('--tx-capture', type=str, default='', help='capture TX chain output to file (localize TX stalls)')
    p.add_argument('--tx-file', type=str, default='', help='file-based TX waveform (steady-rate, eliminates underflow stalls)')
    p.add_argument('--rx-file', type=str, default='', help='RX replay from IQ file through realtime decode chain (offline)')
    p.add_argument('--scales', type=str, default='40', help='comma list of rx_scale to sweep')
    p.add_argument('--ldpc', action='store_true', help='Enable LDPC encoding (default: BCC)')
    args = p.parse_args()
    scales = [float(x) for x in args.scales.split(',')]
    print(f"[P147-T6] scales={scales} capture={'ON:'+args.capture if args.capture else 'off'} "
          f"(build-once + set_k sweep)", flush=True)
    # Build the flowgraph ONCE; sweep rx_scale via gain.set_k() (avoids USRP
    # re-init segfault / RFNoC graph corruption from repeated construct+destroy).
    tb = InstrumentedRxOnly(args)
    tb.start()
    time.sleep(args.warmup)
    print(f"[P147-T6] warmup {args.warmup}s done, starting sweep", flush=True)
    summary = []
    for s in scales:
        r = measure_window(tb, s, args)
        summary.append((s,) + r)
    tb.stop()
    tb.wait()
    print("\n========== SWEEP SUMMARY (PDU-based) ==========", flush=True)
    print("scale  FCS_OK  FCS_FAIL  PDU", flush=True)
    tot_ok = 0
    for s, ok, fail, pdu in summary:
        print(f"{s:6.1f}  {ok:6d}  {fail:8d}  {pdu:5d}", flush=True)
        tot_ok += ok
    print(f"[P147-T6] total PDU FCS_OK across sweep = {tot_ok}", flush=True)
    print(f"[P147-T6] NOTE: compare vs stderr DECODE_SUCCESS count (ground truth) "
          f"to detect message-queue undercounting.", flush=True)


if __name__ == '__main__':
    main()
