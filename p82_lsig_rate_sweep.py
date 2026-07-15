#!/usr/bin/env python3
"""
Phase 82 T2 — Multi-frame L-SIG rate decode distribution on 5250 cable capture.

Goal: Determine if L-SIG viterbi decoder consistently returns rate=0xD (expected)
or rate=0x9 (Phase 81 verdict finding) across many frames in a clean raw IQ
capture at 5250 MHz direct SMA cable.

Method:
1. Load /tmp/p28_loopback_iq.fc32 as memmap
2. Find all L-STF starts via 16-period autocorrelation (chunked, ~30 chunks)
3. For each frame: extract L-LTF0/L-LTF1, compute H_hdr52, equalize L-SIG
4. Hard-decode 48 bits, run viterbi to recover 24 info bits
5. Extract rate field (bits 0-3 of decoded info)
6. Print rate distribution + per-frame SNR stats

Output: stdout summary + per-frame detail (first 30 frames).
"""
import numpy as np
import sys

# ===== Constants =====
CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))  # 52 subcarriers
PILOT_SC = {11, 25, 39, 53}  # pilot positions in 0..63 indexing
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]  # 48 data SCs

# BCC rate 1/2 polynomials (k=7), per IEEE 802.11-2007 17.3.5.2
POLY1 = 0b1011011  # G1 = 133 octal
POLY2 = 0b1111001  # G2 = 171 octal


# ===== L-STF detection (chunked) =====
def find_all_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
    """
    Find all L-STF start positions via 16-period autocorrelation.
    Each chunk processes chunk_size samples + 16 (for cross-period).
    min_distance = min samples between consecutive detections (default 2M = 100ms at 20MHz).
    """
    n = len(iq)
    period = 16
    win = 16
    starts = []
    last_peak_pos = -min_distance  # allow first peak anywhere

    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)

        a = chunk[:-period]
        b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))

        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')

        # Adaptive threshold: 10x median or 0.01, whichever is larger
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


# ===== Per-frame H estimation + L-SIG equalization =====
def analyze_frame(iq, fs):
    """For frame at L-STF start fs, return dict with H, eq, bits, snr, etc."""
    lts0_start = fs + 176
    lts1_start = fs + 256
    sig_start = fs + 336

    if sig_start + 64 > len(iq):
        return None

    LTS0 = iq[lts0_start:lts0_start + 64]
    LTS1 = iq[lts1_start:lts1_start + 64]
    SIG = iq[sig_start:sig_start + 64]

    F0 = np.fft.fft(LTS0, 64)
    F1 = np.fft.fft(LTS1, 64)
    Fsig = np.fft.fft(SIG, 64)

    F0a = F0[ACTIVE_SC]
    F1a = F1[ACTIVE_SC]

    # H estimate: average LTS0 and LTS1
    H = (F0a + F1a) / 2.0

    # Equalize L-SIG: eq[i] = Fsig[sc_i] / H[i]
    eq = Fsig[ACTIVE_SC] / H

    # Global CPE: rotate by -arg(sum(eq))
    cpe = np.angle(np.sum(eq))
    eq_rot = eq * np.exp(-1j * cpe)

    # Hard decode 48 bits
    bits = (eq_rot.real > 0).astype(np.int32)

    # Per-frame SNR
    real_mean = float(np.mean(np.abs(eq_rot.real)))
    imag_std = float(np.std(eq_rot.imag) + 1e-12)
    snr = 20.0 * np.log10(real_mean / imag_std)

    return {
        'fs': fs,
        'H_mag_mean': float(np.mean(np.abs(H))),
        'H_mag_std': float(np.std(np.abs(H))),
        'H_phase_std': float(np.std(np.angle(H))),
        'eq_snr': snr,
        'bits': bits.tolist(),
        'eq_rot_real_mean': real_mean,
        'eq_rot_imag_std': imag_std,
    }


# ===== Viterbi decoder (hard, copy from p28_3f_viterbi.py) =====
def encode_bit(input_bit, state):
    """BCC rate 1/2 encode one bit. state is tuple of 6 bits (s5..s0)."""
    new_state = (state[1], state[2], state[3], state[4], state[5], input_bit)
    o1 = (input_bit ^ state[5] ^ state[3] ^ state[2] ^ state[1] ^ state[0]) & 1
    o2 = (input_bit ^ state[5] ^ state[4] ^ state[3] ^ state[0]) & 1
    return o1, o2, new_state


def viterbi_decode_hard(rx_bits, n_steps=24):
    """Hard-decision viterbi for rate 1/2, k=7. Returns (decoded[24], best_metric)."""
    INF = float('inf')
    n_states = 64

    pm = np.full(n_states, INF)
    pm[0] = 0.0

    prev_state = np.zeros((n_steps, n_states), dtype=np.int32)
    prev_bit = np.zeros((n_steps, n_states), dtype=np.int32)

    for t in range(n_steps):
        new_pm = np.full(n_states, INF)
        r0 = rx_bits[2 * t]
        r1 = rx_bits[2 * t + 1]
        for s in range(n_states):
            if pm[s] == INF:
                continue
            state = tuple((s >> (5 - i)) & 1 for i in range(6))
            for bit in [0, 1]:
                o0, o1, new_state = encode_bit(bit, state)
                metric = (o0 != r0) + (o1 != r1)
                new_metric = pm[s] + metric
                new_s = (new_state[0] << 5) | (new_state[1] << 4) | (new_state[2] << 3) | \
                        (new_state[3] << 2) | (new_state[4] << 1) | new_state[5]
                if new_metric < new_pm[new_s]:
                    new_pm[new_s] = new_metric
                    prev_state[t][new_s] = s
                    prev_bit[t][new_s] = bit
        pm = new_pm

    best_state = int(np.argmin(pm))
    best_metric = pm[best_state]
    decoded = np.zeros(n_steps, dtype=np.int32)
    s = best_state
    for t in range(n_steps - 1, -1, -1):
        decoded[t] = prev_bit[t][s]
        s = prev_state[t][s]
    return decoded.tolist(), float(best_metric)


