#!/usr/bin/env python3
"""Phase 111 T1c: Clean synthetic HT-Mixed frame + Kalman validation (v2).

Fixed from T1b: use proper OFDM convention with FFT(N)/N normalization.
Channel is applied per 64-sample OFDM symbol (excluding CP).
"""
import argparse
import numpy as np

# ===== Constants =====
N_SC = 52
N_FREQ = 64
CP_LEN = 16
OFDM_SYM_LEN = 80

ACTIVE_SC = np.array([sc for sc in range(-26, 27) if sc != 0])
ACTIVE_BIN = np.array([sc + 32 if sc > 0 else sc + 64 for sc in ACTIVE_SC])
PILOT_SC = np.array([-21, -7, 7, 21])
PILOT_BIN = np.array([11, 25, 39, 53])
DATA_SC_IDX = sorted([sc for sc in range(-26, 27) if sc != 0 and sc not in PILOT_SC])
DATA_BIN = np.array([sc + 32 if sc > 0 else sc + 64 for sc in DATA_SC_IDX])

LTF_SEQ = np.array([
    0, 0, 0, 0, 0, 0, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1,
    1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1,
    0, 1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0, 0
], dtype=np.complex64)

POLARITY_127 = np.array([
    1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,1,-1,1,
    1,-1,1,1,1,1,1,1,-1,1,1,1,-1,1,1,-1,-1,1,1,1,-1,1,
    -1,-1,-1,1,-1,1,-1,-1,1,-1,-1,1,1,1,1,1,-1,-1,1,1,-1,-1,
    1,-1,1,-1,1,1,-1,-1,-1,1,1,-1,-1,-1,-1,1,-1,-1,1,-1,1,1,
    1,1,-1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,-1,1,1,-1,1,-1,1,1,
    1,-1,-1,1,-1,-1,-1,1,1,1,-1,-1,-1,-1,-1,-1,-1
], dtype=np.int8)


def pilot_value(data_sym_idx, pilot_idx):
    p = POLARITY_127[data_sym_idx % 127]
    return -p if pilot_idx == 3 else p


def ofdm_modulate(freq_64):
    """OFDM modulate: IFFT(N=64) → add 16-sample CP."""
    time_64 = np.fft.ifft(freq_64) * 64  # standard inverse FFT
    cp = time_64[-CP_LEN:]
    return np.concatenate([cp, time_64]).astype(np.complex64)


def ofdm_demod(samples_80):
    """OFDM demodulate: remove 16-sample CP, then FFT(64)."""
    data = samples_80[CP_LEN:]
    return np.fft.fft(data)  # standard FFT, NO /64 (so we can divide by H directly)


def ltf_freq_data():
    """Generate L-LTF frequency domain data (64 bins, BPSK ±1 at active SCs).

    Note: Guard bins (0-5, 32, 59-63) are set to 1 instead of 0 to avoid divide-by-zero
    in H estimation. These bins are zero in real 802.11 but for synthetic we just want
    to avoid the warning.
    """
    seq = LTF_SEQ.copy()
    seq[seq == 0] = 1  # avoid div-by-zero
    return seq


def lsig_freq_data(rate=0xD, length=10):
    """Generate L-SIG frequency domain data (48 data SCs, BPSK rate 1/2)."""
    # L-SIG has 24 bits: rate(4) | reserved(1) | length(12) | parity(1) | tail(6)
    bits = (rate >> np.arange(4)) & 1  # 4 rate bits
    # Pad to 24 bits: 4 + 1(reserved) + 12(length) + 1(parity) + 6(tail) = 24
    bits = np.concatenate([
        bits, [0],  # +1 reserved = 5
        (length >> np.arange(12)) & 1, [0],  # +12 length +1 parity = 18
        [0, 0, 0, 0, 0, 0]  # +6 tail = 24
    ])
    assert len(bits) == 24, f"Expected 24 bits, got {len(bits)}"
    bpsk = (2 * bits - 1).astype(np.complex64)
    coded = np.repeat(bpsk, 2)[:48]  # 48 elements (rate 1/2 repetition)

    freq = np.zeros(64, dtype=np.complex64)
    for i, bin_idx in enumerate(DATA_BIN):
        freq[bin_idx] = coded[i]
    return freq


