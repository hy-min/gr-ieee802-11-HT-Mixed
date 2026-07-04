#!/usr/bin/env python3
"""Phase 85 T2: estimate per-symbol SFO slope from USRP capture.

Hypothesis: δ estimator at HT-SIG1 (counter=4) measures bulk timing, but L-SIG
(counter=2) has accumulated 2 OFDM symbols of SFO. If per-symbol SFO ε ≠ 0,
the L-SIG correction is off by ε × 2.

Method:
  1. From USRP capture, extract H52 at L-LTF0 (counter=0) and H52 at L-LTF1
     (counter=1). Both should give the same H in absence of SFO. With SFO,
     arg(H_LTF1) = arg(H_LTF0) + ε × 1 symbol × slope_per_sc.
  2. Compute δ_LTF0 and δ_LTF1 separately using the same weighted regression
     as C++ estimate_timing_offset_from_h52.
  3. ε = δ_LTF1 - δ_LTF0 (per-symbol drift in 1/64 sample units).
  4. Cross-check: L-SIG δ should be δ_LTF0 + 2ε if the SFO hypothesis holds.
"""
import numpy as np

CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

# Per 802.11n: L-STF (8 samples) + L-LTF (8+GI2) starts at sample 0 of frame
# L-LTF0 starts at sample 160 (8 L-STF + 8 GI + 64 L-LTF0 = sample 0..159 preamble)
# L-LTF1 starts at sample 160 + 64 = sample 224? No wait...
# Actually: L-STF (8) + GI (8) + L-LTF0 (64) + L-LTF1 (64) + L-SIG (80)
# L-STF = 0..7, GI = 8..15, L-LTF0 = 16..79, L-LTF1 = 80..143, L-SIG = 144..223
# But commonly: L-STF 0..7, L-LTF 8..71, L-SIG 72..151
# In gr-ieee802-11: FRAME_START_BASE was 174 (Phase 33 14-sample shift)
# Let me use the per-capture L-STF start (already known from Phase 82)

# ACTIVE_SC_INDICES for FFT bin order (skip DC and guards)
ACTIVE_SC_INDICES = list(range(1, 27)) + list(range(38, 64))

# LTF reference values (BPSK ±1)
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
LTF_ACTIVE = KLTF64[ACTIVE_SC_INDICES]

# SC index in FFT bin order
SC_INDEX_BIN = np.array(list(range(1, 27)) + list(range(-26, 0)), dtype=np.float64)
assert len(SC_INDEX_BIN) == 52


def estimate_h52_at_offset(iq, frame_start, sym_offset, with_ref=True):
    """H52 = (LTS at frame_start + sym_offset * 80) / LTF_REF."""
    lts0_start = frame_start + sym_offset * 80
    lts1_start = lts0_start + 80
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


def estimate_delta_weighted(H52_bin_order):
    """Match C++ estimate_timing_offset_from_h52: argH = a + b·SC, δ = -b*64/(2π)."""
    argH = np.angle(H52_bin_order).astype(np.float64)
    w = np.abs(H52_bin_order).astype(np.float64)
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


def find_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
    """Same algorithm as Phase 82: L-STF 16-sample autocorrelation."""
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


