#!/usr/bin/env python3
"""
Phase 82 T3.5 — T3 redo with proper LTF reference division.

T3 used (F0+F1)/2 directly as H_est. But C++ estimate_header_channel_from_lltf52
divides by kLltf48TX (TX reference). This fixes the missing reference division
and re-runs the analysis.

Two questions:
1. Does adding LTF reference division close the 10-dB SNR gap with Phase 81?
2. With proper H_est, does δ correction produce 0x9 consistently (matching Phase 81)?

If yes to both: T4 attack lever is real (tune δ to get 0xD from 0x9).
If no to 1: my analysis path is fundamentally broken.
If no to 2: my capture differs from Phase 81's capture.
"""
import numpy as np
import sys

CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

# Standard L-LTF reference in 64-bin FFT order (kLltf64Binned).
# Per lib/ieee80211_constants.h.
KLTF64 = np.array([
    0,    # bin  0: DC
    1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,    # bin 1-14: SC +1..+14
    -1, -1, 1, 1, -1, 1, 1, -1, 1, -1, 1, 1,         # bin 15-26: SC +15..+26
    0, 0, 0, 0, 0,    # bin 27-31: guard
    0,                # bin 32: DC
    0, 0, 0, 0, 0,    # bin 33-37: guard
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1,            # bin 38-48: SC -26..-16
    1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,   # bin 49-63: SC -15..-1
], dtype=np.complex64)

# Active SC indices (bins 1-26 + 38-63)
ACTIVE_SC_INDICES = list(range(1, 27)) + list(range(38, 64))
assert len(ACTIVE_SC_INDICES) == 52

# LTF reference values for active SCs (in bin order)
LTF_ACTIVE = KLTF64[ACTIVE_SC_INDICES]
N_NULL = (LTF_ACTIVE == 0).sum()
print(f"[T3.5] Active SCs: {len(ACTIVE_SC_INDICES)}, null in LTF: {N_NULL}")


# ===== Same helpers as T3, but with LTF ref division =====
def find_all_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
    n = len(iq)
    period = 16; win = 16; starts = []
    last_peak_pos = -min_distance
    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]; b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        median_corr = float(np.median(corr_smooth))
        threshold = max(median_corr * 10.0, 0.01)
        above = corr_smooth > threshold
        rising_edges = np.where(np.diff(above.astype(np.int32)) == 1)[0]
        for r in rising_edges:
            abs_pos = chunk_start + int(r)
            if abs_pos - last_peak_pos >= min_distance:
                starts.append(abs_pos)
                last_peak_pos = abs_pos
        del chunk, a, b, corr_raw, corr_smooth, above
    return starts


def estimate_h52_with_ref(iq, fs):
    """H52 = (rx_LTS0 + rx_LTS1)/2 / LTF_REF, in FFT bin order."""
    lts0_start = fs + 176
    lts1_start = fs + 256
    if lts1_start + 64 > len(iq):
        return None
    LTS0 = iq[lts0_start:lts0_start + 64]
    LTS1 = iq[lts1_start:lts1_start + 64]
    F0 = np.fft.fft(LTS0, 64)
    F1 = np.fft.fft(LTS1, 64)
    F0a = F0[ACTIVE_SC_INDICES]
    F1a = F1[ACTIVE_SC_INDICES]
    avg = (F0a + F1a) / 2.0
    # Divide by LTF reference (sign flips only since LTF_ACTIVE = ±1 or 0)
    H = np.zeros_like(avg)
    valid = np.abs(LTF_ACTIVE) > 1e-6
    H[valid] = avg[valid] / LTF_ACTIVE[valid]
    return H


def estimate_h52_no_ref(iq, fs):
    """Original T2/T3 estimate without LTF reference division."""
    lts0_start = fs + 176
    lts1_start = fs + 256
    if lts1_start + 64 > len(iq):
        return None
    LTS0 = iq[lts0_start:lts0_start + 64]
    LTS1 = iq[lts1_start:lts1_start + 64]
    F0 = np.fft.fft(LTS0, 64)
    F1 = np.fft.fft(LTS1, 64)
    F0a = F0[ACTIVE_SC_INDICES]
    F1a = F1[ACTIVE_SC_INDICES]
    return (F0a + F1a) / 2.0


