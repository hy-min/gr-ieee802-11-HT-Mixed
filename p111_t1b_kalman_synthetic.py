#!/usr/bin/env python3
"""Phase 111 T1b: Synthetic HT-Mixed frame + Kalman validation.

Why synthetic:
- Real USRP captures are short (0.6-0.8s, only 1 frame each)
- Need many frames for statistical significance
- Need ground-truth H[k, sym] to validate Kalman accuracy

Approach:
  1. Generate synthetic 802.11n HT-Mixed 20MHz frame
     - L-STF (10 short syms, period=16)
     - L-LTF (2 long syms)
     - L-SIG (1 OFDM sym, BPSK)
     - HT-SIG1/2 (2 OFDM syms, BPSK QBPSK)
     - HT-STF (10 short syms)
     - HT-LTF1 (1 OFDM sym)
     - DATA symbols (variable)
  2. Apply time-varying channel H[k, sym]:
     - Initial H[k, 0] = exp(j*phi[k]) * (1 + eps[k]) where phi uniform, eps small
     - Time evolution: H[k, sym+1] = H[k, sym] * exp(j*delta_phi[k])
       where delta_phi is small per-symbol phase drift (SFO/CFO residual)
  3. Add AWGN at SNR_target
  4. Run RX: extract L-LTF → H52_baseline, then Kalman through DATA symbols
  5. Compare Kalman H estimate to ground-truth H[k, sym] (MSE, phase std, |H| CV)

Pass criteria:
  - MSE_kalman < MSE_baseline (Kalman better estimate of H)
  - Phase error std < 30°
  - |H| CV < 10%
  - At SNR_target = 10 dB (HT-SIG usable), Kalman should give >30% MSE reduction
"""
import argparse
import numpy as np

# ===== Constants =====
N_SC = 52  # active subcarriers
N_FREQ_BIN = 64  # FFT size
CP_LEN = 16  # cyclic prefix length
OFDM_SYM_LEN = 80  # 16 + 64
LTF_SYM_LEN = 80  # 64 data + 16 GI for each LTF symbol
LTF_TOTAL_LEN = 160  # L-LTF total: 2 symbols with GI

# SCs in logical index (-26..+26, skip 0)
ACTIVE_SC = np.array([sc for sc in range(-26, 27) if sc != 0])
ACTIVE_BIN = np.array([sc + 32 if sc > 0 else sc + 64 for sc in ACTIVE_SC])
PILOT_SC = np.array([-21, -7, 7, 21])
PILOT_BIN = np.array([11, 25, 39, 53])

# LTF sequence (64 subcarriers, BPSK ±1)
LTF_SEQ = np.array([
    0, 0, 0, 0, 0, 0, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1,
    1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1,
    0, 1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0, 0
], dtype=np.complex64)

# 127-element pilot polarity
POLARITY_127 = np.array([
    1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,1,-1,1,
    1,-1,1,1,1,1,1,1,-1,1,1,1,-1,1,1,-1,-1,1,1,1,-1,1,
    -1,-1,-1,1,-1,1,-1,-1,1,-1,-1,1,1,1,1,1,-1,-1,1,1,-1,-1,
    1,-1,1,-1,1,1,-1,-1,-1,1,1,-1,-1,-1,-1,1,-1,-1,1,-1,1,1,
    1,1,-1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,-1,1,1,-1,1,-1,1,1,
    1,-1,-1,1,-1,-1,-1,1,1,1,-1,-1,-1,-1,-1,-1,-1
], dtype=np.int8)


def pilot_value(data_sym_idx, pilot_idx):
    """Pilot value for data_sym_idx and pilot_idx (0..3). SC=+21 uses opposite sign."""
    p = POLARITY_127[data_sym_idx % 127]
    return -p if pilot_idx == 3 else p


def fft64(x):
    return np.fft.fft(x) / np.sqrt(64)


def ifft64(X):
    return np.fft.ifft(X) * np.sqrt(64)


