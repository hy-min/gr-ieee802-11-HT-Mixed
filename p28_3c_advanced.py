#!/usr/bin/env python
"""Phase 28.3c: Check for inter-symbol phase drift (CFO/SFO hypothesis).

Hypothesis: LTS0 and LSIG are 80 samples apart (at fs+176 and fs+336).
If there's an SFO/CFO, the phase difference between L-LTF and L-SIG
will accumulate over those 80 samples.

Test: rotate LSIG by progressively larger angles, find the angle that
maximizes matches. If the angle is non-zero, it's the inter-symbol
phase error.
"""
import numpy as np
import sys

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))


def find_l_stf_region(iq, period=16, search_skip=1000):
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    corr_raw = np.abs(a * np.conj(b))
    win = 16
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')
    ratio = np.zeros_like(corr_smooth)
    ratio[win:] = corr_smooth[win:] / (corr_smooth[:-win] + 1e-12)
    for i in range(search_skip, len(ratio) - 1):
        if ratio[i] > 5 and corr_smooth[i] > 0.1:
            peak = corr_smooth[i]
            end = i
            while end < len(corr_smooth) - 1 and corr_smooth[end + 1] > peak * 0.3:
                end += 1
            return i, end
    return -1, -1


# Load + frame start
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
l_stf_start, _ = find_l_stf_region(iq, period=16)
fs = l_stf_start
print(f"fs = {fs}")

# Standard positions
lts0_start = fs + 176
lts1_start = fs + 256
sig_start = fs + 336

LTS0 = iq[lts0_start:lts0_start+64]
LTS1 = iq[lts1_start:lts1_start+64]
SIG = iq[sig_start:sig_start+64]

F0 = np.fft.fft(LTS0, 64)
F1 = np.fft.fft(LTS1, 64)
Fsig = np.fft.fft(SIG, 64)
F0a = F0[ACTIVE_SC]
F1a = F1[ACTIVE_SC]
Havg = (F0a + F1a) / 2

eq = Fsig[ACTIVE_SC] / Havg

# Sweep fine rotation to find best alignment
print("\n" + "=" * 70)
print("Step 1: Find best fine rotation to align BPSK to real axis")
print("=" * 70)
best_m = 0
best_ang = 0
for ang_deg in np.arange(-180, 180, 0.5):
    eq_rot = eq * np.exp(-1j * np.radians(ang_deg))
    bits = (eq_rot.real > 0).astype(int)
    got = ''.join(map(str, bits.tolist()))
    matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
    if matches > best_m:
        best_m = matches
        best_ang = ang_deg

print(f"Best rotation: {best_ang:+.1f}° → {best_m}/48 matches")

# Now look at the per-SC phase pattern (residual after H)
print("\n" + "=" * 70)
print("Step 2: Look at per-SC phase after H equalization (no global CPE)")
print("=" * 70)
# For BPSK, expected eq[k] is +1 or -1 (real). So angle(eq[k]) should be 0 or pi.
phases = np.angle(eq)
print(f"Per-SC phase after H equalization (degrees):")
for i, k in enumerate(ACTIVE_SC):
    p_deg = np.degrees(phases[i])
    expected_bit = int(EXPECTED_BITS[i])
    bpsk_phase = 0 if expected_bit == 1 else 180
    err_deg = p_deg - bpsk_phase
    # Normalize to [-90, 90]
    err_deg = ((err_deg + 90) % 180) - 90
    print(f"  k={k:2d}: angle={p_deg:+7.1f}°  expected_bit={expected_bit} "
          f"BPSK_phase={bpsk_phase:3d}°  err={err_deg:+6.1f}°")