# SC index in FFT-bin-order for δ correction (NOT TX order!)
# bins 1..26 → SC +1..+26 (positive)
# bins 38..63 → SC -26..-1 (negative)
SC_INDEX_BIN_ORDER = np.array(
    list(range(1, 27)) + list(range(-26, 0)), dtype=np.float64
)
assert len(SC_INDEX_BIN_ORDER) == 52


def estimate_delta_weighted(H52_bin_order):
    argH = np.angle(H52_bin_order).astype(np.float64)
    w = np.abs(H52_bin_order).astype(np.float64)
    sum_w = w.sum()
    if sum_w < 1e-9:
        return 0.0
    mean_sc = (SC_INDEX_BIN_ORDER * w).sum() / sum_w
    mean_arg = (argH * w).sum() / sum_w
    cov = (w * (SC_INDEX_BIN_ORDER - mean_sc) * (argH - mean_arg)).sum()
    var = (w * (SC_INDEX_BIN_ORDER - mean_sc) ** 2).sum()
    if var < 1e-9:
        return 0.0
    b = cov / var
    delta = -b * 64.0 / (2.0 * np.pi)
    delta = delta - np.floor(delta)
    return float(delta)


def apply_delta_to_eq_bin_order(eq_bin_order, delta):
    rot = np.exp(1j * 2.0 * np.pi * SC_INDEX_BIN_ORDER * delta / 64.0)
    return eq_bin_order * rot


def extract_lsig_eq(iq, fs, H52_bin_order, delta=None):
    sig_start = fs + 336
    if sig_start + 64 > len(iq):
        return None
    SIG = iq[sig_start:sig_start + 64]
    Fsig = np.fft.fft(SIG, 64)
    eq = Fsig[ACTIVE_SC_INDICES] / H52_bin_order
    if delta is not None:
        eq = apply_delta_to_eq_bin_order(eq, delta)
    return eq


def snr_db(eq_data_sc):
    return 20.0 * np.log10(np.mean(np.abs(eq_data_sc.real)) / (np.std(eq_data_sc.imag) + 1e-12))


# Viterbi (hard, k=7, rate 1/2)
def encode_bit(input_bit, state):
    new_state = (state[1], state[2], state[3], state[4], state[5], input_bit)
    o1 = (input_bit ^ state[5] ^ state[3] ^ state[2] ^ state[1] ^ state[0]) & 1
    o2 = (input_bit ^ state[5] ^ state[4] ^ state[3] ^ state[0]) & 1
    return o1, o2, new_state


def viterbi_decode_hard(rx_bits, n_steps=24):
    INF = float('inf')
    n_states = 64
    pm = np.full(n_states, INF); pm[0] = 0.0
    prev_state = np.zeros((n_steps, n_states), dtype=np.int32)
    prev_bit = np.zeros((n_steps, n_states), dtype=np.int32)
    for t in range(n_steps):
        new_pm = np.full(n_states, INF)
        r0 = rx_bits[2*t]; r1 = rx_bits[2*t+1]
        for s in range(n_states):
            if pm[s] == INF: continue
            state = tuple((s >> (5-i)) & 1 for i in range(6))
            for bit in [0, 1]:
                o0, o1, new_state = encode_bit(bit, state)
                metric = (o0 != r0) + (o1 != r1)
                new_metric = pm[s] + metric
                new_s = (new_state[0]<<5)|(new_state[1]<<4)|(new_state[2]<<3)|(new_state[3]<<2)|(new_state[4]<<1)|new_state[5]
                if new_metric < new_pm[new_s]:
                    new_pm[new_s] = new_metric
                    prev_state[t][new_s] = s
                    prev_bit[t][new_s] = bit
        pm = new_pm
    best_state = int(np.argmin(pm))
    best_metric = pm[best_state]
    decoded = np.zeros(n_steps, dtype=np.int32)
    s = best_state
    for t in range(n_steps-1, -1, -1):
        decoded[t] = prev_bit[t][s]
        s = prev_state[t][s]
    return decoded.tolist(), float(best_metric)