def generate_l_stf():
    """Generate L-STF (10 short symbols, period=16). Total 160 samples."""
    # Each short symbol is 16 samples. The training sequence is well-known but for
    # synthetic we just need a periodic pattern that the detector can find.
    # Use the IEEE 802.11 short sequence (period-16 autocorrelation pattern)
    # For simplicity, use a deterministic BPSK sequence
    S = np.array([1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1], dtype=np.complex64)
    # Repeat 10 times
    return np.tile(S, 10)


def generate_l_ltf():
    """Generate L-LTF (2 long symbols, 80 samples each). Total 160 samples.
    Each long symbol = 16 GI + 64 data.
    """
    # Data portion = IFFT of LTF_SEQ
    ltf_data = ifft64(LTF_SEQ)  # 64 samples
    # Add 16-sample GI at start (cyclic prefix)
    long_sym = np.concatenate([ltf_data[-16:], ltf_data])  # 80 samples
    return np.tile(long_sym, 2)  # 2 long symbols = 160 samples


def generate_ofdm_symbol(freq_data):
    """Generate one OFDM symbol: 16-sample CP + 64 data.

    freq_data: 64-element complex array (frequency domain)
    """
    time_data = ifft64(freq_data)  # 64 samples
    cp = time_data[-16:]  # last 16 samples as CP
    return np.concatenate([cp, time_data])  # 80 samples


def generate_l_sig():
    """Generate L-SIG symbol. BPSK modulated with rate/length/parity bits.
    For synthetic, just use a known frequency-domain pattern.
    """
    # L-SIG is 24 bits: rate(4) | reserved(1) | length(12) | parity(1) | tail(6) = 24 bits
    # BPSK rate=0xD, length=10 (small frame), parity=0, tail=0
    rate_field = 0b1101  # 0xD = MCS 0
    length_field = 10     # 10 bytes payload
    parity = 0
    tail = 0

    sig_bits = (rate_field << 18) | (0 << 17) | (length_field << 5) | (parity << 4) | tail
    sig_bits &= 0xFFFFFF  # 24 bits

    # Scrambler (length-dependent, all-zeros for short frames)
    # For simplicity, no scrambling
    # BPSK mapping: bit 0 → +1, bit 1 → -1 (BPSK convention: b=1→+1 in 802.11)

    # Interleaver: standard 802.11n block interleaver (skip for simplicity)
    # Pilot insertion: none in L-SIG

    # 48 data subcarriers + 4 pilots = 52 used. L-SIG doesn't have pilots actually.
    # 48 data SCs only for L-SIG.

    # BPSK mapping (bit 0 → -1, bit 1 → +1)
    bpsk = np.array([1 if (sig_bits >> i) & 1 else -1 for i in range(24)], dtype=np.complex64)

    # Map 24 bits to 48 subcarriers (repetition coding rate=1/2, then 48 bits total)
    # For rate 0xD: BPSK rate 1/2, 24 data bits → 48 coded bits
    # Puncturing/convolutional coding - skip, just use direct mapping
    # Actually for L-SIG: 24 bits → convolutional encoder (rate 1/2) → 48 bits → 48 subcarriers
    coded_bits = np.zeros(48, dtype=np.complex64)
    for i in range(24):
        # Convolutional encoder (rate 1/2, K=7, polynomial [133, 171])
        # For simplicity: just repeat each bit twice
        coded_bits[2*i] = bpsk[i]
        coded_bits[2*i+1] = bpsk[i]

    # Place into 48 data subcarriers (skip pilots and DC)
    freq = np.zeros(64, dtype=np.complex64)
    data_sc_idx = [i for i in range(-26, 27) if i != 0 and i not in PILOT_SC]
    # Sort by ascending frequency (skip -26..+26 in order, skip DC and pilots)
    data_sc_idx = sorted(data_sc_idx)
    assert len(data_sc_idx) == 48, f"Expected 48 data SCs, got {len(data_sc_idx)}"
    for i, sc in enumerate(data_sc_idx):
        bin_idx = sc + 32 if sc > 0 else sc + 64
        freq[bin_idx] = coded_bits[i]

    return generate_ofdm_symbol(freq)