# Step 3: The KEY insight — eq[k] = X[k] * (1 + phase_offset[k])
# If phase_offset[k] is linear in k, that's SFO.
# If phase_offset[k] is constant, that's CFO.
# If phase_offset[k] is quadratic, it's timing offset.
print("\n" + "=" * 70)
print("Step 3: Fit phase offset[k] = a*k + b (SFO + CFO model)")
print("=" * 70)
# Use the EXPECTED bits to determine the BPSK phase (0 or pi)
expected_phases = np.array([0 if int(b) == 1 else np.pi for b in EXPECTED_BITS])
residual_phases = phases - expected_phases
# Wrap to [-pi/2, pi/2]
residual_phases = (residual_phases + np.pi/2) % np.pi - np.pi/2
# Fit linear
ks = np.array(ACTIVE_SC)
p = np.polyfit(ks, residual_phases, 1)
slope = p[0]  # rad per SC
intercept = p[1]  # rad
print(f"Linear fit: phase = {np.degrees(slope):.3f} deg/SC * k + {np.degrees(intercept):.2f} deg")
print(f"Slope in ppm (20 MHz BW, 64 SCs span ±32 = 64 SCs across 20 MHz):")
print(f"  {slope:.6f} rad/SC")
print(f"  {slope * 64 / (2 * np.pi) * 1e6:.3f} ppm equivalent SFO")

# Apply this correction
print("\n--- Apply linear fit correction ---")
phase_correction = slope * ks + intercept
eq_corr = eq * np.exp(-1j * phase_correction)
bits_corr = (eq_corr.real > 0).astype(int)
got_corr = ''.join(map(str, bits_corr.tolist()))
matches_corr = sum(1 for a, b in zip(got_corr, EXPECTED_BITS) if a == b)
print(f"After linear phase correction: {matches_corr}/48 matches")
print(f"  Got: {got_corr}")

# Step 4: Try quadratic phase (timing offset)
print("\n" + "=" * 70)
print("Step 4: Fit phase offset[k] = a*k^2 + b*k + c (timing offset)")
print("=" * 70)
p2 = np.polyfit(ks, residual_phases, 2)
a2, b2, c2 = p2
print(f"Quadratic fit: phase = {np.degrees(a2):.6f} deg/k² + "
      f"{np.degrees(b2):.3f} deg/k + {np.degrees(c2):.2f} deg")
print(f"a² term = {a2:.8f} rad/k²")
phase_corr2 = a2 * ks**2 + b2 * ks + c2
eq_corr2 = eq * np.exp(-1j * phase_corr2)
bits_corr2 = (eq_corr2.real > 0).astype(int)
got_corr2 = ''.join(map(str, bits_corr2.tolist()))
matches_corr2 = sum(1 for a, b in zip(got_corr2, EXPECTED_BITS) if a == b)
print(f"After quadratic phase correction: {matches_corr2}/48 matches")
print(f"  Got: {got_corr2}")

# Step 5: Try to brute-force search a 2D rotation + phase
print("\n" + "=" * 70)
print("Step 5: 2D brute-force search (rotation + residual)")
print("=" * 70)
best_m_2d = 0
best_cfg_2d = None
for rot_deg in np.arange(-180, 180, 5):
    eq_rot = eq * np.exp(-1j * np.radians(rot_deg))
    for slope_try in np.arange(-0.05, 0.05, 0.005):
        phase_corr = slope_try * ks
        eq_corr = eq_rot * np.exp(-1j * phase_corr)
        bits = (eq_corr.real > 0).astype(int)
        got = ''.join(map(str, bits.tolist()))
        matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
        if matches > best_m_2d:
            best_m_2d = matches
            best_cfg_2d = (rot_deg, slope_try)

if best_cfg_2d:
    rot, slope = best_cfg_2d
    print(f"Best 2D: rotation={rot}°, slope={slope:.4f} rad/SC → {best_m_2d}/48")
    eq_final = eq * np.exp(-1j * (np.radians(rot) + slope * ks))
    bits = (eq_final.real > 0).astype(int)
    got = ''.join(map(str, bits.tolist()))
    print(f"  Got: {got}")

