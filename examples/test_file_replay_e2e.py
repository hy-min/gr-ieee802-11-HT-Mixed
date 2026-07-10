#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 103: File-replay e2e harness.

Systematic-debugging Phase 4.1: build a SOFTWARE-ONLY e2e test that bypasses
UHD streaming entirely. Two phases:
  1. TX: generate known-good HT-Mixed IQ via wifi_phy_hier (no UHD), save to file
  2. RX: read IQ from file through wifi_phy_hier (no UHD), count FCS_OK

Goal: validate that the full RX chain (sync_short -> sync_long -> equalizer ->
viterbi -> FCS) works end-to-end on a CLEAN, deterministic IQ stream.

This isolates "is the algorithm chain correct?" from "does UHD streaming work?"
which is the upstream blocker from Phase 87-102 chain.

Pass criterion: FCS_OK >= 1 (any success means algorithm chain is correct).

Run with --diag /tmp/p104_diag.csv to capture per-frame metrics.
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

# Standard USRP test config env vars (CLAUDE.md 2026-07-04) — apply BEFORE import.
DEFAULT_ENV = {
    'IEEE80211_LSIG_RATE_FORCE': '0xD',
    'IEEE80211_TIMING_OFFSET_APPLY': '1',
    # Phase 89: sync_short boxcar detector (replaces REFUTED MA(48)/MA(64) ratio)
    'IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR': '1',
    # Phase 89: sync_short adaptive threshold (median*10 with 3.0 startup gate)
    'IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH': '1',
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
            print("[FCS_OK]", flush=True)
        else:
            self.fail += 1
            print("[FCS_FAIL]", flush=True)


class DiagLogger(gr.basic_block):
    """Per-frame diagnostic logger. Writes (frame_idx, timestamp_s, msg_size, mac_crc)
    per detected frame. msg_size is the PSDU byte count; mac_crc=1 means FCS_OK.
    """
    def __init__(self, csv_path):
        gr.basic_block.__init__(self, name="diag_logger", in_sig=None, out_sig=None)
        self.message_port_register_in(pmt.intern("pdu"))
        self.set_msg_handler(pmt.intern("pdu"), self.handle)
        self.csv_path = csv_path
        self.frame_count = 0
        with open(csv_path, 'w') as f:
            f.write("frame_idx,timestamp_s,msg_size,mac_crc\n")

    def handle(self, msg):
        meta = pmt.car(msg)
        data = pmt.cdr(msg)
        self.frame_count += 1
        crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
        size = len(pmt.u8vector_elements(data)) if pmt.is_u8vector(data) else 0
        with open(self.csv_path, 'a') as f:
            f.write(f"{self.frame_count},{time.time():.3f},{size},{crc}\n")


class TxTop(gr.top_block):
    """Generate IQ from a TX wifi_phy_hier; write to file. No UHD."""
    def __init__(self, args):
        gr.top_block.__init__(self, "Phase 103 TX Generator")
        self.wifi_phy_tx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01,
        )
        self.msg_strobe = blocks.message_strobe(
            pmt.intern("x" * args.len), args.interval
        )
        self.mac = ieee802_11.mac(
            [0x23, 0x23, 0x23, 0x23, 0x23, 0x23],
            [0x42, 0x42, 0x42, 0x42, 0x42, 0x42],
            [0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
        )
        # wifi_phy_hier requires port 0 (input) connected even for TX-only.
        # Feed a null source — TX chain internally generates real samples
        # from the message strobe -> mac -> mac_in path.
        self.null_src = blocks.null_source(gr.sizeof_gr_complex)
        self.throttle = blocks.throttle(gr.sizeof_gr_complex, args.rate * 1e6)
        self.head = blocks.head(gr.sizeof_gr_complex, int(args.tx_duration * args.rate * 1e6))
        self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.iq_file, False)

        self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
        self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))

        self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
        self.connect((self.wifi_phy_tx, 0), (self.throttle, 0))
        self.connect((self.throttle, 0), (self.head, 0))
        self.connect((self.head, 0), (self.file_sink, 0))


