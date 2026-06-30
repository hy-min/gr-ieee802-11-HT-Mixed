#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 68 T2: Offline replay of raw IQ capture through the full RX chain.

Reads /tmp/p68_raw_iq.bin (complex64) via gr.blocks.file_source and pipes
through wifi_phy_hier — the same RX chain used by test_usrp_minimal_loopback.py.
Enables all Phase 67 + 68 per-frame dumps so multi-frame Hhdr52 distribution
can be observed (vs the spurious "8 bit-identical dumps" Phase 67 saw, which
were actually 8 data symbols of a single frame; see Phase 68 T1 commit
69e5bf2 for the diagnostic that revealed this).

Dumps enabled (all opt-in via env var):
  IEEE80211_H60_NULL_PER_FRAME_DUMP=1
  IEEE80211_HHDR52_PER_FRAME_DUMP=1
  IEEE80211_LTF_SOURCE_PER_FRAME_DUMP=1
  IEEE80211_LTF_WRITE_PER_FRAME_DUMP=1

Other env vars set for completeness (matching standard USRP test config):
  IEEE80211_LSIG_RATE_FORCE=0xD
  IEEE80211_TIMING_OFFSET_APPLY=1
  IEEE80211_H52_NULL_INTERP=1   (Phase 60 pre-clean)

Usage:
  python examples/p68_replay_offline.py [--in /tmp/p68_raw_iq.bin] [--duration 60]
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

# Defaults — applied BEFORE importing gr modules so they propagate into C++ blocks.
DEFAULT_ENV = {
    'IEEE80211_LSIG_RATE_FORCE': '0xD',
    'IEEE80211_TIMING_OFFSET_APPLY': '1',
    'IEEE80211_H52_NULL_INTERP': '1',
    'IEEE80211_H60_NULL_PER_FRAME_DUMP': '1',
    'IEEE80211_HHDR52_PER_FRAME_DUMP': '1',
    'IEEE80211_LTF_SOURCE_PER_FRAME_DUMP': '1',
    'IEEE80211_LTF_WRITE_PER_FRAME_DUMP': '1',
}
for k, v in DEFAULT_ENV.items():
    os.environ.setdefault(k, v)

from gnuradio import gr, blocks
import pmt
import ieee802_11

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from wifi_phy_hier import wifi_phy_hier


class FcsLogger(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="fcs_logger", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)
        self.ok = 0
        self.fail = 0

    def handle(self, msg):
        meta = pmt.car(msg)
        crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        if crc:
            self.ok += 1
            print("*** FCS OK ***", flush=True)
        else:
            self.fail += 1


class ReplayTop(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "Phase 68 Offline Replay")

        # RX chain — same as test_usrp_minimal_loopback.py, but file_source replaces uhd_source.
        # Capture was saved with rx_scale=40 applied at capture time, so we set rx_scale=1 here.
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01
        )

        self.msg_debug_rx = blocks.message_debug()
        self.fcs = FcsLogger()
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

        self.file_source = blocks.file_source(gr.sizeof_gr_complex, args.in_path, False)
        self.head = blocks.head(gr.sizeof_gr_complex, args.head)

        # Report effective env vars so the log is self-documenting.
        for k in DEFAULT_ENV.keys():
            print(f"[REPLAY] env {k}={os.environ.get(k, '<unset>')}", flush=True)

        # Connections — same shape as test_usrp_minimal_loopback.py RX path.
        self.connect((self.file_source, 0), (self.head, 0))
        self.connect((self.head, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))

        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))

        print(f"[REPLAY] Topology: file_source({args.in_path}) -> head({args.head}) -> wifi_phy_rx -> null_sink", flush=True)


def main():
    parser = argparse.ArgumentParser(description='Phase 68 offline replay')
    parser.add_argument('--in', dest='in_path', default='/tmp/p68_raw_iq.bin', help='Input raw IQ file')
    parser.add_argument('--head', type=int, default=0, help='Max items to replay (0 = entire file)')
    parser.add_argument('--duration', type=float, default=0, help='Wall-clock duration in s (0 = entire file)')
    args = parser.parse_args()

    if not os.path.exists(args.in_path):
        print(f"[REPLAY] ERROR: Input file {args.in_path} does not exist", file=sys.stderr, flush=True)
        sys.exit(1)

    in_size = os.path.getsize(args.in_path)
    in_samples = in_size // 8  # complex64
    print(f"[REPLAY] Input file: {args.in_path} ({in_size} bytes, {in_samples} samples)", flush=True)

    if args.head == 0:
        args.head = in_samples

    tb = ReplayTop(args)
    tb.start()

    print(f"[REPLAY] Replaying up to {args.head} samples ({args.head / 20e6:.3f}s @ 20 MHz)...", flush=True)

    t0 = time.time()
    if args.duration > 0:
        # Wall-clock bound.
        while time.time() - t0 < args.duration:
            time.sleep(0.5)
            elapsed = time.time() - t0
            sent = tb.msg_debug_rx.num_messages()
            print(f"[REPLAY] t={elapsed:.1f}s RX={sent} FCS_OK={tb.fcs.ok} FAIL={tb.fcs.fail}", flush=True)
    else:
        # Wait for file source to drain.
        prev_count = 0
        stable = 0
        while True:
            time.sleep(0.5)
            elapsed = time.time() - t0
            sent = tb.msg_debug_rx.num_messages()
            print(f"[REPLAY] t={elapsed:.1f}s RX={sent} FCS_OK={tb.fcs.ok} FAIL={tb.fcs.fail}", flush=True)
            if sent == prev_count:
                stable += 1
                if stable >= 6:  # 3s of zero progress = drained
                    break
            else:
                stable = 0
                prev_count = sent

    tb.stop()
    tb.wait()

    print(f"[REPLAY] ===== RESULTS =====", flush=True)
    print(f"[REPLAY] RX messages: {tb.msg_debug_rx.num_messages()}", flush=True)
    print(f"[REPLAY] FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}", flush=True)


if __name__ == '__main__':
    main()
