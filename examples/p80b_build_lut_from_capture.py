"""
Phase 80b Stage 2a: Build per-SC phase LUT from USRP capture JSON.

Loads /tmp/p80b_5250_capture.json (Phase 80b Task 6 output):
  - list of frames with htsig0/htsig1 rx52 + H52

Computes median per-SC phase of equalized bins (after Phase 79 δ
correction), saves to JSON format consumable by C++ frame_equalizer.

Usage:
    python examples/p80b_build_lut_from_capture.py \
        --capture /tmp/p80b_5250_capture.json \
        --output /tmp/p80b_lut_5250.json
"""

import argparse
import json
import sys
import numpy as np

K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)

PILOT_IDX = np.array([48, 49, 50, 51])
PILOT_SC = np.array([-21, -7, 7, 21])
DATA_SC = K_SC_INDEX_52[:48]

HT_SIG0_POLARITY = np.array([1j, 1j, 1j, -1j], dtype=np.complex64)
HT_SIG1_POLARITY = np.array([-1j, -1j, -1j, +1j], dtype=np.complex64)

TWO_PI = 2.0 * np.pi
N_GRID = 64


def estimate_symbol_delta_qbpsk(eq_pilots, H_pilots, pilot_polarity):
    """Phase 79 reference: QBPSK-aware grid-search δ estimator."""
    MIN_H_MAG = 0.01
    valid = np.abs(H_pilots) > MIN_H_MAG
    if not np.any(valid):
        return 0.0
    residual = eq_pilots * np.conj(pilot_polarity)
    best_delta = 0.0
    best_mag = 0.0
    for d in range(N_GRID):
        delta = d / N_GRID
        expected = np.exp(1j * TWO_PI * PILOT_SC * delta / 64.0)
        inner = np.sum(np.conj(expected) * residual * valid)
        mag = np.abs(inner)
        if mag > best_mag:
            best_mag = mag
            best_delta = delta
    return best_delta


def build_lut_from_capture(capture_path):
    """Load capture JSON, compute per-SC phase LUT."""
    with open(capture_path, 'r') as f:
        data = json.load(f)

    n_frames = len(data)
    freq_mhz = data[0].get('freq_mhz', 5250)
    print(f"[LOAD] {capture_path}: {n_frames} frames @ {freq_mhz} MHz")

    arg_eq_htsig0 = np.zeros((n_frames, 48), dtype=np.float32)
    arg_eq_htsig1 = np.zeros((n_frames, 48), dtype=np.float32)
    arg_eq_data = np.zeros((n_frames, 52), dtype=np.float32)

    for i, frame in enumerate(data):
        rx52_0 = np.array(frame['htsig0']['rx52'], dtype=np.complex64)
        H52_0 = np.array(frame['htsig0']['H52'], dtype=np.complex64)
        eq_pilots_0 = rx52_0[PILOT_IDX] / H52_0[PILOT_IDX]
        delta_0 = estimate_symbol_delta_qbpsk(eq_pilots_0, H52_0[PILOT_IDX],
                                              HT_SIG0_POLARITY)
        eq48_0 = rx52_0[:48] / H52_0[:48]
        corr_0 = np.exp(1j * TWO_PI * DATA_SC * delta_0 / 64.0)
        arg_eq_htsig0[i] = np.angle(eq48_0 * corr_0)
        eq52_0 = rx52_0 / H52_0
        corr52_0 = np.exp(1j * TWO_PI * K_SC_INDEX_52 * delta_0 / 64.0)
        arg_eq_data[i] = np.angle(eq52_0 * corr52_0)

        rx52_1 = np.array(frame['htsig1']['rx52'], dtype=np.complex64)
        H52_1 = np.array(frame['htsig1']['H52'], dtype=np.complex64)
        eq_pilots_1 = rx52_1[PILOT_IDX] / H52_1[PILOT_IDX]
        delta_1 = estimate_symbol_delta_qbpsk(eq_pilots_1, H52_1[PILOT_IDX],
                                              HT_SIG1_POLARITY)
        eq48_1 = rx52_1[:48] / H52_1[:48]
        corr_1 = np.exp(1j * TWO_PI * DATA_SC * delta_1 / 64.0)
        arg_eq_htsig1[i] = np.angle(eq48_1 * corr_1)

    arg_eq_htsig = 0.5 * (arg_eq_htsig0 + arg_eq_htsig1)
    median_arg_htsig = np.median(arg_eq_htsig, axis=0)
    median_arg_data = np.median(arg_eq_data, axis=0)
    htsig_data_lut = np.exp(-1j * median_arg_htsig).astype(np.complex64)
    data_lut = np.exp(-1j * median_arg_data).astype(np.complex64)

    return htsig_data_lut, data_lut, n_frames, freq_mhz


def save_lut(htsig_data_lut, data_lut, n_frames, freq_mhz, output_path):
    """Save LUT to JSON format consumable by C++."""
    lut = {
        "htsig_data_lut": [[float(c.real), float(c.imag)] for c in htsig_data_lut],
        "data_lut": [[float(c.real), float(c.imag)] for c in data_lut],
        "n_frames": n_frames,
        "freq_mhz": freq_mhz,
        "timestamp": "2026-07-04T00:00:00Z"
    }
    with open(output_path, 'w') as f:
        json.dump(lut, f, indent=2)
    print(f"[SAVE] {output_path}: htsig_data_lut={len(htsig_data_lut)}, "
          f"data_lut={len(data_lut)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default="/tmp/p80b_5250_capture.json")
    parser.add_argument("--output", default="/tmp/p80b_lut_5250.json")
    args = parser.parse_args()

    htsig_lut, data_lut, n_frames, freq_mhz = build_lut_from_capture(args.capture)
    save_lut(htsig_lut, data_lut, n_frames, freq_mhz, args.output)

    assert np.allclose(np.abs(htsig_lut), 1.0, atol=1e-5)
    assert np.allclose(np.abs(data_lut), 1.0, atol=1e-5)
    print(f"[OK] LUT built from {n_frames} frames @ {freq_mhz} MHz")


if __name__ == "__main__":
    main()