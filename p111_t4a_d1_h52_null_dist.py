#!/usr/bin/env python3
"""Phase 111 T4a D1: Analyze H52 null SC distribution in USRP capture.

Per Phase 78b: 5 globally-null SCs on USRP at 5250 MHz cable.
Per Phase 100: 5 null SCs → 10 random bits → viterbi d_free=10 ceiling.

Question: Is |H|² distribution really 5 null + N near-null, or different?
- Count null SCs at various thresholds
- Find threshold that minimizes effective errors (LLR ≈ 0)
- Estimate viterbi success probability

Output: Per-frame null SC count distribution, per-SC |H|² statistics,
        predicted viterbi success.
"""
import numpy as np
import sys

# === Constants ===
PILOT_SC = [11, 25, 39, 53]  # FFT bin indices for {-21,-7,+7,+21}
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))  # 52 SCs
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]  # 48 SCs
N_FFT = 64

# Per Phase 100: 5 globally-null SCs are at {-21,-7,+7,+21,-13}
# -21 → bin 64-21 = 43
# -7  → bin 64-7  = 57
# +7  → bin 7
# +21 → bin 21
# -13 → bin 64-13 = 51
PHASE78B_NULL_BINS = [43, 57, 7, 21, 51]  # all pilots + 1 data

# Capture files to analyze
CAPTURE_FILES = [
    ('/tmp/p110_t10_capture.fc32', 'p110 T10 (5250 MHz 30s)'),
    ('/tmp/p110_t8g_capture.fc32', 'p110 T8g (5250 MHz 30s)'),
    ('/tmp/p110_t9_capture.fc32', 'p110 T9 (5250 MHz 30s)'),
]


def find_l_stf_region(iq, period=16, search_skip=1000):
    """Detect L-STF via period-16 autocorr (per Phase 89)."""
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    corr_raw = np.abs(a * np.conj(b))
    win = 16
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')

    # Find L-STF plateau: 10 periods × 16 samples = 160 samples
    threshold = 0.1
    min_plateau = 32
    for i in range(search_skip, len(corr_smooth) - min_plateau):
        if corr_smooth[i] > threshold:
            # Check plateau length
            end = i
            while end < len(corr_smooth) and corr_smooth[end] > threshold * 0.3:
                end += 1
            if end - i >= min_plateau:
                return i, end
    return -1, -1


def find_all_l_stf_regions(iq, period=16, search_skip=1000, min_gap=20000):
    """Find all L-STF starts in a capture."""
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    corr_raw = np.abs(a * np.conj(b))
    win = 16
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')

    threshold = 0.1
    min_plateau = 32
    starts = []
    i = search_skip
    while i < len(corr_smooth) - min_plateau:
        if corr_smooth[i] > threshold:
            end = i
            while end < len(corr_smooth) and corr_smooth[end] > threshold * 0.3:
                end += 1
            if end - i >= min_plateau:
                starts.append(i)
                i = end + min_gap
                continue
        i += 1
    return starts