def main():
    print("[P85-T2] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P85-T2] Total samples: {len(iq)}")

    print("[P85-T2] Finding L-STF starts...")
    starts = find_l_stf_starts(iq)
    print(f"[P85-T2] Found {len(starts)} L-STF starts")

    # For each frame, compute δ at L-LTF0 (counter=0), L-LTF1 (counter=1)
    # Note: sym_offset counting from L-STF: L-STF=0, L-LTF0=2 (skip GI=1), L-LTF1=3, L-SIG=4
    # Actually: L-STF 8 + GI 8 = 16 samples, then L-LTF0 64 samples, L-LTF1 64 samples, L-SIG 80
    # L-STF is at offset 0
    # L-LTF0 starts at offset 16 (after GI)
    # L-LTF1 starts at offset 16 + 64 = 80
    # L-SIG starts at offset 16 + 64 + 64 = 144
    # Per OFDM symbol with CP: 80 samples (64 data + 16 CP)
    # L-LTF0 ends at 16+64=80, L-LTF1 starts at 80 (no CP between L-LTF0 and L-LTF1)
    # Wait, that's not right either. Let me check 802.11 spec.

    # Actually in 802.11a/g/n OFDM, the long training field is 2 OFDM symbols.
    # Each OFDM symbol = 80 samples (16 CP + 64 data). L-LTF = 160 samples.
    # But there's a GI2 (long GI) of 32 samples between L-STF and L-LTF.
    # Total preamble: L-STF(16) + GI(8) + L-LTF0(80) + L-LTF1(80) + L-SIG(80) = 336 samples.

    # In the 802.11n spec, the L-LTF1 has a 32-sample "double GI" actually it's the
    # L-SIG boundary. The convention varies. Let me just empirically:
    # In Phase 82: lts0_start = fs + 176, lts1_start = fs + 256 (80 apart, =1 OFDM sym)
    # So: lts0 at fs+176, lts1 at fs+256, l-sig at fs+336
    # L-LTF0 is 1 OFDM symbol, L-LTF1 is 1 OFDM symbol later, L-SIG is 1 OFDM symbol later

    # So in code: sym_offset=0 means L-LTF0, sym_offset=1 means L-LTF1, sym_offset=2 means L-SIG

    deltas_LTF0 = []
    deltas_LTF1 = []
    deltas_LSIG = []  # we won't use this, but record for diagnostic
    sfo_per_symbol = []  # delta_LTF1 - delta_LTF0

    for fs in starts:
        # H52 at L-LTF0
        H0 = estimate_h52_at_offset(iq, fs, 0, with_ref=True)
        if H0 is None:
            continue
        d0 = estimate_delta_weighted(H0)
        # H52 at L-LTF1
        H1 = estimate_h52_at_offset(iq, fs, 1, with_ref=True)
        if H1 is None:
            continue
        d1 = estimate_delta_weighted(H1)
        # H52 at L-SIG (won't be used for viterbi, but record for diagnostic)
        Hsig = estimate_h52_at_offset(iq, fs, 2, with_ref=True)
        if Hsig is not None:
            dsig = estimate_delta_weighted(Hsig)
            deltas_LSIG.append(dsig)

        deltas_LTF0.append(d0)
        deltas_LTF1.append(d1)
        sfo_per_symbol.append((d1 - d0) % 1.0)

    deltas_LTF0 = np.array(deltas_LTF0)
    deltas_LTF1 = np.array(deltas_LTF1)
    sfo_per_symbol = np.array(sfo_per_symbol)
    deltas_LSIG = np.array(deltas_LSIG)

    print(f"\n[P85-T2] === δ distribution over {len(deltas_LTF0)} frames ===")
    print(f"  δ_LTF0 (L-LTF0, counter=0): mean={deltas_LTF0.mean():.4f} std={deltas_LTF0.std():.4f}")
    print(f"  δ_LTF1 (L-LTF1, counter=1): mean={deltas_LTF1.mean():.4f} std={deltas_LTF1.std():.4f}")
    print(f"  δ_LSIG (L-SIG, counter=2):  mean={deltas_LSIG.mean():.4f} std={deltas_LSIG.std():.4f}")

    print(f"\n[P85-T2] === Per-symbol SFO (ε = δ_LTF1 - δ_LTF0) ===")
    print(f"  ε mean: {sfo_per_symbol.mean():.4f}")
    print(f"  ε std:  {sfo_per_symbol.std():.4f}")
    print(f"  ε median: {np.median(sfo_per_symbol):.4f}")
    # The "actual δ at L-SIG" should be δ_LTF0 + 2ε (cumulative over 2 symbols)
    delta_at_LSIG_predicted = (deltas_LTF0 + 2.0 * sfo_per_symbol) % 1.0
    print(f"\n[P85-T2] === Predicted δ at L-SIG (δ_LTF0 + 2ε) ===")
    print(f"  vs measured δ_LSIG: mean diff = {(delta_at_LSIG_predicted - deltas_LSIG).mean():.4f}")

    # If the hypothesis holds, δ_LSIG should be close to δ_LTF0 + 2ε
    diff_from_measured = (delta_at_LSIG_predicted - deltas_LSIG) % 1.0
    diff_from_measured = np.minimum(diff_from_measured, 1.0 - diff_from_measured)
    print(f"  absolute diff (wrapped to [0, 0.5]): mean={diff_from_measured.mean():.4f} max={diff_from_measured.max():.4f}")

    # Save
    np.savez('/tmp/p85_t2_deltas.npz',
             deltas_LTF0=deltas_LTF0, deltas_LTF1=deltas_LTF1,
             deltas_LSIG=deltas_LSIG, sfo_per_symbol=sfo_per_symbol)
    print(f"\n[P85-T2] Saved /tmp/p85_t2_deltas.npz")


if __name__ == '__main__':
    main()