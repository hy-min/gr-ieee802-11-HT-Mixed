#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP CFO/SFO Diagnostic — Collects frame_equalizer CFO compensation logs
Usage:
  python test_usrp_cfo_diagnose.py --duration 15 --tx-gain 25 --rx-gain 30

Captures stderr to log file, then analyzes:
- [CFO_EST]: CFO estimation from L-LTF
- [SFO_EST]: SFO estimation
- [PHASE_COMP]: Per-subcarrier phase compensation applied
- [CFO_COMP_DATA]: Data symbol CFO compensation
- [LSIG_DECODE]: L-SIG decode result
- [HTSIG_DECODE]: HT-SIG decode result
- [LTF_CORR]: LTF correlation (with phase_diff)
"""
import argparse
import os
import sys
import time
import subprocess as sp


def run_with_logs(args_list, log_file):
    """Run the test in subprocess, capturing stderr to log file."""
    # Use gnuradio conda env Python, not system Python
    python_exe = '/home/hy/conda/envs/gnuradio/bin/python'
    cmd = [python_exe, '-u', __file__, '--internal-run'] + args_list
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = '/home/hy/conda/envs/gnuradio/lib'
    # Append to existing PYTHONPATH instead of overwriting
    existing_pp = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = '/home/hy/gr-ieee802-11/examples' + (':' + existing_pp if existing_pp else '')

    with open(log_file, 'w') as lf:
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
    import numpy as np

    sys.path.insert(0, '/home/hy/gr-ieee802-11')
    from wifi_phy_hier import wifi_phy_hier

    class AmplitudeProbe(gr.sync_block):
        """Probe to measure signal amplitude passing through."""
        def __init__(self, name, print_interval=10000):
            gr.sync_block.__init__(self, name, [np.complex64], [np.complex64])
            self.print_interval = print_interval
            self.total_samples = 0
            self.sum_power = 0.0
            self.max_peak = 0.0

        def work(self, input_items, output_items):
            n = len(input_items[0])
            if n > 0:
                power = np.mean(np.abs(input_items[0])**2)
                peak = np.max(np.abs(input_items[0]))
                self.sum_power += power * n
                self.max_peak = max(self.max_peak, peak)
                self.total_samples += n
                if self.total_samples >= self.print_interval:
                    avg_power = self.sum_power / self.total_samples
                    block_name = self.name() if hasattr(self, 'name') and callable(self.name) else "PROBE"
                    print(f"[{block_name}] total={self.total_samples} avg_power={avg_power:.6f} rms={np.sqrt(avg_power):.4f} peak={self.max_peak:.4f}", flush=True)
                    self.total_samples = 0
                    self.sum_power = 0.0
                    self.max_peak = 0.0
            output_items[0][:] = input_items[0]
            return n

    print(f"[CFO-DIAG] ===== CFO/SFO Diagnostic =====")
    print(f"[CFO-DIAG] Freq: {args.freq} MHz | Rate: {args.rate} MHz")
    print(f"[CFO-DIAG] TX Gain: {args.tx_gain} dB | RX Gain: {args.rx_gain} dB")
    print(f"[CFO-DIAG] Sensitivity: {args.sensitivity}")
    print(f"[CFO-DIAG] Duration: {args.duration} s")
    print(f"[CFO-DIAG] C-layer logs: CAPTURED to stderr")
    print(f"[CFO-DIAG]")

    class DiagTop(gr.top_block):
        def __init__(self, args):
            gr.top_block.__init__(self, "CFO Diagnostic")

            self.wifi_phy_tx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2, frequency=args.freq * 1e6,
                sensitivity=args.sensitivity
            )

            self.wifi_phy_rx = wifi_phy_hier(
                bandwidth=10e6, chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2, frequency=args.freq * 1e6,
                sensitivity=args.sensitivity
            )

            self.msg_strobe = blocks.message_strobe(
                pmt.intern("x" * args.len), args.interval
            )

            self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)

            self.msg_debug_mac = blocks.message_debug()
            self.msg_debug_rx = blocks.message_debug()

            # TX/RX amplitude probes
            self.tx_probe = AmplitudeProbe("TX_PROBE", print_interval=1000)
            self.rx_probe = AmplitudeProbe("RX_PROBE", print_interval=1000)

            self.uhd_sink = uhd.usrp_sink(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
            )
            self.uhd_sink.set_samp_rate(args.rate * 1e6)
            self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
            self.uhd_sink.set_gain(args.tx_gain, 0)
            self.uhd_sink.set_antenna("TX/RX", 0)
            self.uhd_sink.set_subdev_spec("A:0", 0)

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

            self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
            self.rx_buffer.set_min_output_buffer(5000000)

            self.null_src = blocks.null_source(gr.sizeof_gr_complex)
            self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

            self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))
            self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))
            self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
            self.connect((self.wifi_phy_tx, 0), (self.tx_probe, 0))
            self.connect((self.tx_probe, 0), (self.uhd_sink, 0))

            self.connect((self.uhd_src, 0), (self.rx_probe, 0))
            self.connect((self.rx_probe, 0), (self.rx_buffer, 0))
            self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
            self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

    tb = DiagTop(args)
    print(f"[CFO-DIAG] ===== Starting flowgraph... =====")
    tb.start()

    time.sleep(1.0)

    print(f"[CFO-DIAG]")
    print(f"[CFO-DIAG] {'Time':>6s} | {'Sent':>5s} | {'Recv':>5s} | Status")
    print(f"[CFO-DIAG] {'------':>6s} | {'-----':>5s} | {'-----':>5s} | ------")

    start = time.time()
    try:
        while time.time() - start < args.duration:
            elapsed = time.time() - start
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            status = "RECV+" if recv > 0 else "TXING" if sent > 0 else "WAIT"
            print(f"[CFO-DIAG] {elapsed:>6.1f} | {sent:>5d} | {recv:>5d} | {status}", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[CFO-DIAG] ===== RESULTS =====")
    print(f"[CFO-DIAG] Total Sent:  {sent}")
    print(f"[CFO-DIAG] Total Recv:  {recv}")
    print(f"[CFO-DIAG] Success Rate: {recv/max(1,sent)*100:.1f}%")


def analyze_logs(log_file):
    """Parse the captured log file for CFO/SFO diagnostics."""
    if not os.path.exists(log_file):
        print(f"\n[ANALYZE] ERROR: Log file not found: {log_file}")
        return

    with open(log_file, 'r') as f:
        logs = f.read()

    lines = logs.split('\n')
    print(f"\n{'='*70}")
    print("CFO/SFO LOG ANALYSIS REPORT")
    print(f"{'='*70}")
    print(f"Total log lines: {len(lines)}")

    # Extract key log patterns
    cfo_est = [l for l in lines if '[CFO_EST]' in l]
    sfo_est = [l for l in lines if '[SFO_EST]' in l]
    phase_comp = [l for l in lines if '[PHASE_COMP]' in l]
    cfo_data = [l for l in lines if '[CFO_COMP_DATA]' in l]
    lsig_decode = [l for l in lines if '[LSIG_DECODE]' in l]
    htsig_decode = [l for l in lines if '[HTSIG_DECODE]' in l]
    ltf_corr = [l for l in lines if '[LTF_CORR]' in l]
    lsig_eq = [l for l in lines if '[LSIG_EQ]' in l]

    print(f"\n--- CFO/SFO Estimation ---")
    print(f"  [CFO_EST] logs:       {len(cfo_est)}")
    print(f"  [SFO_EST] logs:       {len(sfo_est)}")
    print(f"  [PHASE_COMP] logs:    {len(phase_comp)}")
    print(f"  [CFO_COMP_DATA] logs: {len(cfo_data)}")

    print(f"\n--- Header Decode ---")
    print(f"  [LSIG_DECODE] logs:   {len(lsig_decode)}")
    print(f"  [HTSIG_DECODE] logs:  {len(htsig_decode)}")

    print(f"\n--- LTF Correlation ---")
    print(f"  [LTF_CORR] logs:      {len(ltf_corr)}")

    # Parse CFO values
    if cfo_est:
        print(f"\n--- CFO Estimation Details ---")
        for l in cfo_est[:5]:
            print(f"  {l.strip()[:120]}")
        if len(cfo_est) > 5:
            print(f"  ... ({len(cfo_est)-5} more)")

    # Parse SFO values
    if sfo_est:
        print(f"\n--- SFO Estimation Details ---")
        for l in sfo_est[:5]:
            print(f"  {l.strip()[:120]}")
        if len(sfo_est) > 5:
            print(f"  ... ({len(sfo_est)-5} more)")

    # Parse L-SIG decode results
    if lsig_decode:
        print(f"\n--- L-SIG Decode Results ---")
        ok_count = sum(1 for l in lsig_decode if 'OK' in l)
        fail_count = len(lsig_decode) - ok_count
        print(f"  OK:   {ok_count}")
        print(f"  FAIL: {fail_count}")
        for l in lsig_decode[:10]:
            print(f"  {l.strip()[:120]}")
        if len(lsig_decode) > 10:
            print(f"  ... ({len(lsig_decode)-10} more)")

    # Parse HT-SIG decode results
    if htsig_decode:
        print(f"\n--- HT-SIG Decode Results ---")
        pass_count = sum(1 for l in htsig_decode if 'PASS' in l)
        fail_count = len(htsig_decode) - pass_count
        print(f"  PASS: {pass_count}")
        print(f"  FAIL: {fail_count}")
        for l in htsig_decode[:10]:
            print(f"  {l.strip()[:120]}")
        if len(htsig_decode) > 10:
            print(f"  ... ({len(htsig_decode)-10} more)")

    # Parse LTF correlation with phase_diff
    if ltf_corr:
        print(f"\n--- LTF Correlation (with phase_diff) ---")
        for l in ltf_corr[:5]:
            print(f"  {l.strip()[:140]}")
        if len(ltf_corr) > 5:
            print(f"  ... ({len(ltf_corr)-5} more)")

    # Parse LSIG_EQ for first frame
    if lsig_eq:
        print(f"\n--- L-SIG Equalization (first frame) ---")
        for l in lsig_eq[:3]:
            print(f"  {l.strip()[:140]}")

    # Check for phase compensation anomalies
    if phase_comp:
        print(f"\n--- Phase Compensation Check ---")
        # Extract avg_phase values
        phases = []
        for l in phase_comp:
            try:
                if 'avg_phase=' in l:
                    p = l.split('avg_phase=')[1].split()[0]
                    phases.append(float(p))
            except:
                pass
        if phases:
            import numpy as np
            print(f"  Phase comp calls: {len(phases)}")
            print(f"  Avg phase range: {min(phases):.4f} to {max(phases):.4f} rad")
            print(f"  Avg phase mean:  {np.mean(phases):.4f} rad")

    # Summary / hypothesis check
    print(f"\n{'='*70}")
    print("HYPOTHESIS CHECK")
    print(f"{'='*70}")

    if not cfo_est:
        print("❌ No [CFO_EST] logs found — CFO estimation may not be triggered")
        print("   Possible causes:")
        print("   - sync_long not reaching frame_equalizer")
        print("   - d_internal_symbol_counter not reaching kLltf1Rel")
    else:
        print("✅ CFO estimation is running")

    if not lsig_decode:
        print("❌ No [LSIG_DECODE] logs — L-SIG never decoded")
        print("   This means frame_equalizer never reaches L-SIG processing")
    elif ok_count == 0:
        print("⚠️  L-SIG decode always FAILS")
        print("   Check [LSIG_EQ] for equalized symbol values")
        if lsig_eq:
            print("   LSIG_EQ logs available — inspect constellation rotation")
    else:
        print(f"✅ L-SIG decode OK rate: {ok_count}/{len(lsig_decode)}")

    if not htsig_decode:
        print("ℹ️  No [HTSIG_DECODE] logs — may be Legacy frames or L-SIG failed first")
    elif pass_count == 0:
        print("⚠️  HT-SIG CRC always FAILS")
    else:
        print(f"✅ HT-SIG CRC pass rate: {pass_count}/{len(htsig_decode)}")

    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description='CFO/SFO Diagnostic')
    parser.add_argument('--freq', type=float, default=5180, help='Center freq MHz')
    parser.add_argument('--tx-gain', type=float, default=25, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=30, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    parser.add_argument('--interval', type=int, default=200, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=15, help='Test duration s')
    parser.add_argument('--len', type=int, default=10, help='Payload bytes')
    parser.add_argument('--sensitivity', type=float, default=0.01, help='Frame detect threshold')
    parser.add_argument('--log', type=str, default='/tmp/usrp_cfo_diagnose.log', help='Log file path')
    parser.add_argument('--internal-run', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.internal_run:
        sys.exit(internal_run(args))
    else:
        print(f"[MAIN] Running CFO diagnostic, logs will be saved to: {args.log}")
        rc = run_with_logs(sys.argv[1:], args.log)
        analyze_logs(args.log)
        print(f"\n[MAIN] Full log saved to: {args.log}")
        return rc


if __name__ == '__main__':
    sys.exit(main())
