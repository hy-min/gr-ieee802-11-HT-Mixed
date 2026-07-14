#!/usr/bin/env python3
"""Phase 145b: offline δ-correction sign analysis on USRP capture.

Tests hypotheses for why L-SIG is NOISE_LIKE on USRP:
  none      : let δ cancel naturally in eq = rx / H
  rx_plus   : current C++ code (apply +δ phase to rx only)
  rx_minus  : sign-flip (apply -δ phase to rx only)
  h_plus    : apply +δ phase to H instead of rx
  h_minus   : apply -δ phase to H instead of rx

If the C++ δ correction is the root cause, one alternative should produce a
clean BPSK L-SIG constellation on USRP captures where δ is large.
"""
import argparse
import numpy as np
import sys

# 52-element order used in frame_equalizer (48 data + 4 pilots):
# [-26..-22, -20..-8, -6..-1, +1..+6, +8..+13, +14..+19, +20, +22..+26, -21,-7,+7,+21]
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def sc_to_bin(sc):
    return sc if sc >= 0 else sc + 64


# L-LTF TX reference in same 52-element order (BPSK +/-1 for data + pilots)
LTF_52 = np.array([
    +1,+1,-1,-1,+1,-1, +1,-1,+1,+1,+1,+1,
    +1,+1,-1,-1,+1,+1, +1,-1,+1,+1,+1,+1,
    +1,-1,-1,+1,+1,-1, -1,+1,-1,-1,-1,-1,
    -1,+1,+1,-1,-1,+1, -1,-1,+1,+1,+1,+1,
    +1,+1,+1,+1
], dtype=np.float32)


def detect_l_stf_starts(iq, threshold_factor=10.0, min_distance=20000):
    n = len(iq)
    period = 16
    win = 16
    starts = []
    last_peak_pos = -min_distance
    chunk_size = 5_000_000
    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]
        b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        median_corr = float(np.median(corr_smooth))
        threshold = max(median_corr * threshold_factor, 0.01)
        above = corr_smooth > threshold
        rising_edges = np.where(np.diff(above.astype(np.int32)) == 1)[0]
        for r in rising_edges:
            abs_pos = chunk_start + int(r)
            if abs_pos - last_peak_pos >= min_distance:
                starts.append(abs_pos)
                last_peak_pos = abs_pos
    return np.array(starts, dtype=np.int64)


def extract_symbol(iq, start):
    if start + 64 > len(iq):
        return None
    return np.fft.fft(iq[start:start+64].astype(np.complex64))


def fft52(iq, start):
    """Return 52 active SCs in frame_equalizer order for FFT window at start."""
    F = extract_symbol(iq, start)
    if F is None:
        return None
    return np.array([F[sc_to_bin(sc)] for sc in SC_INDEX_52], dtype=np.complex64)


def estimate_delta_from_h52(H52):
    """Weighted linear regression of arg(H) vs SC, return delta in [0,1)."""
    sc = SC_INDEX_52[:48].astype(np.float64)
    a = np.angle(H52[:48])
    w = np.abs(H52[:48])
    sum_w = np.sum(w)
    if sum_w < 1e-9:
        return 0.0
    mean_sc = np.sum(sc * w) / sum_w
    mean_a = np.sum(a * w) / sum_w
    cov = np.sum(w * (sc - mean_sc) * (a - mean_a))
    var = np.sum(w * (sc - mean_sc) ** 2)
    if var < 1e-9:
        return 0.0
    b = cov / var
    delta = -b * 64.0 / (2.0 * np.pi)
    delta = delta - np.floor(delta)
    return float(delta)


def estimate_cfo_sfo(H0, H1):
    """Estimate CFO (rad/symbol) and SFO (rad/SC) from received L-LTF0/L-LTF1."""
    ratio = H1 / (H0 + 1e-12)
    pd = np.angle(ratio)
    sc = SC_INDEX_52.astype(np.float64)
    sum_sc2 = np.sum(sc * sc)
    sum_sc_phase = np.sum(sc * pd)
    sum_phase = np.sum(pd)
    sfo = sum_sc_phase / sum_sc2 if sum_sc2 > 1e-6 else 0.0
    cfo = sum_phase / 52.0
    # Soft clamp like C++
    if abs(sfo) > 1e-2:
        sfo = 1e-2 if sfo > 0 else -1e-2
    return cfo, sfo