class RxTop(gr.top_block):
    """Read IQ from file through RX wifi_phy_hier; report FCS. No UHD."""
    def __init__(self, args):
        gr.top_block.__init__(self, "Phase 103 RX Replay")
        self.file_source = blocks.file_source(gr.sizeof_gr_complex, args.iq_file, False)
        # Loop the file N times so the receiver sees many copies of the same frame.
        # This is critical: a single file dump is ~5s of TX which is not enough
        # to drive sync_short's algorithm past startup transients.
        if args.loop > 1:
            # blocks.file_source doesn't natively loop. We instead read N * file_size
            # by setting head accordingly. The file_source rewinds on EOF only with
            # the repeat=True flag.
            self.file_source = blocks.file_source(
                gr.sizeof_gr_complex, args.iq_file, True
            )
        self.head = blocks.head(
            gr.sizeof_gr_complex,
            int(args.rx_duration * args.rate * 1e6)
        )
        self.wifi_phy_rx = wifi_phy_hier(
            bandwidth=10e6,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,
            frequency=5.89e9,
            sensitivity=0.01,
        )
        self.msg_debug_rx = blocks.message_debug()
        self.fcs = FcsLogger()
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)

        self.connect((self.file_source, 0), (self.head, 0))
        self.connect((self.head, 0), (self.wifi_phy_rx, 0))
        self.connect((self.wifi_phy_rx, 0), (self.null_sink, 0))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.msg_debug_rx, 'store'))
        self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.fcs, 'pdu'))
        if args.diag:
            self.diag = DiagLogger(args.diag)
            self.msg_connect((self.wifi_phy_rx, 'mac_out'), (self.diag, 'pdu'))


def phase1_generate(args):
    print(f"[P103-TX] Generating IQ to {args.iq_file}", flush=True)
    print(f"[P103-TX]   len={args.len} interval={args.interval}ms "
          f"tx_dur={args.tx_duration}s rate={args.rate}MHz", flush=True)
    tb = TxTop(args)
    tb.start()
    time.sleep(args.tx_duration + 1)
    tb.stop()
    tb.wait()
    size = os.path.getsize(args.iq_file) if os.path.exists(args.iq_file) else 0
    nsamp = size // 8
    print(f"[P103-TX] Done. File: {size} bytes, {nsamp} samples "
          f"({nsamp/(args.rate*1e6):.3f}s)", flush=True)
    return size > 0


def phase2_replay(args):
    print(f"[P103-RX] Reading {args.iq_file}, replay {args.rx_duration}s "
          f"(file_source repeat={args.loop > 1})", flush=True)
    tb = RxTop(args)
    tb.start()
    t0 = time.time()
    while time.time() - t0 < args.rx_duration:
        time.sleep(0.5)
        elapsed = time.time() - t0
        print(f"[P103-RX] t={elapsed:.1f}s RX={tb.msg_debug_rx.num_messages()} "
              f"FCS_OK={tb.fcs.ok} FCS_FAIL={tb.fcs.fail}", flush=True)
    tb.stop()
    tb.wait()
    return tb.msg_debug_rx.num_messages(), tb.fcs.ok, tb.fcs.fail