def decode_lsig_full(eq_bin_order):
    """eq_bin_order: 52 SCs in FFT bin order. Pilots at bin indices 11, 25, 39, 53 (which are at array indices 10, 24, 38, 52... no wait).

    Bin order is: 1..26 (positions 0..25), 38..63 (positions 26..51).
    Pilots are at SCs -21,-7,+7,+21 = bins 43, 57, 7, 21 (in 0-indexed FFT bins) which is array position 5, 19, 6, 20.
    Wait, SC -21 = FFT bin 43 (since bin 43 = 64-21). SC -7 = bin 57. SC +7 = bin 7. SC +21 = bin 21.

    Bin positions in ACTIVE_SC_INDICES list (1-26 + 38-63):
      bin 7 → position 6 (in the 1-26 range, 7-1 = 6)
      bin 21 → position 20 (21-1 = 20)
      bin 43 → position 26+5 = 31 (38..43 is positions 26..31)
      bin 57 → position 26+19 = 45 (38..57 is positions 26..45)

    So pilot positions in bin-ordered eq array: [6, 20, 31, 45].
    """
    PILOT_IDX = [6, 20, 31, 45]  # bin-ordered positions of 4 pilots
    DATA_IDX = [i for i in range(52) if i not in PILOT_IDX]

    cpe = np.angle(np.sum(eq_bin_order[DATA_IDX]))
    eq_rot = eq_bin_order * np.exp(-1j * cpe)

    bits48 = (eq_rot[DATA_IDX].real > 0).astype(np.int32).tolist()
    snr = snr_db(eq_rot[DATA_IDX])
    decoded, metric = viterbi_decode_hard(bits48, n_steps=24)
    rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
    parity_ok = (decoded[16] == 0)
    return {'bits': bits48, 'decoded': decoded, 'rate': rate,
            'metric': metric, 'snr': snr, 'parity_ok': parity_ok}


