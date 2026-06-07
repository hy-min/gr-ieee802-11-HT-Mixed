#!/usr/bin/env python
"""
CW (continuous wave) tone test to verify basic TX->RX RF connectivity.

Generates a pure sine wave tone and transmits it via one USRP daughterboard,
while receiving on another (or same-board TDD mode). After capture, performs
FFT-based analysis to detect the tone and measure SNR.

Usage:
    python test_usrp_cw_tone.py
    python test_usrp_cw_tone.py --tx-subdev A:0 --rx-subdev A:0  # TDD same-board
    python test_usrp_cw_tone.py --tx-gain 30 --rx-gain 30 --duration 5
"""
import os
import sys
import time
import argparse
import numpy as np

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks, analog, uhd


class CWToneTestTop(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, "CW Tone Connectivity Test")

        self.args = args
        self.samp_rate = args.rate * 1e6
        self.center_freq = args.freq * 1e6
        self.tone_offset = args.tone_offset * 1e3
        self.duration = args.duration
        self.n_samples = int(self.samp_rate * self.duration)

        # ===== TX Chain: sig_source -> USRP sink =====
        self.sig_src = analog.sig_source_c(
            self.samp_rate,
            analog.GR_SIN_WAVE,
            self.tone_offset,
            args.tx_amplitude,
            0.0
        )

        self.uhd_sink = uhd.usrp_sink(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=range(1)
            ),
        )
        self.uhd_sink.set_samp_rate(self.samp_rate)
        self.uhd_sink.set_center_freq(self.center_freq, 0)
        self.uhd_sink.set_gain(args.tx_gain, 0)
        self.uhd_sink.set_antenna("TX/RX", 0)
        self.uhd_sink.set_subdev_spec(args.tx_subdev, 0)

        # ===== RX Chain: USRP source -> vector sink =====
        self.uhd_source = uhd.usrp_source(
            device_addr="addr=192.168.10.2",
            stream_args=uhd.stream_args(
                cpu_format="fc32",
                otw_format="sc16",
                channels=range(1)
            ),
        )
        self.uhd_source.set_samp_rate(self.samp_rate)
        self.uhd_source.set_center_freq(self.center_freq, 0)
        self.uhd_source.set_gain(args.rx_gain, 0)
        self.uhd_source.set_antenna("RX2", 0)
        self.uhd_source.set_subdev_spec(args.rx_subdev, 0)
        self.uhd_source.set_bandwidth(self.samp_rate, 0)

        # Vector sink to capture all RX samples
        self.vector_sink = blocks.vector_sink_c()
        self.vector_sink.set_min_output_buffer(self.n_samples + 100000)

        # Head block to limit capture duration
        self.head = blocks.head(gr.sizeof_gr_complex, self.n_samples)

        # ===== Connections =====
        self.connect((self.sig_src, 0), (self.uhd_sink, 0))
        self.connect((self.uhd_source, 0), (self.head, 0))
        self.connect((self.head, 0), (self.vector_sink, 0))

    def get_rx_samples(self):
        """Retrieve captured RX samples from vector sink."""
        return np.array(self.vector_sink.data(), dtype=np.complex64)


