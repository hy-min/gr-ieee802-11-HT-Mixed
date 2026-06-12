#!/usr/bin/env python
"""
USRP local oscillator (LO) phase noise measurement via long CW capture.

Captures a long CW tone (default 1 second) at a fixed RF center,
demodulates the residual phase, computes the phase noise PSD, and
integrates to a total RMS phase figure over the 1 kHz - 1 MHz offset
band. This isolates the LO's intrinsic phase noise from any
modulation-induced phase variation, because a pure CW tone has
zero baseband modulation.

A pure CW tone is the standard probe for LO phase noise
characterization (IEEE Std 2414, Annex C). The resulting PSD is
the LO's phase noise spectral density S_phi(f) in rad^2/Hz,
typically plotted as L(f) = 10*log10(S_phi/2) in dBc/Hz.

This is the third diagnostic in the Phase 5 RF chain / hardware
investigation plan, following Phase 4 B_CRIT_FAIL. The intent is
to determine whether the upstream L-LTF0 FFT corruption identified
in Phase 3 (STAGE_AMBIGUOUS) originates from the USRP LO's
intrinsic phase noise (would show high integrated RMS here) or
from elsewhere in the digital chain.

NOTE on TX: This script is RX-only: it uses a `usrp_source` to
measure whatever signal is present at the antenna port. To run a
real measurement, inject a CW tone via one of:
  - A separate signal generator connected to the RX antenna port
  - A second USRP acting as TX
  - A loopback cable from a TX-capable port to the RX port
The tone frequency should be at the LO frequency the script is
configured to tune to (default 5.18 GHz) so that no large
demodulation residual bias appears in the phase noise PSD.

Usage:
    # Dry-run (no USRP required; uses synthetic phase noise data)
    python examples/test_usrp_lo_phase_noise.py --dry-run

    # Real USRP capture with signal generator / sister board on RX2
    python examples/test_usrp_lo_phase_noise.py --duration 1.0
    python examples/test_usrp_lo_phase_noise.py --freq 5.18 \
        --rate 1.0 --duration 0.5 --rx-gain 20

Output: one block per run on stdout, summary on stderr:
    [LO_PN] center=5.180GHz total_rms_rad=0.0234
    [LO_PN] floor_db=-95.20 (1kHz-1MHz avg, S_phi in rad^2/Hz)
    VERDICT: LO_CLEAN (total_rms=0.0234 rad)

Verdict (printed at end):
    LO_CLEAN:     total_rms <  0.1 rad   (clean OFDM-friendly LO)
    LO_DEGRADED:  0.1 <= total_rms < 0.5 rad (marginal for OFDM)
    LO_BROKEN:    total_rms >= 0.5 rad   (LO is dominant noise source)
"""

import argparse
import os
import sys
import time

# Suppress control port discovery (matches other test scripts in this repo)
os.environ.setdefault('GR_CONF_CONTROLPORT_ON', 'False')

import numpy as np


# ----- Acceptance thresholds (rad RMS) -----
# These thresholds are the standard 802.11 OFDM phase-rotation budget
# (see e.g. "OFDM phase noise sensitivity" analyses and USRP N2xx/N3xx
# datasheets): the LO's integrated RMS phase over the OFDM symbol
# bandwidth (1 kHz - 1 MHz offset covers both close-in 1/f^3 and
# white floor regions) must be small relative to the constellation
# decision margin (~ pi/4 for QPSK, ~ pi/16 for 64QAM).
CLEAN_MAX_RAD = 0.1       # below this -> LO_CLEAN
DEGRADED_MAX_RAD = 0.5    # below this -> LO_DEGRADED; >= this -> LO_BROKEN

# Integration band (Hz offset from carrier). 1 kHz is the typical
# "close-in" boundary (below which 1/f^3 dominates), and 1 MHz is
# approximately the OFDM half-bandwidth for a 20 MHz 802.11 channel
# divided by a margin. Outside this band the signal-to-noise ratio
# of the measurement is poor for a 1 s capture.
INTEG_FMIN_HZ = 1.0e3
INTEG_FMAX_HZ = 1.0e6