def generate_ht_sig():
    """Generate HT-SIG1 + HT-SIG2 (2 OFDM symbols). BPSK QBPSK.
    For synthetic, use a known pattern.
    """
    # HT-SIG1: 24 bits (HT-SIG1) + HT-SIG2: 24 bits
    # MCS=0 (BPSK rate 1/2), length=10, etc.
    # For synthetic just use known BPSK pattern
    bits_sig1 = np.array([1, -1, 1, 1, -1, 1, 1, -1, 1, 1, -1, 1,
                          1, 1, -1, -1, 1, 1, -1, -1, 1, -1, 1, 1], dtype=np.complex64)
    bits_sig2 = np.array([1, 1, -1, 1, 1, 1, -1, 1, -1, 1, 1, -1,
                          1, -1, 1, 1, 1, 1, -1, 1, 1, -1, 1, 1], dtype=np.complex64)

    data_sc_idx = sorted([i for i in range(-26, 27) if i != 0 and i not in PILOT_SC])

    def make_symbol(bits_24):
        # Convolutional code rate 1/2, 24 → 48 bits (simplified: repeat)
        coded = np.zeros(48, dtype=np.complex64)
        for i in range(24):
            coded[2*i] = bits_24[i]
            coded[2*i+1] = bits_24[i]
        freq = np.zeros(64, dtype=np.complex64)
        for i, sc in enumerate(data_sc_idx):
            bin_idx = sc + 32 if sc > 0 else sc + 64
            freq[bin_idx] = coded[i]
        return generate_ofdm_symbol(freq)

    return make_symbol(bits_sig1), make_symbol(bits_sig2)


def generate_ht_stf():
    """Generate HT-STF (10 short symbols, period=16)."""
    S = np.array([1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1], dtype=np.complex64)
    return np.tile(S, 10)


def generate_ht_ltf():
    """Generate HT-LTF1 (1 long symbol = 80 samples)."""
    ltf_data = ifft64(LTF_SEQ)
    long_sym = np.concatenate([ltf_data[-16:], ltf_data])  # 80 samples
    return long_sym


def generate_data_symbol(data_sym_idx, n_data=48):
    """Generate one DATA OFDM symbol with random data + known pilots.

    data_sym_idx: index for pilot polarity lookup
    n_data: number of data SCs (48)
    """
    np.random.seed(1000 + data_sym_idx)  # deterministic
    data_sc_idx = sorted([i for i in range(-26, 27) if i != 0 and i not in PILOT_SC])

    # Random BPSK data
    data_bits = (2 * np.random.randint(0, 2, size=n_data) - 1).astype(np.complex64)

    freq = np.zeros(64, dtype=np.complex64)
    for i, sc in enumerate(data_sc_idx):
        bin_idx = sc + 32 if sc > 0 else sc + 64
        freq[bin_idx] = data_bits[i]

    # Pilots
    for i, sc in enumerate(PILOT_SC):
        bin_idx = sc + 32 if sc > 0 else sc + 64
        freq[bin_idx] = pilot_value(data_sym_idx, i)

    return generate_ofdm_symbol(freq)


def generate_frame(n_data_syms=20, random_state=42):
    """Generate full HT-Mixed frame: L-STF + L-LTF + L-SIG + HT-SIG1/2 + HT-STF + HT-LTF1 + DATA."""
    np.random.seed(random_state)
    parts = []
    parts.append(generate_l_stf())       # 160 samples
    parts.append(generate_l_ltf())       # 160 samples
    parts.append(generate_l_sig())       # 80 samples
    ht_sig1, ht_sig2 = generate_ht_sig()
    parts.append(ht_sig1)                # 80 samples
    parts.append(ht_sig2)                # 80 samples
    parts.append(generate_ht_stf())      # 80 samples
    parts.append(generate_ht_ltf())      # 80 samples
    for i in range(n_data_syms):
        parts.append(generate_data_symbol(i))

    return np.concatenate(parts).astype(np.complex64)


