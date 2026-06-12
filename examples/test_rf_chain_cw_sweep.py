#!/usr/bin/env python
"""
RF chain characterization via CW (continuous wave) tone sweep.

Sweeps the USRP center frequency across the 5 GHz WiFi band and measures
the received amplitude at each step. A pure CW tone has zero modulation,
so any amplitude variation comes from the RF chain (USRP LO power
variation, cable loss, antenna VSWR), not from the digital baseband.

This is the first diagnostic in the Phase 5 RF chain / hardware
investigation plan, following Phase 4 B_CRIT_FAIL. The intent is to
determine whether the upstream L-LTF0 FFT corruption identified in
Phase 3 (STAGE_AMBIGUOUS) originates from the RF chain or from
elsewhere in the digital chain.

NOTE on TX: The original Phase 5 reference implementation assumed a TX
chain. This script is RX-only: it uses a `usrp_source` to measure
whatever signal is present at the antenna port. To run a real sweep,
inject a CW tone via one of:
  - A separate signal generator connected to the RX antenna port
  - A second USRP acting as TX
  - A loopback cable from a TX-capable port to the RX port
The --tx-gain argument is accepted for compatibility with the original
spec but is unused without a TX chain.

Usage:
    # Dry-run (no USRP required; uses synthetic amplitude data)
    python examples/test_rf_chain_cw_sweep.py --dry-run

    # Real USRP sweep with signal generator / sister board on RX2
    python examples/test_rf_chain_cw_sweep.py
    python examples/test_rf_chain_cw_sweep.py --start 5.17 --stop 5.19 \
        --step 0.005 --duration 0.2

Output: one line per frequency on stdout:
    [CW_SWEEP] freq=5.000GHz amp=-12.34 dBFS
    [CW_SWEEP] freq=5.010GHz amp=-12.31 dBFS
    ...

Verdict (printed at end):
    RF_CHAIN_FLAT:     max - min amp < 3 dB   (clean RF chain)
    RF_CHAIN_DEGRADED: 3 <= max - min < 6 dB  (cable/antenna issue likely)
    RF_CHAIN_BROKEN:   max - min >= 6 dB      (hardware failure)
"""

import argparse
import os
import sys
import time

# Suppress control port discovery (matches other test scripts in this repo)
os.environ.setdefault('GR_CONF_CONTROLPORT_ON', 'False')

import numpy as np


# ----- Acceptance thresholds (dB) -----
FLAT_MAX_DB = 3.0       # below this -> RF_CHAIN_FLAT
DEGRADED_MAX_DB = 6.0   # below this -> RF_CHAIN_DEGRADED; >= this -> RF_CHAIN_BROKEN


def synth_amp_db(freq_ghz, base_db=-12.0, ripple_amp_db=0.6, slope_db_per_ghz=0.4):
    """
    Generate a plausible synthetic amplitude for dry-run mode.

    Models:
      - a flat baseline (base_db)
      - small ripple from antenna VSWR (sinusoidal, ~0.6 dB peak)
      - a gentle linear slope (cable loss vs frequency, ~0.4 dB/GHz)

    A clean RF chain in real life typically shows < 1 dB variation;
    a broken one can show 10+ dB. Dry-run values stay under 1 dB so
    the verdict is RF_CHAIN_FLAT when this mode is used as-is.
    """
    ripple = ripple_amp_db * np.sin(2 * np.pi * (freq_ghz - 5.0) / 0.05)
    slope = slope_db_per_ghz * (freq_ghz - 5.15)
    return base_db + ripple + slope


def dry_run_sweep(freqs_ghz, args):
    """
    Dry-run mode: skip UHD entirely, return synthetic amplitude data.

    Useful for verifying script structure (argparse, output format,
    verdict logic) without requiring USRP hardware.
    """
    print(f"[DRY_RUN] Synthesizing {len(freqs_ghz)} amplitude samples "
          f"(no USRP access).", file=sys.stderr)
    amps_db = [synth_amp_db(f) for f in freqs_ghz]
    # Artificial per-frequency sleep so progress is visible
    for f_ghz, amp in zip(freqs_ghz, amps_db):
        time.sleep(0.01)
        print(f"[CW_SWEEP] freq={f_ghz:.3f}GHz amp={amp:.2f} dBFS")
        sys.stdout.flush()
    return np.array(amps_db)


