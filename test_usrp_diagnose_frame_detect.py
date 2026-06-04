#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP Frame Detection Diagnostic — Collects sync_short + sync_long logs
Usage:
  python test_usrp_diagnose_frame_detect.py --duration 10 --tx-gain 25 --rx-gain 30

Captures stderr to log file, then analyzes:
- sync_short: Did it detect frames? ("Frame detected!")
- sync_long: What were correlation peaks? ("Top correlation magnitude")
- sync_long: Did it find frame start? ("HT-mode-plateau SELECTED" / "Legacy-mode-plateau")
"""
import argparse
import os
import sys
import time
import subprocess as sp


def run_with_logs(args_list, log_file):
    """Run the test in subprocess, capturing stderr to log file."""
    cmd = [sys.executable, '-u', __file__, '--internal-run'] + args_list
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = '/home/hy/conda/envs/gnuradio/lib'
    env['PYTHONPATH'] = '/home/hy/gr-ieee802-11/examples'

    with open(log_file, 'w') as lf:
        # stdout piped, stderr goes to log file
        proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=lf, text=True, bufsize=1, env=env)
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
    """Actual test logic - runs in subprocess with stderr captured."""
    os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
    os.environ['GR_RPC_ENABLE'] = 'False'

    from gnuradio import gr, blocks, uhd
    import pmt
    import ieee802_11

    sys.path.insert(0, '/home/hy/gr-ieee802-11')
    from wifi_phy_hier import wifi_phy_hier

    print(f"[DIAG] ===== Frame Detection Diagnostic =====")
    print(f"[DIAG] Freq: {args.freq} MHz | Rate: {args.rate} MHz")
    print(f"[DIAG] TX Gain: {args.tx_gain} dB | RX Gain: {args.rx_gain} dB")
    print(f"[DIAG] Sensitivity: {args.sensitivity}")
    print(f"[DIAG] Duration: {args.duration} s")
    print(f"[DIAG] C-layer logs: CAPTURED to stderr")
    print(f"[DIAG]")

    class DiagTop(gr.top_block):
        def __init__(self, args):
            gr.top_block.__init__(self, "Frame Detection Diagnostic")

            # TX PHY
            self.wifi_phy_tx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2, frequency=args.freq * 1e6,
                sensitivity=args.sensitivity
            )

            # RX PHY
            self.wifi_phy_rx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2, frequency=args.freq * 1e6,
                sensitivity=args.sensitivity
            )

            self.msg_strobe = blocks.message_strobe(
                pmt.intern("x" * args.len), args.interval
            )

            self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)

            # Debug counters
            self.msg_debug_mac = blocks.message_debug()
            self.msg_debug_rx = blocks.message_debug()

            # USRP TX (Radio 0)
            self.uhd_sink = uhd.usrp_sink(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
            )
            self.uhd_sink.set_samp_rate(args.rate * 1e6)
            self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
            self.uhd_sink.set_gain(args.tx_gain, 0)
            self.uhd_sink.set_antenna("TX/RX", 0)
            self.uhd_sink.set_subdev_spec("A:0", 0)

            # USRP RX (Radio 1)
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

            self.null_src = blocks.null_source(gr.sizeof_gr_complex)
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

            # Connections
            self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))
            self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))
            self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
            self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

            self.connect((self.uhd_src, 0), (self.rx_buffer, 0))
            self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
            self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

    tb = DiagTop(args)
    print(f"[DIAG] ===== Starting flowgraph... =====")
    tb.start()

    # Wait for LO stabilization
    time.sleep(1.0)

    print(f"[DIAG]")
    print(f"[DIAG] {'Time':>6s} | {'Sent':>5s} | {'Recv':>5s} | Status")
    print(f"[DIAG] {'------':>6s} | {'-----':>5s} | {'-----':>5s} | ------")

    start = time.time()
    try:
        while time.time() - start < args.duration:
            elapsed = time.time() - start
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            status = "RECV+" if recv > 0 else "TXING" if sent > 0 else "WAIT"
            print(f"[DIAG] {elapsed:>6.1f} | {sent:>5d} | {recv:>5d} | {status}", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[DIAG] ===== RESULTS =====")
    print(f"[DIAG] Total Sent:  {sent}")
    print(f"[DIAG] Total Recv:  {recv}")
    print(f"[DIAG] Success Rate: {recv/max(1,sent)*100:.1f}%")


def analyze_logs(log_file):
    """Parse the captured log file for key diagnostics."""
    if not os.path.exists(log_file):
        print(f"\n[ANALYZE] ERROR: Log file not found: {log_file}")
        return

    with open(log_file, 'r') as f:
        logs = f.read()

    lines = logs.split('\n')

    print(f"\n{'='*70}")
    print("LOG ANALYSIS REPORT")
    print(f"{'='*70}")
    print(f"Total log lines: {len(lines)}")

    # Count key events
    frame_detected = [l for l in lines if 'Frame detected!' in l]
    top_corr = [l for l in lines if 'Top correlation magnitude' in l]
    ht_selected = [l for l in lines if 'HT-mode-plateau SELECTED' in l]
    leg_selected = [l for l in lines if 'Legacy-mode-plateau SELECTED' in l]
    method2 = [l for l in lines if 'Method2-peak' in l]
    no_valid = [l for l in lines if 'No valid detection' in l or 'd_frame_start=SYNC_LENGTH' in l]
    sync_long_tag = [l for l in lines if 'SYNC->COPY via wifi_start tag' in l]
    sync_short_calls = [l for l in lines if 'SYNC-SHORT] general_work called' in l]
    sync_long_work = [l for l in lines if 'SYNC_LONG_WORK] call=' in l]

    print(f"\n--- sync_short Activity ---")
    print(f"  general_work calls: {len(sync_short_calls)}")
    print(f"  Frame detected:     {len(frame_detected)}")
    if frame_detected:
        print(f"  First detection:")
        print(f"    {frame_detected[0][:120]}")

    print(f"\n--- sync_long Activity ---")
    print(f"  WORK calls:         {len(sync_long_work)}")
    print(f"  SYNC->COPY via tag: {len(sync_long_tag)}")
    print(f"  Correlation peaks:  {len(top_corr)}")
    print(f"  HT-mode selected:   {len(ht_selected)}")
    print(f"  Legacy selected:    {len(leg_selected)}")
    print(f"  Method2-peak:       {len(method2)}")
    print(f"  No valid detection: {len(no_valid)}")

    # Analyze correlation magnitudes
    if top_corr:
        print(f"\n--- Correlation Peak Analysis ---")
        magnitudes = []
        for l in top_corr:
            # Parse "Top correlation magnitude: X.XXXX"
            try:
                parts = l.split('Top correlation magnitude:')
                if len(parts) > 1:
                    mag = float(parts[1].strip().split()[0])
                    magnitudes.append(mag)
            except:
                pass
        if magnitudes:
            import numpy as np
            print(f"  Peaks measured: {len(magnitudes)}")
            print(f"  Max:  {max(magnitudes):.4f}")
            print(f"  Min:  {min(magnitudes):.4f}")
            print(f"  Mean: {np.mean(magnitudes):.4f}")
            print(f"  Threshold: 0.5 (MIN_ABS_MAGNITUDE)")
            above_thresh = sum(1 for m in magnitudes if m >= 0.5)
            below_thresh = len(magnitudes) - above_thresh
            print(f"  Above threshold: {above_thresh}/{len(magnitudes)}")
            print(f"  Below threshold: {below_thresh}/{len(magnitudes)}")
            if below_thresh > 0 and above_thresh == 0:
                print(f"\n  ⚠️  ALL correlation peaks are BELOW 0.5 threshold!")
                print(f"      This explains why sync_long fails to detect frames.")

    # Check for SYNC->COPY transitions
    print(f"\n--- State Transitions ---")
    if sync_long_tag:
        print(f"  ✓ sync_long received wifi_start tags from sync_short")
        print(f"    (sync_short IS detecting frames)")
    else:
        print(f"  ✗ No wifi_start tags observed")
        print(f"    (sync_short may NOT be detecting frames)")

    if ht_selected or leg_selected or method2:
        print(f"  ✓ sync_long found valid frame starts")
    else:
        print(f"  ✗ sync_long did NOT find any valid frame starts")

    print(f"\n--- Raw Log Snippets (first 20 relevant lines) ---")
    relevant = [l for l in lines if any(k in l for k in [
        'Frame detected', 'Top correlation', 'SELECTED', 'SYNC->COPY',
        'SYNC_LONG_TAG', 'SYNC-SHORT'
    ])]
    for l in relevant[:20]:
        print(f"  {l[:120]}")

    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description='Frame Detection Diagnostic')
    parser.add_argument('--freq', type=float, default=5180, help='Center freq MHz')
    parser.add_argument('--tx-gain', type=float, default=25, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=30, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    parser.add_argument('--interval', type=int, default=200, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=10, help='Test duration s')
    parser.add_argument('--len', type=int, default=10, help='Payload bytes')
    parser.add_argument('--sensitivity', type=float, default=0.01, help='Frame detect threshold')
    parser.add_argument('--log', type=str, default='/tmp/usrp_frame_detect.log', help='Log file path')
    parser.add_argument('--internal-run', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.internal_run:
        sys.exit(internal_run(args))
    else:
        print(f"[MAIN] Running diagnostic, logs will be saved to: {args.log}")
        rc = run_with_logs(sys.argv[1:], args.log)
        analyze_logs(args.log)
        print(f"\n[MAIN] Full log saved to: {args.log}")
        return rc


if __name__ == '__main__':
    sys.exit(main())
