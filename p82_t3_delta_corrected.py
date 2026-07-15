#!/usr/bin/env python3
"""
Phase 82 T3 — Apply Phase 34 δ correction offline; verify production behavior.

Goal:
1. Reproduce Phase 81 verdict (avg_snr_lsig ~7 dB, rate=0x9) on my 5250 cable
   capture by applying Phase 34 δ correction offline (no C++ involved).
2. If reproducible: characterize WHY δ produces 0x9 not 0xD — is it a δ-grid
   quantization error? A deterministic per-frame δ bias? Or a structural
   bit-flip from Phase 34's correction itself?
3. Try targeted δ adjustments (delta ± 1/64, ± 2/64, etc.) to see if a small
   shift recovers 0xD — this is the Phase 82 attack lever.

Method:
- For each frame: extract LTS0/LTS1/SIG, compute H52 = (F0+F1)/2 (52 SCs)
- Estimate δ via Phase 34 weighted linear regression of argH vs SC index
- Apply δ correction: rotate each SC by exp(+j*2π*k*δ/64)
- Hard-decode 48 bits, run viterbi, extract rate field
- ALSO: try ±δ_grid corrections to map rate→0xD
"""
import numpy as np
import sys

# Constants
CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

# 52 active SCs in TX order (must match frame_equalizer_impl.cc)
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
    1,2,3,4,5,6,
    8,9,10,11,12,13,
    14,15,16,17,18,19,
    20,22,23,24,25,26,
    -21,-7,7,21
], dtype=np.float64)
assert len(SC_INDEX_52) == 52

# Indexing in 64-point FFT
ACTIVE_SC_INDICES = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,
                     38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63]

# BCC rate 1/2 polynomials (k=7)
POLY1 = 0b1011011
POLY2 = 0b1111001


def find_all_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
    """Same as p82_lsig_rate_sweep.py"""
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


def estimate_h52(iq, fs):
    """Returns H52 in TX order matching SC_INDEX_52"""
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
    H = (F0a + F1a) / 2.0
    return H


def estimate_delta_weighted(H52):
    """Phase 34 weighted linear regression of argH vs SC index.

    Returns delta in [0, 1) sample units.
    """
    argH = np.angle(H52).astype(np.float64)
    w = np.abs(H52).astype(np.float64)
    sum_w = w.sum()
    if sum_w < 1e-9:
        return 0.0
    mean_sc = (SC_INDEX_52 * w).sum() / sum_w
    mean_arg = (argH * w).sum() / sum_w
    cov = (w * (SC_INDEX_52 - mean_sc) * (argH - mean_arg)).sum()
    var = (w * (SC_INDEX_52 - mean_sc) ** 2).sum()
    if var < 1e-9:
        return 0.0
    b = cov / var  # slope
    delta = -b * 64.0 / (2.0 * np.pi)
    delta = delta - np.floor(delta)
    return float(delta)


def apply_delta_to_eq(eq_in_tx_order, delta):
    """Rotate each SC by exp(+j*2π*k*δ/64), k from SC_INDEX_52."""
    rot = np.exp(1j * 2.0 * np.pi * SC_INDEX_52 * delta / 64.0)
    return eq_in_tx_order * rot


def extract_lsig_eq(iq, fs, H52, delta=None):
    """Equalize L-SIG, optionally apply δ correction. Returns eq (52 SCs, TX order)."""
    sig_start = fs + 336
    if sig_start + 64 > len(iq):
        return None
    SIG = iq[sig_start:sig_start + 64]
    Fsig = np.fft.fft(SIG, 64)
    eq = Fsig[ACTIVE_SC_INDICES] / H52  # 52 SCs in TX order
    if delta is not None:
        eq = apply_delta_to_eq(eq, delta)
    return eq


def snr_db(eq):
    """SNR = 20 log10(mean(|real|) / std(imag))"""
    return 20.0 * np.log10(np.mean(np.abs(eq.real)) / (np.std(eq.imag) + 1e-12))


# ===== Viterbi (hard, k=7, rate 1/2) =====
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


def decode_lsig_full(eq52):
    """eq52 = 52 SCs in TX order (with δ applied if requested).
    Returns dict: bits (48), decoded (24), rate, metric, snr.
    """
    # Global CPE
    cpe = np.angle(np.sum(eq52))
    eq_rot = eq52 * np.exp(-1j * cpe)

    # Hard decode 48 bits (48 DATA SCs, exclude 4 pilots)
    # Pilots at indices 48, 49, 50, 51 in TX order
    DATA_IDX = [i for i in range(52) if i not in {48, 49, 50, 51}]
    bits48 = (eq_rot[DATA_IDX].real > 0).astype(np.int32).tolist()

    snr = snr_db(eq_rot[DATA_IDX])

    # Viterbi decode 48 encoded bits -> 24 info bits
    decoded, metric = viterbi_decode_hard(bits48, n_steps=24)
    rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
    parity_ok = (decoded[16] == 0)
    return {'bits': bits48, 'decoded': decoded, 'rate': rate,
            'metric': metric, 'snr': snr, 'parity_ok': parity_ok}