def main():
    p = argparse.ArgumentParser(description='Phase 103 file-replay e2e')
    p.add_argument('--iq-file', default='/tmp/p103_iq.bin')
    p.add_argument('--len', type=int, default=10, help='Payload bytes')
    p.add_argument('--interval', type=int, default=200, help='Frame interval ms')
    p.add_argument('--rate', type=float, default=20, help='Sample rate MHz')
    p.add_argument('--tx-duration', type=float, default=10.0, help='TX capture duration s')
    p.add_argument('--rx-duration', type=float, default=30.0, help='RX replay duration s')
    p.add_argument('--loop', type=int, default=1, help='Loop file in RX (>1 = repeat=True)')
    p.add_argument('--phase', choices=['tx', 'rx', 'both'], default='both')
    p.add_argument('--diag', type=str, default='', help='Path to per-frame diagnostic CSV (appends per-frame metrics)')
    p.add_argument('--phase137-on', action='store_true',
                   help='Phase 137: enable stable-null-aware masking '
                        '(IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 + '
                        'IEEE80211_HTSIG_NULL_PILOT_MASK=1)')
    p.add_argument('--phase138-on', action='store_true',
                   help='Phase 138: enable H52 freq-domain low-pass filter '
                        '(IEEE80211_H52_FREQ_LOWPASS=1 + '
                        'IEEE80211_H52_FREQ_LOWPASS_K=N)')
    p.add_argument('--phase138-k', type=int, default=10,
                   help='Phase 138: K value for freq-domain low-pass '
                        '(default 10, range 1..51)')
    p.add_argument('--phase139-on', action='store_true',
                   help='Phase 139: enable 2-way L-LTF0+L-LTF1 SNR-weighted H52 '
                        'for L-SIG viterbi (IEEE80211_H52_2WAY_DEFAULT=1)')
    p.add_argument('--phase139-3way', action='store_true',
                   help='Phase 139: enable 3-way HT-SIG pilot refinement '
                        '(IEEE80211_HT_SIG_PILOT_REFINE=1, requires --phase139-on)')
    p.add_argument('--phase139-4way', action='store_true',
                   help='Phase 139: enable 4-way HT-SIG0+HT-SIG1 pilot refinement '
                        '(IEEE80211_HT_SIG_PILOT_REFINE=2, requires --phase139-on)')
    args = p.parse_args()

    # Phase 137: stable-null-aware masking (opt-in via --phase137-on).
    # Default OFF preserves baseline.
    if args.phase137_on:
        os.environ['IEEE80211_HTSIG_NULL_SCS'] = '-21,-13,-7,7,21'
        os.environ['IEEE80211_HTSIG_NULL_PILOT_MASK'] = '1'
        os.environ['IEEE80211_HT_PER_SYMBOL_CPE'] = '1'  # required for pilot CPE code path
        print(f"[TEST] Phase 137 ENABLED: "
              "IEEE80211_HTSIG_NULL_SCS=-21,-13,-7,7,21 "
              "IEEE80211_HTSIG_NULL_PILOT_MASK=1", flush=True)

    # Phase 138: H52 frequency-domain low-pass filter (opt-in via --phase138-on).
    # Default OFF preserves baseline.
    if args.phase138_on:
        os.environ['IEEE80211_H52_FREQ_LOWPASS'] = '1'
        os.environ['IEEE80211_H52_FREQ_LOWPASS_K'] = str(args.phase138_k)
        print(f"[TEST] Phase 138 ENABLED: "
              "IEEE80211_H52_FREQ_LOWPASS=1 "
              f"IEEE80211_H52_FREQ_LOWPASS_K={args.phase138_k}", flush=True)

    # Phase 139: 2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi
    # (opt-in via --phase139-on). Default OFF preserves baseline.
    if args.phase139_on:
        os.environ['IEEE80211_H52_2WAY_DEFAULT'] = '1'
        print(f"[TEST] Phase 139 ENABLED: IEEE80211_H52_2WAY_DEFAULT=1 "
              f"(2-way L-LTF0+L-LTF1 SNR-weighted H52 for L-SIG viterbi)",
              flush=True)

    if args.phase139_3way:
        os.environ['IEEE80211_HT_SIG_PILOT_REFINE'] = '1'
        print(f"[TEST] Phase 139 3-way ENABLED: IEEE80211_HT_SIG_PILOT_REFINE=1 "
              f"(HT-SIG0 4 pilots)",
              flush=True)
    elif args.phase139_4way:
        os.environ['IEEE80211_HT_SIG_PILOT_REFINE'] = '2'
        print(f"[TEST] Phase 139 4-way ENABLED: IEEE80211_HT_SIG_PILOT_REFINE=2 "
              f"(HT-SIG0 + HT-SIG1 8 pilots)",
              flush=True)

    print(f"[P103] Env: LSIG_RATE_FORCE={os.environ.get('IEEE80211_LSIG_RATE_FORCE')} "
          f"TIMING_OFFSET_APPLY={os.environ.get('IEEE80211_TIMING_OFFSET_APPLY')}",
          flush=True)

    if args.phase in ('tx', 'both'):
        ok = phase1_generate(args)
        if not ok:
            print("[P103] TX phase FAILED — no IQ file produced", flush=True)
            sys.exit(2)

    if args.phase in ('rx', 'both'):
        if not os.path.exists(args.iq_file):
            print(f"[P103] RX phase FAILED — {args.iq_file} missing", flush=True)
            sys.exit(2)
        rx, ok, fail = phase2_replay(args)
        print(f"[P103] ===== FINAL =====", flush=True)
        print(f"[P103] RX messages: {rx}", flush=True)
        print(f"[P103] FCS_OK={ok} FCS_FAIL={fail}", flush=True)
        if ok >= 1:
            print(f"[P103] PASS — algorithm chain correct in file-replay "
                  f"(FCS_OK={ok}>=1)", flush=True)
            sys.exit(0)
        else:
            print(f"[P103] FAIL — algorithm chain does not produce FCS_OK", flush=True)
            sys.exit(1)


if __name__ == '__main__':
    sys.exit(main() or 0)