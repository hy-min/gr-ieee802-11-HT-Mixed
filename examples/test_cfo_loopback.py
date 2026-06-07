#!/home/hy/conda/envs/gnuradio/bin/python
"""
CFO (Carrier Frequency Offset) injection loopback test

Injects artificial CFO using blocks.rotator_cc to validate receiver CFO
compensation. Uses software loopback (no USRP hardware required).

Usage:
    # Baseline (no CFO)
    python test_cfo_loopback.py --cfo-ppm 0 --duration 15

    # With CFO
    python test_cfo_loopback.py --cfo-ppm 500 --duration 15

Exit code:
    0 if frames were decoded
    1 if no frames decoded
"""
import argparse
import os
import sys
import time
import signal
from pathlib import Path

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks, channels
import pmt

# Add project paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'examples'))

try:
    import ieee802_11
except ImportError as e:
    print(f"ERROR: Cannot import ieee802_11 module: {e}")
    print("Make sure the project is built and installed. See docs/superpowers/plans/ for build instructions.")
    sys.exit(1)

from wifi_phy_hier import wifi_phy_hier


SAMPLE_RATE = 20e6  # 20 MHz, PHY hardcoded


class encoding_stripper(gr.basic_block):
    """Remove encoding/mcs tags from PDU meta so mapper uses set_encoding()."""

    def __init__(self):
        gr.basic_block.__init__(
            self,
            name="encoding_stripper",
            in_sig=None,
            out_sig=None
        )
        self.message_port_register_in(pmt.intern("pdu"))
        self.message_port_register_out(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle_pdu)

    def handle_pdu(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        meta = pmt.dict_delete(meta, pmt.mp("encoding"))
        meta = pmt.dict_delete(meta, pmt.mp("mcs"))
        self.message_port_pub(pmt.intern("pdu"), pmt.cons(meta, data))


class CFOLoopbackTest(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "CFO Loopback Test")
        self.args = args

        # CFO frequency in Hz
        cfo_hz = args.cfo_ppm * SAMPLE_RATE / 1e6
        # rotator phase increment = cfo_hz / sample_rate (radians per sample)
        phase_inc = cfo_hz / SAMPLE_RATE

        # TX PHY
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=args.sensitivity
        )
        self.wifi_phy_tx.set_use_ldpc(args.ldpc)

        # RX PHY (separate instance)
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=args.sensitivity
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

        # Encoding stripper
        self.encoding_stripper = encoding_stripper()

        # CFO injection: rotator applies exp(j*2*pi*cfo_hz*t)
        self.cfo_rotator = blocks.rotator_cc(phase_inc)

        # Channel model: minimal noise, no natural CFO (we inject our own)
        noise_voltage = 10**(-args.snr / 20.0)
        self.channel = channels.channel_model(
            noise_voltage=noise_voltage,
            frequency_offset=0.0,  # CFO injected via rotator
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False
        )

        # Null source/sink for TX/RX stream ports
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

        # ===== Connections =====
        # TX path
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))

        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))

        # Loopback path: TX -> CFO rotator -> channel -> RX
        self.connect((self.wifi_phy_tx, 0), (self.cfo_rotator, 0))
        self.connect((self.cfo_rotator, 0), (self.channel, 0))
        self.connect((self.channel, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))

        # RX message output
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

        print(f"[TEST] CFO={args.cfo_ppm} ppm -> {cfo_hz:.1f} Hz")
        print(f"[TEST] Phase increment={phase_inc:.6f} rad/sample")
        print(f"[TEST] SNR={args.snr} dB, sensitivity={args.sensitivity}")


def main():
    parser = argparse.ArgumentParser(
        description='CFO Injection Loopback Test'
    )
    parser.add_argument(
        '--cfo-ppm', type=float, default=0,
        help='CFO in parts per million (0=baseline, 500=0.05%% CFO)'
    )
    parser.add_argument(
        '--snr', type=float, default=30,
        help='SNR in dB (default: 30)'
    )
    parser.add_argument(
        '--sensitivity', type=float, default=0.01,
        help='RX sensitivity (default: 0.01)'
    )
    parser.add_argument(
        '--interval', type=int, default=1000,
        help='Frame interval in ms (default: 1000)'
    )
    parser.add_argument(
        '--duration', type=float, default=10,
        help='Test duration in seconds (default: 10)'
    )
    parser.add_argument(
        '--len', type=int, default=10,
        help='Payload length in bytes (default: 10)'
    )
    parser.add_argument(
        '--ldpc', action='store_true',
        help='Enable LDPC coding'
    )
    args = parser.parse_args()

    tb = CFOLoopbackTest(args)
    tb.start()

    print(f"\n[TEST] Running for {args.duration} seconds...")
    print(f"[TEST] Press Ctrl+C to stop early\n")

    start_time = time.time()
    try:
        while time.time() - start_time < args.duration:
            elapsed = time.time() - start_time
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            print(
                f"\r[TEST] Elapsed: {elapsed:.1f}s | "
                f"Sent: {sent} | Recv: {recv} | "
                f"Rate: {recv/max(1,sent)*100:.1f}%",
                end='', flush=True
            )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    print()
    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[TEST] ===== RESULTS =====")
    print(f"[TEST] CFO: {args.cfo_ppm} ppm")
    print(f"[TEST] Sent: {sent}")
    print(f"[TEST] Recv: {recv}")
    print(f"[TEST] Success Rate: {recv/max(1,sent)*100:.1f}%")

    if recv > 0:
        print(f"[TEST] PASS: Frames decoded")
        return 0
    else:
        print(f"[TEST] FAIL: No frames decoded")
        return 1


if __name__ == '__main__':
    sys.exit(main())