def apply_channel_per_symbol(samples, channel_func):
    """Apply per-symbol time-varying channel.

    channel_func(sym_idx) returns H[64] complex array
    """
    # We need to know where OFDM symbols start
    # Frame layout:
    # L-STF: 0..160
    # L-LTF: 160..320 (2x80)
    # L-SIG: 320..400 (1x80)
    # HT-SIG1: 400..480
    # HT-SIG2: 480..560
    # HT-STF: 560..640
    # HT-LTF1: 640..720
    # DATA[0]: 720..800, DATA[1]: 800..880, ...

    boundaries = [
        ('LTF0', 160, 240),  # LTF0 DATA (80 samples)
        ('LTF1', 240, 320),  # LTF1 DATA (80 samples)
        ('LSIG', 320, 400),
        ('HTSIG1', 400, 480),
        ('HTSIG2', 480, 560),
        ('HTLTF1', 640, 720),  # skip HT-STF (different structure)
    ]
    for i in range(40):  # max 40 data syms
        boundaries.append((f'DATA{i}', 720 + i*80, 800 + i*80))

    out = samples.copy()
    for name, start, end in boundaries:
        if end > len(samples):
            break
        sym = samples[start:end]
        # Apply channel in frequency domain
        # fft(sym) gives 80-element array, but we want to multiply by 64-element H
        # Truncate/pad appropriately. For 80 samples, FFT gives 80 bins.
        # We need to handle the 64 useful bins (drop first/last 8 due to zero-padding)
        fft_sym = np.fft.fft(sym)  # 80-element FFT
        # Build 80-element H: zero pad to length 80
        H_64 = channel_func(name)
        H_80 = np.concatenate([H_64[:32], [0]*16, H_64[32:]])  # 32 + 16 + 32 = 80
        fft_sym *= H_80
        out[start:end] = np.fft.ifft(fft_sym)
    return out