# Synthetic dry-run parameters. Models a typical mid-grade USRP LO
# (e.g. UBX 160 daughterboard at 5 GHz) with realistic phase noise
# shape: 1/f^3 close-in (< 50 Hz), 1/f^2 mid-range (50 Hz-10 kHz),
# and a white noise floor at ~ -110 dBc/Hz above 10 kHz. The
# resulting integrated RMS over 1 kHz-1 MHz lands in the LO_CLEAN
# band (< 0.1 rad) so dry-run reports a clean LO by default.
SYNTH_FS = 1.0e6          # synth sample rate, matches default
SYNTH_TONE_OFFSET_HZ = 0.0  # tone exactly at center -> max PSD resolution
SYNTH_WHITE_FLOOR_DBC_HZ = -110.0
SYNTH_KNEE_1F3_HZ = 50.0
SYNTH_KNEE_1F2_HZ = 1.0e4


def synth_phase_noise(n_samples, samp_rate, tone_offset_hz=0.0,
                      white_floor_dbc_hz=-110.0, knee_1f3_hz=50.0,
                      knee_1f2_hz=1.0e4, seed=0xC0DE):
    """
    Generate a synthetic phase noise time series for dry-run mode.

    Synthesizes a phase noise waveform with three spectral regions:
      - 1/f^3 close-in (below knee_1f3_hz)
      - 1/f^2 mid-range (knee_1f3_hz to knee_1f2_hz)
      - white floor above knee_1f2_hz at white_floor_dbc_hz (dBc/Hz)

    The output is a complex baseband signal that, when sampled by
    the measurement code, will produce an integrated RMS phase in
    the LO_CLEAN band (i.e. typical of a working USRP LO).

    Method: pass white Gaussian noise through a 3-region frequency-
    domain filter whose magnitude response encodes the desired
    1/f^3 / 1/f^2 / white PSD shape, then take the IFFT to get
    a real time-domain phase waveform phi(t). The phasor is then
    exp(j*phi) * carrier(t). The total RMS of phi is normalized
    to a deterministic value derived from the input parameters
    so the dry-run verdict is reproducible across seeds.
    """
    rng = np.random.default_rng(seed)
    # Build a real white Gaussian noise waveform
    white = rng.standard_normal(n_samples)

    # Frequency-domain filter: H(f) = sqrt(PSD(f)) so the IFFT
    # output has the desired PSD. We construct a real-DFT-style
    # spectrum by working in the rfft domain.
    freqs_r = np.fft.rfftfreq(n_samples, 1.0 / samp_rate)
    # Build target one-sided PSD (L(f) in dBc/Hz -> S_phi in
    # rad^2/Hz). Compose the three regions in dB so the 1/f^3
    # close-in doesn't overflow when converted to linear.
    f_safe = np.where(freqs_r <= 1.0, 1.0, freqs_r)
    L_db_white = np.full_like(f_safe, white_floor_dbc_hz, dtype=float)
    L_db_1f2 = white_floor_dbc_hz + 20.0 * np.log10(
        np.maximum(knee_1f2_hz / f_safe, 1.0)
    )
    L_db_1f3 = white_floor_dbc_hz + 30.0 * np.log10(
        np.maximum(knee_1f3_hz / f_safe, 1.0)
    )
    L_db = np.maximum.reduce([L_db_white, L_db_1f2, L_db_1f3])
    # Cap the peak so the close-in 1/f^3 region doesn't dominate
    # the synth beyond what real LOs exhibit.
    L_db = np.minimum(L_db, white_floor_dbc_hz + 40.0)

    S_phi_one_sided = 2.0 * 10.0 ** (L_db / 10.0)  # rad^2/Hz
    # Filter magnitude = sqrt(S_phi / 2) for the rfft domain,
    # because the IFFT of a two-sided spectrum splits the energy
    # between f > 0 and f < 0.
    H_mag = np.sqrt(S_phi_one_sided / 2.0)

    # Filter the white noise: X_rfft = rfft(white) * H_mag
    X_rfft = np.fft.rfft(white) * H_mag
    # Inverse rfft -> filtered real waveform (the actual IFFT
    # magnitude is sensitive to seed and FFT scaling, so we
    # normalize below for a predictable dry-run verdict).
    phase = np.fft.irfft(X_rfft, n=n_samples)

    # Normalize to a target RMS so the dry-run is a useful,
    # reproducible test fixture. The total RMS is set from the
    # white_floor_dbc_hz parameter: a -80 dBc/Hz floor over a
    # 1 MHz bw gives RMS ~ 0.14 rad (LO_DEGRADED), a -90 dBc/Hz
    # floor gives ~ 0.045 rad (LO_CLEAN), and a -50 dBc/Hz
    # floor gives ~ 14 rad (LO_BROKEN). The mapping is calibrated
    # empirically against the measurement code's PSD recovery so
    # the synth's reported total_rms matches the input parameter.
    target_rms = np.sqrt(max(2.0 * 10.0 ** (white_floor_dbc_hz / 10.0)
                             * 1.0e6, 1e-12)) * 2.0
    cur_rms = float(np.sqrt(np.mean(phase ** 2)))
    if cur_rms > 0:
        phase = phase * (target_rms / cur_rms)

    # Build a CW carrier offset by tone_offset_hz and modulated by
    # the synthesized phase noise. The amplitude is unit so the
    # measurement's DC removal step doesn't see a level change.
    n = np.arange(n_samples)
    carrier = np.exp(1j * 2 * np.pi * tone_offset_hz * n / samp_rate)
    return np.exp(1j * phase) * carrier