def analyze_tone(samples, samp_rate, tone_offset, tone_bw_hz=50e3):
    """
    Analyze captured samples for tone presence and SNR.

    Parameters:
        samples: complex64 array of RX samples
        samp_rate: sample rate in Hz
        tone_offset: expected tone frequency offset from center in Hz
        tone_bw_hz: bandwidth around tone to consider as "signal" in Hz

    Returns:
        dict with total_power, rms_amp, tone_power, noise_power, snr_db, tone_detected
    """
    n = len(samples)
    if n == 0:
        return {
            'total_power': 0.0,
            'rms_amp': 0.0,
            'tone_power': 0.0,
            'noise_power': 0.0,
            'snr_db': -999.0,
            'tone_detected': False,
        }

    # Total power and RMS amplitude
    total_power = np.mean(np.abs(samples) ** 2)
    rms_amp = np.sqrt(total_power)

    # FFT for spectral analysis
    n_fft = min(n, 2**20)  # cap FFT size for performance
    if n_fft < 1024:
        n_fft = n

    # Use the middle segment for FFT
    start_idx = (n - n_fft) // 2
    segment = samples[start_idx:start_idx + n_fft]

    window = np.hanning(n_fft)
    fft_in = segment * window
    fft_out = np.fft.fftshift(np.fft.fft(fft_in))
    freq = np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / samp_rate))

    # Power spectral density (periodogram)
    psd = np.abs(fft_out) ** 2 / (n_fft * np.mean(window**2))

    # Find tone peak near expected offset
    tone_idx = np.argmin(np.abs(freq - tone_offset))
    tone_bins = int(tone_bw_hz / (samp_rate / n_fft))
    tone_bins = max(tone_bins, 3)  # at least 3 bins

    # Signal region around tone
    sig_start = max(0, tone_idx - tone_bins // 2)
    sig_end = min(n_fft, tone_idx + tone_bins // 2 + 1)
    tone_power_psd = np.sum(psd[sig_start:sig_end]) * (samp_rate / n_fft)

    # Noise region: exclude tone and DC
    noise_mask = np.ones(n_fft, dtype=bool)
    noise_mask[sig_start:sig_end] = False
    # Exclude DC region
    dc_bins = max(5, tone_bins)
    dc_center = n_fft // 2
    noise_mask[max(0, dc_center - dc_bins):min(n_fft, dc_center + dc_bins + 1)] = False

    if np.any(noise_mask):
        noise_power_psd = np.mean(psd[noise_mask]) * (samp_rate / n_fft) * n_fft
        # Actually, for noise floor we want the average PSD value
        noise_floor_per_hz = np.mean(psd[noise_mask])
        noise_power = noise_floor_per_hz * samp_rate
    else:
        noise_power_psd = 1e-20
        noise_floor_per_hz = 1e-20
        noise_power = 1e-20

    # Tone power from time-domain (more reliable for CW)
    # For a pure tone, power in frequency domain should match time domain
    tone_power = tone_power_psd

    # Alternative: direct measurement via bandpass filter in time domain
    # But FFT method is sufficient for CW

    # SNR calculation
    if noise_power > 0:
        snr_linear = tone_power / noise_power
        snr_db = 10.0 * np.log10(snr_linear)
    else:
        snr_db = -999.0

    # Also compute total SNR (total_power / noise_power)
    if noise_power > 0:
        total_snr_db = 10.0 * np.log10(total_power / noise_power)
    else:
        total_snr_db = -999.0

    # Tone detection: peak should be significantly above noise floor
    peak_psd = np.max(psd[sig_start:sig_end])
    tone_detected = (peak_psd > 10.0 * noise_floor_per_hz) and (snr_db > 3.0)

    return {
        'total_power': total_power,
        'rms_amp': rms_amp,
        'tone_power': tone_power,
        'noise_power': noise_power,
        'snr_db': snr_db,
        'total_snr_db': total_snr_db,
        'tone_detected': tone_detected,
        'peak_psd': peak_psd,
        'noise_floor_per_hz': noise_floor_per_hz,
        'freq': freq,
        'psd': psd,
        'tone_idx': tone_idx,
    }


def print_banner(title, width=50):
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def main():
    parser = argparse.ArgumentParser(
        description="CW tone test for USRP TX->RX connectivity"
    )
    parser.add_argument(
        '--freq', type=float, default=5180,
        help='Center frequency in MHz (default: 5180)'
    )
    parser.add_argument(
        '--rate', type=float, default=20,
        help='Sample rate in MHz (default: 20)'
    )
    parser.add_argument(
        '--tx-gain', type=float, default=20,
        help='TX gain in dB (default: 20)'
    )
    parser.add_argument(
        '--rx-gain', type=float, default=20,
        help='RX gain in dB (default: 20)'
    )
    parser.add_argument(
        '--tx-amplitude', type=float, default=0.5,
        help='TX tone amplitude 0-1 (default: 0.5)'
    )
    parser.add_argument(
        '--tx-subdev', type=str, default='A:0',
        help='TX subdev spec (default: A:0)'
    )
    parser.add_argument(
        '--rx-subdev', type=str, default='B:0',
        help='RX subdev spec (default: B:0)'
    )
    parser.add_argument(
        '--duration', type=float, default=3,
        help='Test duration in seconds (default: 3)'
    )
    parser.add_argument(
        '--tone-offset', type=float, default=100,
        help='Tone offset from center in kHz (default: 100)'
    )
    parser.add_argument(
        '--save', type=str, default=None,
        help='Save captured samples to file (optional)'
    )
    args = parser.parse_args()

    # Determine mode label
    if args.tx_subdev == args.rx_subdev:
        mode = "TDD (same-board)"
    else:
        mode = "FDD (cross-board)"

    print_banner("CW Tone Test")
    print(f"  TX: {args.tx_subdev} TX/RX, gain={args.tx_gain} dB, amp={args.tx_amplitude}")
    print(f"  RX: {args.rx_subdev} RX2, gain={args.rx_gain} dB")
    print(f"  Freq: {args.freq} MHz, Tone offset: {args.tone_offset} kHz")
    print(f"  Rate: {args.rate} MHz, Duration: {args.duration} s")
    print(f"  Mode: {mode}")
    print("=" * 50)

    # Build and run flowgraph
    tb = CWToneTestTop(args)

    print("\nSending tone...")
    tb.start()

    # Wait for capture to complete (head block will stop RX after n_samples)
    # But we need to keep TX running for the full duration
    time.sleep(args.duration + 0.5)

    tb.stop()
    tb.wait()

    # Retrieve and analyze samples
    samples = tb.get_rx_samples()

    if len(samples) == 0:
        print("\n[ERROR] No samples captured!")
        sys.exit(1)

    # Analyze
    result = analyze_tone(
        samples,
        tb.samp_rate,
        tb.tone_offset,
        tone_bw_hz=50e3
    )

    # Print results
    print_banner("Results")
    print(f"  Samples captured: {len(samples):,}")
    print(f"  Total power:      {result['total_power']:.6e}")
    print(f"  RMS amplitude:    {result['rms_amp']:.5f}")
    print(f"  Tone power:       {result['tone_power']:.6e}")
    print(f"  Noise power:      {result['noise_power']:.6e}")
    print(f"  Tone SNR:         {result['snr_db']:.1f} dB")
    print(f"  Total SNR:        {result['total_snr_db']:.1f} dB")
    print("=" * 50)

    # Verdict
    if result['tone_detected']:
        print("\n  [PASS] Tone detected! TX->RX path is working.")
        print(f"         Config: TX={args.tx_subdev} -> RX={args.rx_subdev}")
    else:
        print("\n  [FAIL] Tone NOT detected.")
        print(f"         Config: TX={args.tx_subdev} -> RX={args.rx_subdev}")
        if result['rms_amp'] < 1e-6:
            print("         RX signal is extremely weak or absent.")
            print("         Check: cables, antennas, gain settings, frequency.")
        elif result['snr_db'] < 3.0:
            print("         Signal present but SNR too low for reliable detection.")
            print("         Try increasing TX/RX gain or reducing distance.")
        else:
            print("         Unexpected failure. Check tone offset and sample rate.")

    # Save samples if requested
    if args.save:
        samples.tofile(args.save)
        print(f"\n  Saved {len(samples)} samples to {args.save}")

    # Optional: save a simple spectrum plot
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, figsize=(12, 8))

        # Spectrum plot
        ax1 = axes[0]
        freq_mhz = result['freq'] / 1e6
        psd_db = 10 * np.log10(result['psd'] + 1e-20)
        ax1.plot(freq_mhz, psd_db, 'b-', linewidth=0.5)
        ax1.axvline(x=args.tone_offset / 1e3, color='r', linestyle='--',
                    label=f'Tone @ {args.tone_offset} kHz')
        ax1.set_xlabel('Frequency Offset (MHz)')
        ax1.set_ylabel('PSD (dB)')
        ax1.set_title(f'RX Spectrum — CW Tone Test ({mode})')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        ax1.set_xlim(-args.rate / 2, args.rate / 2)

        # Time domain plot (first 10000 samples)
        ax2 = axes[1]
        n_plot = min(10000, len(samples))
        t_ms = np.arange(n_plot) / tb.samp_rate * 1000
        ax2.plot(t_ms, np.real(samples[:n_plot]), 'b-', linewidth=0.5, label='I')
        ax2.plot(t_ms, np.imag(samples[:n_plot]), 'r-', linewidth=0.5, label='Q')
        ax2.set_xlabel('Time (ms)')
        ax2.set_ylabel('Amplitude')
        ax2.set_title('RX Time Domain (first 10k samples)')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = args.save.replace('.cfile', '_spectrum.png') if args.save else 'cw_tone_spectrum.png'
        plt.savefig(plot_path, dpi=150)
        print(f"  Saved spectrum plot to {plot_path}")
        plt.close()
    except ImportError:
        pass  # matplotlib not available

    return 0 if result['tone_detected'] else 1


if __name__ == '__main__':
    sys.exit(main())