# ===== Main =====
def main():
    print(f"[P82] Loading {CAPTURE_FILE} as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P82] Total samples: {len(iq)}")

    print("[P82] Finding all L-STF starts (chunked)...")
    starts = find_all_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000)
    print(f"[P82] Found {len(starts)} L-STF starts")

    if not starts:
        print("[P82] No L-STFs found. Aborting.")
        return

    # Print L-STF positions to verify periodicity (should be ~4M apart = 200ms)
    if len(starts) > 1:
        diffs = np.diff(starts)
        print(f"[P82] Inter-frame intervals (samples):")
        print(f"  Median: {np.median(diffs):.0f}")
        print(f"  Std:    {np.std(diffs):.0f}")
        print(f"  Min:    {np.min(diffs):.0f}")
        print(f"  Max:    {np.max(diffs):.0f}")

    # Analyze each frame
    print(f"\n[P82] Analyzing L-SIG per frame...")
    results = []
    for idx, fs in enumerate(starts):
        try:
            r = analyze_frame(iq, fs)
            if r is None:
                continue
            # Viterbi decode
            decoded, metric = viterbi_decode_hard(r['bits'], n_steps=24)
            rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
            length = 0
            for k in range(12):
                length |= (decoded[4 + k] << (11 - k))
            parity = decoded[16]
            r['decoded'] = decoded
            r['metric'] = metric
            r['rate'] = rate
            r['length'] = length
            r['parity'] = parity
            r['parity_ok'] = (parity == 0)  # SIGNAL field parity should be even
            results.append(r)
        except Exception as e:
            print(f"[P82] Frame {idx} at fs={fs}: error {type(e).__name__}: {e}")
            continue

    print(f"[P82] Successfully analyzed {len(results)} frames")

    # ===== Summary =====
    snrs = np.array([r['eq_snr'] for r in results])
    print(f"\n[P82] ===== L-SIG EQUALIZED SNR =====")
    print(f"  Mean:   {snrs.mean():.2f} dB")
    print(f"  Std:    {snrs.std():.2f} dB")
    print(f"  Min:    {snrs.min():.2f} dB")
    print(f"  Max:    {snrs.max():.2f} dB")
    print(f"  Median: {np.median(snrs):.2f} dB")

    h_mag = np.array([r['H_mag_mean'] for r in results])
    h_phase = np.array([r['H_phase_std'] for r in results])
    print(f"\n[P82] ===== H_hdr52 (52 active SCs) =====")
    print(f"  |H| mean: {h_mag.mean():.3f} (std {h_mag.std():.3f})")
    print(f"  arg(H) std: {h_phase.mean():.3f} rad ({np.degrees(h_phase.mean()):.1f}°)")

    # Rate distribution
    rates = [r['rate'] for r in results]
    rate_counts = {}
    for rate in rates:
        rate_counts[rate] = rate_counts.get(rate, 0) + 1
    print(f"\n[P82] ===== L-SIG RATE FIELD DISTRIBUTION =====")
    print(f"  Expected (TX sends): 0xD = 13")
    print(f"  Phase 81 finding:    0x9 = 9")
    for rate in sorted(rate_counts.keys()):
        cnt = rate_counts[rate]
        pct = 100.0 * cnt / len(rates)
        marker = " <<<" if rate == 13 else (" <<< (Phase 81 finding)" if rate == 9 else "")
        print(f"  rate=0x{rate:X} ({rate:3d}): {cnt:4d} frames ({pct:5.1f}%){marker}")

    # Viterbi metric distribution
    metrics = np.array([r['metric'] for r in results])
    print(f"\n[P82] ===== VITERBI METRIC (lower=cleaner) =====")
    print(f"  Mean:   {metrics.mean():.2f}")
    print(f"  Median: {np.median(metrics):.2f}")
    print(f"  Min:    {metrics.min():.2f}")
    print(f"  Max:    {metrics.max():.2f}")

    # Parity check
    parity_ok = sum(1 for r in results if r['parity_ok'])
    print(f"\n[P82] ===== L-SIG PARITY CHECK =====")
    print(f"  PASS: {parity_ok}/{len(results)} ({100.0*parity_ok/len(results):.1f}%)")

    # Per-frame detail (first 20)
    print(f"\n[P82] ===== FIRST 20 FRAMES DETAIL =====")
    for idx, r in enumerate(results[:20]):
        rate_hex = f"0x{r['rate']:X}"
        marker = " <<< EXPECTED" if r['rate'] == 13 else (" <<< Phase 81 mismatch" if r['rate'] == 9 else "")
        print(f"  Frame {idx:3d}: rate={rate_hex} snr={r['eq_snr']:5.1f}dB "
              f"metric={r['metric']:5.1f} parity={'OK' if r['parity_ok'] else 'BAD'} "
              f"|H|_std={r['H_mag_std']:.3f} H_phase_std={r['H_phase_std']:.2f}rad{marker}")

    # Save per-frame data for T3 analysis
    out_file = '/tmp/p82_t2_per_frame.npz'
    np.savez(out_file,
             fs=[r['fs'] for r in results],
             eq_snr=snrs,
             rate=[r['rate'] for r in results],
             metric=metrics,
             parity_ok=[r['parity_ok'] for r in results],
             H_mag_mean=h_mag,
             H_phase_std=h_phase)
    print(f"\n[P82] Per-frame data saved to {out_file}")


if __name__ == '__main__':
    main()