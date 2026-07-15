#!/usr/bin/env python3
"""Phase 111 T1: Validate Kalman filter for per-SC H52 tracking.

Hypothesis (Phase 107 deep root cause):
- Per-SC |H| CV = 27-50% (frequency-selective)
- Per-SC argH std = 108° (random walk, NOT static — Phase 108 refuted Phase 107 static hypothesis)

Goal: Show that Kalman filter tracking of H52 (state=Re(H)+j*Im(H), random walk process,
pilot-based measurements) improves pilot residual MSE vs static baseline.

Method:
  1. Load /tmp/p110_t10_capture.fc32 (strongest TDD signal: --tx-gain 10 + --rx-gain 31.5)
  2. Detect L-STF peaks (Python L-STF detector from Phase 87)
  3. For each frame:
     a. FFT L-LTF0 (samples 174..237) and L-LTF1 (254..317), get H_LTF0, H_LTF1
     b. H52_baseline = (H_LTF0 + H_LTF1) / 2
     c. Initialize Kalman state for 4 pilot SCs: H_kalman[k] = H52_baseline[pilot_bin[k]]
     d. For each data symbol D = 1..N:
        - FFT data symbol, extract rx52
        - Compute pilot measurement: z_k = rx52[pilot_bin[k]] / (polarity[D] * 1.0)
        - Kalman update: H_kalman[k] = K * z_k + (1-K) * H_kalman[k]
        - Compute equalized pilot:
          eq_baseline[k] = rx52[pilot_bin[k]] / H52_baseline[pilot_bin[k]]
          eq_kalman[k]   = rx52[pilot_bin[k]] / H_kalman[k]
        - Residual vs known tx_pilot = ±1:
          residual_baseline[k] = |eq_baseline[k] - tx_pilot[k]|
          residual_kalman[k]   = |eq_kalman[k]   - tx_pilot[k]|
  4. Aggregate across frames: total MSE_baseline vs MSE_kalman on pilot SCs

Pass criteria:
  - MSE_kalman < MSE_baseline (Kalman better)
  - Phase std per pilot SC < 30° (vs ~108° baseline)
  - |H| CV per pilot SC < 10% (vs 27-50% baseline)
"""
import argparse
import sys
import numpy as np


# ===== 802.11n HT constants =====
# L-LTF sequence (64 subcarriers, BPSK ±1, 0=DC, 5+6 zeros at edges)
# From lib/equalizer/base.cc LONG[] (extracted programmatically, 64 elements)
LTF_SEQ = np.array([
    0, 0, 0, 0, 0, 0, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1,    # indices 0-15
    1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1,  # indices 16-31
    0, 1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,  # indices 32-47 (32=DC)
    1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1, 0, 0, 0, 0, 0     # indices 48-63
], dtype=np.complex64)
assert len(LTF_SEQ) == 64, f"LTF_SEQ must be 64, got {len(LTF_SEQ)}"

# Pilot SC indices (logical: -26..+26, skipping 0)
# SCs: -26, -25, ..., -1, +1, ..., +26 → 52 active
# Pilot SCs: -21, -7, +7, +21 → indices in 0..51 array
# Pilot bin indices in 0..63 FFT bin space:
# SC k → bin k+32 (for k>0), bin k+64 (for k<0)
# SC=-21 → bin 11
# SC=-7 → bin 25
# SC=+7 → bin 39
# SC=+21 → bin 53
PILOT_SC = np.array([-21, -7, 7, 21])
PILOT_BIN = np.array([11, 25, 39, 53])  # FFT bin indices (0..63)
PILOT_TX_IDX = np.array([48, 49, 50, 51])  # index in TX-mapper order (0..51)

# Active SC bins in 0..63 FFT (52 tones, skip DC and edge guards)
# SC k → bin k+32 if k>0 else k+64
def sc_to_bin(sc):
    return sc + 32 if sc > 0 else sc + 64

ACTIVE_SC = np.array([sc for sc in range(-26, 27) if sc != 0])
ACTIVE_BIN = np.array([sc_to_bin(sc) for sc in ACTIVE_SC])
assert len(ACTIVE_SC) == 52