def try_find_usrp():
    """
    Probe for a USRP device. Returns a device_addr string if found,
    or None if no device is visible to UHD.

    We import UHD lazily so the script can still run --dry-run on
    machines without the UHD Python module installed.
    """
    try:
        from gnuradio import uhd  # noqa: F401
    except ImportError:
        print("[ERROR] gnuradio.uhd not importable. "
              "Are you running in the gnuradio conda env?", file=sys.stderr)
        return None

    try:
        # uhd_find_devices() returns an iterable of device_addr dicts
        devices = uhd.find_devices(uhd.device_addr(""))
    except Exception as e:
        print(f"[ERROR] uhd.find_devices() failed: {e}", file=sys.stderr)
        return None

    devices = list(devices)
    if not devices:
        return None
    # Pick the first device; use its full args string if present
    dev = devices[0]
    return dev.to_string() if hasattr(dev, "to_string") else str(dev)


def real_usrp_sweep(freqs_hz, freqs_ghz, args, device_addr):
    """
    Real USRP sweep: tune to each frequency, capture, measure RMS amplitude.

    Returns an array of amplitudes in dBFS (one per frequency).
    """
    # Lazy UHD import so --dry-run works without UHD
    from gnuradio import gr, uhd, blocks

    class cw_capture(gr.top_block):
        def __init__(self, freq_hz, samp_rate, rx_gain, rx_subdev, n_samples):
            gr.top_block.__init__(self)
            # If the user passed a bare IP/hostname, prefix with 'addr=' so UHD
            # interprets it as the network address (not a key name).
            addr_str = device_addr if "=" in device_addr else f"addr={device_addr}"
            self.usrp = uhd.usrp_source(
                device_addr=uhd.device_addr(addr_str),
                stream_args=uhd.stream_args(
                    cpu_format="fc32", otw_format="sc16", channels=range(1)
                ),
            )
            self.usrp.set_samp_rate(samp_rate)
            self.usrp.set_center_freq(freq_hz, 0)
            self.usrp.set_gain(rx_gain, 0)
            # Try RX2 first (the standard same-board TDD layout);
            # fall back to TX/RX if RX2 is unavailable.
            try:
                self.usrp.set_antenna("RX2", 0)
                self.usrp.set_subdev_spec(rx_subdev, 0)
            except RuntimeError:
                self.usrp.set_antenna("TX/RX", 0)
            self.usrp.set_bandwidth(samp_rate, 0)

            self.sink = blocks.vector_sink_c()
            self.head = blocks.head(gr.sizeof_gr_complex, n_samples)
            self.connect((self.usrp, 0), (self.head, 0))
            self.connect((self.head, 0), (self.sink, 0))

        def samples(self):
            return np.array(self.sink.data(), dtype=np.complex64)

    samp_rate = args.rate * 1e6
    n_samples = int(samp_rate * args.duration)
    amps_db = []

    print(f"[USRP] Using device: {device_addr}", file=sys.stderr)
    print(f"[USRP] samp_rate={samp_rate/1e6:.1f} MHz, rx_gain={args.rx_gain} dB, "
          f"rx_subdev={args.rx_subdev}, n_samples={n_samples}", file=sys.stderr)

    for f_hz, f_ghz in zip(freqs_hz, freqs_ghz):
        tb = cw_capture(f_hz, samp_rate, args.rx_gain, args.rx_subdev, n_samples)
        tb.start()
        time.sleep(args.duration + 0.1)
        tb.stop()
        tb.wait()

        samples = tb.samples()
        if len(samples) == 0:
            amp_db = -np.inf
        else:
            rms = np.sqrt(np.mean(np.abs(samples) ** 2))
            amp_db = 20.0 * np.log10(max(rms, 1e-9))
        amps_db.append(amp_db)
        print(f"[CW_SWEEP] freq={f_ghz:.3f}GHz amp={amp_db:.2f} dBFS")
        sys.stdout.flush()

    return np.array(amps_db)