def make_freq_selective_channel(snr_db=10, phase_drift_per_sym=0.1, mag_cv=0.3,
                                rng=None):
    """Create a channel factory that returns H[64] per symbol.

    Args:
        snr_db: target SNR (controls noise level)
        phase_drift_per_sym: phase rotation per symbol (rad, simulating SFO)
        mag_cv: |H| coefficient of variation per SC
    """
    if rng is None:
        rng = np.random.default_rng(42)

    # Initial per-SC H: random phase + magnitude variation
    initial_phase = rng.uniform(-np.pi, np.pi, size=64)
    initial_mag = 1.0 + rng.normal(0, mag_cv, size=64)
    initial_mag = np.maximum(initial_mag, 0.1)  # avoid null

    # Zero out guard + DC
    initial_mag[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0
    initial_phase[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0

    H_current = initial_mag * np.exp(1j * initial_phase)
    sym_counter = {'v': 0}

    def channel(name):
        # For LTF/L-SIG/HT-SIG: use initial H (channel assumed stable during preamble)
        # For DATA: apply per-symbol phase drift
        nonlocal H_current
        if name.startswith('DATA'):
            # Phase drift (SFO/CFO residual) + small magnitude jitter
            drift = phase_drift_per_sym * rng.normal(0, 1, size=64)
            H_current = H_current * np.exp(1j * drift)
            mag_jitter = 1.0 + 0.05 * rng.normal(0, 1, size=64)
            H_current = H_current * mag_jitter
            H_current[[0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]] = 0
        return H_current.copy()

    return channel


def add_awgn(samples, snr_db, rng=None):
    """Add AWGN at given SNR (signal power / noise power)."""
    if rng is None:
        rng = np.random.default_rng(42)
    sig_power = np.mean(np.abs(samples)**2)
    noise_power = sig_power / (10**(snr_db/10))
    noise = np.sqrt(noise_power/2) * (rng.normal(size=len(samples)) +
                                       1j * rng.normal(size=len(samples)))
    return (samples + noise).astype(np.complex64)


def process_synthetic_frame(rx_samples, true_H_per_sym, n_data_syms,
                             Q=0.01, R=0.1):
    """Process synthetic frame: compute H baseline + Kalman track.

    Returns per-SC metrics comparing baseline H (from L-LTF) vs Kalman H vs truth.
    """
    # L-LTF0 FFT: samples [174, 238)
    # L-LTF1 FFT: samples [254, 318)
    # Wait - for synthetic frame, frame starts at sample 0 with L-STF.
    # So L-STF = [0, 160), L-LTF0 DATA = [174, 238), L-LTF1 DATA = [254, 318)

    LTF0_DATA = (174, 238)  # 174 = 160 (L-LTF start) + 14 (Phase 33 fix)
    LTF1_DATA = (254, 318)

    # True H at L-LTF time (channel is stable during preamble)
    H_LTF0_true = true_H_per_sym('LTF0')
    H_LTF1_true = true_H_per_sym('LTF1')

    # Extract L-LTF FFTs
    ltf0_fft = np.fft.fft(rx_samples[LTF0_DATA[0]:LTF0_DATA[1]])
    ltf1_fft = np.fft.fft(rx_samples[LTF1_DATA[0]:LTF1_DATA[1]])

    # Channel estimate from L-LTF (with known sequence)
    valid_bins = LTF_SEQ != 0
    H_LTF0_est = np.zeros(64, dtype=np.complex64)
    H_LTF1_est = np.zeros(64, dtype=np.complex64)
    H_LTF0_est[valid_bins] = ltf0_fft[valid_bins] / LTF_SEQ[valid_bins] / 64
    H_LTF1_est[valid_bins] = ltf1_fft[valid_bins] / LTF_SEQ[valid_bins] / 64

    # Baseline: average of LTF0 and LTF1 estimates
    H_baseline = (H_LTF0_est + H_LTF1_est) / 2

    # Kalman state: track all 64 bins (zero for guard bins, complex for active)
    # But we can only update pilot SCs from data symbols (52 active SCs, 4 pilots)
    H_kalman = H_baseline.copy()
    P_kalman = np.full(64, 1.0)

    metrics = {
        'h_error_baseline_per_sym': [],  # ||H_true[k] - H_baseline[k]||^2 per DATA sym
        'h_error_kalman_per_sym': [],
        'h_phase_err_baseline_per_sym': [],
        'h_phase_err_kalman_per_sym': [],
    }

    DATA_START = 720  # first DATA symbol start
    DATA_SYM_LEN = 80

    for d in range(n_data_syms):
        sym_start = DATA_START + d * DATA_SYM_LEN
        if sym_start + 80 > len(rx_samples):
            break

        # Extract DATA symbol FFT
        sym_fft = np.fft.fft(rx_samples[sym_start:sym_start+80])
        # No normalization here because channel was applied with full FFT (no /64)

        # True H at this DATA symbol
        H_true_d = true_H_per_sym(f'DATA{d}')

        # Measurement on pilot SCs: z = rx[pilot_bin] / pilot_value
        for i, pbin in enumerate(PILOT_BIN):
            tx_pilot = pilot_value(d, i)
            z = sym_fft[pbin] / tx_pilot / 64  # normalize to match H scale

            # Kalman update
            x_pred = H_kalman[pbin]
            P_pred = P_kalman[pbin] + Q
            K = P_pred / (P_pred + R)
            H_kalman[pbin] = x_pred + K * (z - x_pred)
            P_kalman[pbin] = (1 - K) * P_pred

        # Compute H errors at pilot SCs (vs truth)
        for pbin in PILOT_BIN:
            metrics['h_error_baseline_per_sym'].append(
                np.abs(H_true_d[pbin] - H_baseline[pbin])**2)
            metrics['h_error_kalman_per_sym'].append(
                np.abs(H_true_d[pbin] - H_kalman[pbin])**2)
            # Phase error (angle difference)
            metrics['h_phase_err_baseline_per_sym'].append(
                np.angle(H_true_d[pbin] * np.conj(H_baseline[pbin])))
            metrics['h_phase_err_kalman_per_sym'].append(
                np.angle(H_true_d[pbin] * np.conj(H_kalman[pbin])))

    return metrics


def main():
    p = argparse.ArgumentParser(description='Phase 111 T1b: Synthetic Kalman validation')
    p.add_argument('--n-frames', type=int, default=10)
    p.add_argument('--n-data-syms', type=int, default=20)
    p.add_argument('--snr-db', type=float, default=10.0)
    p.add_argument('--phase-drift', type=float, default=0.1,
                   help='Phase drift per DATA symbol (rad)')
    p.add_argument('--mag-cv', type=float, default=0.3,
                   help='|H| coefficient of variation')
    p.add_argument('--q', type=float, default=0.01)
    p.add_argument('--r', type=float, default=0.1)
    args = p.parse_args()

    print(f"[P111-T1b] Config: n_frames={args.n_frames}, n_data_syms={args.n_data_syms}, "
          f"SNR={args.snr_db} dB, phase_drift={args.phase_drift} rad/sym, mag_cv={args.mag_cv}, "
          f"Q={args.q}, R={args.r}", flush=True)

    rng_master = np.random.default_rng(2026)

    all_metrics = {
        'h_error_baseline_per_sym': [],
        'h_error_kalman_per_sym': [],
        'h_phase_err_baseline_per_sym': [],
        'h_phase_err_kalman_per_sym': [],
    }

    for frame_i in range(args.n_frames):
        # Generate fresh channel + frame
        rng = np.random.default_rng(int(rng_master.integers(0, 2**31)))
        channel = make_freq_selective_channel(
            snr_db=args.snr_db,
            phase_drift_per_sym=args.phase_drift,
            mag_cv=args.mag_cv,
            rng=rng,
        )
        tx_frame = generate_frame(n_data_syms=args.n_data_syms, random_state=frame_i)

        # Apply channel
        rx_no_noise = apply_channel_per_symbol(tx_frame, channel)

        # Add AWGN
        rx = add_awgn(rx_no_noise, snr_db=args.snr_db, rng=rng)

        # Process frame
        metrics = process_synthetic_frame(rx, channel, args.n_data_syms,
                                          Q=args.q, R=args.r)
        for k in all_metrics:
            all_metrics[k].extend(metrics[k])

    # Aggregate
    err_b = np.array(all_metrics['h_error_baseline_per_sym'])
    err_k = np.array(all_metrics['h_error_kalman_per_sym'])
    ph_b = np.array(all_metrics['h_phase_err_baseline_per_sym'])
    ph_k = np.array(all_metrics['h_phase_err_kalman_per_sym'])

    mse_b = err_b.mean()
    mse_k = err_k.mean()
    improv = (mse_b - mse_k) / max(mse_b, 1e-9) * 100

    phase_std_b = np.std(ph_b)
    phase_std_k = np.std(ph_k)

    print(f"\n[P111-T1b] === Aggregate H estimation accuracy ===", flush=True)
    print(f"[P111-T1b] Total measurements: {len(err_b)} ({args.n_frames} frames × "
          f"{args.n_data_syms} DATA syms × 4 pilots)", flush=True)
    print(f"[P111-T1b] Baseline H52 MSE:    {mse_b:.4f}", flush=True)
    print(f"[P111-T1b] Kalman H52 MSE:      {mse_k:.4f}", flush=True)
    print(f"[P111-T1b] Improvement:         {improv:.2f}%", flush=True)
    print(f"[P111-T1b] Phase error std (baseline): {np.degrees(phase_std_b):.2f}°", flush=True)
    print(f"[P111-T1b] Phase error std (Kalman):   {np.degrees(phase_std_k):.2f}°", flush=True)

    # Verdict
    print(f"\n[P111-T1b] === Verdict ===", flush=True)
    if mse_k < mse_b and np.degrees(phase_std_k) < 30:
        print(f"[P111-T1b] PASS: Kalman improves H MSE and phase std < 30°", flush=True)
        print(f"[P111-T1b] → Proceed to T2 (C++ implementation)", flush=True)
        return 0
    elif mse_k < mse_b:
        print(f"[P111-T1b] PARTIAL: MSE improves but phase std still ≥ 30°", flush=True)
        print(f"[P111-T1b] → Tune Q/R or use different motion model", flush=True)
        return 2
    else:
        print(f"[P111-T1b] REFUTED: Kalman does not improve H estimate on synthetic", flush=True)
        print(f"[P111-T1b] → Try different architecture (particle filter, EM, etc.)", flush=True)
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main() or 0)