def analyze_capture(capture_path, label, max_frames=20):
    """Compute H52 for each detected frame and report null SC stats."""
    print(f"\n{'='*70}")
    print(f"[T4a D1] Analyzing {label}: {capture_path}")
    print('='*70)

    iq = np.fromfile(capture_path, dtype=np.complex64)
    print(f"  Total samples: {len(iq)} = {len(iq)/20e6:.1f}s @ 20 MHz")

    # Find all L-STF starts
    l_stf_starts = find_all_l_stf_regions(iq, period=16)
    print(f"  Detected L-STF starts: {len(l_stf_starts)}")

    if not l_stf_starts:
        print("  No L-STF found!")
        return

    # For each frame, compute H52 from L-LTF0 + L-LTF1
    H52_frames = []  # list of 52-element complex arrays
    frame_rates = []  # L-SIG rate for each frame

    # L-LTF: starts 160 samples after L-STF start (10 short symbols × 16 = 160)
    # L-LTF0 at +160, L-LTF1 at +240, L-SIG at +320
    LTS_OFFSET = 160  # L-STF is 8µs = 160 samples at 20 MHz

    for fs in l_stf_starts[:max_frames]:
        # Extract L-LTF0 and L-LTF1
        lts0_start = fs + LTS_OFFSET
        lts1_start = fs + LTS_OFFSET + 80  # LTS is 80 samples (4µs) apart
        l_sig_start = fs + LTS_OFFSET + 160  # L-SIG is 80 samples after LTS1

        if l_sig_start + 80 > len(iq):
            continue

        LTS0 = iq[lts0_start:lts0_start+80]
        LTS1 = iq[lts1_start:lts1_start+80]
        SIG = iq[l_sig_start:l_sig_start+80]

        # Apply windowing and FFT (64-point)
        # Per 802.11n: L-LTF uses 64-pt FFT, L-SIG is 80 samples (1 OFDM symbol + 16 GI)
        win = np.hanning(64)
        LTS0_64 = LTS0[:64] * win
        LTS1_64 = LTS1[:64] * win
        SIG_64 = SIG[:64] * win

        F0 = np.fft.fft(LTS0_64, 64)
        F1 = np.fft.fft(LTS1_64, 64)
        Fsig = np.fft.fft(SIG_64, 64)

        # H52 estimate (LTS0 + LTS1 average)
        H52_complex = (F0 + F1) / 2.0

        # Store all 52 active SCs
        H52_active = H52_complex[ACTIVE_SC]
        H52_frames.append(H52_active)

        # L-SIG rate detection (just to filter valid frames)
        Havg_sig = (F0[DATA_SC] + F1[DATA_SC]) / 2.0
        eq_sig = Fsig[DATA_SC] / Havg_sig
        cpe_sig = np.angle(np.sum(eq_sig))
        eq_sig_rot = eq_sig * np.exp(-1j * cpe_sig)
        bits = (eq_sig_rot.real > 0).astype(int)
        # Rate is bits[0:4] (MSB first)
        rate = 0
        # Need to interleave and viterbi to get exact rate, but raw rate from
        # interleaved bits is OK as a frame validity filter
        frame_rates.append(rate)

    if not H52_frames:
        print("  No valid frames extracted!")
        return

    H52_stack = np.array(H52_frames)  # shape (n_frames, 52)
    H52_mag = np.abs(H52_stack)
    H52_mag2 = H52_mag ** 2

    print(f"  Extracted H52 for {len(H52_frames)} frames")
    print(f"  H52 shape: {H52_stack.shape}")

    # === Per-SC |H|² statistics ===
    print(f"\n  {'SC':>4} {'bin':>5} {'mean|H|²':>10} {'std|H|²':>10} {'min|H|²':>10} "
          f"{'max|H|²':>10}  type")
    for j, sc in enumerate(ACTIVE_SC):
        mags = H52_mag2[:, j]
        sc_type = ""
        if sc in PHASE78B_NULL_BINS:
            if sc in [7, 21, 43, 57]:
                sc_type = "PILOT (Phase 78b: stable null)"
            else:
                sc_type = "Phase 78b: stable null (data SC -13)"
        elif sc in PILOT_SC:
            sc_type = "pilot"
        print(f"  {sc:>4} {j:>5} {mags.mean():>10.4f} {mags.std():>10.4f} "
              f"{mags.min():>10.4f} {mags.max():>10.4f}  {sc_type}")

    # === Find globally-null SCs (low mean |H|²) ===
    mean_h2 = H52_mag2.mean(axis=0)
    print(f"\n  Per-SC mean |H|² sorted (ascending):")
    sorted_idx = np.argsort(mean_h2)
    for j in sorted_idx[:10]:
        sc = ACTIVE_SC[j]
        print(f"    SC {sc:>3} (idx {j}): mean |H|² = {mean_h2[j]:.4f}")

    # === Threshold sweep: count null SCs at each threshold ===
    print(f"\n  Threshold sweep — null SC count (mean |H|² < threshold):")
    print(f"  {'threshold':>10} {'n_null':>8} {'list'}")
    for thresh in [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]:
        n_null = np.sum(mean_h2 < thresh)
        null_scs = [ACTIVE_SC[j] for j in range(52) if mean_h2[j] < thresh]
        print(f"  {thresh:>10.3f} {n_null:>8} {null_scs}")

    # === Per-frame null SC count ===
    print(f"\n  Per-frame null SC count distribution (threshold=0.05):")
    thresh = 0.05
    null_per_frame = np.sum(H52_mag2 < thresh, axis=1)
    unique, counts = np.unique(null_per_frame, return_counts=True)
    for u, c in zip(unique, counts):
        print(f"    {u} null SCs: {c} frames ({100*c/len(null_per_frame):.1f}%)")

    # === Theoretical viterbi limit analysis ===
    # d_free = 10 for K=7 r=1/2 convolutional code
    # With erasure decoding: can correct up to d_free-1 = 9 erasures
    # With random errors: can correct up to (d_free-1)/2 = 4 errors
    # We classify SCs by |H|²:
    #   null (|H|² < 0.05): erasure (1 effective error)
    #   near-null (0.05 < |H|² < 0.2): partial confidence, likely 1 error if wrong
    #   good (|H|² > 0.2): high confidence
    print(f"\n  Viterbi success analysis (assuming |H|² > 0.05 are reliable):")
    for thresh in [0.01, 0.05, 0.1, 0.2, 0.5]:
        null_count = np.sum(H52_mag2 < thresh, axis=1)
        n_frames = len(null_count)
        # Expected null count
        mean_null = null_count.mean()
        max_null = null_count.max()
        # P(null > 9) = viterbi fails on this frame
        p_fail = np.sum(null_count > 9) / n_frames
        # P(null > 4) = viterbi may fail (random error limit)
        p_marginal = np.sum(null_count > 4) / n_frames
        print(f"  threshold={thresh:.2f}: mean_null={mean_null:.1f} max_null={max_null} "
              f"P(fail, null>9)={100*p_fail:.1f}% P(marginal, null>4)={100*p_marginal:.1f}%")


def main():
    for capture_path, label in CAPTURE_FILES:
        try:
            analyze_capture(capture_path, label, max_frames=30)
        except FileNotFoundError:
            print(f"\n  Capture not found: {capture_path}")


if __name__ == '__main__':
    main()
