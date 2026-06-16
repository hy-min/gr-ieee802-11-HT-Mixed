#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 28.2: L-LTF0 Sample Boundary Timing Verification (5 GHz A:0)

Preamble layout (20 Msps, 8 µs = 160 samples):
  L-STF  DATA:  fs + 0  to fs + 160
  L-LTF0 CP:   fs + 160 to fs + 176   (16 samples CP)
  L-LTF0 DATA: fs + 176 to fs + 240   (64 samples = LTS0)
  L-LTF1 CP:   fs + 240 to fs + 256   (16 samples GI)
  L-LTF1 DATA: fs + 256 to fs + 320   (64 samples = LTS1)
  L-SIG CP:    fs + 320 to fs + 336
  L-SIG DATA:  fs + 336 to fs + 400   (64 samples)

Frame start 'fs' is the L-STF DATA start, found via 16-sample auto-correlation.
"""
import os
import sys
import time
import argparse
import numpy as np

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_PORT'] = '0'

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')


def capture_usrp_loopback(freq_mhz, rate_mhz, tx_gain, rx_gain, rx_scale,
                          duration, outfile):
    """Capture raw IQ from 5 GHz same-board TDD loopback.

    Two configurations supported (per spec/user direction):
      --rx-subdev A  (default) → Radio#0 TX + Radio#0 RX (same-board TDD)
      --rx-subdev B           → Radio#0 TX + Radio#1 RX (cross-board cable)
    """
    from gnuradio import gr, blocks, uhd
    import pmt
    import ieee802_11
    from wifi_phy_hier import wifi_phy_hier

    # Default to A:0 same-board TDD per spec; switchable via module attr
    rx_subdev = getattr(capture_usrp_loopback, "rx_subdev", "A:0")
    rx_chan = 0 if rx_subdev == "A:0" else 1

    class LoopbackCapture(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self, "P28 Loopback Capture")
            self.wifi_phy_tx = wifi_phy_hier(
                bandwidth=rate_mhz*1e6,
                chan_est=ieee802_11.LS,
                encoding=ieee802_11.BPSK_1_2,
                frequency=freq_mhz*1e6,
                sensitivity=0.01
            )
            self.msg_strobe = blocks.message_strobe(pmt.intern("x"*100), 200)
            self.mac = ieee802_11.mac([0x23]*6, [0x42]*6, [0xff]*6)

            # TX: Radio 0, A:0, TX/RX port
            self.uhd_sink = uhd.usrp_sink(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16",
                                            channels=range(1)),
            )
            self.uhd_sink.set_samp_rate(rate_mhz*1e6)
            self.uhd_sink.set_center_freq(freq_mhz*1e6, 0)
            self.uhd_sink.set_gain(tx_gain, 0)
            self.uhd_sink.set_antenna("TX/RX", 0)
            self.uhd_sink.set_subdev_spec("A:0", 0)
            self.uhd_sink.set_bandwidth(160e6, 0)

            # RX: configurable subdev, RX2 port
            # Trick: keep stream channel at 0, remap channel 0 to desired subdev
            # via set_subdev_spec. This matches test_usrp_cable_loopback.py.
            self.uhd_src = uhd.usrp_source(
                device_addr="addr=192.168.10.2",
                stream_args=uhd.stream_args(cpu_format="fc32", otw_format="sc16",
                                            channels=range(1)),
            )
            self.uhd_src.set_samp_rate(rate_mhz*1e6)
            self.uhd_src.set_center_freq(freq_mhz*1e6, 0)
            self.uhd_src.set_gain(rx_gain, 0)
            self.uhd_src.set_antenna("RX2", 0)
            self.uhd_src.set_subdev_spec(rx_subdev, 0)
            self.uhd_src.set_bandwidth(rate_mhz*1e6, 0)

            self.rx_buffer = blocks.copy(gr.sizeof_gr_complex)
            self.rx_buffer.set_min_output_buffer(5000000)
            self.null_src = blocks.null_source(gr.sizeof_gr_complex)

            # Software gain block (USRP signal is small)
            self.rx_scale = blocks.multiply_const_cc(rx_scale)

            nsamples = int(duration * rate_mhz * 1e6)
            self.head = blocks.head(gr.sizeof_gr_complex, nsamples)
            self.file_sink = blocks.file_sink(gr.sizeof_gr_complex, outfile, False)

            self.msg_connect((self.msg_strobe, 'strobe'), (self.mac, 'app in'))
            self.msg_connect((self.mac, 'phy out'), (self.wifi_phy_tx, 'mac_in'))
            self.connect((self.null_src, 0), (self.wifi_phy_tx, 0))
            self.connect((self.wifi_phy_tx, 0), (self.uhd_sink, 0))

            self.connect((self.uhd_src, 0), (self.rx_buffer, 0))
            self.connect((self.rx_buffer, 0), (self.rx_scale, 0))
            self.connect((self.rx_scale, 0), (self.head, 0))
            self.connect((self.head, 0), (self.file_sink, 0))

    print(f"[CAPTURE] Freq={freq_mhz} MHz Rate={rate_mhz} MHz "
          f"TX=A:0/TX-RX RX={rx_subdev}/RX2 "
          f"TXg={tx_gain} RXg={rx_gain} rx_scale={rx_scale} dur={duration}s",
          flush=True)
    tb = LoopbackCapture()
    tb.start()
    time.sleep(duration + 0.5)
    tb.stop()
    tb.wait()


def find_l_stf_region(iq, period=16, search_skip=1000):
    """Find L-STF start using sudden-rise detection.

    The L-STF has 10 short symbols of 16 samples each. The 16-sample-period
    auto-correlation magnitude (UN-normalized, raw) jumps suddenly at the L-STF
    start: low (noise) → high (~signal_power^2).

    We use a moving-window approach: at each sample, compare the average
    correlation in the last 16 samples to the average in the 16 samples before.
    A sudden 10x rise marks the L-STF start.

    Returns (l_stf_start, l_stf_end).
    """
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    # Raw magnitude of correlation (no normalization, keeps signal power)
    corr_raw = np.abs(a * np.conj(b))

    # Average over 16-sample windows
    win = 16
    if len(corr_raw) < 2 * win:
        return -1, -1
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')

    # Find first place where correlation rises sharply (ratio > 5)
    # Compare moving baseline (samples before) to current window
    ratio = np.zeros_like(corr_smooth)
    ratio[win:] = corr_smooth[win:] / (corr_smooth[:-win] + 1e-12)

    # Skip initial samples, find first big jump
    for i in range(search_skip, len(ratio) - 1):
        if ratio[i] > 5 and corr_smooth[i] > 0.1:
            # Find end: walk forward until correlation drops below 50% of peak
            peak = corr_smooth[i]
            end = i
            while end < len(corr_smooth) - 1 and corr_smooth[end + 1] > peak * 0.3:
                end += 1
            return i, end

    # Fallback: if no sharp rise found, look for first high-power region
    if corr_smooth[search_skip:].max() > 1.0:
        peak_idx = int(np.argmax(corr_smooth[search_skip:])) + search_skip
        # Find region around peak
        start = peak_idx
        end = peak_idx
        threshold = corr_smooth[peak_idx] * 0.3
        while start > 0 and corr_smooth[start] > threshold:
            start -= 1
        while end < len(corr_smooth) - 1 and corr_smooth[end] > threshold:
            end += 1
        return start, end

    return -1, -1


def estimate_h(iq, ltf0_start, ltf1_start):
    """Compute H_avg from L-LTF0 and L-LTF1 DATA, return per-SC H and stats."""
    lts0 = iq[ltf0_start:ltf0_start+64]
    lts1 = iq[ltf1_start:ltf1_start+64]
    if len(lts0) < 64 or len(lts1) < 64:
        return None
    F0 = np.fft.fft(lts0, 64)
    F1 = np.fft.fft(lts1, 64)

    # L-LTF has known sign pattern on active subcarriers. We use the L-LTF
    # TX reference of (+1, ±1) on active SCs — for the H estimate we use
    # (F0 + F1) / 2 / LTF_REF. The LTF sign on each SC is consistent
    # between LTS0 and LTS1, so we can divide by it.
    # L-LTF sequence is defined in 802.11n-2009 19.3.6. The simplest approach:
    # |H| = (|F0| + |F1|)/2 and phase = angle(F0 * F1) which doubles the phase
    # but cancels the LTF sign. So H_phase = angle(F0) - angle(F1) / 2 is
    # NOT correct. The correct trick: F0 and F1 both have the same LTF sign
    # pattern, so F0/F1 has no LTF sign, only the channel phase.
    # But for L-SIG decoding, we only need the channel response, not |H|.

    # Simpler: use sign(F0*F1) for sign-flip correction, then (F0 + F1) / 2
    active_sc = list(range(1, 27)) + list(range(38, 64))
    F0a = F0[active_sc]
    F1a = F1[active_sc]

    # LTF sign on each SC — both LTS carry the same sign, so sign(F0a) == sign(F1a)
    # up to channel noise. Use F0a as the H estimate (the standard L-LTF H):
    H = (F0a + F1a) / 2.0
    H_mag = np.abs(H)
    H_phase = np.angle(H)
    return {
        'H_mag': H_mag,
        'H_phase': H_phase,
        'H_mag_mean': float(H_mag.mean()),
        'H_mag_std': float(H_mag.std()),
        'H_phase_std': float(H_phase.std()),
        'F0a': F0a,
        'F1a': F1a,
    }


def decode_lsig(iq, sig_start, H):
    """Decode L-SIG DATA using H. Returns raw bits and L-SIG structure validity."""
    sig = iq[sig_start:sig_start+64]
    if len(sig) < 64:
        return None
    Fsig = np.fft.fft(sig, 64)
    active_sc = list(range(1, 27)) + list(range(38, 64))
    eq = Fsig[active_sc] / H['F0a']  # equalize using F0a as channel (F0a = H * LTF, F0a/LTF = H)
    bits = (eq.real > 0).astype(int)
    return {
        'bits': bits,
        'eq_real': eq.real,
        'eq_imag': eq.imag,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--freq', type=float, default=5890.0)
    parser.add_argument('--rate', type=float, default=20.0)
    parser.add_argument('--tx-gain', type=float, default=20.0)
    parser.add_argument('--rx-gain', type=float, default=20.0)
    parser.add_argument('--rx-scale', type=float, default=40.0,
                        help='Software gain on RX IQ (USRP signal is small)')
    parser.add_argument('--rx-subdev', type=str, default='A:0',
                        choices=['A:0', 'B:0'],
                        help='RX subdev: A:0 = same-board TDD, B:0 = cross-board cable')
    parser.add_argument('--duration', type=float, default=2.0)
    parser.add_argument('--out-iq', type=str, default='/tmp/p28_loopback_iq.npy')
    parser.add_argument('--out-log', type=str, default='/tmp/p28_ltf0_timing.log')
    args = parser.parse_args()

    # Capture
    capture_usrp_loopback.rx_subdev = args.rx_subdev
    capture_usrp_loopback(
        args.freq, args.rate, args.tx_gain, args.rx_gain, args.rx_scale,
        args.duration, '/tmp/p28_loopback_iq.fc32'
    )
    iq = np.fromfile('/tmp/p28_loopback_iq.fc32', dtype=np.complex64)
    print(f"[ANALYZE] Captured {len(iq)} samples "
          f"({len(iq)/(args.rate*1e6):.2f}s)")
    np.save(args.out_iq, iq)

    # Find L-STF region
    l_stf_start, l_stf_end = find_l_stf_region(iq, period=16)
    print(f"[ANALYZE] L-STF region: samples {l_stf_start} to {l_stf_end} "
          f"(length={l_stf_end - l_stf_start + 1})")
    if l_stf_start < 0:
        print("[ANALYZE] ERROR: L-STF not found. Aborting.")
        return
    # L-STF length should be ~160 samples (10x 16-sample)
    if l_stf_end - l_stf_start < 100:
        print(f"[ANALYZE] WARNING: L-STF region too short "
              f"({l_stf_end - l_stf_start + 1} samples)")

    # Use the START of L-STF as the frame start 'fs'
    fs = l_stf_start
    print(f"[ANALYZE] Frame start (L-STF DATA start) = sample {fs}")

    # Standard L-LTF0 DATA is at fs + 176 (16-sample CP at fs+160)
    # L-LTF1 DATA is at fs + 256
    # L-SIG DATA is at fs + 336
    # We sweep ±4 sample offsets on L-LTF0 DATA start (so the LTS0 window
    # shifts by 'offset' samples).
    log_lines = []
    results = []
    for offset in range(-4, 5):
        ltf0_data = fs + 176 + offset
        ltf1_data = fs + 256 + offset  # keep LTS1 80 samples after LTS0 (no GI shift)
        sig_data = fs + 336 + offset  # L-SIG follows LTS1 by 80 samples (no GI)

        H = estimate_h(iq, ltf0_data, ltf1_data)
        if H is None:
            log_lines.append(f"offset={offset:+d}: out-of-range")
            continue
        lsig = decode_lsig(iq, sig_data, H)
        if lsig is None:
            log_lines.append(f"offset={offset:+d}: L-SIG out-of-range")
            continue

        bits = lsig['bits']
        # L-SIG is BPSK rate 1/2 convolutionally encoded, 24 info bits + 6 tail = 30
        # then 18 zero-pad bits → 48 bits total. The decoder in C++ does viterbi.
        # We don't have a viterbi decoder here. We report the raw equalized bits.
        # Compare to expected: 6 Mbps L-SIG word (MCS0 HT-mixed) starts with rate=0xD=1101
        # then length (12 bits) then parity (1) then tail (6 zeros) = 24 bits → 48 encoded bits.
        # Without viterbi, we can still measure magnitude noise and visual structure.
        bits_str = ''.join(map(str, bits.tolist()))

        # Quality metric: eq_real sign confidence. If channel estimation is good,
        # all 48 bits will have |eq_real| >> |eq_imag| and clear sign.
        # We compute |real| mean/|imag| mean — high ratio = clean equalization.
        ratio = float(np.mean(np.abs(lsig['eq_real'])) /
                      (np.mean(np.abs(lsig['eq_imag'])) + 1e-12))
        # The BER proxy: even without viterbi, if H is correct, the BPSK
        # constellation should be tightly aligned with the real axis.
        # So compute the "constellation tightness":
        ev_mag_mean = float(np.mean(np.abs(lsig['eq_real'])))
        ev_imag_std = float(np.std(lsig['eq_imag']))
        snr_db = 20 * np.log10(ev_mag_mean / (ev_imag_std + 1e-12))

        result = {
            'offset': offset,
            'H_mag_mean': H['H_mag_mean'],
            'H_mag_std': H['H_mag_std'],
            'H_phase_std': H['H_phase_std'],
            'eq_real_imag_ratio': ratio,
            'lsig_snr_db': snr_db,
            'bits': bits_str,
            'eq_imag_std': ev_imag_std,
        }
        results.append(result)
        log_lines.append(
            f"offset={offset:+d} "
            f"|H|_mean={H['H_mag_mean']:.3f} |H|_std={H['H_mag_std']:.4f} "
            f"phase_std={H['H_phase_std']:.3f} rad "
            f"eq_R/I={ratio:.2f} lsig_SNR={snr_db:.1f}dB "
            f"imag_std={ev_imag_std:.3f}"
        )

    print("\n[ANALYZE] === L-LTF0 Sample Boundary Sweep ===")
    for line in log_lines:
        print(line)

    # Pick the offset that maximizes L-SIG SNR (or minimizes imag_std)
    if results:
        best_snr = max(results, key=lambda r: r['lsig_snr_db'])
        best_imag = min(results, key=lambda r: r['eq_imag_std'])
        print(f"\n[ANALYZE] Best by L-SIG SNR: offset={best_snr['offset']:+d} "
              f"SNR={best_snr['lsig_snr_db']:.1f}dB")
        print(f"[ANALYZE] Best by min imag_std: offset={best_imag['offset']:+d} "
              f"imag_std={best_imag['eq_imag_std']:.3f}")

    with open(args.out_log, 'w') as f:
        f.write(f"freq={args.freq} rate={args.rate} duration={args.duration}\n")
        f.write(f"l_stf_start={l_stf_start} l_stf_end={l_stf_end}\n")
        f.write(f"frame_start={fs}\n\n")
        for line in log_lines:
            f.write(line + "\n")
        if results:
            f.write(f"\nbest_SNR_offset={best_snr['offset']}\n")
            f.write(f"best_imag_offset={best_imag['offset']}\n")
    print(f"\n[ANALYZE] Log written to {args.out_log}")


if __name__ == '__main__':
    main()
