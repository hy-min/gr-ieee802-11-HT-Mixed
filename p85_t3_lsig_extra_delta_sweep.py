#!/usr/bin/env python3
"""Phase 85 T3: empirical sweep of L-SIG extra δ to test SFO hypothesis.

C++ applies δ to L-SIG based on HT-SIG1 measurement. If per-symbol SFO is
non-zero, the L-SIG correction is off by ε × 2 (L-SIG is 2 OFDM symbols
before HT-SIG1).

Method: replay the existing USRP capture, but instead of using C++'s
δ (already applied), apply an ADDITIONAL rotation of exp(j·2π·SC·ε_extra/64)
to the L-SIG eq output and see if rate=0xD count increases.

Approach:
  1. Run p68_replay_offline.py with IEEE80211_TIMING_OFFSET_APPLY=0 to get
     L-SIG eq output WITHOUT δ correction.
  2. From the dump, extract L-SIG eq per frame.
  3. Sweep over ε_extra in [0, 1/64, 2/64, ..., 63/64] and check rate=0xD
     distribution.

Note: this requires C++ env var IEEE80211_LSIG_DUMP_EQ=1 (hypothetical) or
similar to expose the L-SIG eq data. If not available, fall back to offline
Python replay.
"""
import numpy as np
import sys

CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

ACTIVE_SC_INDICES = list(range(1, 27)) + list(range(38, 64))
assert len(ACTIVE_SC_INDICES) == 52

KLTF64 = np.array([
    0,    1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
    -1, -1, 1, 1, -1, 1, 1, -1, 1, -1, 1, 1,
    0, 0, 0, 0, 0,
    0,
    0, 0, 0, 0, 0,
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1,
    1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
], dtype=np.complex64)
LTF_ACTIVE = KLTF64[ACTIVE_SC_INDICES]
SC_INDEX_BIN = np.array(list(range(1, 27)) + list(range(-26, 0)), dtype=np.float64)


def estimate_h52_with_ref(iq, fs, with_ref=True):
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
    if with_ref:
        H = np.zeros_like(avg)
        valid = np.abs(LTF_ACTIVE) > 1e-6
        H[valid] = avg[valid] / LTF_ACTIVE[valid]
        return H
    return avg


def estimate_delta_weighted(H52):
    argH = np.angle(H52).astype(np.float64)
    w = np.abs(H52).astype(np.float64)
    sum_w = w.sum()
    if sum_w < 1e-9:
        return 0.0
    mean_sc = (SC_INDEX_BIN * w).sum() / sum_w
    mean_arg = (argH * w).sum() / sum_w
    cov = (w * (SC_INDEX_BIN - mean_sc) * (argH - mean_arg)).sum()
    var = (w * (SC_INDEX_BIN - mean_sc) ** 2).sum()
    if var < 1e-9:
        return 0.0
    b = cov / var
    delta = -b * 64.0 / (2.0 * np.pi)
    delta = delta - np.floor(delta)
    return float(delta)


def apply_delta_bin_order(eq, delta):
    rot = np.exp(1j * 2.0 * np.pi * SC_INDEX_BIN * delta / 64.0).astype(np.complex64)
    return (eq * rot).astype(np.complex64)


def extract_lsig_eq(iq, fs, H52, delta_apply=None):
    """Extract L-SIG EQ, optionally applying δ rotation."""
    sig_start = fs + 336
    if sig_start + 64 > len(iq):
        return None
    SIG = iq[sig_start:sig_start + 64]
    Fsig = np.fft.fft(SIG, 64)
    eq = Fsig[ACTIVE_SC_INDICES] / H52
    if delta_apply is not None:
        eq = apply_delta_bin_order(eq, delta_apply)
    return eq.astype(np.complex64)


def find_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
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


def bcc_encode_bit(input_bit, state):
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
                o0, o1, new_state = bcc_encode_bit(bit, state)
                metric = (o0 != r0) + (o1 != r1)
                new_metric = pm[s] + metric
                new_s = (new_state[0]<<5)|(new_state[1]<<4)|(new_state[2]<<3)|(new_state[3]<<2)|(new_state[4]<<1)|new_state[5]
                if new_metric < new_pm[new_s]:
                    new_pm[new_s] = new_metric
                    prev_state[t][new_s] = s
                    prev_bit[t][new_s] = bit
        pm = new_pm
    best_state = int(np.argmin(pm))
    decoded = np.zeros(n_steps, dtype=np.int32)
    s = best_state
    for t in range(n_steps-1, -1, -1):
        decoded[t] = prev_bit[t][s]
        s = prev_state[t][s]
    return decoded.tolist()