def htsig_freq_data(mcs=0, length=10):
    """Generate HT-SIG frequency domain data (BPSK QBPSK pattern)."""
    # Simplified: just generate a deterministic BPSK pattern
    np.random.seed(42)
    bits = np.random.randint(0, 2, size=24)
    bpsk = (2 * bits - 1).astype(np.complex64)
    coded = np.repeat(bpsk, 2)[:48]

    freq = np.zeros(64, dtype=np.complex64)
    for i, bin_idx in enumerate(DATA_BIN):
        freq[bin_idx] = coded[i]
    return freq


def data_freq_data(sym_idx, rng):
    """Generate DATA symbol frequency domain data (48 data SCs + 4 pilots)."""
    np.random.seed(1000 + sym_idx)
    data_bits = (2 * rng.integers(0, 2, size=48) - 1).astype(np.complex64)

    freq = np.zeros(64, dtype=np.complex64)
    for i, bin_idx in enumerate(DATA_BIN):
        freq[bin_idx] = data_bits[i]
    for i, bin_idx in enumerate(PILOT_BIN):
        freq[bin_idx] = pilot_value(sym_idx, i)
    return freq


def generate_frame(n_data_syms=20, rng=None):
    """Generate clean HT-Mixed frame (no channel applied, no noise).
    Returns dict with each OFDM symbol's TX freq + time samples.
    """
    if rng is None:
        rng = np.random.default_rng(42)

    frame = {
        'ltf0_freq': ltf_freq_data(),
        'ltf1_freq': ltf_freq_data(),
        'lsig_freq': lsig_freq_data(),
        'htsig1_freq': htsig_freq_data(),
        'htsig2_freq': htsig_freq_data(),
        'htltf1_freq': ltf_freq_data(),  # HT-LTF uses same sequence as L-LTF
        'data_freq': [data_freq_data(d, rng) for d in range(n_data_syms)],
    }
    return frame


def apply_channel_to_frame(frame, channel_func, n_data_syms):
    """Apply time-varying channel to each OFDM symbol independently.

    channel_func(symbol_name) returns H[64] (1.0 at active SCs, can be 0 at edges/DC)
    """
    rx = {}

    def conv(freq):
        """Modulate freq → apply channel → demodulate."""
        tx_time = ofdm_modulate(freq)
        # FFT of TX time domain
        tx_fft = np.fft.fft(tx_time[CP_LEN:])  # 64-element FFT
        # Apply channel
        H = channel_func_for_sym
        rx_fft = tx_fft * H
        # IFFT back to time
        rx_time = np.fft.ifft(rx_fft) * 64  # standard inverse
        # Add CP
        rx_time_full = np.concatenate([rx_time[-CP_LEN:], rx_time]).astype(np.complex64)
        return rx_time_full, rx_fft

    # We need different channel for each sym
    def make_apply(name):
        def apply(freq):
            H = channel_func(name)
            tx_time = ofdm_modulate(freq)
            tx_fft = np.fft.fft(tx_time[CP_LEN:])
            rx_fft = tx_fft * H
            rx_time = np.fft.ifft(rx_fft) * 64
            rx_time_full = np.concatenate([rx_time[-CP_LEN:], rx_time]).astype(np.complex64)
            return rx_time_full, rx_fft, H
        return apply

    apply_ltf0 = make_apply('LTF0')
    apply_ltf1 = make_apply('LTF1')
    apply_lsig = make_apply('LSIG')
    apply_htsig1 = make_apply('HTSIG1')
    apply_htsig2 = make_apply('HTSIG2')
    apply_htltf1 = make_apply('HTLTF1')

    rx['ltf0_time'], rx['ltf0_rx_fft'], H_ltf0 = apply_ltf0(frame['ltf0_freq'])
    rx['ltf1_time'], rx['ltf1_rx_fft'], H_ltf1 = apply_ltf1(frame['ltf1_freq'])
    rx['lsig_time'], rx['lsig_rx_fft'], H_lsig = apply_lsig(frame['lsig_freq'])
    rx['htsig1_time'], rx['htsig1_rx_fft'], H_htsig1 = apply_htsig1(frame['htsig1_freq'])
    rx['htsig2_time'], rx['htsig2_rx_fft'], H_htsig2 = apply_htsig2(frame['htsig2_freq'])
    rx['htltf1_time'], rx['htltf1_rx_fft'], H_htltf1 = apply_htltf1(frame['htltf1_freq'])

    rx['data_time'] = []
    rx['data_rx_fft'] = []
    H_data = []
    for d in range(n_data_syms):
        apply_d = make_apply(f'DATA{d}')
        t, f, H = apply_d(frame['data_freq'][d])
        rx['data_time'].append(t)
        rx['data_rx_fft'].append(f)
        H_data.append(H)

    return rx, {'LTF0': H_ltf0, 'LTF1': H_ltf1, 'LSIG': H_lsig,
                'HTSIG1': H_htsig1, 'HTSIG2': H_htsig2, 'HTLTF1': H_htltf1,
                'DATA': H_data}