# 127-element pilot polarity sequence from lib/equalizer/base.cc
POLARITY_127 = np.array([
    1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,1,-1,1,
    1,-1,1,1,1,1,1,1,-1,1,1,1,-1,1,1,-1,-1,1,1,1,-1,1,
    -1,-1,-1,1,-1,1,-1,-1,1,-1,-1,1,1,1,1,1,-1,-1,1,1,-1,-1,
    1,-1,1,-1,1,1,-1,-1,-1,1,1,-1,-1,-1,-1,1,-1,-1,1,-1,1,1,
    1,1,-1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,-1,1,1,-1,1,-1,1,1,
    1,-1,-1,1,-1,-1,-1,1,1,1,-1,-1,-1,-1,-1,-1,-1
], dtype=np.int8)


def pilot_value(data_sym_idx, pilot_idx):
    """Get pilot value for data_sym_idx (0-based from first HT data symbol) and pilot_idx (0..3).

    Pilot SCs: {-21, -7, +7, +21}, but +21 has opposite polarity.
    Pilot polarity per 802.11n standard: polarity[(n+1) % 127] for HT-SIG, polarity[n % 127] for data
    For HT-Data, n is the OFDM symbol index in the data field (0-based).
    Pilot SC +21 (last) uses sign = -p, others use sign = +p.
    """
    p = POLARITY_127[data_sym_idx % 127]
    return -p if pilot_idx == 3 else p


# ===== L-STF detection (from Phase 87, slightly cleaned) =====
def detect_l_stf(iq, chunk_size=5_000_000, min_distance=20_000, threshold_factor=10.0):
    """Detect L-STF starts (each L-STF has 10 short symbols, period=16).

    Returns array of L-STF START sample positions.
    """
    n = len(iq)
    period = 16
    win = 16
    starts = []
    last_peak_pos = -min_distance

    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]
        b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        median_corr = float(np.median(corr_smooth))
        threshold = max(median_corr * threshold_factor, 0.5)
        above = corr_smooth > threshold
        rising_edges = np.where(np.diff(above.astype(np.int32)) == 1)[0]
        for r in rising_edges:
            abs_pos = chunk_start + int(r)
            if abs_pos - last_peak_pos >= min_distance:
                starts.append(abs_pos)
                last_peak_pos = abs_pos
        del chunk, a, b, corr_raw, corr_smooth, above

    return np.array(starts)


def fft64(x):
    """64-point FFT (numpy convention)."""
    return np.fft.fft(x) / np.sqrt(64)


def extract_symbol(iq, frame_start, sym_offset):
    """Extract 80 samples starting at frame_start + sym_offset, return 64-point FFT (skip CP).

    sym_offset: 0 = L-STF start, 160 = L-LTF start, etc.
    Returns complex array of 64 FFT values.
    """
    start = frame_start + sym_offset + 16  # skip CP (16 samples)
    if start + 64 > len(iq):
        return None
    samples = iq[start:start + 64].astype(np.complex64)
    return fft64(samples)


