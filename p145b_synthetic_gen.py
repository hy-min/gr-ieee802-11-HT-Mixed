#!/usr/bin/env python3
"""Generate a clean HT-Mixed frame with known sub-sample timing offset delta.

Output: /tmp/p145_synthetic_delta_<delta>.fc32

The frame contains L-STF + L-LTF + L-SIG only (enough for p145b analysis).
A fractional sample delay delta is applied per OFDM symbol by rotating each
symbol's FFT bins: X_delta[k] = X[k] * exp(-j*2*pi*k*delta/64).
"""
import numpy as np
import argparse
import os

# 52-element SC order used by p145b and frame_equalizer
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def sc_to_bin(sc):
    return sc if sc >= 0 else sc + 64


# L-LTF TX reference in 52-element order (BPSK +/-1)
LTF_52 = np.array([
    +1,+1,-1,-1,+1,-1, +1,-1,+1,+1,+1,+1,
    +1,+1,-1,-1,+1,+1, +1,-1,+1,+1,+1,+1,
    +1,-1,-1,+1,+1,-1, -1,+1,-1,-1,-1,-1,
    -1,+1,+1,-1,-1,+1, -1,-1,+1,+1,+1,+1,
    +1,+1,+1,+1
], dtype=np.float32)


def bin_to_sc(k):
    """Map 64-point FFT bin to subcarrier index (only valid for active SCs)."""
    if 1 <= k <= 26:
        return k
    elif 33 <= k <= 63:
        return k - 64
    else:
        return 0  # DC/guard


def apply_delta(F, delta):
    """Apply sub-sample timing offset delta to frequency-domain symbol F."""
    if delta == 0.0:
        return F
    out = F.copy()
    for k in range(64):
        sc = bin_to_sc(k)
        if sc != 0:
            out[k] *= np.exp(-1j * 2.0 * np.pi * sc * delta / 64.0)
    return out


def lltf_symbol(delta=0.0):
    """Return one 64-sample L-LTF symbol (time domain) with optional delta shift."""
    F = np.zeros(64, dtype=np.complex64)
    for i, sc in enumerate(SC_INDEX_52):
        F[sc_to_bin(sc)] = LTF_52[i]
    F = apply_delta(F, delta)
    return np.fft.ifft(F)


def lsig_symbol(delta=0.0):
    """Return one 64-sample L-SIG symbol with rate=0xD, len=45, optional delta."""
    # L-SIG bits: rate (4) + reserved (1) + length (12) + parity (1) + tail (6) = 26
    # After encoding/interleaving = 48 bits. We use the already-encoded 48 bits
    # from test_mcs_end_to_end output for rate=0xD, length=45, parity=1.
    coded_bits = np.array([
        1,1,0,1,1,0,0,0,1,0,0,1,
        1,1,1,1,1,1,1,0,0,1,0,1,
        1,0,0,1,0,0,0,1,1,1,1,1,
        1,0,0,0,1,1,1,1,0,1,1,1
    ], dtype=np.int32)
    # BPSK mapping: bit 0 -> -1, bit 1 -> +1
    data_48 = np.where(coded_bits == 1, 1.0, -1.0).astype(np.float32)

    F = np.zeros(64, dtype=np.complex64)
    # Data SCs are first 48 in SC_INDEX_52 order
    for i in range(48):
        F[sc_to_bin(SC_INDEX_52[i])] = data_48[i]
    # Pilots at {-21,-7,7,21}
    for pilot_sc in [-21, -7, 7, 21]:
        F[sc_to_bin(pilot_sc)] = 1.0 + 0.0j

    F = apply_delta(F, delta)
    return np.fft.ifft(F)


def lstf_sequence(n_samples=160):
    """Generate L-STF: 10 repetitions of 16-sample short training symbol."""
    # Short training sequence (period 16) from 802.11a
    stf_16 = np.array([
        0.0455+0.0455j,  0.0455+0.0455j, -0.0455+0.0455j, -0.0455+0.0455j,
        0.0455+0.0455j,  0.0455+0.0455j, -0.0455+0.0455j, -0.0455+0.0455j,
       -0.0455-0.0455j, -0.0455-0.0455j,  0.0455-0.0455j,  0.0455-0.0455j,
       -0.0455-0.0455j, -0.0455-0.0455j,  0.0455-0.0455j,  0.0455-0.0455j
    ], dtype=np.complex64)
    return np.tile(stf_16, n_samples // 16)


def generate_frame(delta=0.0):
    """Generate L-STF + L-LTF0 + L-LTF1 + L-SIG with per-symbol delta."""
    lstf = lstf_sequence(160)

    # L-LTF: two 80-sample symbols (CP+data), each data is 64 samples
    lltf0_data = lltf_symbol(delta)
    lltf1_data = lltf_symbol(delta)
    lltf0_cp = lltf0_data[-16:]  # cyclic prefix
    lltf1_cp = lltf1_data[-16:]

    # L-SIG: one 80-sample symbol
    lsig_data = lsig_symbol(delta)
    lsig_cp = lsig_data[-16:]

    frame = np.concatenate([
        lstf,
        lltf0_cp, lltf0_data,
        lltf1_cp, lltf1_data,
        lsig_cp, lsig_data,
    ]).astype(np.complex64)

    # Add some trailing silence so p145b has room
    frame = np.concatenate([frame, np.zeros(1000, dtype=np.complex64)])
    return frame


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--delta', type=float, default=0.3,
                   help='sub-sample timing offset to inject (samples)')
    p.add_argument('--out', default=None, help='output .fc32 path')
    args = p.parse_args()

    frame = generate_frame(args.delta)
    out_path = args.out or f'/tmp/p145_synthetic_delta_{args.delta:.2f}.fc32'
    frame.tofile(out_path)
    print(f'Generated {len(frame)} samples ({len(frame)/20e6:.4f}s)')
    print(f'L-STF start=0, L-LTF0 DATA=176, L-LTF1 DATA=256, L-SIG DATA=336')
    print(f'Injected delta={args.delta:.4f} samples')
    print(f'Saved to {out_path}')


if __name__ == '__main__':
    main()