def add_awgn_to_frame(rx_frame, snr_db, rng):
    """Add AWGN to all time-domain samples in frame."""
    noise_var_per_real = None
    for key in ['ltf0_time', 'ltf1_time', 'lsig_time', 'htsig1_time', 'htsig2_time',
                'htltf1_time']:
        samples = rx_frame[key]
        sig_power = np.mean(np.abs(samples)**2)
        noise_power = sig_power / (10**(snr_db/10))
        noise = np.sqrt(noise_power/2) * (rng.normal(size=len(samples)) +
                                           1j * rng.normal(size=len(samples)))
        rx_frame[key] = (samples + noise).astype(np.complex64)
    for d in range(len(rx_frame['data_time'])):
        samples = rx_frame['data_time'][d]
        sig_power = np.mean(np.abs(samples)**2)
        noise_power = sig_power / (10**(snr_db/10))
        noise = np.sqrt(noise_power/2) * (rng.normal(size=len(samples)) +
                                           1j * rng.normal(size=len(samples)))
        rx_frame['data_time'][d] = (samples + noise).astype(np.complex64)


def make_channel_factory(snr_db=10, phase_drift_per_sym=0.1, mag_cv=0.3, rng=None):
    """Channel factory: returns H[64] per symbol call."""
    if rng is None:
        rng = np.random.default_rng(42)

    initial_phase = rng.uniform(-np.pi, np.pi, size=64)
    initial_mag = 1.0 + rng.normal(0, mag_cv, size=64)
    initial_mag = np.maximum(initial_mag, 0.1)
    initial_mag[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0
    initial_phase[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0

    H_current = (initial_mag * np.exp(1j * initial_phase)).astype(np.complex64)

    def channel(name):
        nonlocal H_current
        if name.startswith('DATA'):
            drift = phase_drift_per_sym * rng.normal(0, 1, size=64)
            H_current = H_current * np.exp(1j * drift)
            mag_jitter = 1.0 + 0.05 * rng.normal(0, 1, size=64)
            H_current = H_current * mag_jitter
            H_current[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0
        return H_current.copy()

    return channel


def process_frame(rx_frame, true_H_dict, n_data_syms, Q=0.01, R=0.1):
    """Process RX frame: estimate H from L-LTF, run Kalman through DATA symbols.

    Returns metrics dict.
    """
    # H estimation from L-LTF
    H_ltf0_est = rx_frame['ltf0_rx_fft'] / LTF_SEQ
    H_ltf1_est = rx_frame['ltf1_rx_fft'] / LTF_SEQ
    H_baseline = (H_ltf0_est + H_ltf1_est) / 2

    H_kalman = H_baseline.copy()
    P_kalman = np.full(64, 1.0)

    metrics = {
        'h_error_baseline': [],
        'h_error_kalman': [],
        'h_phase_err_baseline': [],
        'h_phase_err_kalman': [],
    }

    for d in range(n_data_syms):
        rx_fft_d = rx_frame['data_rx_fft'][d]
        H_true_d = true_H_dict['DATA'][d]

        # Measurement: z = rx_fft[pilot_bin] / pilot_value
        for i, pbin in enumerate(PILOT_BIN):
            tx_pilot = pilot_value(d, i)
            z = rx_fft_d[pbin] / tx_pilot

            x_pred = H_kalman[pbin]
            P_pred = P_kalman[pbin] + Q
            K = P_pred / (P_pred + R)
            H_kalman[pbin] = x_pred + K * (z - x_pred)
            P_kalman[pbin] = (1 - K) * P_pred

        # Compute error at pilot SCs
        for pbin in PILOT_BIN:
            metrics['h_error_baseline'].append(np.abs(H_true_d[pbin] - H_baseline[pbin])**2)
            metrics['h_error_kalman'].append(np.abs(H_true_d[pbin] - H_kalman[pbin])**2)
            metrics['h_phase_err_baseline'].append(
                np.angle(H_true_d[pbin] * np.conj(H_baseline[pbin])))
            metrics['h_phase_err_kalman'].append(
                np.angle(H_true_d[pbin] * np.conj(H_kalman[pbin])))

    return metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--n-frames', type=int, default=20)
    p.add_argument('--n-data-syms', type=int, default=20)
    p.add_argument('--snr-db', type=float, default=10.0)
    p.add_argument('--phase-drift', type=float, default=0.1)
    p.add_argument('--mag-cv', type=float, default=0.3)
    p.add_argument('--q', type=float, default=0.01)
    p.add_argument('--r', type=float, default=0.1)
    args = p.parse_args()

    print(f"[P111-T1c] Config: n_frames={args.n_frames}, n_data_syms={args.n_data_syms}, "
          f"SNR={args.snr_db} dB, drift={args.phase_drift} rad/sym, mag_cv={args.mag_cv}, "
          f"Q={args.q}, R={args.r}", flush=True)

    rng_master = np.random.default_rng(2026)
    all_metrics = {k: [] for k in
                   ['h_error_baseline', 'h_error_kalman',
                    'h_phase_err_baseline', 'h_phase_err_kalman']}

    for fi in range(args.n_frames):
        seed = int(rng_master.integers(0, 2**31))
        rng_ch = np.random.default_rng(seed)
        rng_data = np.random.default_rng(seed + 10000)
        rng_noise = np.random.default_rng(seed + 20000)

        # Generate clean frame
        frame = generate_frame(n_data_syms=args.n_data_syms, rng=rng_data)

        # Apply channel
        channel = make_channel_factory(
            snr_db=args.snr_db, phase_drift_per_sym=args.phase_drift,
            mag_cv=args.mag_cv, rng=rng_ch)
        rx_frame, true_H_dict = apply_channel_to_frame(frame, channel, args.n_data_syms)

        # Add noise
        add_awgn_to_frame(rx_frame, snr_db=args.snr_db, rng=rng_noise)

        # Process
        metrics = process_frame(rx_frame, true_H_dict, args.n_data_syms,
                                Q=args.q, R=args.r)
        for k in all_metrics:
            all_metrics[k].extend(metrics[k])

    err_b = np.array(all_metrics['h_error_baseline'])
    err_k = np.array(all_metrics['h_error_kalman'])
    ph_b = np.array(all_metrics['h_phase_err_baseline'])
    ph_k = np.array(all_metrics['h_phase_err_kalman'])

    mse_b = err_b.mean()
    mse_k = err_k.mean()
    improv = (mse_b - mse_k) / max(mse_b, 1e-9) * 100
    phase_std_b = np.degrees(np.std(ph_b))
    phase_std_k = np.degrees(np.std(ph_k))

    print(f"\n[P111-T1c] === Aggregate (clean synthetic) ===", flush=True)
    print(f"[P111-T1c] Total measurements: {len(err_b)}", flush=True)
    print(f"[P111-T1c] Baseline H MSE:    {mse_b:.4f}", flush=True)
    print(f"[P111-T1c] Kalman H MSE:      {mse_k:.4f}", flush=True)
    print(f"[P111-T1c] Improvement:       {improv:.2f}%", flush=True)
    print(f"[P111-T1c] Phase err std (baseline): {phase_std_b:.2f}°", flush=True)
    print(f"[P111-T1c] Phase err std (Kalman):   {phase_std_k:.2f}°", flush=True)

    # Per-SC breakdown
    err_b_arr = np.array(all_metrics['h_error_baseline']).reshape(-1, 4)
    err_k_arr = np.array(all_metrics['h_error_kalman']).reshape(-1, 4)
    print(f"\n[P111-T1c] === Per pilot SC ===", flush=True)
    print(f"{'Pilot SC':<10} {'MSE_b':>10} {'MSE_k':>10} {'Improv':>8}", flush=True)
    for i, sc in enumerate(PILOT_SC):
        eb = err_b_arr[:, i].mean()
        ek = err_k_arr[:, i].mean()
        imp = (eb - ek) / max(eb, 1e-9) * 100
        marker = " ✓" if ek < eb else ""
        print(f"SC={sc:<7} {eb:>10.4f} {ek:>10.4f} {imp:>7.2f}%{marker}", flush=True)

    # Verdict
    print(f"\n[P111-T1c] === Verdict ===", flush=True)
    if mse_k < mse_b and phase_std_k < 30:
        print(f"[P111-T1c] PASS — Kalman improves MSE and phase std < 30°", flush=True)
        return 0
    elif mse_k < mse_b:
        print(f"[P111-T1c] PARTIAL — MSE improves but phase std still ≥ 30°", flush=True)
        return 2
    else:
        print(f"[P111-T1c] REFUTED — Kalman does not improve H estimate", flush=True)
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main() or 0)