def main():
    print("[T3.5] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[T3.5] Total samples: {len(iq)}")

    print("[T3.5] Finding L-STF starts...")
    starts = find_all_l_stf_starts(iq)
    print(f"[T3.5] Found {len(starts)} L-STF starts")

    # Pass A: With LTF reference division (C++ style)
    print(f"\n[T3.5] === Pass A: WITH LTF reference division ===")
    deltas_A = []
    snrs_A = []
    rates_A = []
    metrics_A = []
    eq_no_delta_A = []

    for fs in starts:
        H52 = estimate_h52_with_ref(iq, fs)
        if H52 is None:
            continue
        delta = estimate_delta_weighted(H52)
        deltas_A.append(delta)

        eq0 = extract_lsig_eq(iq, fs, H52, delta=None)
        if eq0 is None:
            continue
        r0 = decode_lsig_full(eq0)
        eq1 = extract_lsig_eq(iq, fs, H52, delta=delta)
        r1 = decode_lsig_full(eq1)

        snrs_A.append(r1['snr'])
        rates_A.append(r1['rate'])
        metrics_A.append(r1['metric'])
        eq_no_delta_A.append(eq0)

    deltas_A = np.array(deltas_A)
    snrs_A = np.array(snrs_A)
    rates_A = np.array(rates_A)
    metrics_A = np.array(metrics_A)

    print(f"\n[T3.5] === SNR WITH δ (LTF ref divided) ===")
    print(f"  Mean:   {snrs_A.mean():.2f} dB  (Phase 81: 7.11 dB)")
    print(f"  Median: {np.median(snrs_A):.2f} dB")
    print(f"  Std:    {snrs_A.std():.2f} dB")
    print(f"  Min:    {snrs_A.min():.2f} dB")
    print(f"  Max:    {snrs_A.max():.2f} dB")

    rate_counts = {}
    for r in rates_A:
        rate_counts[r] = rate_counts.get(r, 0) + 1
    print(f"\n[T3.5] === Rate distribution WITH δ (LTF ref divided) ===")
    print(f"  Expected: 0xD (13)")
    print(f"  Phase 81 finding: 0x9 (9)")
    for rate in sorted(rate_counts.keys()):
        cnt = rate_counts[rate]
        pct = 100.0 * cnt / len(rates_A)
        marker = ""
        if rate == 13: marker = " <<< EXPECTED"
        elif rate == 9: marker = " <<< Phase 81 finding"
        print(f"  rate=0x{rate:X} ({rate:3d}): {cnt:4d} frames ({pct:5.1f}%){marker}")

    # Pass B: WITHOUT LTF reference division (T2/T3 style) for comparison
    print(f"\n[T3.5] === Pass B: WITHOUT LTF reference division (T2/T3 baseline) ===")
    deltas_B = []
    snrs_B = []
    rates_B = []

    for fs in starts:
        H52 = estimate_h52_no_ref(iq, fs)
        if H52 is None:
            continue
        delta = estimate_delta_weighted(H52)
        deltas_B.append(delta)

        eq0 = extract_lsig_eq(iq, fs, H52, delta=None)
        if eq0 is None:
            continue
        eq1 = extract_lsig_eq(iq, fs, H52, delta=delta)
        r1 = decode_lsig_full(eq1)

        snrs_B.append(r1['snr'])
        rates_B.append(r1['rate'])

    snrs_B = np.array(snrs_B)
    rates_B = np.array(rates_B)
    print(f"  SNR WITH δ (no ref div): {snrs_B.mean():.2f} dB mean")

    rate_counts_B = {}
    for r in rates_B:
        rate_counts_B[r] = rate_counts_B.get(r, 0) + 1
    for rate in sorted(rate_counts_B.keys()):
        cnt = rate_counts_B[rate]
        pct = 100.0 * cnt / len(rates_B)
        print(f"  rate=0x{rate:X}: {cnt} ({pct:.1f}%)")

    # Compare δ distributions
    print(f"\n[T3.5] === δ distribution comparison ===")
    print(f"  A (with ref div): mean={deltas_A.mean():.4f} std={deltas_A.std():.4f}")
    print(f"  B (no ref div):   mean={deltas_B.mean():.4f} std={deltas_B.std():.4f}")
    print(f"  Mean shift:       {(deltas_A.mean() - deltas_B.mean()):.4f}")

    # Pass C: ε scan on Pass A results (with LTF ref)
    print(f"\n[T3.5] === Pass C: ε scan [-16, +16]/64 with LTF ref ===")
    eps_grid = np.arange(-16, 17) / 64.0
    n_D_per_eps = []
    snr_per_eps = []

    for eps in eps_grid:
        n_D = 0
        snrs = []
        for i, eq0 in enumerate(eq_no_delta_A):
            delta_total = deltas_A[i] + eps
            delta_total = delta_total - np.floor(delta_total)
            eq_with_eps = apply_delta_to_eq_bin_order(eq0, delta_total)
            r = decode_lsig_full(eq_with_eps)
            if r['rate'] == 13:
                n_D += 1
            snrs.append(r['snr'])
        n_D_per_eps.append(n_D)
        snr_per_eps.append(np.mean(snrs))

    n_D_per_eps = np.array(n_D_per_eps)
    snr_per_eps = np.array(snr_per_eps)

    # Best ε
    best_idx = np.argmax(n_D_per_eps)
    print(f"  Best ε: {eps_grid[best_idx]*64:+.1f}/64 → {n_D_per_eps[best_idx]} frames 0xD")
    print(f"  SNR at best ε: {snr_per_eps[best_idx]:.2f} dB")

    # SNR-best ε
    best_snr_idx = np.argmax(snr_per_eps)
    print(f"  Best ε by SNR: {eps_grid[best_snr_idx]*64:+.1f}/64 → {snr_per_eps[best_snr_idx]:.2f} dB "
          f"({n_D_per_eps[best_snr_idx]} frames 0xD)")

    if n_D_per_eps.max() > 0:
        top5 = np.argsort(n_D_per_eps)[-5:][::-1]
        print(f"  Top 5 ε by 0xD count:")
        for idx in top5:
            print(f"    ε={eps_grid[idx]*64:+.1f}/64: {n_D_per_eps[idx]} 0xD, "
                  f"SNR={snr_per_eps[idx]:.2f}dB")

    # Save
    np.savez('/tmp/p82_t3_5.npz',
             deltas_A=deltas_A, snrs_A=snrs_A, rates_A=rates_A,
             deltas_B=deltas_B, snrs_B=snrs_B, rates_B=rates_B)


if __name__ == '__main__':
    main()