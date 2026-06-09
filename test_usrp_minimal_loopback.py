#!/home/hy/conda/envs/gnuradio/bin/python
"""
Minimal USRP loopback test - NO GUI, NO DEBUG LOGS
Disables C-layer stderr to eliminate fprintf flood.
Captures: sent count, recv count, FCS status
Usage: python test_usrp_minimal_loopback.py --duration 10
"""
import argparse
import os
import sys
import time
import signal

# Disable ALL debug output from C layer
# DEBUG: Temporarily disabled to see C-layer logs
# orig_stderr_fd = os.dup(2)
# with open('/dev/null', 'w') as devnull:
#     os.dup2(devnull.fileno(), 2)

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

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
        
        # USRP TX (Radio 0, TX/RX port)
        self.uhd_usrp_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_sink.set_gain(args.tx_gain, 0)
        self.uhd_usrp_sink.set_antenna("TX/RX", 0)
        self.uhd_usrp_sink.set_subdev_spec("A:0", 0)
        
        # USRP RX (Radio 0, RX2 port - same board TDD)
        self.uhd_usrp_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_source.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_source.set_gain(args.rx_gain, 0)
        self.uhd_usrp_source.set_antenna("RX2", 0)
        self.uhd_usrp_source.set_subdev_spec("A:0", 0)
        self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, 0)
        
        # RX Buffer
        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(5000000)

        # RX software gain: amplify low-amplitude USRP signal to ~1.0
        # Observed USRP RX amplitude ~0.0265, need ~40x gain to reach ~1.06
        self.rx_gain_block = blocks.multiply_const_cc(args.rx_scale)
        
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
        
        # RX path
        self.connect((self.uhd_usrp_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.rx_gain_block, 0))
        if args.capture:
            self.connect((self.rx_gain_block, 0), (self.head, 0))
            self.connect((self.head, 0), (self.file_sink, 0))
        self.connect((self.rx_gain_block, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
        
        print(f"[TEST] Config: freq={args.freq}MHz rate={args.rate}MHz tx_gain={args.tx_gain} rx_gain={args.rx_gain}")
        print(f"[TEST] C-layer stderr redirected to /dev/null")
        if args.capture:
            print(f"[TEST] Raw IQ capture enabled: {args.capture}")


def main():
    parser = argparse.ArgumentParser(description='Minimal USRP Loopback Test (No GUI)')
    parser.add_argument('--freq', type=float, default=5180, help='Center frequency in MHz')
    parser.add_argument('--tx-gain', type=float, default=10, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=20, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate in MHz')
    parser.add_argument('--interval', type=int, default=1000, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=10, help='Test duration seconds')
    parser.add_argument('--len', type=int, default=10, help='Payload length bytes')
    parser.add_argument('--ldpc', action='store_true', help='Enable LDPC')
    parser.add_argument('--mcs', type=int, default=0, choices=range(9), help='MCS mode')
    parser.add_argument('--rx-scale', type=float, default=40.0, help='RX software gain (multiplier)')
    parser.add_argument('--capture', type=str, default='', help='Capture raw IQ to file')
    args = parser.parse_args()
    
    tb = MinimalUSRPTest(args)
    tb.start()

    # CRITICAL: Wait for LO to lock before sending data
    print("\n[TEST] Waiting for LO to stabilize...")
    time.sleep(1.0)
    print("[TEST] LO should be locked now.")

    # Restore stderr for Python print output
    # os.dup2(orig_stderr_fd, 2)
    # os.close(orig_stderr_fd)

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
    
    if args.capture and os.path.exists(args.capture):
        size = os.path.getsize(args.capture)
        print(f"[TEST] Capture file: {args.capture} ({size} bytes)")

if __name__ == '__main__':
    main()