def classify_flatness(flatness_db):
    """Map flatness (dB) to the three-tier verdict label."""
    if flatness_db < FLAT_MAX_DB:
        return "RF_CHAIN_FLAT"
    if flatness_db < DEGRADED_MAX_DB:
        return "RF_CHAIN_DEGRADED"
    return "RF_CHAIN_BROKEN"


def main():
    parser = argparse.ArgumentParser(
        description="RF chain characterization via CW tone sweep.",
    )
    parser.add_argument('--start', type=float, default=5.0,
                        help='Start frequency in GHz (default: 5.0)')
    parser.add_argument('--stop', type=float, default=5.3,
                        help='Stop frequency in GHz (default: 5.3)')
    parser.add_argument('--step', type=float, default=0.01,
                        help='Frequency step in GHz (default: 0.01 = 10 MHz)')
    parser.add_argument('--tx-gain', type=float, default=20.0,
                        help='TX gain in dB (unused — RX-only sweep, kept for spec compat)')
    parser.add_argument('--rx-gain', type=float, default=20.0,
                        help='RX gain in dB (default: 20)')
    parser.add_argument('--rate', type=float, default=20.0,
                        help='USRP sample rate in MHz (default: 20)')
    parser.add_argument('--duration', type=float, default=0.5,
                        help='Capture duration per frequency in seconds (default: 0.5)')
    parser.add_argument('--rx-subdev', type=str, default='B:0',
                        help='RX subdev spec (default: B:0)')
    parser.add_argument('--device-addr', type=str, default=None,
                        help='USRP device address (e.g. 192.168.10.2). '
                             'If omitted, tries uhd.find_devices() first.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Use synthetic amplitude data; do not access USRP hardware')
    args = parser.parse_args()

    # Build the frequency grid. Use a small epsilon to include the stop
    # freq when (stop - start) is an exact multiple of step.
    freqs_ghz = np.arange(args.start, args.stop + args.step / 2, args.step)
    freqs_hz = freqs_ghz * 1e9
    n_pts = len(freqs_ghz)

    print(f"CW sweep: {args.start:.3f} - {args.stop:.3f} GHz, "
          f"step {args.step * 1000:.0f} MHz, {n_pts} points, "
          f"rx_gain={args.rx_gain}", file=sys.stderr)

    # --- Sweep ---
    if args.dry_run:
        amps_db = dry_run_sweep(freqs_ghz, args)
    else:
        device_addr = args.device_addr
        if device_addr is None:
            device_addr = try_find_usrp()
        if device_addr is None:
            print("[ERROR] No USRP device found by UHD.", file=sys.stderr)
            print("        Check: power, Ethernet, UHD driver, "
                  "`uhd_find_devices` on the command line.", file=sys.stderr)
            print("        Re-run with --dry-run to test the script structure "
                  "without hardware.", file=sys.stderr)
            return 2
        try:
            amps_db = real_usrp_sweep(freqs_hz, freqs_ghz, args, device_addr)
        except RuntimeError as e:
            print(f"[ERROR] USRP sweep failed: {e}", file=sys.stderr)
            return 3

    # --- Verdict ---
    if len(amps_db) == 0:
        print("[ERROR] No amplitude samples collected.", file=sys.stderr)
        return 4

    flatness = float(np.max(amps_db) - np.min(amps_db))
    verdict = classify_flatness(flatness)
    print(f"\nSummary: max={np.max(amps_db):.2f} min={np.min(amps_db):.2f} "
          f"flatness={flatness:.2f} dB", file=sys.stderr)
    print(f"VERDICT: {verdict} (flatness={flatness:.2f} dB)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