def analyze_frame(iq, fs, lltf0_off, lltf1_off, lsig_off):
    H0_rx = fft52(iq, fs + lltf0_off)
    H1_rx = fft52(iq, fs + lltf1_off)
    lsig_rx = fft52(iq, fs + lsig_off)
    if H0_rx is None or H1_rx is None or lsig_rx is None:
        return None

    # H estimate (L-LTF0 only, like baseline)
    H52 = H0_rx / LTF_52
    delta = estimate_delta_from_h52(H52)

    # CFO/SFO from L-LTF0/1
    cfo, sfo = estimate_cfo_sfo(H0_rx, H1_rx)

    # Compensate L-SIG for CFO/SFO (symbol index 2 relative to L-LTF0)
    sym_idx = 2
    phase_per_sc = cfo + sfo * SC_INDEX_52
    lsig_comp = lsig_rx * np.exp(-1j * phase_per_sc * sym_idx)

    # Test 5 strategies
    sc = SC_INDEX_52.astype(np.float64)
    delta_phase = 2.0 * np.pi * sc * delta / 64.0

    strategies = {
        'none':     (lsig_comp,           H52),
        'rx_plus':  (lsig_comp * np.exp(+1j * delta_phase), H52),
        'rx_minus': (lsig_comp * np.exp(-1j * delta_phase), H52),
        'h_plus':   (lsig_comp,           H52 * np.exp(+1j * delta_phase)),
        'h_minus':  (lsig_comp,           H52 * np.exp(-1j * delta_phase)),
    }

    results = {}
    for name, (rx, H) in strategies.items():
        eq = np.zeros(52, dtype=np.complex64)
        for i in range(48):
            if abs(H[i]) > 1e-3:
                eq[i] = rx[i] / H[i]
        # BPSK quality metrics
        im_var = float(np.var(eq[:48].imag))
        im_mean = float(np.mean(np.abs(eq[:48].imag)))
        re_spread = float(np.std(eq[:48].real))
        # Hard bits (BPSK: real > 0 -> bit 1)
        bits = (eq[:48].real > 0).astype(int)
        results[name] = {
            'im_var': im_var,
            'im_mean': im_mean,
            're_spread': re_spread,
            'bits': bits,
        }

    return {
        'delta': delta,
        'cfo': cfo,
        'sfo': sfo,
        'H_mag_mean': float(np.mean(np.abs(H52))),
        'strategies': results,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--iq', default='/tmp/p145_postfix_5250.fc32')
    p.add_argument('--max-frames', type=int, default=20)
    p.add_argument('--lltf0', type=int, default=174, help='L-LTF0 DATA start offset from L-STF start')
    p.add_argument('--lltf1', type=int, default=254, help='L-LTF1 DATA start offset')
    p.add_argument('--lsig', type=int, default=334, help='L-SIG DATA start offset')
    p.add_argument('--expected-bits', default=None, help='48-bit expected L-SIG bits')
    args = p.parse_args()

    print(f'Loading {args.iq} ...')
    iq = np.memmap(args.iq, dtype=np.complex64, mode='r')
    print(f'  {len(iq)} samples ({len(iq)/20e6:.2f}s @ 20MHz)')

    print('Detecting L-STF starts ...')
    starts = detect_l_stf_starts(iq)
    print(f'  found {len(starts)} frames')
    if len(starts) == 0:
        sys.exit(1)

    expected_bits = None
    if args.expected_bits:
        expected_bits = np.array([int(c) for c in args.expected_bits[:48]])

    print(f'Using offsets: L-LTF0={args.lltf0}, L-LTF1={args.lltf1}, L-SIG={args.lsig}')
    all_results = []
    for fs in starts[:args.max_frames]:
        r = analyze_frame(iq, fs, args.lltf0, args.lltf1, args.lsig)
        if r:
            all_results.append(r)

    if not all_results:
        print('No valid frames analyzed')
        sys.exit(1)

    print(f'\nAnalyzed {len(all_results)} frames')
    header = f"{'frame':>5} {'delta':>6} {'|H|':>7} {'strategy':>10} {'im_var':>10} {'im_mean':>9} {'re_spread':>10}"
    if expected_bits is not None:
        header += f" {'n_correct':>9}"
    print(header)

    for i, r in enumerate(all_results):
        for name in ['none', 'rx_plus', 'rx_minus', 'h_plus', 'h_minus']:
            sr = r['strategies'][name]
            line = (f"{i:>5} {r['delta']:>6.3f} {r['H_mag_mean']:>7.3f} "
                    f"{name:>10} {sr['im_var']:>10.4f} {sr['im_mean']:>9.4f} {sr['re_spread']:>10.4f}")
            if expected_bits is not None:
                n_correct = int(np.sum(sr['bits'] == expected_bits))
                line += f" {n_correct:>9}"
            print(line)

    print('\n=== Aggregate im_var (lower is better) ===')
    for name in ['none', 'rx_plus', 'rx_minus', 'h_plus', 'h_minus']:
        vals = [r['strategies'][name]['im_var'] for r in all_results]
        print(f'  {name:>10}: {np.mean(vals):.4f} +/- {np.std(vals):.4f}')

    if expected_bits is not None:
        print('\n=== Aggregate n_correct / 48 (higher is better) ===')
        for name in ['none', 'rx_plus', 'rx_minus', 'h_plus', 'h_minus']:
            vals = [r['strategies'][name]['bits'] == expected_bits for r in all_results]
            counts = [int(np.sum(v)) for v in vals]
            print(f'  {name:>10}: {np.mean(counts):.2f} / 48')


if __name__ == '__main__':
    main()
