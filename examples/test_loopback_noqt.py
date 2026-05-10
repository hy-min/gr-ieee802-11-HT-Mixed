#!/usr/bin/env python3
"""
HT Mixed mode loopback test - Rapid Fire version
Send multiple packets rapidly so subsequent packets push out previous packet tails

TX HT-SIG Capture:
The TX HT-SIG bits are output via C++ printf from gr-htsig module and appear
in the test output (stderr/stdout). To capture:
  1. Run: python examples/test_loopback_noqt.py 2>&1 | tee /tmp/tx_capture.txt
  2. Or use the --capture flag: python examples/test_loopback_noqt.py --capture
     This will run the test and extract TX HT-SIG bits to /tmp/tx_htsig_bits.txt

The log file /tmp/test_output.log captures Python-level debug output including
[TX][MM-CA] htsig1_data48 and htsig2_data48 which contain the same HT-SIG bits
as the gr-htsig [TX][HTSIG] intl96 output.
"""
import sys
import time
import os
import threading
from gnuradio import gr, blocks, channels
import pmt
import ieee802_11
sys.path.insert(0, 'examples')
from wifi_phy_hier import wifi_phy_hier

# TX debug capture - Tee class to write stdout to both console and file
log_file_path = '/tmp/test_output.log'
log_file = open(log_file_path, 'w')

class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, msg):
        for f in self.files:
            f.write(msg)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()


def make_pdu(payload_len=38):
    total_payload = payload_len + 30
    payload = [0x42] * total_payload
    u8v = pmt.init_u8vector(total_payload, payload)
    meta = pmt.make_dict()
    meta = pmt.dict_add(meta, pmt.intern("mcs"), pmt.from_long(0))
    meta = pmt.dict_add(meta, pmt.intern("encoding"), pmt.from_long(0))
    meta = pmt.dict_add(meta, pmt.intern("len"), pmt.from_long(total_payload))
    meta = pmt.dict_add(meta, pmt.intern("psdu_len"), pmt.from_long(total_payload))
    return pmt.cons(meta, u8v)


def extract_tx_htsig_bits():
    """Extract TX HT-SIG bits from the log file and write to /tmp/tx_htsig_bits.txt"""
    tx_bits_file = '/tmp/tx_htsig_bits.txt'
    htsig_lines = {'htsig1': None, 'htsig2': None}

    try:
        with open(log_file_path, 'r') as f:
            for line in f:
                if '[TX][MM-CA] htsig1_data48=' in line:
                    htsig_lines['htsig1'] = line.split('htsig1_data48=')[1].strip()
                elif '[TX][MM-CA] htsig2_data48=' in line:
                    htsig_lines['htsig2'] = line.split('htsig2_data48=')[1].strip()

        if htsig_lines['htsig1'] and htsig_lines['htsig2']:
            intl96 = htsig_lines['htsig1'] + htsig_lines['htsig2']
            with open(tx_bits_file, 'w') as f:
                f.write(f"# TX HT-SIG Interleaved 96 bits (HT-SIG0 + HT-SIG1)\n")
                f.write(f"# HT-SIG0 (bits 0-47): {htsig_lines['htsig1']}\n")
                f.write(f"# HT-SIG1 (bits 48-95): {htsig_lines['htsig2']}\n")
                f.write(f"intl96={intl96}\n")
            print(f"[TX CAPTURE] TX HT-SIG bits written to {tx_bits_file}", flush=True)
            print(f"[TX CAPTURE] HT-SIG0: {htsig_lines['htsig1']}", flush=True)
            print(f"[TX CAPTURE] HT-SIG1: {htsig_lines['htsig2']}", flush=True)
        else:
            print(f"[TX CAPTURE] Warning: Could not find complete HT-SIG bits", flush=True)
            print(f"[TX CAPTURE] htsig1={htsig_lines['htsig1']}, htsig2={htsig_lines['htsig2']}", flush=True)
    except Exception as e:
        print(f"[TX CAPTURE] Error extracting TX bits: {e}", flush=True)


def main():
    # Redirect stdout/stderr to also write to log file
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)

    print("=== HT Mixed Mode Loopback Test (Rapid Fire) ===", flush=True)

    tb = gr.top_block()

    wifi = wifi_phy_hier(
        bandwidth=20e6,
        chan_est=ieee802_11.LS,
        encoding=ieee802_11.BPSK_1_2,
        frequency=5.89e9,
        sensitivity=0.01,
    )

    chan = channels.channel_model(
        noise_voltage=0.0,
        frequency_offset=0.0,
        epsilon=1.0,
        taps=[1.0],
        noise_seed=0,
        block_tags=False
    )

    mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)
    dbg = blocks.message_debug(True, gr.log_levels.info)

    tb.msg_connect((mac, 'phy out'), (wifi, 'mac_in'))
    tb.msg_connect((wifi, 'mac_out'), (dbg, 'print_pdu'))

    tb.connect((wifi, 0), (chan, 0))
    tb.connect((chan, 0), (wifi, 0))

    print("Flowgraph built. Starting...", flush=True)

    tb.start()

    time.sleep(0.1)
    mb = mac.to_basic_block()

    for i in range(3):
        mb._post(pmt.intern("app in"), make_pdu(38))
        print(f"[TX] Sent packet {i+1}", flush=True)
        time.sleep(0.05)

    print("Waiting...", flush=True)
    time.sleep(0.5)

    print("Stopping...", flush=True)
    tb.stop()
    tb.wait()
    print("Done", flush=True)

    # Extract TX HT-SIG bits from log file
    extract_tx_htsig_bits()


if __name__ == "__main__":
    main()
