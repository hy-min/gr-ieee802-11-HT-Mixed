#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 146: Hardware-free RX-chain throughput probe.

Systematic-debugging Phase 3 minimal test. Pushes pure complex noise through
the RX decode chain (wifi_phy_hier) with NO throttle and measures the maximum
sustained throughput in MHz. This reproduces the dominant realtime condition
(the chain spends ~99% of wall-clock on inter-frame noise between 100ms-interval
frames). If throughput < 20 MHz, the decode chain CANNOT sustain the USRP
realtime rate, so when it shares a buffer with the capture file_sink it
backpressures uhd_source and truncates the capture.

Usage:
  python p146_rx_throughput_probe.py [--samples N] [--amp A]
  (stderr is left intact so we can count the fprintf volume separately)
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

# Match test_usrp_minimal_loopback.py baked-in env (Phase 89 sync_short, Phase 18, 34)
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks, analog
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier


class ThroughputProbe(gr.top_block):
    def __init__(self, nsamp, amp, iq_file=None):
        gr.top_block.__init__(self, "RX Throughput Probe")
        if iq_file:
            self.src = blocks.file_source(gr.sizeof_gr_complex, iq_file, False)
        else:
            self.src = analog.noise_source_c(analog.GR_GAUSSIAN, amp, 0)
        self.head = blocks.head(gr.sizeof_gr_complex, nsamp)
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.01)
        self.msg_debug_rx = blocks.message_debug()
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)
        self.connect((self.src, 0), (self.head, 0))
        self.connect((self.head, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--samples', type=int, default=40_000_000, help='total noise samples (40M = 2s @ 20MHz)')
    p.add_argument('--amp', type=float, default=0.5, help='noise RMS amplitude')
    p.add_argument('--file', type=str, default='', help='replay a real IQ capture instead of gaussian noise')
    args = p.parse_args()

    nsamp = args.samples
    src_desc = f"file {args.file}" if args.file else f"gaussian noise amp={args.amp}"
    print(f"[PROBE] Feeding {nsamp} samples ({nsamp/20e6:.2f}s @ 20MHz) from {src_desc} "
          f"through wifi_phy_rx, no throttle ...", flush=True)
    tb = ThroughputProbe(nsamp, args.amp, args.file or None)
    t0 = time.time()
    tb.start()
    tb.wait()
    dt = time.time() - t0
    mhz = nsamp / dt / 1e6
    print(f"[PROBE] DONE: {nsamp} samples in {dt:.2f}s wall-clock", flush=True)
    print(f"[PROBE] Throughput = {mhz:.3f} MHz  (realtime target = 20.0 MHz)", flush=True)
    print(f"[PROBE] RX messages decoded: {tb.msg_debug_rx.num_messages()}", flush=True)
    if mhz >= 20.0:
        print("[PROBE] VERDICT: chain CAN sustain 20 MHz — CPU-bound hypothesis REFUTED", flush=True)
    else:
        print(f"[PROBE] VERDICT: chain runs at {mhz/20.0*100:.1f}% of realtime — "
              f"CPU-bound hypothesis CONFIRMED (would backpressure shared capture buffer)",
              flush=True)


if __name__ == '__main__':
    main()