def main():
    print("[P82-T3] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P82-T3] Total samples: {len(iq)}")

    print("[P82-T3] Finding L-STF starts...")
    starts = find_all_l_stf_starts(iq)
    print(f"[P82-T3] Found {len(starts)} L-STF starts")

    if not starts:
        return

    # ===== Pass 1: compute δ per frame, decode L-SIG with δ correction =====
    print("\n[P82-T3] Pass 1: per-frame δ + δ-corrected decode")
    deltas = []
    snrs_with_delta = []
    rates_with_delta = []
    metrics_with_delta = []
    eq_no_delta = []      # cache eq without δ for Pass 2
    eq_with_delta = []    # cache eq WITH δ for Pass 2

    for fs in starts:
        H52 = estimate_h52(iq, fs)
        if H52 is None:
            continue
        delta = estimate_delta_weighted(H52)
        deltas.append(delta)

        # Without δ
        eq0 = extract_lsig_eq(iq, fs, H52, delta=None)
        if eq0 is None:
            continue
        r0 = decode_lsig_full(eq0)

        # With δ
        eq1 = extract_lsig_eq(iq, fs, H52, delta=delta)
        r1 = decode_lsig_full(eq1)

        snrs_with_delta.append(r1['snr'])
        rates_with_delta.append(r1['rate'])
        metrics_with_delta.append(r1['metric'])
        eq_no_delta.append(eq0)
        eq_with_delta.append(eq1)

    deltas = np.array(deltas)
    snrs_with_delta = np.array(snrs_with_delta)
    rates_with_delta = np.array(rates_with_delta)
    metrics_with_delta = np.array(metrics_with_delta)

    print(f"\n[P82-T3] ===== δ distribution (N={len(deltas)} frames) =====")
    print(f"  mean:   {deltas.mean():.4f}")
    print(f"  std:    {deltas.std():.4f}")
    print(f"  min:    {deltas.min():.4f}")
    print(f"  max:    {deltas.max():.4f}")
    # Quantize to 1/64 grid
    k_quant = np.round(deltas * 64.0).astype(int) % 64
    delta_quant = k_quant / 64.0
    quant_err = np.minimum(np.abs(deltas - delta_quant), 1.0 - np.abs(deltas - delta_quant))
    print(f"  1/64 RMS err: {np.sqrt(np.mean(quant_err**2)):.5f}")
    print(f"  within 0.005 of grid: {(quant_err < 0.005).mean()*100:.1f}%")
    print(f"\n[P82-T3] ===== δ histogram (k/64) =====")
    hist, _ = np.histogram(deltas, bins=64, range=(0, 1))
    for i in range(64):
        if hist[i] > 0:
            bar = "#" * min(40, hist[i] * 40 // max(1, hist.max()))
            print(f"  k={i:2d} δ∈[{i/64:.4f},{(i+1)/64:.4f}): {hist[i]:4d} {bar}")

    print(f"\n[P82-T3] ===== L-SIG SNR WITH δ correction =====")
    print(f"  Mean:   {snrs_with_delta.mean():.2f} dB")
    print(f"  Median: {np.median(snrs_with_delta):.2f} dB")
    print(f"  Min:    {snrs_with_delta.min():.2f} dB")
    print(f"  Max:    {snrs_with_delta.max():.2f} dB")

    # Rate distribution with δ
    rate_counts = {}
    for r in rates_with_delta:
        rate_counts[r] = rate_counts.get(r, 0) + 1
    print(f"\n[P82-T3] ===== Rate distribution WITH δ (Phase 34 equivalent) =====")
    print(f"  Expected: 0xD (13)")
    print(f"  Phase 81 finding: 0x9 (9)")
    for rate in sorted(rate_counts.keys()):
        cnt = rate_counts[rate]
        pct = 100.0 * cnt / len(rates_with_delta)
        marker = ""
        if rate == 13: marker = " <<< EXPECTED"
        elif rate == 9: marker = " <<< Phase 81 finding"
        print(f"  rate=0x{rate:X} ({rate:3d}): {cnt:4d} frames ({pct:5.1f}%){marker}")

    print(f"\n[P82-T3] ===== Viterbi metric WITH δ =====")
    print(f"  Mean:   {metrics_with_delta.mean():.2f}")
    print(f"  Median: {np.median(metrics_with_delta):.2f}")
    print(f"  Min:    {metrics_with_delta.min():.2f}")
    print(f"  Max:    {metrics_with_delta.max():.2f}")

    # Save intermediate state
    np.savez('/tmp/p82_t3_pass1.npz',
             deltas=deltas, snrs=snrs_with_delta,
             rates=np.array(rates_with_delta), metrics=metrics_with_delta)
    print(f"\n[P82-T3] Saved Pass 1 results to /tmp/p82_t3_pass1.npz")

    # ===== Pass 2: try δ + ε for ε ∈ {-8/64, -7/64, ..., 0, ..., +7/64} =====
    # Goal: see if a small grid shift recovers rate=0xD
    print(f"\n[P82-T3] Pass 2: scan ε ∈ [-8, +8] × 1/64 around δ")
    print(f"  For each frame, try delta_base + ε * 1/64")
    print(f"  Track: rate distribution + SNR for each ε")

    eps_grid = np.arange(-8, 9) / 64.0
    rate_matrix = np.zeros((len(eq_no_delta), len(eps_grid)), dtype=np.int32)
    snr_matrix = np.zeros((len(eq_no_delta), len(eps_grid)))
    metric_matrix = np.zeros((len(eq_no_delta), len(eps_grid)))

    for i, eq0 in enumerate(eq_no_delta):
        for j, eps in enumerate(eps_grid):
            # Re-apply: first δ, then ε (or just ε if starting from no-δ eq)
            delta_total = deltas[i] + eps
            delta_total = delta_total - np.floor(delta_total)
            eq_with_eps = apply_delta_to_eq(eq0, delta_total)
            r = decode_lsig_full(eq_with_eps)
            rate_matrix[i, j] = r['rate']
            snr_matrix[i, j] = r['snr']
            metric_matrix[i, j] = r['metric']

    # Summary: for each ε, what's the rate distribution + SNR?
    print(f"\n[P82-T3] ===== Rate distribution by ε =====")
    print(f"  {'ε/64':>5s} {'rate=0xD':>10s} {'rate=0x9':>10s} {'rate=other':>12s} {'SNR mean':>10s} {'metric':>8s}")
    for j, eps in enumerate(eps_grid):
        rates_eps = rate_matrix[:, j]
        n_D = (rates_eps == 13).sum()
        n_9 = (rates_eps == 9).sum()
        n_other = len(rates_eps) - n_D - n_9
        snr_mean = snr_matrix[:, j].mean()
        metric_mean = metric_matrix[:, j].mean()
        marker = " <<<" if n_D > 0 else ""
        print(f"  {eps*64:+5.1f}  {n_D:>10d} {n_9:>10d} {n_other:>12d} {snr_mean:>10.2f} {metric_mean:>8.2f}{marker}")

    # Best ε by SNR
    best_eps_idx = np.argmax(snr_matrix.mean(axis=0))
    print(f"\n[P82-T3] Best ε by mean SNR: ε = {eps_grid[best_eps_idx]*64:+.1f}/64 "
          f"(SNR={snr_matrix[:, best_eps_idx].mean():.2f} dB)")

    # Best ε by rate=0xD count
    n_D_per_eps = (rate_matrix == 13).sum(axis=0)
    if n_D_per_eps.max() > 0:
        best_eps_for_D = np.argmax(n_D_per_eps)
        print(f"[P82-T3] Best ε for rate=0xD: ε = {eps_grid[best_eps_for_D]*64:+.1f}/64 "
              f"({n_D_per_eps[best_eps_for_D]} frames = 0xD)")
    else:
        print(f"[P82-T3] NO ε in [-8, +8]/64 produces rate=0xD in any frame")
        print(f"[P82-T3] Max rate=0xD count across ε grid: {n_D_per_eps.max()}")

    # Wider search?
    print(f"\n[P82-T3] Pass 3: wider ε scan ±32/64 (half-symbol offset)")
    eps_wide = np.arange(-32, 33, 1) / 64.0
    rate_wide = np.zeros((len(eq_no_delta), len(eps_wide)), dtype=np.int32)
    snr_wide = np.zeros((len(eq_no_delta), len(eps_wide)))
    for i, eq0 in enumerate(eq_no_delta):
        for j, eps in enumerate(eps_wide):
            delta_total = deltas[i] + eps
            delta_total = delta_total - np.floor(delta_total)
            eq_with_eps = apply_delta_to_eq(eq0, delta_total)
            r = decode_lsig_full(eq_with_eps)
            rate_wide[i, j] = r['rate']
            snr_wide[i, j] = r['snr']

    # Find global best ε for 0xD count
    n_D_wide = (rate_wide == 13).sum(axis=0)
    if n_D_wide.max() > 0:
        best_idx = np.argmax(n_D_wide)
        print(f"  Best ε: {eps_wide[best_idx]*64:+.1f}/64 → {n_D_wide[best_idx]} frames at 0xD")
        print(f"  SNR at that ε: {snr_wide[:, best_idx].mean():.2f} dB")
        # Show top 5
        top5 = np.argsort(n_D_wide)[-5:][::-1]
        print(f"  Top 5 ε by 0xD count:")
        for idx in top5:
            print(f"    ε={eps_wide[idx]*64:+.1f}/64: {n_D_wide[idx]} frames 0xD, "
                  f"{n_D_wide[idx]/len(rate_wide)*100:.1f}%, SNR={snr_wide[:, idx].mean():.2f}dB")
    else:
        print(f"  NO ε in [-32, +32]/64 produces 0xD in any frame. "
              f"Max count: {n_D_wide.max()}")
    # Save full sweep
    np.savez('/tmp/p82_t3_pass2.npz',
             eps_grid=eps_wide, rate_matrix=rate_wide,
             snr_matrix=snr_wide, deltas=deltas)


if __name__ == '__main__':
    main()