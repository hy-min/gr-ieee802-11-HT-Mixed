#!/home/hy/conda/envs/gnuradio/bin/python
"""
Minimal USRP loopback test - NO GUI, NO DEBUG LOGS
Disables C-layer stderr to eliminate fprintf flood.
Captures: sent count, recv count, FCS status
Usage: python test_usrp_minimal_loopback.py --duration 10

Phase 52: Cross-board support via subprocess stderr suppression
(lifts pattern from test_usrp_air_loopback.py)
"""
import argparse
import os
import sys
import time
import signal
import subprocess as sp

# === Subprocess wrapper REMOVED in Phase 53 ===
# Phase 52 used a subprocess+stderr wrapper (lifted from test_usrp_air_loopback.py)
# to suppress C-layer fprintf noise. Phase 53 testing revealed this approach
# BREAKS UHD streaming on cross-board and same-board: when stderr is redirected
# (to /dev/null OR to a file), the RX chain produces 0 HT_SIG_CAND events
# vs 16 events with direct stdout/stderr. The stderr redirection somehow
# starves the UHD async stream.
#
# Phase 53: Run test directly, accept stderr noise. Use `2>/dev/null` shell
# redirect if you want to suppress.
# Note: --internal-run flag is preserved for backward compatibility with
# legacy scripts that may invoke this script as a subprocess.


