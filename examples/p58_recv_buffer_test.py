#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 58 Task 3: Sweep UHD recv_buff_size + num_recv_frames to find overflow-minimizing config.
Outputs to /tmp/p58_t3_recv_buffer.log

Usage: p58_recv_buffer_test.py --config <preset_label> --duration <seconds>

The script is a SELF-CONTAINED test for ONE config. To sweep, invoke it multiple
times with different --config values (we deliberately do NOT loop in-process to
avoid GR/UHD teardown issues — see comments below).

Mechanism: Build a minimal GR flowgraph (source + probe), schedule a fixed number
of samples via issue_stream_cmd, then count how many samples arrive at the probe.
If actual < expected, samples were lost (overflows in UHD terminology).
"""
import argparse
import os
import sys
import time
import numpy as np
from gnuradio import gr
from gnuradio import uhd


class _Probe(gr.sync_block):
    """Sink block that counts samples. out_sig=[] so no consumer needed."""

    def __init__(self):
        gr.sync_block.__init__(self, "probe",
                               in_sig=[np.complex64],
                               out_sig=[])
        self.n = 0

    def work(self, input_items, output_items):
        n = len(input_items[0])
        self.n += n
        return n


class _TopBlock(gr.top_block):
    def __init__(self, recv_buff_size, num_recv_frames, freq, rate, duration):
        super().__init__("p58_t3")
        addr = uhd.device_addr(f"recv_buff_size={recv_buff_size},num_recv_frames={num_recv_frames}")
        self.src = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                args=addr,
                channels=[0],
            ),
        )
        self.src.set_subdev_spec("A:0", 0)
        self.src.set_antenna("RX2", 0)
        self.src.set_samp_rate(rate * 1e6)
        self.src.set_center_freq(freq * 1e6, 0)
        self.src.set_bandwidth(rate * 1e6, 0)

        self.probe = _Probe()
        self.connect((self.src, 0), (self.probe, 0))

        self._n_samples = int(duration * rate * 1e6)

    def issue(self):
        sc = uhd.stream_cmd(uhd.STREAM_MODE_NUM_SAMPS_AND_DONE)
        sc.num_samps = self._n_samples
        sc.stream_now = True
        self.src.issue_stream_cmd(sc)

    def samples_received(self):
        return self.probe.n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=10)
    parser.add_argument('--config', type=str, required=True,
                        help='preset label: default | 16MB-256 | 64MB-512')
    parser.add_argument('--freq', type=float, default=5890)
    parser.add_argument('--rate', type=float, default=20)
    args = parser.parse_args()

    presets = {
        'default': (1048576, 32),       # UHD defaults
        '16MB-256': (16 * 1048576, 256),
        '64MB-512': (64 * 1048576, 512),
    }
    if args.config not in presets:
        print(f"unknown config '{args.config}'; choose from {list(presets.keys())}", flush=True)
        os._exit(2)
    buff, nframes = presets[args.config]

    print(f"CONFIG: {args.config} recv_buff_size={buff} num_recv_frames={nframes} duration={args.duration}s", flush=True)
    tb = _TopBlock(buff, nframes, args.freq, args.rate, args.duration)
    tb.start()
    tb.issue()
    for i in range(int(args.duration * 2)):
        time.sleep(0.5)
    samples = tb.samples_received()
    elapsed = args.duration
    pct = 100.0 * samples / tb._n_samples if tb._n_samples > 0 else 0.0
    print(f"RESULT: config={args.config} recv_buff={buff} n_frames={nframes} "
          f"elapsed={elapsed:.2f}s samples={samples} expected={tb._n_samples} pct={pct:.1f}", flush=True)
    # Force exit to avoid GR __del__ hang
    os._exit(0)


if __name__ == '__main__':
    main()
