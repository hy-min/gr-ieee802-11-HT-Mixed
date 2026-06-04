#!/home/hy/conda/envs/gnuradio/bin/python
"""
Cross-verification: Swap TX/RX daughterboards
Test if signal is received when TX is on B:0 and RX is on A:0
"""
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr, blocks, uhd
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from wifi_phy_hier import wifi_phy_hier


class CrossVerifyTest(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "Cross Verification")
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

        # RX PHY
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01
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

        # === CROSS VERIFY: TX on B:0, RX on A:0 ===
        print(f"[CROSS] TX -> Radio#1 (B:0) TX/RX port")
        print(f"[CROSS] RX -> Radio#0 (A:0) RX2 port")

        # USRP TX (Radio 1, B:0, TX/RX port)
        self.uhd_usrp_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_sink.set_gain(args.tx_gain, 0)
        self.uhd_usrp_sink.set_antenna("TX/RX", 0)
        self.uhd_usrp_sink.set_subdev_spec("B:0", 0)  # CHANGED: B:0 for TX

        # USRP RX (Radio 0, A:0, RX2 port)
        self.uhd_usrp_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_source.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_source.set_gain(args.rx_gain, 0)
        self.uhd_usrp_source.set_antenna("RX2", 0)
        self.uhd_usrp_source.set_subdev_spec("A:0", 0)  # CHANGED: A:0 for RX
        self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, 0)

        # RX Buffer
        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(5000000)

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
        self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))

        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

        print(f"[CROSS] Config: freq={args.freq}MHz rate={args.rate}MHz tx_gain={args.tx_gain} rx_gain={args.rx_gain}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Cross Verification: Swap TX/RX')
    parser.add_argument('--freq', type=float, default=5180, help='Center frequency in MHz')
    parser.add_argument('--tx-gain', type=float, default=10, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=20, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate in MHz')
    parser.add_argument('--interval', type=int, default=500, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=10, help='Test duration seconds')
    parser.add_argument('--len', type=int, default=10, help='Payload length bytes')
    parser.add_argument('--ldpc', action='store_true', help='Enable LDPC')
    args = parser.parse_args()

    tb = CrossVerifyTest(args)
    tb.start()

    print(f"\n[CROSS] Running for {args.duration} seconds...")
    print(f"[CROSS] {'Time':>6s} | {'Sent':>5s} | {'Recv':>5s} | {'Rate':>6s} | Status")
    print(f"[CROSS] {'------':>6s} | {'-----':>5s} | {'-----':>5s} | {'------':>6s} | ------")

    start_time = time.time()
    last_sent = 0
    last_recv = 0

    try:
        while time.time() - start_time < args.duration:
            elapsed = time.time() - start_time
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            rate = recv / max(1, sent) * 100

            if recv > last_recv:
                status = "RECV+"
            elif sent > last_sent:
                status = "TXING"
            else:
                status = "WAIT"

            last_sent = sent
            last_recv = recv

            print(f"\r[CROSS] {elapsed:>6.1f} | {sent:>5d} | {recv:>5d} | {rate:>5.1f}% | {status}", end='', flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    print()
    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[CROSS] ===== RESULTS =====")
    print(f"[CROSS] Sent: {sent}")
    print(f"[CROSS] Recv: {recv}")
    print(f"[CROSS] Success Rate: {recv/max(1,sent)*100:.1f}%")

    if recv > 0:
        print(f"[CROSS] ✅ Cross-verify PASSED! Signal received on swapped config.")
        print(f"[CROSS] This suggests original Radio#0 TX or Radio#1 RX has hardware issue.")
    else:
        print(f"[CROSS] ❌ Cross-verify FAILED. No signal received even with swapped config.")
        print(f"[CROSS] Possible causes:")
        print(f"[CROSS]   1. Both daughterboards have TX/RX issues")
        print(f"[CROSS]   2. Antenna connection problem (wrong port or loose)")
        print(f"[CROSS]   3. Antenna not 2.4GHz compatible")
        print(f"[CROSS]   4. TX gain too low for current antenna distance")


if __name__ == '__main__':
    main()