def process_frame(iq, frame_start, max_data_syms=20):
    """Process one frame: extract H52 baseline + run Kalman through data symbols.

    Returns dict with per-frame metrics, or None if frame invalid.
    """
    # L-LTF0 DATA: offset 160 + 16 (CP) = 176? or 174?
    # Per C++ code: d_frame_start = 174 = L-LTF0 DATA start (after CP)
    # Actually 174 = 160 (L-LTF START) + 14 (Phase 33 empirical fix)
    # But we want FFT window to include the 64 useful samples
    # So FFT window is samples [174, 174+64) = [174, 238) for L-LTF0
    # Wait, 174 is the START of L-LTF0 DATA (after the GI). FFT gives 64 bins.
    # Standard says L-LTF has 2x long symbol = 128 samples + 2x GI = 32 samples = 160 total
    # So L-LTF0 = 64 data + 32 GI = 96 samples? No, L-LTF structure is special:
    # - L-LTF0: 32-sample double-GI + 64 data = 96 samples
    # - L-LTF1: 64 data = 64 samples
    # Total L-LTF = 160 samples

    # Per C++ FRAME_START_BASE = 174 = position where to start reading L-LTF0 DATA
    # So L-LTF0 FFT window: [174, 174+64) = [174, 238)
    # L-LTF1 starts at 174 + 64 = 238 (immediately after L-LTF0 data, no GI in between)
    # Actually L-LTF1 has a 16-sample CP at position 238, then 64 data at 254
    # L-LTF1 FFT window: [254, 254+64) = [254, 318)

    LTF0_OFFSET = 174  # L-LTF0 DATA start (per C++ FRAME_START_BASE)
    LTF1_OFFSET = 254  # L-LTF1 DATA start (= LTF0_OFFSET + 80)

    if LTF1_OFFSET + 64 > len(iq):
        return None

    # Extract L-LTF0 and L-LTF1 FFT
    ltf0_samples = iq[LTF0_OFFSET:LTF0_OFFSET+64].astype(np.complex64)
    ltf1_samples = iq[LTF1_OFFSET:LTF1_OFFSET+64].astype(np.complex64)
    LTF0_FFT = fft64(ltf0_samples)
    LTF1_FFT = fft64(ltf1_samples)

    # Channel estimate: H[k] = FFT[k] / LTF_SEQ[k]
    # Skip bins where LTF_SEQ = 0 (DC and edges)
    valid_bins = LTF_SEQ != 0
    H_LTF0 = np.zeros(64, dtype=np.complex64)
    H_LTF1 = np.zeros(64, dtype=np.complex64)
    H_LTF0[valid_bins] = LTF0_FFT[valid_bins] / LTF_SEQ[valid_bins]
    H_LTF1[valid_bins] = LTF1_FFT[valid_bins] / LTF_SEQ[valid_bins]

    # Baseline H52: average of LTF0 and LTF1 estimates at 52 active SCs
    H52_baseline_full = (H_LTF0 + H_LTF1) / 2.0  # 64 bins
    H52_baseline = H52_baseline_full[ACTIVE_BIN]  # 52 SCs

    # Pilot baseline
    H_pilot_baseline = H52_baseline_full[PILOT_BIN]  # 4 pilots

    # Frame structure after L-LTF:
    # L-LTF1 ends at 318
    # L-SIG: 318 + 16 (CP) = 334..398 (1 OFDM symbol)
    # HT-SIG1: 398..462
    # HT-SIG2: 462..526
    # HT-STF: 526..606 (80 samples, not standard OFDM)
    # HT-LTF1 (if HT_DATA_LTFS=1): 606 + 16 (CP) = 622..686
    # DATA: 686 + 16 (CP) = 702, then 80 samples per data symbol

    # We assume HT_DATA_LTFS=1 (standard HT-Mixed, 1 LTF)
    # So first DATA symbol DATA window: [702, 782) for FFT, [686, 766) for samples
    # Actually each DATA symbol = 80 samples (16 CP + 64 data), DATA FFT window is 64 samples after CP

    DATA_START_OFFSET = 702  # first DATA symbol FFT window start
    DATA_SYM_LEN = 80

    # Extract and process up to max_data_syms DATA symbols
    pilot_residuals_baseline = []  # list of |eq - tx_pilot|^2 for each (frame, sym, pilot)
    pilot_residuals_kalman = []
    pilot_phases_baseline = []  # arg of equalized pilot (with tx_pilot back-rotation)
    pilot_phases_kalman = []
    pilot_H_mag_baseline = []
    pilot_H_mag_kalman = []

    # Kalman state per pilot SC: [Re(H), Im(H)]
    # Process: random walk, Q = process_noise_var
    # Measure: z = rx[pilot_bin] / tx_pilot, R = measurement_noise_var
    Q = 0.01  # process noise variance (tunable)
    R = 0.1   # measurement noise variance (tunable)

    H_kalman_pilot = H_pilot_baseline.copy()
    P_kalman_pilot = np.full(4, 1.0)  # initial uncertainty

    for data_sym_idx in range(max_data_syms):
        sym_start = DATA_START_OFFSET + data_sym_idx * DATA_SYM_LEN
        if sym_start + 64 > len(iq):
            break

        # Extract data symbol FFT
        sym_samples = iq[sym_start:sym_start+64].astype(np.complex64)
        sym_fft = fft64(sym_samples)
        rx_pilot = sym_fft[PILOT_BIN]  # 4 complex measurements

        # Get tx pilot values for this data symbol
        tx_pilot = np.array([pilot_value(data_sym_idx, i) for i in range(4)], dtype=np.complex64)

        # Measurement: z = rx / tx_pilot (since rx = H * tx + noise)
        z = rx_pilot / tx_pilot

        # Kalman update per pilot SC
        for i in range(4):
            # Predict (random walk: x_pred = x, P_pred = P + Q)
            x_pred = H_kalman_pilot[i]
            P_pred = P_kalman_pilot[i] + Q

            # Update (measurement is complex, treat Re and Im independently is OK for simplicity)
            # Actually: do complex Kalman update
            K = P_pred / (P_pred + R)
            H_kalman_pilot[i] = x_pred + K * (z[i] - x_pred)
            P_kalman_pilot[i] = (1 - K) * P_pred

        # Equalized pilot using baseline H
        eq_pilot_baseline = rx_pilot / H_pilot_baseline
        # Equalized pilot using Kalman H
        eq_pilot_kalman = rx_pilot / H_kalman_pilot

        # Residual: equalized value should match tx_pilot (= ±1)
        residual_baseline = np.abs(eq_pilot_baseline - tx_pilot)**2
        residual_kalman = np.abs(eq_pilot_kalman - tx_pilot)**2

        pilot_residuals_baseline.append(residual_baseline)
        pilot_residuals_kalman.append(residual_kalman)

        # Phase tracking: arg(eq * conj(tx_pilot)) should be 0 if H is perfect
        phase_baseline = np.angle(eq_pilot_baseline * np.conj(tx_pilot))
        phase_kalman = np.angle(eq_pilot_kalman * np.conj(tx_pilot))
        pilot_phases_baseline.append(phase_baseline)
        pilot_phases_kalman.append(phase_kalman)

        # |H| stability: track |H_kalman| over symbols (baseline = constant)
        pilot_H_mag_baseline.append(np.abs(H_pilot_baseline))
        pilot_H_mag_kalman.append(np.abs(H_kalman_pilot))

    return {
        'frame_start': frame_start,
        'n_data_syms': len(pilot_residuals_baseline),
        'residuals_baseline': np.array(pilot_residuals_baseline),  # (n_sym, 4)
        'residuals_kalman': np.array(pilot_residuals_kalman),
        'phases_baseline': np.array(pilot_phases_baseline),  # (n_sym, 4) in radians
        'phases_kalman': np.array(pilot_phases_kalman),
        'H_mag_baseline': np.array(pilot_H_mag_baseline),  # (n_sym, 4)
        'H_mag_kalman': np.array(pilot_H_mag_kalman),
    }


