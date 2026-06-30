#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 68 T2: Raw IQ capture-only script.

Standalone UHD capture to /tmp/p68_raw_iq.bin. Runs concurrently with
test_usrp_minimal_loopback.py (TX side) to obtain a multi-frame
~30-60s capture for offline replay.

Configuration matches the standard USRP test config (Phase 65):
  - addr=192.168.10.2, A:0 TX -> A:0 RX2 (same-board)
  - freq=5890 MHz, rate=20 MHz, rx-gain=20
  - recv_buff_size=16MB, num_recv_frames=256 (Phase 58 T3 verified 100% delivery)

Output: /tmp/p68_raw_iq.bin (complex64, native byte order).

Usage:
  python examples/p68_capture_raw_iq.py [--duration 60] [--out /tmp/p68_raw_iq.bin]
"""
import argparse
import os
import sys
import time
import signal

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr, blocks, uhd


class CaptureTop(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "Phase 68 Raw IQ Capture")
        nsamples = int(args.duration * args.rate * 1e6)
        print(f"[CAPTURE] Configuring UHD source at {args.freq} MHz, rate {args.rate} MHz", flush=True)
        print(f"[CAPTURE] Subdev: {args.rx_subdev}, antenna: {args.antenna}", flush=True)
        print(f"[CAPTURE] Output: {args.out}, max {nsamples} samples ({args.duration}s)", flush=True)

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

        # RX software gain (matches test_usrp_minimal_loopback.py default: 40x)
        self.rx_gain_block = blocks.multiply_const_cc(args.rx_scale)

        # Two-stage copy buffers to absorb UHD burst pressure (Phase 58 T3).
        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(20000000)
        self.rx_buffer2 = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer2.set_min_output_buffer(10000000)

        # Cap to exactly duration * rate samples.
        self.head = blocks.head(gr.sizeof_gr_complex, nsamples)
        self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.out, False)

        self.connect((self.uhd_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.rx_gain_block, 0))
        self.connect((self.rx_gain_block, 0), (self.rx_buffer2, 0))
        self.connect((self.rx_buffer2, 0), (self.head, 0))
        self.connect((self.head, 0), (self.file_sink, 0))

        print(f"[CAPTURE] Topology: uhd_source -> rx_buffer -> *{args.rx_scale} -> rx_buffer2 -> head({nsamples}) -> file_sink", flush=True)


def main():
    parser = argparse.ArgumentParser(description='Phase 68 Raw IQ capture')
    parser.add_argument('--freq', type=float, default=5890, help='Center frequency in MHz')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate in MHz')
    parser.add_argument('--rx-gain', type=float, default=20, help='USRP RX gain dB')
    parser.add_argument('--rx-scale', type=float, default=40.0, help='RX software gain multiplier')
    parser.add_argument('--rx-subdev', type=str, default='A:0', help='RX subdev spec')
    parser.add_argument('--antenna', type=str, default='RX2', help='RX antenna (RX2 same-board, TX/RX cross-board)')
    parser.add_argument('--duration', type=float, default=60, help='Capture duration in seconds')
    parser.add_argument('--out', type=str, default='/tmp/p68_raw_iq.bin', help='Output file path')
    args = parser.parse_args()

    tb = CaptureTop(args)

    # Write header so we can verify the file content type.
    print(f"[CAPTURE] top_block type: {type(tb).__name__}", flush=True)

    print(f"[CAPTURE] Starting top block...", flush=True)
    tb.start()
    print(f"[CAPTURE] Running for {args.duration}s...", flush=True)

    interrupted = False

    def handle_signal(_sig, _frame):
        nonlocal interrupted
        print("\n[CAPTURE] Interrupted, stopping...", flush=True)
        interrupted = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    t0 = time.time()
    while time.time() - t0 < args.duration and not interrupted:
        time.sleep(1.0)
        elapsed = time.time() - t0
        print(f"[CAPTURE] t={elapsed:.1f}s / {args.duration}s", flush=True)

    print(f"[CAPTURE] Stopping top block...", flush=True)
    tb.stop()
    tb.wait()

    if os.path.exists(args.out):
        size = os.path.getsize(args.out)
        nsamp = size // 8  # complex64 = 8 bytes
        secs = nsamp / (args.rate * 1e6)
        print(f"[CAPTURE] ===== RESULTS =====")
        print(f"[CAPTURE] File: {args.out}")
        print(f"[CAPTURE] Size: {size} bytes")
        print(f"[CAPTURE] Samples: {nsamp}")
        print(f"[CAPTURE] Captured duration: {secs:.3f}s (target {args.duration}s)")
        print(f"[CAPTURE] Capture rate: {nsamp / max(1e-9, time.time() - t0):.0f} samples/sec")
    else:
        print(f"[CAPTURE] ERROR: Output file {args.out} not created!", flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