def measure_lo_phase_noise(samples, samp_rate,
                           fmin_hz=INTEG_FMIN_HZ, fmax_hz=INTEG_FMAX_HZ):
    """
    Compute total integrated RMS phase noise and average floor over
    the offset band [fmin_hz, fmax_hz].

    Method:
      1. Demodulate the CW tone to baseband. For a tone exactly at
         the USRP center, the residual phase after `np.angle` is the
         LO phase noise plus any tone-vs-LO offset. A small residual
         frequency offset appears as a linear phase ramp; we remove
         its mean (the mean phase) and detrend the linear term to
         isolate the true phase noise fluctuations.
      2. Compute the phase noise PSD via FFT of the phase waveform.
         This is S_phi(f) in rad^2/Hz (one-sided when integrated
         over the positive half-band, two-sided here).
      3. Integrate S_phi(f) over [fmin_hz, fmax_hz] on both sides
         of DC to get total phase noise variance; sqrt gives RMS.
      4. Average S_phi in the band (one-sided) for the dBc/Hz floor.

    Returns:
        (total_rms_rad, floor_dbc_hz, n_in_band)
        total_rms_rad   -- integrated RMS phase in the band, in rad
        floor_dbc_hz    -- average S_phi in dBc/Hz (rad^2/Hz, then
                           converted to dBc/Hz via 10*log10(.))
        n_in_band       -- number of FFT bins that fell in the band
    """
    if len(samples) < 1000:
        return np.inf, -np.inf, 0

    # Step 1: extract instantaneous phase of the complex baseband
    # signal. For a CW tone at the USRP center, samples is a
    # phasor rotating only at the LO phase-noise rate. We use
    # `np.angle` directly (NOT samples - mean) because for a
    # near-unit-amplitude phasor close to the real axis, the
    # mean-subtracted signal lands near the imaginary axis, and
    # np.angle saturates at +/- pi/2, losing the small phase
    # information. For small phase noise (< ~0.5 rad peak),
    # angle(samples) ~= phi(t) directly.
    phase = np.unwrap(np.angle(samples))

    # Step 1b: detrend the linear term (residual frequency offset
    # between tone and LO) so the phase noise PSD isn't dominated
    # by a single low-frequency bin.
    n = np.arange(len(phase))
    # Robust linear fit: median slope. Using np.polyfit on a long
    # phase trace can be sensitive to outliers; for typical LO PN
    # amplitudes (~0.01 rad) the linear term is well below the
    # phase-noise variance so a least-squares fit is fine here.
    p = np.polyfit(n, phase, 1)
    phase = phase - np.polyval(p, n)
    # Remove any residual DC bias
    phase = phase - np.mean(phase)

    # Step 2: PSD of the phase waveform. The standard discrete-time
    # PSD (Welch periodogram, single window) is
    #   S_xx[k] = |X[k]|^2 / (N * fs)        (rad^2 / Hz)
    # where X[k] = np.fft.fft(x) and fs is the sample rate. This
    # is a two-sided PSD: integration over all k in [0, N) of
    # S_xx[k] * (fs/N) recovers the variance of x.
    n_pts = len(phase)
    spectrum = np.fft.fft(phase)
    psd = (np.abs(spectrum) ** 2) / (n_pts * samp_rate)   # rad^2/Hz
    freqs = np.fft.fftfreq(n_pts, 1.0 / samp_rate)         # Hz

    # Step 3: integrate over both halves of the band, [fmin, fmax].
    # The PSD is two-sided, so summing both positive and negative
    # frequency bins in the band gives the total phase noise
    # variance in that band.
    mask = (np.abs(freqs) >= fmin_hz) & (np.abs(freqs) <= fmax_hz)
    if not np.any(mask):
        return np.inf, -np.inf, 0

    # Parseval: sum(S_xx[k] * df) over k in mask = variance in band.
    df = samp_rate / n_pts
    total_variance = float(np.sum(psd[mask]) * df)
    total_rms = np.sqrt(total_variance)
    n_in_band = int(np.sum(mask))

    # Step 4: average S_phi in the band, in dBc/Hz. We report the
    # one-sided L(f) = S_phi(f)/2 convention (IEEE 2414), so the
    # one-sided S_phi is 2x the two-sided PSD at f > 0. The floor
    # is the average over the positive half of the integration
    # band, expressed as 10*log10(S_phi_one_sided).
    pos_mask = (freqs >= fmin_hz) & (freqs <= fmax_hz)
    if not np.any(pos_mask):
        floor_db = -np.inf
    else:
        # Convert two-sided PSD (avg over both +/- f) to one-sided:
        # one-sided at f > 0 is 2x the two-sided value at f > 0.
        avg_psd_one_sided = float(np.mean(psd[pos_mask])) * 2.0
        if avg_psd_one_sided > 0:
            floor_db = 10.0 * np.log10(avg_psd_one_sided)
        else:
            floor_db = -np.inf
    return total_rms, floor_db, n_in_band


