#!/home/hy/conda/envs/gnuradio/bin/python
"""
Same-board FDD test: TX on TX/RX port, RX on RX2 port of the SAME daughterboard
This verifies the daughterboard itself works.
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


class SameBoardFDDTest(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "Same Board FDD Test")
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

        self.msg_strobe = blocks.message_strobe(
            pmt.intern("x" * args.len), args.interval
        )
        self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
        self.msg_debug_mac = blocks.message_debug()
        self.msg_debug_rx = blocks.message_debug()

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

        # === SAME BOARD FDD: Use A:0 for both TX and RX ===
        # UBX supports FDD: TX on TX/RX, RX on RX2 simultaneously
        board = args.board  # "A:0" or "B:0"
        print(f"[FDD] Using {board} for BOTH TX and RX")
        print(f"[FDD] TX antenna: TX/RX port")
        print(f"[FDD] RX antenna: RX2 port")
        print(f"[FDD] IMPORTANT: Connect antenna to TX/RX port for TX")
        print(f"[FDD] IMPORTANT: Connect another antenna to RX2 port for RX")

        # USRP TX
        self.uhd_usrp_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_sink.set_gain(args.tx_gain, 0)
        self.uhd_usrp_sink.set_antenna("TX/RX", 0)
        self.uhd_usrp_sink.set_subdev_spec(board, 0)

        # USRP RX (same board, RX2 port)
        self.uhd_usrp_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16", channels=range(1)),
        )
        self.uhd_usrp_source.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_source.set_gain(args.rx_gain, 0)
        self.uhd_usrp_source.set_antenna("RX2", 0)
        self.uhd_usrp_source.set_subdev_spec(board, 0)
        self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, 0)

        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(5000000)
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

        # Connections
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.encoding_stripper, 'pdu'))
        self.msg_connect((self.encoding_stripper, 'pdu'), (self.wifi_phy_tx, 'mac_in'))
        self.msg_connect((self.mac, 'phy out'), (self.msg_debug_mac, 'store'))

        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.uhd_usrp_sink, 0))

        self.connect((self.uhd_usrp_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))

        print(f"[FDD] Config: freq={args.freq}MHz tx_gain={args.tx_gain} rx_gain={args.rx_gain}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Same-board FDD Test')
    parser.add_argument('--board', type=str, default='A:0', choices=['A:0', 'B:0'],
                        help='Daughterboard to use for both TX and RX')
    parser.add_argument('--freq', type=float, default=5180, help='Center frequency MHz')
    parser.add_argument('--tx-gain', type=float, default=10, help='TX gain dB')
    parser.add_argument('--rx-gain', type=float, default=20, help='RX gain dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    parser.add_argument('--interval', type=int, default=500, help='Frame interval ms')
    parser.add_argument('--duration', type=float, default=10, help='Test duration')
    parser.add_argument('--len', type=int, default=10, help='Payload bytes')
    parser.add_argument('--ldpc', action='store_true', help='Enable LDPC')
    args = parser.parse_args()

    tb = SameBoardFDDTest(args)
    tb.start()

    print(f"\n[FDD] Running for {args.duration}s...")
    start_time = time.time()
    try:
        while time.time() - start_time < args.duration:
            elapsed = time.time() - start_time
            sent = tb.msg_debug_mac.num_messages()
            recv = tb.msg_debug_rx.num_messages()
            print(f"\r[FDD] {elapsed:.1f}s | Sent: {sent} | Recv: {recv} | Rate: {recv/max(1,sent)*100:.1f}%",
                  end='', flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    print()
    tb.stop()
    tb.wait()

    sent = tb.msg_debug_mac.num_messages()
    recv = tb.msg_debug_rx.num_messages()
    print(f"\n[FDD] ===== RESULTS =====")
    print(f"[FDD] Sent: {sent}")
    print(f"[FDD] Recv: {recv}")
    if recv > 0:
        print(f"[FDD] ✅ Same-board FDD WORKS! Board {args.board} is functional.")
    else:
        print(f"[FDD] ❌ Same-board FDD FAILED on {args.board}.")


if __name__ == '__main__':
    main()