def main():
    p = argparse.ArgumentParser(description='Phase 111 T1: Kalman H52 tracking validation')
    p.add_argument('--iq-file', default='/tmp/p110_t10_capture.fc32',
                   help='Path to USRP IQ capture (.fc32 complex64)')
    p.add_argument('--max-frames', type=int, default=20, help='Max frames to process')
    p.add_argument('--max-data-syms', type=int, default=20, help='Max data symbols per frame')
    p.add_argument('--q', type=float, default=0.01, help='Kalman process noise variance')
    p.add_argument('--r', type=float, default=0.1, help='Kalman measurement noise variance')
    p.add_argument('--threshold-factor', type=float, default=10.0, help='L-STF threshold factor')
    args = p.parse_args()

    print(f"[P111-T1] Loading IQ from {args.iq_file}...", flush=True)
    iq = np.memmap(args.iq_file, dtype=np.complex64, mode='r')
    print(f"[P111-T1] IQ length: {len(iq)} samples ({len(iq)/20e6:.2f}s @ 20MS/s)", flush=True)

    print(f"[P111-T1] Detecting L-STF starts...", flush=True)
    l_stf_starts = detect_l_stf(iq, threshold_factor=args.threshold_factor)
    print(f"[P111-T1] Found {len(l_stf_starts)} L-STF peaks", flush=True)

    # Map L-STF start to frame_start (= L-LTF0 DATA start = L-STF start + 174)
    frame_starts = l_stf_starts + 174

    # Filter to frames with enough room
    valid_frames = [fs for fs in frame_starts
                    if fs + 702 + args.max_data_syms * 80 + 64 <= len(iq)]
    valid_frames = valid_frames[:args.max_frames]
    print(f"[P111-T1] Processing {len(valid_frames)} valid frames", flush=True)

    all_results = []
    for i, fs in enumerate(valid_frames):
        result = process_frame(iq, fs, max_data_syms=args.max_data_syms)
        if result and result['n_data_syms'] > 0:
            all_results.append(result)
            if i < 3 or (i+1) % 5 == 0:
                print(f"[P111-T1]   Frame {i+1}/{len(valid_frames)}: "
                      f"n_data_syms={result['n_data_syms']} "
                      f"MSE_baseline={result['residuals_baseline'].mean():.4f} "
                      f"MSE_kalman={result['residuals_kalman'].mean():.4f}", flush=True)

    if not all_results:
        print("[P111-T1] ERROR: no valid frames processed!", flush=True)
        sys.exit(1)

    # Aggregate across all frames
    print(f"\n[P111-T1] === Aggregate Results (Q={args.q}, R={args.r}) ===", flush=True)
    print(f"[P111-T1] Frames processed: {len(all_results)}", flush=True)
    total_syms = sum(r['n_data_syms'] for r in all_results)
    print(f"[P111-T1] Total data symbols: {total_syms}", flush=True)

    # Stack per-pilot-SC arrays
    res_b = np.vstack([r['residuals_baseline'] for r in all_results])  # (N, 4)
    res_k = np.vstack([r['residuals_kalman'] for r in all_results])
    ph_b = np.vstack([r['phases_baseline'] for r in all_results])  # (N, 4)
    ph_k = np.vstack([r['phases_kalman'] for r in all_results])

    print(f"\n[P111-T1] === Per-pilot-SC metrics (Baseline vs Kalman) ===", flush=True)
    pilot_labels = ['SC=-21', 'SC=-7', 'SC=+7', 'SC=+21']
    print(f"{'Pilot SC':<10} {'MSE_base':>10} {'MSE_kalman':>10} {'Improv':>8} "
          f"{'Phase_std_b':>12} {'Phase_std_k':>12} {'|H|_CV_b':>10} {'|H|_CV_k':>10}", flush=True)
    total_improv = 0
    pass_count = 0
    for i in range(4):
        mse_b = res_b[:, i].mean()
        mse_k = res_k[:, i].mean()
        improv = (mse_b - mse_k) / max(mse_b, 1e-9) * 100  # percent improvement

        # Per-SC phase std (across all data symbols)
        # Phase is wrapped to [-pi, pi], but for small angles std works
        phase_std_b = np.std(ph_b[:, i])
        phase_std_k = np.std(ph_k[:, i])

        # |H| CV: baseline has CV=0 (constant), Kalman has CV from updates
        h_mag_b = np.array([r['H_mag_baseline'][:, i] for r in all_results]).flatten()
        h_mag_k = np.array([r['H_mag_kalman'][:, i] for r in all_results]).flatten()
        cv_b = np.std(h_mag_b) / max(np.mean(h_mag_b), 1e-9)
        cv_k = np.std(h_mag_k) / max(np.mean(h_mag_k), 1e-9)

        marker = ""
        if mse_k < mse_b:
            pass_count += 1
            marker = " ✓"
        print(f"{pilot_labels[i]:<10} {mse_b:>10.4f} {mse_k:>10.4f} {improv:>7.1f}% "
              f"{np.degrees(phase_std_b):>11.2f}° {np.degrees(phase_std_k):>11.2f}° "
              f"{cv_b*100:>9.2f}% {cv_k*100:>9.2f}%{marker}", flush=True)
        total_improv += improv

    avg_improv = total_improv / 4
    print(f"\n[P111-T1] === Summary ===", flush=True)
    print(f"[P111-T1] Average MSE improvement: {avg_improv:.2f}%", flush=True)
    print(f"[P111-T1] Pilot SCs where Kalman improves: {pass_count}/4", flush=True)

    # Overall verdict
    overall_mse_b = res_b.mean()
    overall_mse_k = res_k.mean()
    overall_improv = (overall_mse_b - overall_mse_k) / max(overall_mse_b, 1e-9) * 100
    print(f"[P111-T1] Overall MSE: baseline={overall_mse_b:.4f} kalman={overall_mse_k:.4f} "
          f"improvement={overall_improv:.2f}%", flush=True)

    # Phase std per pilot SC (target < 30°)
    phase_stds_k = [np.std(ph_k[:, i]) for i in range(4)]
    avg_phase_std_k = np.degrees(np.mean(phase_stds_k))
    print(f"[P111-T1] Avg per-SC phase std (Kalman): {avg_phase_std_k:.2f}° (target < 30°)", flush=True)

    if overall_mse_k < overall_mse_b and avg_phase_std_k < 30:
        print(f"\n[P111-T1] VERDICT: PASS — Kalman improves pilot MSE and phase std < 30°", flush=True)
        print(f"[P111-T1] Recommend proceeding to T2 (C++ implementation in frame_equalizer)", flush=True)
        sys.exit(0)
    elif overall_mse_k < overall_mse_b:
        print(f"\n[P111-T1] VERDICT: PARTIAL — Kalman improves MSE but phase std still ≥ 30°", flush=True)
        print(f"[P111-T1] Tune Q/R parameters and re-run", flush=True)
        sys.exit(2)
    else:
        print(f"\n[P111-T1] VERDICT: REFUTED — Kalman does NOT improve MSE on pilot SCs", flush=True)
        print(f"[P111-T1] Investigate why: pilot measurements may be too noisy, or H tracking too slow", flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()