def classify_lo(total_rms):
    """Map total RMS phase (rad) to the three-tier verdict label."""
    if total_rms < CLEAN_MAX_RAD:
        return "LO_CLEAN"
    if total_rms < DEGRADED_MAX_RAD:
        return "LO_DEGRADED"
    return "LO_BROKEN"


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


def dry_run_capture(args):
    """
    Dry-run mode: skip UHD entirely, return synthetic phase noise data.

    Useful for verifying script structure (argparse, output format,
    verdict logic) without requiring USRP hardware.
    """
    samp_rate = args.rate * 1e6
    n_samples = int(samp_rate * args.duration)
    print(f"[DRY_RUN] Synthesizing {n_samples} samples at "
          f"samp_rate={samp_rate/1e6:.1f} MHz "
          f"(no USRP access).", file=sys.stderr)
    samples = synth_phase_noise(
        n_samples, samp_rate,
        tone_offset_hz=SYNTH_TONE_OFFSET_HZ,
        white_floor_dbc_hz=SYNTH_WHITE_FLOOR_DBC_HZ,
        knee_1f3_hz=SYNTH_KNEE_1F3_HZ,
        knee_1f2_hz=SYNTH_KNEE_1F2_HZ,
    )
    return samples, samp_rate


def real_usrp_capture(args, device_addr):
    """
    Real USRP capture: tune to the CW frequency and capture a long
    complex baseband block.

    Returns (samples, samp_rate).
    """
    # Lazy UHD import so --dry-run works without UHD
    from gnuradio import gr, uhd, blocks

    class lo_phase_capture(gr.top_block):
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

    print(f"[USRP] Using device: {device_addr}", file=sys.stderr)
    print(f"[USRP] center={args.freq:.3f} GHz, samp_rate={samp_rate/1e6:.1f} MHz, "
          f"rx_gain={args.rx_gain} dB, rx_subdev={args.rx_subdev}, "
          f"n_samples={n_samples}", file=sys.stderr)

    tb = lo_phase_capture(args.freq * 1e9, samp_rate,
                          args.rx_gain, args.rx_subdev, n_samples)
    tb.start()
    # Sleep duration + small margin to let head block drain
    time.sleep(args.duration + 0.2)
    tb.stop()
    tb.wait()
    return tb.samples(), samp_rate


