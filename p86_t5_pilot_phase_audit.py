#!/usr/bin/env python3
"""Phase 86 T5: Pilot SC phase audit.

T3 v2 finding: pilots are NOT null on average (|H| mean 267-455), but
inner pilots {-7, +7} drop to min=17.6 and 25.5 in some frames.

Question: when an inner pilot is in a temporal null, does CPE from
all 4 pilots produce a wildly wrong phase estimate?

Method:
  1. For each frame, compute CPE = angle of sum(pilot_reals) for
     all 4 pilots {-21,-7,7,21}.
  2. Find frames where min pilot |H| is <50 (temporal-null frame).
  3. Compare CPE phase variance: temporal-null frames vs all-frame CPE.
  4. Check: does CPE estimate change by more than 90° in temporal-null
     frames? If yes, that explains bit-flips in BPSK rate field.

Note: L-SIG pilots have real part = ±1 (BPSK), so phase reference is
the real axis. CPE = atan2(imag_sum, real_sum) of (H_pilot / |H_pilot|).
"""
import numpy as np
import sys

CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'

ACTIVE_SC_INDICES = list(range(1, 27)) + list(range(38, 64))
KLTF64 = np.array([
    0, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
    -1, -1, 1, 1, -1, 1, 1, -1, 1, -1, 1, 1,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1,
    1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
], dtype=np.complex64)
LTF_ACTIVE = KLTF64[ACTIVE_SC_INDICES]

# Pilot SC indices in the 52-element L-SIG TX order:
# SC_INDEX_52 = [-26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
#                -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
#                20,22,23,24,25,26, -21,-7,7,21]
# Pilots {-21,-7,7,21} are at indices [48, 49, 50, 51]
PILOT_IDX = [48, 49, 50, 51]  # in L-SIG TX order
PILOT_SC_VALUES = [-21, -7, 7, 21]
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def estimate_h52(iq, fs):
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
    H = np.zeros_like(avg)
    valid = np.abs(LTF_ACTIVE) > 1e-6
    H[valid] = avg[valid] / LTF_ACTIVE[valid]
    return H.astype(np.complex64)


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


def main():
    print("[P86-T5] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P86-T5] Total samples: {len(iq)}")

    print("[P86-T5] Finding L-STF starts...")
    starts = find_l_stf_starts(iq)
    print(f"[P86-T5] Found {len(starts)} L-STF starts")

    # For each frame, compute per-pilot magnitudes and CPE from real axis
    print("[P86-T5] Computing per-frame pilot |H| and CPE phase...")
    pilot_mag_per_frame = []  # shape (n_frames, 4)
    cpe_phase_per_frame = []
    for fs in starts:
        H = estimate_h52(iq, fs)
        if H is None:
            continue
        pilot_H = H[PILOT_IDX]
        pilot_mag = np.abs(pilot_H)
        # L-SIG pilots: real = ±1, so divide by sign of LTF ref to get pure
        # channel effect. LTF ref values at pilot bins are all ±1.
        # H_pilot = (Y_pilot / X_pilot) where X_pilot = ±1, so
        # arg(H_pilot) ≈ channel phase at pilot SC.
        # CPE: weight by |H_pilot| to suppress null pilots
        cpe_phase = np.angle(np.sum(pilot_H))  # magnitude-weighted
        pilot_mag_per_frame.append(pilot_mag)
        cpe_phase_per_frame.append(cpe_phase)

    pilot_mag_arr = np.array(pilot_mag_per_frame)
    cpe_phase_arr = np.array(cpe_phase_per_frame)
    print(f"[P86-T5] Got {len(pilot_mag_arr)} frames with valid H52")

    # Per-pilot |H| statistics
    print("\n[P86-T5] === Per-pilot |H| statistics (149 frames) ===")
    for i, sc in enumerate(PILOT_SC_VALUES):
        mags = pilot_mag_arr[:, i]
        print(f"  SC {sc:>4}: mean={mags.mean():.1f} std={mags.std():.1f} "
              f"min={mags.min():.1f} max={mags.max():.1f}")
        # Count frames where pilot |H| < 50 (temporal null)
        n_null = int((mags < 50).sum())
        print(f"           |H|<50 frames: {n_null} ({100*n_null/len(mags):.1f}%)")
        n_null_30 = int((mags < 30).sum())
        print(f"           |H|<30 frames: {n_null_30} ({100*n_null_30/len(mags):.1f}%)")

    # Frames where ANY inner pilot {-7, +7} has |H| < 50
    inner_idx = [1, 2]  # SC -7, +7
    outer_idx = [0, 3]  # SC -21, +21
    inner_min = pilot_mag_arr[:, inner_idx].min(axis=1)
    outer_min = pilot_mag_arr[:, outer_idx].min(axis=1)
    all_min = pilot_mag_arr.min(axis=1)

    n_inner_null = int((inner_min < 50).sum())
    n_outer_null = int((outer_min < 50).sum())
    n_any_null = int((all_min < 50).sum())
    print(f"\n[P86-T5] === Temporal-null frames ===")
    print(f"  Inner pilot (SC -7 or +7) |H|<50: {n_inner_null} ({100*n_inner_null/len(all_min):.1f}%)")
    print(f"  Outer pilot (SC -21 or +21) |H|<50: {n_outer_null} ({100*n_outer_null/len(all_min):.1f}%)")
    print(f"  ANY pilot |H|<50: {n_any_null} ({100*n_any_null/len(all_min):.1f}%)")

    # CPE phase variance comparison
    print("\n[P86-T5] === CPE phase distribution ===")
    cpe_rad = np.array(cpe_phase_per_frame)
    print(f"  All frames: mean={np.mean(cpe_rad):.4f} std={np.std(cpe_rad):.4f}")

    inner_null_mask = inner_min < 50
    if inner_null_mask.any():
        cpe_inner_null = cpe_rad[inner_null_mask]
        cpe_inner_ok = cpe_rad[~inner_null_mask]
        print(f"  Inner-null frames: mean={np.mean(cpe_inner_null):.4f} std={np.std(cpe_inner_null):.4f}")
        print(f"  Inner-OK frames:   mean={np.mean(cpe_inner_ok):.4f} std={np.std(cpe_inner_ok):.4f}")
        # KS-like test: difference between two distributions
        diff = abs(np.mean(cpe_inner_null) - np.mean(cpe_inner_ok))
        print(f"  |mean diff|: {diff:.4f} rad = {np.degrees(diff):.2f}°")

    # Robust CPE: median across 4 pilots
    print("\n[P86-T5] === Robust CPE (median of 4 pilots) ===")
    pilot_phases = np.array([np.angle(pilot_mag_per_frame[i][j]) for i in range(len(pilot_mag_per_frame)) for j in range(4)]).reshape(-1, 4)
    median_cpe = np.median(pilot_phases, axis=1)
    print(f"  Median CPE: mean={np.mean(median_cpe):.4f} std={np.std(median_cpe):.4f}")
    diff_median_vs_weighted = np.std(median_cpe - cpe_rad)
    print(f"  Diff median vs weighted: std={diff_median_vs_weighted:.4f} rad = "
          f"{np.degrees(diff_median_vs_weighted):.2f}°")

    # Save
    np.savez('/tmp/p86_t5_pilot_audit.npz',
             pilot_mag=pilot_mag_arr, cpe_phase=cpe_rad, median_cpe=median_cpe)


if __name__ == '__main__':
    main()