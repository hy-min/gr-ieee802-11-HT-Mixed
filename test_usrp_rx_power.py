#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP RX Signal Power Measurement — 在 TX 发送 802.11 帧时测量 RX 信号功率
Usage:
  python test_usrp_rx_power.py --freq 5180 --tx-gain 25 --rx-gain 30 --duration 10

Purpose: 判断 USRP RX 信号幅度是否足够触发 sync_long 检测 (MIN_ABS_MAGNITUDE=0.5)
"""
import argparse
import os
import sys
import time
import numpy as np

# Disable C-layer stderr to avoid fprintf flood
orig_stderr_fd = os.dup(2)
with open('/dev/null', 'w') as devnull:
    os.dup2(devnull.fileno(), 2)

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr, blocks, uhd
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from wifi_phy_hier import wifi_phy_hier


class RxPowerMeasurement(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "RX Power Measurement")
        self.args = args

        # ===== TX Chain: send 802.11 frames =====
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=args.freq * 1e6,
            sensitivity=0.01
        )

        self.msg_strobe = blocks.message_strobe(
            pmt.intern("x" * args.len), args.interval
        )

        self.mac = ieee802_11.mac(
            [0x23]*6, [0x42]*6, [0xff]*6
        )

        # Encoding stripper (remove encoding/mcs tags)
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

        self.null_src = blocks.null_source(gr.sizeof_gr_complex)

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

        # ===== RX Chain: power measurement =====
        self.uhd_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_source.set_samp_rate(args.rate * 1e6)
        self.uhd_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_source.set_gain(args.rx_gain, 0)
        self.uhd_source.set_antenna("RX2", 0)
        self.uhd_source.set_subdev_spec("B:0", 0)
        self.uhd_source.set_bandwidth(args.rate * 1e6, 0)

        # RX power probe (measures current signal level)
        self.rx_probe = blocks.probe_signal_c()
        self.rx_null = blocks.null_sink(gr.sizeof_gr_complex)

        # ===== Connections =====
        # TX
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

        # RX
        self.connect((self.uhd_source, 0), (self.rx_probe, 0))
        self.connect((self.uhd_source, 0), (self.rx_null, 0))

        print(f"[POWER] Config: freq={args.freq}MHz rate={args.rate}MHz")
        print(f"[POWER] TX: gain={args.tx_gain}dB (Radio#0 A:0 TX/RX)")
        print(f"[POWER] RX: gain={args.rx_gain}dB (Radio#1 B:0 RX2)")

    def measure_power(self, n_samples=10000):
        """Measure RX power using probe samples."""
        # probe_signal_c returns the current signal value (last sample)
        # For power measurement, we need multiple samples
        # Use a simpler approach: the probe gives us instantaneous value
        val = self.rx_probe.level()
        if val is None:
            return 0.0, 0.0
        inst_power = abs(val) ** 2
        inst_db = 10 * np.log10(inst_power + 1e-20) if inst_power > 0 else -999
        return inst_power, inst_db


def main():
    parser = argparse.ArgumentParser(description='USRP RX Power Measurement')
    parser.add_argument('--freq', type=float, default=5180, help='Center frequency MHz')
    parser.add_argument('--tx-gain', type=float, default=25, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=30, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    parser.add_argument('--interval', type=int, default=200, help='Frame interval ms (shorter for more activity)')
    parser.add_argument('--duration', type=float, default=10, help='Measurement duration seconds')
    parser.add_argument('--len', type=int, default=10, help='Payload bytes')
    args = parser.parse_args()

    tb = RxPowerMeasurement(args)
    tb.start()

    # Wait for LO stabilization
    time.sleep(1.0)

    # Restore stderr for Python output
    os.dup2(orig_stderr_fd, 2)
    os.close(orig_stderr_fd)

    print(f"\n[POWER] Measuring for {args.duration} seconds...")
    print(f"{'Time':>6s} | {'Inst Power':>12s} | {'Inst dB':>10s} | {'Status':>15s}")
    print(f"{'------':>6s} | {'----------':>12s} | {'-------':>10s} | {'---------------':>15s}")

    powers = []
    db_values = []
    start = time.time()

    try:
        while time.time() - start < args.duration:
            elapsed = time.time() - start
            pwr, db = tb.measure_power()
            powers.append(pwr)
            db_values.append(db)

            # Status based on power level
            if pwr > 0.1:
                status = "STRONG SIGNAL"
            elif pwr > 0.01:
                status = "MODERATE"
            elif pwr > 0.001:
                status = "WEAK"
            elif pwr > 1e-6:
                status = "VERY WEAK"
            else:
                status = "NOISE ONLY"

            print(f"{elapsed:>6.1f} | {pwr:>12.6f} | {db:>10.1f} | {status:>15s}", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    tb.stop()
    tb.wait()

    # Summary statistics
    if powers:
        p_arr = np.array(powers)
        d_arr = np.array(db_values)
        print(f"\n{'='*60}")
        print("POWER MEASUREMENT SUMMARY")
        print(f"{'='*60}")
        print(f"Mean power:   {np.mean(p_arr):.6f} ({np.mean(d_arr):.1f} dB)")
        print(f"Max power:    {np.max(p_arr):.6f} ({np.max(d_arr):.1f} dB)")
        print(f"Min power:    {np.min(p_arr):.6f} ({np.min(d_arr):.1f} dB)")
        print(f"Std power:    {np.std(p_arr):.6f}")
        print(f"\nPeak amplitude estimate: {np.sqrt(np.max(p_arr)):.4f}")
        print(f"\nReference thresholds:")
        print(f"  sync_short threshold:   0.01 (correlation, amplitude-independent)")
        print(f"  sync_long MIN_ABS_MAG:  0.5 (FIR output, amplitude-dependent)")
        print(f"  Estimated FIR peak (if signal amp ~ {np.sqrt(np.max(p_arr)):.4f}):")
        print(f"    ~{np.sqrt(np.max(p_arr)) * 1.0:.4f} (depends on L-LTF correlation)")
        print(f"{'='*60}")

        # Assessment
        peak_amp = np.sqrt(np.max(p_arr))
        if peak_amp < 0.05:
            print("\n⚠️  ASSESSMENT: Signal amplitude VERY LOW")
            print("   Likely cause: sync_long MIN_ABS_MAGNITUDE=0.5 is too high")
            print("   Suggestion: Increase TX/RX gain or reduce MIN_ABS_MAGNITUDE")
        elif peak_amp < 0.2:
            print("\n⚠️  ASSESSMENT: Signal amplitude LOW")
            print("   May be insufficient for sync_long detection")
        else:
            print("\n✅ ASSESSMENT: Signal amplitude seems adequate")
            print("   Problem may be elsewhere (CFO, timing, etc.)")


if __name__ == '__main__':
    main()