# Step 6: What if the channel changed between L-LTF and L-SIG?
# Try using the F0a from L-SIG's own start (L-SIG is repeated?)
# In HT-mixed mode, HT-SIG follows L-SIG, but L-SIG is just 64 samples.
# However, the SIGNAL field has the same L-LTF-like structure (QPSK on 52 SCs)
# Wait, L-SIG is BPSK rate 1/2, not LTF-like.
print("\n" + "=" * 70)
print("Step 6: Check expected bits and inspect alternating pattern")
print("=" * 70)
# Expected: 111111011101101010000010111001001111100101101111
# RATE=1101, LENGTH=16=0x010=000000010000, parity=0, tail=000000, reserved=0
# Full SIGNAL: 1101 000000010000 0 000000 0 = 24 bits
# Encoded with rate 1/2 BCC (polys 133, 171), interleaved over 48 SCs
# Convolutional encoder output for 24 input bits:
# 24 bits * 2 (rate 1/2) = 48 coded bits
# Then interleaved across 48 SCs

# Let's just deinterleave the expected and see what the SIGNAL field encodes
import re

# 802.11n interleaver for 48 SCs, k=1, s=max(1, Nbpscs/2)
# For BPSK (Nbpscs=1), s=1
# Permutation: j = s * floor(i/s) + (i + Ncol - floor(i*s/Ncol)) mod Ncol
# where Ncol = 16, Nrow = 3 (for 48 bits)
# Inverse permutation:
Ncol = 16
Nrow = 3
s = 1

def deinterleave(bits_48):
    """Deinterleave 48 bits per 802.11n. Input: received order (interleaved). Output: encoded bits in encoder order."""
    # First undo the permutation
    # Original i -> j; we need to find i for each j
    # Forward: j = s * floor(i/s) + (i + Ncol - floor(i*s/Ncol)) mod Ncol
    # i=0..47, j=0..47
    perm = np.zeros(48, dtype=int)
    for i in range(48):
        j = s * (i // s) + (i + Ncol - (i * s) // Ncol) % Ncol
        perm[i] = j
    # perm[i] = j means encoded bit i is placed at position j
    # To deinterleave: take received[j] and put back at position i
    # so deinterleaved[i] = received[perm[i]]
    out = np.zeros(48, dtype=int)
    for i in range(48):
        out[i] = bits_48[perm[i]]
    return out

# For the simple case s=1:
# j = floor(i/1) * 1 + (i + 16 - floor(i*1/16)) % 16
# j = i + (i + 16 - floor(i/16)) % 16
# For i<16: floor(i/16)=0, so j = i + (i+16) % 16 = i + (i) % 16 = i + i = 2i
# Wait that doesn't seem right. Let me check.
# For i=0: j = 0 + (0+16-0)%16 = 0
# For i=1: j = 1 + (1+16-0)%16 = 1 + 1 = 2
# For i=15: j = 15 + (15+16-0)%16 = 15 + 15 = 30
# For i=16: j = 16 + (16+16-1)%16 = 16 + 15 = 31
# For i=31: j = 31 + (31+16-1)%16 = 31 + 14 = 45
# For i=32: j = 32 + (32+16-2)%16 = 32 + 14 = 46

# Actually, the formula for j in 802.11n is:
# j = s * floor(i/s) + (i + Ncol - floor(i*s/Ncol)) mod Ncol
# For s=1: j = i + (i + 16 - floor(i/16)) mod 16
# Hmm, let me re-check this against the actual standard.
# Actually, the perm should map encoded bit index to subcarrier index.
# The expected_bits we have are the bits AT subcarriers 1, 2, ..., 26, 38, ..., 63.
# We need to know the SUBCARRIER LOGICAL INDEX vs ENCODER OUTPUT INDEX.

print("Expected bits:", EXPECTED_BITS)
# Note: These 48 bits are the BPSK-mapped bits at the 52 subcarriers (in some order).
# The encoder outputs are interleaved across 48 SCs. Then 4 SCs are pilots (4 of 52).
# For 20 MHz HT, 4 pilots at positions k = -21, -7, 7, 21 (in 64-point notation)
# = SCs 11, 25, 39, 53 (in 1-indexed)
# So among ACTIVE_SC = 1..26 + 38..63:
# 11, 25, 39, 53 are PILOT subcarriers
PILOT_SC = [11, 25, 39, 53]
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]
print(f"Data SCs ({len(DATA_SC)}): {DATA_SC}")
# That gives 48 data SCs.
