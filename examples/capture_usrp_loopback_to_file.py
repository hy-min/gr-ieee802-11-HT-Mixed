#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 105: TX + raw-IQ capture-only script (no RX chain).

TX side uses wifi_phy_hier to generate HT-Mixed frames via uhd_sink on
A:0 TX/RX. RX side uses uhd_source on A:0 RX2 and writes the raw IQ
stream to a file. No frame_equalizer / sync_short / viterbi is run.

This isolates "USRP TX/RX signal quality" from "RX algorithm chain".
The captured IQ is intended to be replayed through test_file_replay_e2e.py
--phase rx to test whether the algorithm chain can decode a FRESH capture.

Configuration (per CLAUDE.md standard USRP test config):
  - addr=192.168.10.2, A:0 TX -> A:0 RX2 (same-board TDD)
  - freq=5250 MHz (Phase 81 quietest 5 GHz band)
  - rate=20 MHz, tx-gain=0, rx-gain=20
  - rx-scale=40 (matches test_usrp_minimal_loopback.py default)

Output: <args.out> (complex64, native byte order).

Usage:
  python examples/capture_usrp_loopback_to_file.py [--duration 60] \
      [--out /tmp/p105_usrp_capture_60s.bin]
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

# Standard USRP test config env vars (CLAUDE.md 2026-07-04) — apply BEFORE import.
DEFAULT_ENV = {
    'IEEE80211_LSIG_RATE_FORCE': '0xD',
    'IEEE80211_TIMING_OFFSET_APPLY': '1',
    'IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR': '1',
    'IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH': '1',
    'IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE': '16',
}
for k, v in DEFAULT_ENV.items():
    os.environ.setdefault(k, v)

from gnuradio import gr, blocks, uhd
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier


class CaptureLoopbackTop(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "Phase 105 TX + Raw IQ Capture")

        # === TX side ===
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=args.rate * 1e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=args.freq * 1e6,
            sensitivity=0.01,
        )
        self.msg_strobe = blocks.message_strobe(
            pmt.intern("x" * args.psdu_len), args.frame_interval_ms
        )
        self.mac = ieee802_11.mac(
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
        )
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.throttle = blocks.throttle(gr.sizeof_gr_complex, args.rate * 1e6)

        # UHD sink (TX) on A:0 TX/RX
        self.uhd_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=[0],
            ),
        )
        self.uhd_sink.set_subdev_spec("A:0", 0)
        self.uhd_sink.set_samp_rate(args.rate * 1e6)
        self.uhd_sink.set_center_freq(args.freq * 1e6, 0)
        self.uhd_sink.set_gain(args.tx_gain, 0)
        self.uhd_sink.set_antenna("TX/RX", 0)
        self.uhd_sink.set_bandwidth(160e6, 0)

        # === RX side (capture only) ===
        self.uhd_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                args=uhd.device_addr("recv_buff_size=16777216,num_recv_frames=256"),
                channels=[0],
            ),
        )
        self.uhd_source.set_subdev_spec(args.rx_subdev, 0)
        self.uhd_source.set_antenna(args.antenna, 0)
        self.uhd_source.set_samp_rate(args.rate * 1e6)
        self.uhd_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_source.set_gain(args.rx_gain, 0)
        self.uhd_source.set_bandwidth(args.rate * 1e6, 0)

        # Software gain on RX IQ (USRP signal is small)
        self.rx_scale = blocks.multiply_const_cc(args.rx_scale)

        # Two-stage copy buffers to absorb UHD burst pressure (Phase 58 T3)
        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(20000000)
        self.rx_buffer2 = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer2.set_min_output_buffer(10000000)

        # Cap to exactly duration * rate samples
        nsamples = int(args.duration * args.rate * 1e6)
        self.head = blocks.head(gr.sizeof_gr_complex, nsamples)
        self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.out, False)

        # === Wiring ===
        # TX
        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))
        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.throttle, 0))
        self.connect((self.throttle, 0), (self.uhd_sink, 0))

        # RX (capture only — no RX chain)
        self.connect((self.uhd_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.rx_scale, 0))
        self.connect((self.rx_scale, 0), (self.rx_buffer2, 0))
        self.connect((self.rx_buffer2, 0), (self.head, 0))
        self.connect((self.head, 0), (self.file_sink, 0))

        print(f"[P105-CAP] Freq={args.freq} MHz Rate={args.rate} MHz "
              f"TX={args.tx_gain}dB RX={args.rx_gain}dB rx_scale={args.rx_scale}",
              flush=True)
        print(f"[P105-CAP] Subdev: TX=A:0/TX-RX RX={args.rx_subdev}/{args.antenna}",
              flush=True)
        print(f"[P105-CAP] Output: {args.out} max {nsamples} samples "
              f"({args.duration}s)", flush=True)


def main():
    p = argparse.ArgumentParser(description='Phase 105 TX + Raw IQ Capture')
    p.add_argument('--freq', type=float, default=5250, help='Center frequency MHz')
    p.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    p.add_argument('--tx-gain', type=float, default=0, help='USRP TX gain dB')
    p.add_argument('--rx-gain', type=float, default=20, help='USRP RX gain dB')
    p.add_argument('--rx-scale', type=float, default=40.0,
                   help='RX software gain multiplier')
    p.add_argument('--rx-subdev', type=str, default='A:0', help='RX subdev spec')
    p.add_argument('--antenna', type=str, default='RX2', help='RX antenna')
    p.add_argument('--psdu-len', type=int, default=10, help='PSDU payload bytes')
    p.add_argument('--frame-interval-ms', type=int, default=200,
                   help='Frame interval ms')
    p.add_argument('--duration', type=float, default=60, help='Capture duration s')
    p.add_argument('--out', type=str, default='/tmp/p105_usrp_capture_60s.bin',
                   help='Output IQ file')
    p.add_argument('--warmup', type=float, default=5.0,
                   help='Sleep seconds before start (UHD settling)')
    args = p.parse_args()

    print(f"[P105-CAP] Env: LSIG_RATE_FORCE={os.environ.get('IEEE80211_LSIG_RATE_FORCE')} "
          f"TIMING_OFFSET_APPLY={os.environ.get('IEEE80211_TIMING_OFFSET_APPLY')} "
          f"SYNC_SHORT_BOXCAR={os.environ.get('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR')}",
          flush=True)

    tb = CaptureLoopbackTop(args)
    print(f"[P105-CAP] Starting top block (warmup {args.warmup}s)...", flush=True)
    time.sleep(args.warmup)
    tb.start()
    print(f"[P105-CAP] Running for {args.duration}s...", flush=True)
    time.sleep(args.duration + 0.5)
    tb.stop()
    tb.wait()

    if os.path.exists(args.out):
        size = os.path.getsize(args.out)
        nsamp = size // 8
        print(f"[P105-CAP] Done. File: {size} bytes, {nsamp} samples "
              f"({nsamp/(args.rate*1e6):.2f}s)", flush=True)
    else:
        print(f"[P105-CAP] ERROR: {args.out} not created", flush=True)
        sys.exit(1)


if __name__ == '__main__':
    sys.exit(main() or 0)
