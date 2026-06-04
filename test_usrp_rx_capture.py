#!/home/hy/conda/envs/gnuradio/bin/python
"""
USRP Raw IQ Capture for Diagnostic
Only RX chain, no TX. Captures raw IQ samples to file for offline analysis.
Usage: python test_usrp_rx_capture.py --duration 2 --output /tmp/usrp_rx_capture.fc32
"""
import argparse
import os
import sys
import time
import numpy as np

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'

from gnuradio import gr, blocks, uhd

class USRP_Rx_Capture(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "USRP RX Capture")
        
        # USRP Source (Radio 0, RX2 port)
        self.uhd_usrp_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=range(1),
            ),
        )
        self.uhd_usrp_source.set_samp_rate(args.rate * 1e6)
        self.uhd_usrp_source.set_center_freq(args.freq * 1e6, 0)
        self.uhd_usrp_source.set_gain(args.rx_gain, 0)
        self.uhd_usrp_source.set_antenna("RX2", 0)
        self.uhd_usrp_source.set_subdev_spec("B:0", 0)
        self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, 0)
        
        # RX Buffer (same as test_mcs_usrp.py)
        self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
        self.rx_buffer.set_min_output_buffer(5000000)
        
        # File sink for raw IQ capture
        self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, args.output, False)
        self.file_sink.set_unbuffered(False)
        
        # Head block: limit capture duration
        nsamples = int(args.duration * args.rate * 1e6)
        self.head = blocks.head(gr.sizeof_gr_complex, nsamples)
        
        # Null sink for any extra output
        self.null_sink = blocks.null_sink(gr.sizeof_gr_complex)
        
        # Connections
        self.connect((self.uhd_usrp_source, 0), (self.rx_buffer, 0))
        self.connect((self.rx_buffer, 0), (self.head, 0))
        self.connect((self.head, 0), (self.file_sink, 0))
        
        print(f"[CAPTURE] Settings:")
        print(f"  Freq:      {args.freq} MHz")
        print(f"  Rate:      {args.rate} MHz")
        print(f"  RX Gain:   {args.rx_gain} dB")
        print(f"  Duration:  {args.duration} s")
        print(f"  Samples:   {nsamples}")
        print(f"  Output:    {args.output}")
        print(f"[CAPTURE] Starting USRP RX...")

def main():
    parser = argparse.ArgumentParser(description='USRP RX Raw IQ Capture')
    parser.add_argument('--freq', type=float, default=2437, help='Center frequency in MHz')
    parser.add_argument('--rx-gain', type=float, default=20, help='RX gain in dB')
    parser.add_argument('--rate', type=float, default=20, help='Sample rate in MHz')
    parser.add_argument('--duration', type=float, default=2.0, help='Capture duration in seconds')
    parser.add_argument('--output', type=str, default='/tmp/usrp_rx_capture.fc32', help='Output file path')
    args = parser.parse_args()
    
    tb = USRP_Rx_Capture(args)
    tb.start()
    
    # Wait for capture to complete
    print(f"[CAPTURE] Recording for {args.duration} seconds...")
    time.sleep(args.duration + 0.5)
    
    tb.stop()
    tb.wait()
    
    # Check file size
    if os.path.exists(args.output):
        size = os.path.getsize(args.output)
        nsamps = size // 8  # fc32 = 8 bytes per sample
        print(f"[CAPTURE] Done. File: {args.output}")
        print(f"[CAPTURE] Size: {size} bytes ({nsamps} samples)")
    else:
        print(f"[CAPTURE] ERROR: Output file not created!")

if __name__ == '__main__':
    main()