def internal_run(args):
    """Actual test logic - runs in subprocess with stderr suppressed.
    All gnuradio imports and class definitions scoped to this function.
    """
    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'

    # === Phase 110: Bake in standard env vars (matches examples/capture_usrp_loopback_to_file.py) ===
    # Phase 18: L-SIG viterbi force rate=0xD (HT-Mixed)
    os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
    # Phase 34: δ timing offset correction at frame_equalizer
    os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
    # Phase 89: sync_short boxcar detector (replaces REFUTED MA(48)/MA(64) ratio)
    os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
    # Phase 89: sync_short adaptive threshold (median*10 with 3.0 startup gate)
    os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

    # Phase 112: T7e (opt-in via --t7e-on). Default OFF to preserve baseline.
    if args.t7e_on:
        os.environ['IEEE80211_T7E_MULTISYM_H'] = '1'
        os.environ['IEEE80211_T7E_MULTISYM_K'] = str(args.t7e_k)
        print(f"[TEST] T7e ENABLED K={args.t7e_k} "
              "(IEEE80211_T7E_MULTISYM_H=1, IEEE80211_T7E_MULTISYM_K={})".format(args.t7e_k),
              flush=True)

    # Phase 114: T5.A + T3.B (Step 1) + T4.D (Step 2) stack (opt-in via --uhd-tune).
    # Step 1 re-tests Phase 77c SNR-weighted L-LTF averaging under T5.A's cleaned-up
    # analog chain state. Phase 77c was REFUTED before T5.A; signal quality has
    # changed since (LSIG_DECODE OK 1→11). Default OFF preserves baseline.
    if args.uhd_tune:
        os.environ['IEEE80211_H52_SNR_WEIGHTED'] = '1'
        # Step 2 (HT-LTF 2x averaging) requires explicit --htltf-avg flag
        # to keep Step 1 test isolation. Default OFF.
        if args.htltf_avg:
            os.environ['IEEE80211_HTLTF_AVG'] = '1'
            # Phase 114 root cause diag: enable T4D diagnostic logging
            if os.environ.get('IEEE80211_HTLTF_AVG_DEBUG') is None:
                pass  # Don't auto-enable; user can set manually
            print(f"[TEST] Phase 114 Step 2 ENABLED: "
                  "IEEE80211_HTLTF_AVG=1 (HT-LTF 2x averaging)")
        print(f"[TEST] Phase 114 Step 1 ENABLED: "
              "IEEE80211_H52_SNR_WEIGHTED=1 (L-LTF0+L-LTF1 SNR-weighted)")

    # Phase 137: stable-null-aware masking (opt-in via --phase137-on).
    # Default OFF preserves baseline.
    if args.phase137_on:
        os.environ['IEEE80211_HTSIG_NULL_SCS'] = '-21,-13,-7,7,21'
        os.environ['IEEE80211_HTSIG_NULL_PILOT_MASK'] = '1'
        os.environ['IEEE80211_HT_PER_SYMBOL_CPE'] = '1'  # required for pilot CPE code path
        print(f"[TEST] Phase 137 ENABLED: "
              "IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 "
              "IEEE80211_HTSIG_NULL_PILOT_MASK=1", flush=True)

    # Phase 138: H52 frequency-domain low-pass filter (opt-in via --phase138-on).
    # Default OFF preserves baseline.
    if args.phase138_on:
        os.environ['IEEE80211_H52_FREQ_LOWPASS'] = '1'
        os.environ['IEEE80211_H52_FREQ_LOWPASS_K'] = str(args.phase138_k)
        print(f"[TEST] Phase 138 ENABLED: "
              "IEEE80211_H52_FREQ_LOWPASS=1 "
              f"IEEE80211_H52_FREQ_LOWPASS_K={args.phase138_k}", flush=True)

    # Phase 139: 2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi
    if args.phase139_on:
        os.environ['IEEE80211_H52_2WAY_DEFAULT'] = '1'
        print(f"[TEST] Phase 139 ENABLED: IEEE80211_H52_2WAY_DEFAULT=1 "
              f"(2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi)",
              flush=True)

    if args.phase139_3way:
        os.environ['IEEE80211_HT_SIG_PILOT_REFINE'] = '1'
        print(f"[TEST] Phase 139 3-way ENABLED: IEEE80211_HT_SIG_PILOT_REFINE=1",
              flush=True)
    elif args.phase139_4way:
        os.environ['IEEE80211_HT_SIG_PILOT_REFINE'] = '2'
        print(f"[TEST] Phase 139 4-way ENABLED: IEEE80211_HT_SIG_PILOT_REFINE=2",
              flush=True)

    # Phase 140: 2-way + L-SIG cross-frame H52
    # Default OFF (preserves Phase 139 2-way default baseline).
    # N=0 is a no-op (2-way default since Phase 139); N in {1,2,4,8} for FIFO averaging.
    if args.phase140_on is not None:
        os.environ['IEEE80211_PHASE140_ON'] = str(args.phase140_on)
        print(f"[TEST] Phase 140 2-way + L-SIG cross-frame H52 ENABLED: "
              f"IEEE80211_PHASE140_ON={args.phase140_on} "
              f"(N=0 is no-op; N in {{1,2,4,8}} for FIFO avg after 2-way baseline)",
              flush=True)

    if args.phase140_log:
        os.environ['IEEE80211_LSIG_H52_CROSS_FRAME_LOG'] = '1'
        print(f"[TEST] Phase 140 cross-frame diagnostic log ENABLED: "
              f"IEEE80211_LSIG_H52_CROSS_FRAME_LOG=1",
              flush=True)

    # Phase 141: Wiener H52 estimation (default OFF preserves baseline)
    if args.wiener_on:
        os.environ['IEEE80211_WIENER_H52'] = '1'
        os.environ['IEEE80211_WIENER_FIFO_N'] = str(args.wiener_fifo_n)
        print(f"[TEST] Phase 141 Wiener ENABLED: "
              f"IEEE80211_WIENER_H52=1 FIFO_N={args.wiener_fifo_n}",
              flush=True)
    if args.wiener_log:
        os.environ['IEEE80211_WIENER_LOG'] = '1'
        print(f"[TEST] Phase 141 Wiener diagnostic log ENABLED", flush=True)

    # Phase 143: BPSK-HT-SIG fallback (non-standard, TX/RX coordinated).
    if args.htsig_bpsk_fallback:
        os.environ['IEEE80211_HTSIG_BPSK_FALLBACK'] = '1'
        print("[TEST] Phase 143 BPSK-HT-SIG fallback ENABLED", flush=True)

    from gnuradio import gr, blocks, uhd
    import pmt
    import ieee802_11

    sys.path.insert(0, '/home/hy/gr-ieee802-11')
    sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
    from wifi_phy_hier import wifi_phy_hier

    class MinimalUSRPTest(gr.top_block):
        def __init__(self, args):
            gr.top_block.__init__(self, "Minimal USRP Loopback")
            self.args = args

            # TX PHY
            self.wifi_phy_tx = wifi_phy_hier(
                bandwidth=10e6,
                chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2,
                frequency=5.89e9,
                sensitivity=0.01
            )
            self.wifi_phy_tx.set_use_ldpc(args.ldpc)

            # RX PHY (separate instance)
            self.wifi_phy_rx = wifi_phy_hier(
                bandwidth=10e6,
                chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2,
                frequency=5.89e9,
                sensitivity=0.01
            )

            # Message strobe: send frames periodically
            self.msg_strobe = blocks.message_strobe(
                pmt.intern("x" * args.len), args.interval
            )

            # MAC
            self.mac = ieee802_11.mac(
                [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
                [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
                [0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
            )

            # Debug counters
            self.msg_debug_mac = blocks.message_debug()
            self.msg_debug_rx = blocks.message_debug()

            # FCS logger: counts PDUs by crc metadata (Phase 34 e2e verification).
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

            self.fcs = FcsLogger()

            # Encoding stripper
            class encoding_stripper(gr.basic_block):
                def __init__(self):
                    gr.basic_block.__init__(self, name="encoding_stripper",
                                            in_sig=None, out_sig=None)
                    self.message_port_register_in(pmt.intern("pdu"))
                    self.message_port_register_out(pmt.intern("pdu"))
                    self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)
                def handle_pdu(self, msg):
                    meta = pmt.car(msg)
                    data = pmt.cdr(msg)
                    meta = pmt.dict_delete(meta, pmt.mp("encoding"))
                    meta = pmt.dict_delete(meta, pmt.mp("mcs"))
                    self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))

            self.encoding_stripper = encoding_stripper()

            # USRP TX. Channel 0 = A:0 subdev (slot A, subdev 0).
            # USRP X310 has 2 daughterboard slots: A and B. Each has its own UBX-160.
            # Per UHD RFNoC mapping: ch 0 -> A:0, ch 1 -> B:0.
            # Phase 112: Explicit send_buff_size=1048576 to avoid RFNOC graph failure
            # when net.core.wmem_max=1048576 (sysctl cannot be raised without sudo).
            # Default UHD requests 2453333, fails with "IO Error during GSM initialization".
            self.uhd_usrp_sink = uhd.usrp_sink(
                device_addr="addr=192.168.10.2,send_buff_size=1048576,recv_buff_size=1048576",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
            )
            self.uhd_usrp_sink.set_samp_rate(args.rate * 1e6)
            self.uhd_usrp_sink.set_center_freq(args.freq * 1e6, 0)
            self.uhd_usrp_sink.set_gain(args.tx_gain, 0)
            self.uhd_usrp_sink.set_antenna("TX/RX", 0)
            self.uhd_usrp_sink.set_subdev_spec("A:0", 0)

            # USRP RX. Two configurations supported:
            # (1) Default: ch 0, A:0 subdev, RX2 port, same-board TDD. Per commit 515b543.
            # (2) --cross-board: ch 0, B:0 subdev, TX/RX port. Phase 52.
            # Phase 58 Task 3: increase UHD recv buffer + num_recv_frames.
            # Default 1MB / 32 frames yields 0% sample delivery at 20 MHz x 4B/complex = 80 MB/s
            # (see /tmp/p58_t3_recv_buffer.log). 16MB / 256 frames absorbs USRP burst pressure
            # and achieves 100.1% delivery. 64MB / 512 is no better — pick 16MB / 256 for memory.
            self.uhd_usrp_source = uhd.usrp_source(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(
                    cpu_format="fc32",
                    otw_format="sc16",
                    args=uhd.device_addr("recv_buff_size=16777216,num_recv_frames=256"),
                    channels=[0],
                ),
            )
            rx_ch = 0
            if args.cross_board:
                # Phase 52 default cross-board: B:0 subdev, TX/RX port (half-duplex cable).
                self.uhd_usrp_source.set_subdev_spec("B:0", rx_ch)
                self.uhd_usrp_source.set_antenna("TX/RX", rx_ch)
            elif args.cross_board_rx2:
                # Phase 141 user wiring: B:0 subdev, RX2 port (separate RX path,
                # full-duplex capable via dual-cable). See project photo 2026-07-10.
                self.uhd_usrp_source.set_subdev_spec("B:0", rx_ch)
                self.uhd_usrp_source.set_antenna("RX2", rx_ch)
            else:
                self.uhd_usrp_source.set_antenna("RX2", rx_ch)
                self.uhd_usrp_source.set_subdev_spec(args.rx_subdev, rx_ch)
            self.uhd_usrp_source.set_gain(args.rx_gain, rx_ch)
            self.uhd_usrp_source.set_center_freq(args.freq * 1e6, rx_ch)
            self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, rx_ch)

            # Phase 113 T5.A: UHD API micro-tunings (default OFF via --uhd-tune flag)
            # Direct low-level UHD calls to attack 1.77 rad per-SC phase noise floor
            # (Phase 112 R1 ceiling). try/except prevents experiment interruption if
            # UHD 4.7.0.HEAD rejects any specific API on this hardware/driver combo.
            if args.uhd_tune:
                print("[TEST] UHD micro-tunings ENABLED (Phase 113 T5.A): "
                      "DC=off, IQ=off, LO=internal")
                try:
                    # gr-uhd 4.9.0.0 binding uses set_auto_dc_offset / set_auto_iq_balance
                    # (not set_rx_dc_offset as in raw UHD C++ API).
                    # Disabling auto-calibration may reduce per-SC phase noise floor.
                    # set_rx_agc not supported on UBX-160 (per Task 4 test) — omit.
                    # set_lo_source: UBX-160 ONLY supports internal, UHD rejects explicit
                    # set with "This device only supports setting internal source on all LOs".
                    # Hence omit set_lo_source from the block.
                    self.uhd_usrp_source.set_auto_dc_offset(False, 0)
                    self.uhd_usrp_source.set_auto_iq_balance(False, 0)
                    print("[TEST] UHD micro-tunings applied successfully")
                except (RuntimeError, AttributeError) as e:
                    print(f"[TEST] UHD API micro-tuning failed (non-fatal): {e}")

            # Phase 52: Diagnostic print of cross-board wiring
            print(f"[TEST] RX subdev_spec: {self.uhd_usrp_source.get_subdev_spec(rx_ch)}")
            print(f"[TEST] RX antenna: {self.uhd_usrp_source.get_antenna(rx_ch)}")
            print(f"[TEST] TX subdev_spec: {self.uhd_usrp_sink.get_subdev_spec(0)}")
            print(f"[TEST] TX antenna: {self.uhd_usrp_sink.get_antenna(0)}")

            # RX Buffer (Phase 52: 20MB absorbs USRP burst pressure)
            self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
            self.rx_buffer.set_min_output_buffer(20000000)

            # RX software gain: amplify low-amplitude USRP signal to ~1.0
            # Observed USRP RX amplitude ~0.0265, need ~40x gain to reach ~1.06
            self.rx_gain_block = blocks.multiply_const_cc(args.rx_scale)

            # Phase 52: Second buffer absorbs downstream burst pressure
            self.rx_buffer2 = blocks.copy(gr.sizeof_gr_complex)
            self.rx_buffer2.set_min_output_buffer(10000000)

            # File sink for raw IQ (optional)
            if args.capture:
                nsamples = int(args.duration * args.rate * 1e6)
                self.head = blocks.head(gr.sizeof_gr_complex, nsamples)
                self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.capture, False)

            # Null sources/sinks
            self.null_src = blocks.null_source(gr.sizeof_gr_complex)
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

            # ===== Connections =====
            # TX path
            self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
            self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
            self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))

            self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
            self.connect((self.wifi_phy_tx, 0), (self.uhd_usrp_sink, 0))

            # RX path (Phase 52: rx_buffer2 inserted between rx_gain_block and wifi_phy_rx)
            self.connect((self.uhd_usrp_source, 0), (self.rx_buffer, 0))
            self.connect((self.rx_buffer, 0), (self.rx_gain_block, 0))
            self.connect((self.rx_gain_block, 0), (self.rx_buffer2, 0))
            if args.capture:
                self.connect((self.rx_buffer2, 0), (self.head, 0))
                self.connect((self.head, 0), (self.file_sink, 0))
            self.connect((self.rx_buffer2, 0), (self.wifi_phy_rx, 0))
            self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))

            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))

            print(f"[TEST] Config: freq={args.freq}MHz rate={args.rate}MHz tx_gain={args.tx_gain} rx_gain={args.rx_gain}")
            # Phase 58: report current CPU governor + cpuset for verification
            try:
                with open('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor') as f:
                    print(f"[TEST] CPU governor: {f.read().strip()}")
            except (FileNotFoundError, PermissionError):
                print(f"[TEST] CPU governor: (unavailable)")
            print(f"[TEST] For best results, run with: taskset --cpu-list 0-1 <cmd>")
            if args.capture:
                print(f"[TEST] Raw IQ capture enabled: {args.capture}")

    # ===== Test loop body =====
    tb = MinimalUSRPTest(args)
    tb.start()

    # CRITICAL: Wait for USRP LO to lock + thermal stabilization before sending data
    # Phase 58: --warmup default 60s. Phase 31b healthy baseline used fresh-reboot
    # + cold caches. Subsequent runs (Phase 47+) saw progressive degradation as
    # thermal state and UHD socket buffer state varied.
    print(f"\n[TEST] Waiting for USRP warmup ({args.warmup}s: LO lock + thermal stab)...")
    time.sleep(args.warmup)
    print(f"[TEST] USRP warmup complete. LO should be locked and thermally stable.")

    print(f"\n[TEST] Running for {args.duration} seconds...")
    print(f"[TEST] Press Ctrl+C to stop early\n")

    start_time = time.time()
    try:
        while time.time() - start_time < args.duration:
            elapsed = time.time() - start_time
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            print(f"\r[TEST] Elapsed: {elapsed:.1f}s | Sent: {sent} | Recv: {recv} | Rate: {recv/max(1,sent)*100:.1f}%",
                  end='', flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    print()
    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[TEST] ===== RESULTS =====")
    print(f"[TEST] Sent: {sent}")
    print(f"[TEST] Recv: {recv}")
    print(f"[TEST] Success Rate: {recv/max(1,sent)*100:.1f}%")
    print(f"[TEST] FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}")

    if args.capture and os.path.exists(args.capture):
        size = os.path.getsize(args.capture)
        print(f"[TEST] Capture file: {args.capture} ({size} bytes)")


def main():
    parser = argparse.ArgumentParser(description='Minimal USRP Loopback Test (No GUI)')
    parser.add_argument('--freq', type=float, default=5890, help='Center frequency in MHz')
    parser.add_argument('--tx-gain', type=float, default=20, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=20, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate in MHz')
    parser.add_argument('--interval', type=int, default=1000, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=10, help='Test duration seconds')
    parser.add_argument('--warmup', type=float, default=60.0, help='USRP warmup seconds (LO lock + thermal stab)')
    parser.add_argument('--len', type=int, default=10, help='Payload length bytes')
    parser.add_argument('--ldpc', action='store_true', help='Enable LDPC')
    parser.add_argument('--mcs', type=int, default=0, choices=range(9), help='MCS mode')
    parser.add_argument('--rx-scale', type=float, default=40.0, help='RX software gain (multiplier)')
    parser.add_argument('--capture', type=str, default='', help='Capture raw IQ to file')
    parser.add_argument('--cross-board', action='store_true', help='Use A:0 TX -> B:0 TX/RX (cross-daughterboard, no internal leak)')
    parser.add_argument('--cross-board-rx2', action='store_true', help='Use A:0 TX -> B:0 RX2 (cross-daughterboard via RX2 port, full-duplex capable)')
    parser.add_argument('--rx-subdev', type=str, default='A:0', help='RX subdev spec (default A:0, use B:0 for cross-board)')
    # Phase 112: T7e multi-symbol H52 averaging + HT-SIG re-decode
    parser.add_argument('--t7e-on', action='store_true', help='Enable T7e (IEEE80211_T7E_MULTISYM_H=1)')
    parser.add_argument('--t7e-k', type=int, default=5, help='T7e K (number of DATA symbols to average, default 5)')
    # Phase 113: T5.A UHD API micro-tunings (DC offset, IQ balance, LO source)
    # Default OFF — Phase 112 baseline preserved when flag absent.
    parser.add_argument('--uhd-tune', action='store_true',
                        help='Phase 113 T5.A: disable RX DC offset + IQ balance '
                             'calibration, force LO source internal. Attacks 1.77 rad '
                             'analog chain noise floor (Phase 112 R1 ceiling).')
    # Phase 114: T4.D HT-LTF 2x averaging (opt-in via --htltf-avg, requires --uhd-tune).
    # Uses 2 HT-LTF symbols for cleaner H52 estimation. Default OFF.
    parser.add_argument('--htltf-avg', action='store_true',
                        help='Phase 114 Step 2: enable HT-LTF 2x averaging '
                             '(IEEE80211_HTLTF_AVG=1). Requires --uhd-tune.')
    # Phase 137: stable-null-aware masking (opt-in via --phase137-on).
    # Sets IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 and
    # IEEE80211_HTSIG_NULL_PILOT_MASK=1 to mask the 5 globally-null SCs
    # discovered in Phase 100 (smoking gun for viterbi wall). Default OFF.
    parser.add_argument('--phase137-on', action='store_true',
                        help='Phase 137: enable stable-null-aware masking '
                             '(IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 + '
                             'IEEE80211_HTSIG_NULL_PILOT_MASK=1)')
    # Phase 138: H52 frequency-domain low-pass filter (opt-in via --phase138-on).
    # Default OFF preserves baseline.
    parser.add_argument('--phase138-on', action='store_true',
                        help='Phase 138: enable H52 freq-domain low-pass filter '
                             '(IEEE80211_H52_FREQ_LOWPASS=1 + '
                             'IEEE80211_H52_FREQ_LOWPASS_K=N)')
    parser.add_argument('--phase138-k', type=int, default=10,
                        help='Phase 138: K value for freq-domain low-pass '
                             '(default 10, range 1..51)')
    # Phase 139: 2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi
    # (opt-in via --phase139-on). Default OFF preserves baseline.
    parser.add_argument('--phase139-on', action='store_true',
                        help='Phase 139: enable 2-way L-LTF0+L-LTF1 SNR-weighted H52 '
                             'for L-SIG viterbi (IEEE80211_H52_2WAY_DEFAULT=1)')
    parser.add_argument('--phase139-3way', action='store_true',
                        help='Phase 139: enable 3-way HT-SIG pilot refinement '
                             '(IEEE80211_HT_SIG_PILOT_REFINE=1)')
    parser.add_argument('--phase139-4way', action='store_true',
                        help='Phase 139: enable 4-way HT-SIG0+HT-SIG1 pilot refinement '
                             '(IEEE80211_HT_SIG_PILOT_REFINE=2)')

    # Phase 140: combined 2-way + L-SIG cross-frame H52
    parser.add_argument('--phase140-on', type=int, default=None, metavar='N',
                        help='Phase 140: enable 2-way L-LTF0+L-LTF1 H52 + '
                             'L-SIG cross-frame FIFO averaging with N frames. '
                             'N=0 (2-way only), N in {1,2,4,8} (combined). '
                             '(IEEE80211_PHASE140_ON=N, opt-in)')
    parser.add_argument('--phase140-log', action='store_true',
                        help='Phase 140: enable cross-frame FIFO diagnostic log. '
                             '(IEEE80211_LSIG_H52_CROSS_FRAME_LOG=1, opt-in)')
    parser.add_argument('--wiener-on', action='store_true',
                        help='Phase 141: enable Wiener H52 estimation '
                             '(IEEE80211_WIENER_H52=1, opt-in)')
    parser.add_argument('--wiener-log', action='store_true',
                        help='Phase 141: enable Wiener diagnostic log '
                             '(IEEE80211_WIENER_LOG=1, opt-in)')
    parser.add_argument('--wiener-fifo-n', type=int, default=4,
                        help='Phase 141: R_hh FIFO depth (default 4, range 1..8)')
    # Phase 143: BPSK-HT-SIG fallback (opt-in, non-standard).
    # Replaces QBPSK HT-SIG0/HT-SIG1 with BPSK to double angular margin
    # against the USRP 1.77 rad per-SC phase-noise floor.
    parser.add_argument('--htsig-bpsk-fallback', action='store_true',
                        help='Phase 143: use BPSK instead of QBPSK for '
                             'HT-SIG0/HT-SIG1 (IEEE80211_HTSIG_BPSK_FALLBACK=1, '
                             'opt-in, non-standard)')
    parser.add_argument('--internal-run', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Phase 53: always run internal_run directly. Subprocess wrapper removed.
    sys.exit(internal_run(args))


if __name__ == '__main__':
    main()