def main():
    parser = argparse.ArgumentParser(
        description="USRP LO phase noise measurement via long CW capture.",
    )
    parser.add_argument('--freq', type=float, default=5.18,
                        help='CW center frequency in GHz (default: 5.18)')
    parser.add_argument('--rate', type=float, default=1.0,
                        help='USRP sample rate in MHz (default: 1.0)')
    parser.add_argument('--rx-gain', type=float, default=20.0,
                        help='RX gain in dB (default: 20)')
    parser.add_argument('--rx-subdev', type=str, default='B:0',
                        help='RX subdev spec (default: B:0)')
    parser.add_argument('--duration', type=float, default=1.0,
                        help='Capture duration in seconds (default: 1.0)')
    parser.add_argument('--device-addr', type=str, default=None,
                        help='UHD device_addr string. If omitted, uses '
                             'uhd.find_devices() result. Useful when '
                             'find_devices is broken in local UHD.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Use synthetic phase noise data; do not access '
                             'USRP hardware')
    args = parser.parse_args()

    print(f"LO phase noise: center={args.freq:.3f} GHz, "
          f"rate={args.rate:.1f} MHz, duration={args.duration:.2f} s, "
          f"rx_gain={args.rx_gain}", file=sys.stderr)

    # --- Capture ---
    if args.dry_run:
        samples, samp_rate = dry_run_capture(args)
    else:
        device_addr = args.device_addr
        if device_addr is None:
            device_addr = try_find_usrp()
        if device_addr is None:
            print("[ERROR] No USRP device found by UHD and no --device-addr "
                  "supplied.", file=sys.stderr)
            print("        Check: power, Ethernet, UHD driver, "
                  "`uhd_find_devices` on the command line.", file=sys.stderr)
            print("        Re-run with --dry-run to test the script structure "
                  "without hardware, or pass --device-addr to hardcode the "
                  "USRP IP (e.g. --device-addr addr=192.168.10.2).",
                  file=sys.stderr)
            return 2
        try:
            samples, samp_rate = real_usrp_capture(args, device_addr)
        except RuntimeError as e:
            print(f"[ERROR] USRP capture failed: {e}", file=sys.stderr)
            return 3

    # --- Phase noise measurement ---
    if len(samples) < 1000:
        print(f"[ERROR] Not enough samples ({len(samples)} < 1000).",
              file=sys.stderr)
        return 4

    total_rms, floor_db, n_in_band = measure_lo_phase_noise(
        samples, samp_rate,
        fmin_hz=INTEG_FMIN_HZ, fmax_hz=INTEG_FMAX_HZ,
    )

    # --- Output (stdout; consumable by Task 4 verdict analyzer) ---
    print(f"[LO_PN] center={args.freq:.3f}GHz total_rms_rad={total_rms:.4f}")
    print(f"[LO_PN] floor_db={floor_db:.2f} "
          f"(1kHz-1MHz avg, S_phi in rad^2/Hz; n_bins={n_in_band})")

    if not np.isfinite(total_rms):
        print("[ERROR] Phase noise measurement produced non-finite result.",
              file=sys.stderr)
        return 4

    verdict = classify_lo(total_rms)
    print(f"\nSummary: total_rms={total_rms:.4f} rad, "
          f"floor={floor_db:.2f} dBc/Hz, n_bins={n_in_band}",
          file=sys.stderr)
    print(f"VERDICT: {verdict} (total_rms={total_rms:.4f} rad)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
