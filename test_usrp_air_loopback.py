#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP Air Loopback Test - Optimized for over-the-air transmission
Usage: 
  # Place antennas ~5-10cm apart, facing each other
  python test_usrp_air_loopback.py --duration 30 --tx-gain 25 --rx-gain 30
"""
import argparse
import os
import sys
import time
import signal

# === CRITICAL: Redirect C-layer stderr to /dev/null BEFORE importing gnuradio ===
# This eliminates the 150+ fprintf debug logs that cause RX overflow
import subprocess as sp

def run_test_in_subprocess(args_list):
    """Run the actual test in a subprocess with stderr suppressed."""
    cmd = [sys.executable, '-u', __file__, '--internal-run'] + args_list
    # stderr to /dev/null kills all C-layer fprintf logs
    # stdout is piped so we can capture Python print output
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = '/home/hy/conda/envs/gnuradio/lib'
    env['PYTHONPATH'] = '/home/hy/gr-ieee802-11/examples'
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, text=True, bufsize=1, env=env)
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    return proc.returncode

def internal_run(args):
    """Actual test logic - runs in subprocess with stderr suppressed."""
    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'

    from gnuradio import gr, blocks, uhd
    import pmt
    import ieee802_11

    sys.path.insert(0, '/home/hy/gr-ieee802-11')
    from wifi_phy_hier import wifi_phy_hier

    print(f"[AIR] ===== USRP Air Loopback Test =====")
    print(f"[AIR] Freq: {args.freq} MHz | Rate: {args.rate} MHz")
    print(f"[AIR] TX Gain: {args.tx_gain} dB | RX Gain: {args.rx_gain} dB")
    print(f"[AIR] Sensitivity: {args.sensitivity} | Interval: {args.interval} ms")
    print(f"[AIR] MCS: {args.mcs} | LDPC: {args.ldpc}")
    print(f"[AIR] Duration: {args.duration} s")
    print(f"[AIR] C-layer logs: SUPPRESSED (stderr → /dev/null)")
    print(f"[AIR]")
    print(f"[AIR] IMPORTANT: Ensure antennas are 5-10cm apart, facing each other")
    print(f"[AIR] ===== Building flowgraph... =====")

    class AirLoopback(gr.top_block):
        def __init__(self, args):
            gr.top_block.__init__(self, "Air Loopback")

            # TX PHY
            encodings = [
                ieee802_11.BPSK_1_2, ieee802_11.BPSK_3_4,
                ieee802_11.QPSK_1_2, ieee802_11.QPSK_3_4,
                ieee802_11.QAM16_1_2, ieee802_11.QAM16_3_4,
                ieee802_11.QAM64_2_3, ieee802_11.QAM64_3_4,
                ieee802_11.QAM64_5_6,
            ]
            self.wifi_phy_tx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=encodings[args.mcs], frequency=5.89e9,
                sensitivity=args.sensitivity
            )
            self.wifi_phy_tx.set_use_ldpc(args.ldpc)

            # RX PHY
            self.wifi_phy_rx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=encodings[0], frequency=5.89e9,
                sensitivity=args.sensitivity
            )

            # Message strobe
            self.msg_strobe = blocks.message_strobe(
                pmt.intern("x" * args.len), args.interval
            )

            # MAC
            self.mac = ieee802_11.mac(
                [0x23]*6, [0x42]*6, [0xff]*6
            )

            # Debug counters
            self.msg_debug_mac = blocks.message_debug()
            self.msg_debug_rx = blocks.message_debug()

            # USRP TX
            self.uhd_sink = uhd.usrp_sink(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
            )
            self.uhd_sink.set_samp_rate(args.rate * 1e6)
            self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
            self.uhd_sink.set_gain(args.tx_gain, 0)
            self.uhd_sink.set_antenna("TX/RX", 0)
            self.uhd_sink.set_subdev_spec("A:0", 0)

            # USRP RX (Radio 1, RX2 port)
            self.uhd_src = uhd.usrp_source(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
            )
            self.uhd_src.set_samp_rate(args.rate * 1e6)
            self.uhd_src.set_center_freq(args.freq * 1e6, 0)
            self.uhd_src.set_gain(args.rx_gain, 0)
            self.uhd_src.set_antenna("RX2", 0)
            self.uhd_src.set_subdev_spec("B:0", 0)
            self.uhd_src.set_bandwidth(args.rate * 1e6, 0)

            # RX buffer
            self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
            self.rx_buffer.set_min_output_buffer(5000000)

            # Null source/sink
            self.null_src = blocks.null_source(gr.sizeof_gr_complex)
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

            # === Connections ===
            # TX
            self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))
            self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))
            self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
            self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

            # RX
            self.connect((self.uhd_src, 0), (self.rx_buffer, 0))
            self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
            self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

    tb = AirLoopback(args)
    print(f"[AIR] ===== Starting flowgraph... =====")
    tb.start()

    print(f"[AIR]")
    print(f"[AIR] {'Time':>6s} | {'Sent':>5s} | {'Recv':>5s} | {'Rate':>6s} | Status")
    print(f"[AIR] {'------':>6s} | {'-----':>5s} | {'-----':>5s} | {'------':>6s} | ------")

    start = time.time()
    last_sent = 0
    last_recv = 0
    recv_history = []

    try:
        while True:
            elapsed = time.time() - start
            if elapsed >= args.duration:
                break

            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            rate = recv / max(1, sent) * 100

            recv_history.append(recv)
            # Keep last 10 seconds of history
            if len(recv_history) > 20:
                recv_history.pop(0)

            # Detect if we're making progress
            if len(recv_history) >= 4 and recv_history[-1] == recv_history[-4]:
                status = "STUCK"
            elif recv > last_recv:
                status = "RECV+"
            elif sent > last_sent:
                status = "TXING"
            else:
                status = "WAIT"

            last_sent = sent
            last_recv = recv

            print(f"[AIR] {elapsed:>6.1f} | {sent:>5d} | {recv:>5d} | {rate:>5.1f}% | {status}", flush=True)
            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n[AIR] Interrupted by user")

    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[AIR] ===== RESULTS =====")
    print(f"[AIR] Total Sent:  {sent}")
    print(f"[AIR] Total Recv:  {recv}")
    print(f"[AIR] Success Rate: {recv/max(1,sent)*100:.1f}%")
    if recv > 0:
        print(f"[AIR] ✅ Frames decoded successfully!")
    else:
        print(f"[AIR] ❌ No frames decoded")
        print(f"[AIR] Suggestions:")
        print(f"[AIR]   1. Move antennas closer (5cm or less)")
        print(f"[AIR]   2. Increase TX gain (--tx-gain 30)")
        print(f"[AIR]   3. Increase RX gain (--rx-gain 35)")
        print(f"[AIR]   4. Lower sensitivity (--sensitivity 0.005)")
        print(f"[AIR]   5. Check antennas are 2.4GHz compatible")

def main():
    parser = argparse.ArgumentParser(description='USRP Air Loopback Test')
    parser.add_argument('--freq', type=float, default=5180, help='Center freq MHz')
    parser.add_argument('--tx-gain', type=float, default=25, help='TX gain dB (air: 20-30)')
    parser.add_argument('--rx-gain', type=float, default=30, help='RX gain dB (air: 25-35)')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    parser.add_argument('--interval', type=int, default=1000, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=30, help='Test duration s')
    parser.add_argument('--len', type=int, default=10, help='Payload bytes')
    parser.add_argument('--ldpc', action='store_true', help='Enable LDPC')
    parser.add_argument('--mcs', type=int, default=0, choices=range(9), help='MCS')
    parser.add_argument('--sensitivity', type=float, default=0.01, help='Frame detect threshold')
    parser.add_argument('--internal-run', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.internal_run:
        # We're in the subprocess - run actual test
        sys.exit(internal_run(args))
    else:
        # We're the parent - spawn subprocess with stderr suppressed
        sys.exit(run_test_in_subprocess(sys.argv[1:]))

if __name__ == '__main__':
    main()