def decode_lsig_with_delta(eq, pilot_cpe=True):
    """Use L-SIG pilots {-21,-7,7,21} for CPE, then BPSK demod on data SCs."""
    PILOT_IDX = [6, 20, 31, 45]  # bin-ordered positions of 4 pilots
    DATA_IDX = [i for i in range(52) if i not in PILOT_IDX]
    if pilot_cpe:
        # Pilots have real = ±1 (L-SIG), so use real axis
        cpe = np.angle(np.sum(eq[PILOT_IDX].real + 0j))
    else:
        cpe = np.angle(np.sum(eq[DATA_IDX]))
    eq_rot = eq * np.exp(-1j * cpe)
    bits48 = (eq_rot[DATA_IDX].real > 0).astype(np.int32).tolist()
    decoded = viterbi_decode_hard(bits48, n_steps=24)
    rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
    return rate, decoded


def main():
    print("[P85-T3] Loading capture...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P85-T3] Total samples: {len(iq)}")

    print("[P85-T3] Finding L-STF starts...")
    starts = find_l_stf_starts(iq)
    print(f"[P85-T3] Found {len(starts)} L-STF starts")

    # First, compute per-frame δ and extract L-SIG eq WITHOUT δ applied
    print("\n[P85-T3] === Computing per-frame δ and L-SIG eq (no correction) ===")
    frames_data = []
    for fs in starts:
        H52 = estimate_h52_with_ref(iq, fs, with_ref=True)
        if H52 is None:
            continue
        delta = estimate_delta_weighted(H52)
        eq_no_delta = extract_lsig_eq(iq, fs, H52, delta_apply=None)
        if eq_no_delta is None:
            continue
        rate_no_delta, _ = decode_lsig_with_delta(eq_no_delta, pilot_cpe=True)
        frames_data.append({
            'fs': fs,
            'H52': H52,
            'delta': delta,
            'eq_no_delta': eq_no_delta,
            'rate_no_delta': rate_no_delta,
        })

    print(f"[P85-T3] Got {len(frames_data)} frames")

    # Distribution of rate with no δ
    from collections import Counter
    c_no_delta = Counter(f['rate_no_delta'] for f in frames_data)
    print(f"\n[P85-T3] === Rate distribution NO δ (Python offline) ===")
    for r, cnt in sorted(c_no_delta.items()):
        marker = ' <-- correct 0xD' if r == 0xD else ''
        print(f"  0x{r:X}: {cnt}{marker}")

    # Sweep over ε_extra applied to L-SIG (in 1/64 sample units)
    print(f"\n[P85-T3] === Sweep ε_extra in 1/64 increments ===")
    best_eps = None
    best_D_count = 0
    eps_grid = np.arange(0, 1.0, 1.0/64.0)
    for eps in eps_grid:
        rate_counts = Counter()
        for f in frames_data:
            # Apply δ + ε_extra
            eq = f['eq_no_delta']
            # Reverse the no-δ state: we need to add (δ + ε_extra) correction
            # Since eq_no_delta has no correction, we apply (δ + ε_extra) from scratch
            total_delta = (f['delta'] + eps) % 1.0
            eq_corrected = apply_delta_bin_order(eq, total_delta)
            rate, _ = decode_lsig_with_delta(eq_corrected, pilot_cpe=True)
            rate_counts[rate] += 1
        n_D = rate_counts.get(0xD, 0)
        if n_D > best_D_count:
            best_D_count = n_D
            best_eps = eps
            best_dist = dict(rate_counts)
        if eps in [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]:
            print(f"  ε_extra={eps:.4f} (={eps*64:.1f}/64): 0xD={n_D}/{len(frames_data)} "
                  f"({100*n_D/len(frames_data):.0f}%)")

    print(f"\n[P85-T3] === Best ε_extra ===")
    print(f"  ε_extra = {best_eps:.4f} ({best_eps*64:.1f}/64 sample units)")
    print(f"  rate=0xD count: {best_D_count}/{len(frames_data)} ({100*best_D_count/len(frames_data):.1f}%)")
    print(f"  rate distribution: {best_dist}")

    np.savez('/tmp/p85_t3_sweep.npz',
             eps_grid=eps_grid,
             best_eps=best_eps,
             best_D_count=best_D_count)


if __name__ == '__main__':
    main()