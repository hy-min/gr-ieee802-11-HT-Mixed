#!/usr/bin/env python
"""
USRP RX Gain Diagnostic Tool

Systematically verifies USRP RX gain configuration and measures signal amplitude.
Sweeps RX gain values and reports whether gain changes actually affect amplitude.

Usage:
    python test_usrp_gain_diagnose.py [--normalized]

Options:
    --normalized    Use set_normalized_gain() instead of set_gain()
"""
import os
import sys
import time
import argparse
import numpy as np

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks, uhd


# Configuration
DEVICE_ADDR = "addr=192.168.10.2"
RX_SUBDEV = "B:0"
RX_ANTENNA = "RX2"
SAMP_RATE = 20e6
CENTER_FREQ = 5180e6
MEASURE_DURATION = 2.0  # seconds per gain point

GAIN_VALUES = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 31.5]


class GainDiagTop(gr.top_block):
    def __init__(self, gain_db, normalized=False):
        gr.top_block.__init__(self, "USRP Gain Diagnostic")

        # USRP Source (RX only)
        self.uhd_source = uhd.usrp_source(
            device_addr=DEVICE_ADDR,
            stream_args=uhd.stream_args(cpu_format="fc32", channels=range(1)),
        )
        self.uhd_source.set_samp_rate(SAMP_RATE)
        self.uhd_source.set_center_freq(CENTER_FREQ, 0)
        self.uhd_source.set_antenna(RX_ANTENNA, 0)
        self.uhd_source.set_subdev_spec(RX_SUBDEV, 0)
        self.uhd_source.set_bandwidth(SAMP_RATE, 0)

        if normalized:
            # normalized gain: 0.0 to 1.0
            norm_gain = gain_db / 31.5
            self.uhd_source.set_normalized_gain(norm_gain, 0)
        else:
            self.uhd_source.set_gain(gain_db, 0)

        # Vector sink for sample capture
        self.vector_sink = blocks.vector_sink_c()

        # Connect: USRP source -> vector_sink
        self.connect((self.uhd_source, 0), (self.vector_sink, 0))

        self.gain_db = gain_db
        self.normalized = normalized

    def get_samples(self):
        """Retrieve captured samples from vector sink."""
        return np.array(self.vector_sink.data(), dtype=np.complex64)


def compute_stats(samples):
    """Compute signal statistics from captured samples."""
    if len(samples) == 0:
        return {
            'rms': 0.0,
            'peak': 0.0,
            'saturation_pct': 0.0,
            'n': 0,
        }

    # RMS amplitude (magnitude)
    magnitudes = np.abs(samples)
    rms = np.sqrt(np.mean(magnitudes ** 2))
    peak = np.max(magnitudes)

    # Saturation: percentage of samples with magnitude > 0.99
    saturated = np.sum(magnitudes > 0.99)
    saturation_pct = 100.0 * saturated / len(samples)

    return {
        'rms': rms,
        'peak': peak,
        'saturation_pct': saturation_pct,
        'n': len(samples),
    }


def print_header(normalized=False):
    """Print diagnostic header."""
    gain_mode = "normalized (0-1)" if normalized else "manual (dB)"
    print("=" * 58)
    print("USRP RX Gain Diagnostic")
    print("=" * 58)
    print("Device: USRP X310")
    print("RX Subdev: {}, Antenna: {}".format(RX_SUBDEV, RX_ANTENNA))
    print("Freq: {:.0f} MHz, Rate: {:.0f} MHz".format(
        CENTER_FREQ / 1e6, SAMP_RATE / 1e6))
    print("Gain mode: {}".format(gain_mode))
    print("=" * 58)
    print()


def run_gain_sweep(normalized=False):
    """Run the gain sweep test."""
    print("[Test 1] Gain Sweep")
    print()
    print("  Gain (dB) |     RMS |    Peak |  Sat % |          N")
    print("-" * 58)

    results = []

    for gain in GAIN_VALUES:
        # Build flowgraph with specified gain
        tb = GainDiagTop(gain, normalized=normalized)

        # Start capture
        tb.start()
        time.sleep(MEASURE_DURATION)
        tb.stop()
        tb.wait()

        # Get samples and compute stats
        samples = tb.get_samples()
        stats = compute_stats(samples)
        results.append((gain, stats))

        print("  {:>8.1f} | {:>7.5f} | {:>7.5f} | {:>5.2f}% | {:>10,d}".format(
            gain, stats['rms'], stats['peak'],
            stats['saturation_pct'], stats['n']))

        # Small delay between runs to let USRP settle
        time.sleep(0.5)

    return results


def verify_gain_working(results):
    """Verify whether gain changes actually affect amplitude."""
    print()
    print("[Test 2] Gain Change Verification")

    low_gain, low_stats = results[0]
    high_gain, high_stats = results[-1]

    low_rms = low_stats['rms']
    high_rms = high_stats['rms']

    print("  RMS at gain={:.1f}: {:.5f}".format(low_gain, low_rms))
    print("  RMS at gain={:.1f}: {:.5f}".format(high_gain, high_rms))

    if low_rms > 1e-10:
        ratio = high_rms / low_rms
        print("  Ratio (high/low): {:.2f}x".format(ratio))

        # Determine if gain is working
        # A working gain should show at least 2x difference between min and max
        if ratio > 2.0:
            print("  Gain is working correctly")
            return True
        elif ratio > 1.2:
            print("  Gain has weak effect (possible issue)")
            return False
        else:
            print("  Gain has NO effect (check hardware/config)")
            return False
    else:
        print("  Signal at minimum gain is too weak to measure")
        print("  (This may be normal if no TX signal is present)")
        if high_rms > 1e-10:
            print("  But signal IS present at higher gain")
            return True
        return False


def main():
    parser = argparse.ArgumentParser(
        description="USRP RX Gain Diagnostic Tool")
    parser.add_argument(
        '--normalized', action='store_true',
        help='Use set_normalized_gain() instead of set_gain()')
    args = parser.parse_args()

    print_header(normalized=args.normalized)

    try:
        results = run_gain_sweep(normalized=args.normalized)
        working = verify_gain_working(results)

        print()
        print("=" * 58)
        if working:
            print("RESULT: Gain control is functional")
        else:
            print("RESULT: Gain control may be BROKEN")
            print("  - Check daughterboard connections")
            print("  - Verify subdev spec and antenna selection")
            print("  - Confirm UHD/driver version compatibility")
        print("=" * 58)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print("\nError: {}".format(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
