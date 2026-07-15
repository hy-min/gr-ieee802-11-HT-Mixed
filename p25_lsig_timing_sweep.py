#!/usr/bin/env python
"""Task 25.1: L-SIG timing offset sweep on captured USRP IQ (corrected offsets)."""
import numpy as np
import time

EXPECTED_BITS = '111111011101101010000010111001001111100101101111'
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
F_SAMPLE = 20e6

print("=" * 70)
print("TASK 25.1: L-SIG TIMING OFFSET SWEEP (CORRECTED OFFSETS)")
print("=" * 70)

# Load
iq = np.fromfile('/tmp/p24_usrp_iq.bin', dtype=np.complex64)
print(f"\n[1] IQ loaded: {len(iq)} samples")

# Actual frame start (found via cross-correlation)
fs = 30038863
print(f"  Actual frame start (from xcorr): {fs}")
print(f"  |L-STF[fs:fs+160]|     = {np.linalg.norm(iq[fs:fs+160]):.3f}")
print(f"  |L-LTF[fs+160:fs+304]| = {np.linalg.norm(iq[fs+160:fs+304]):.3f}")
print(f"  |L-SIG[fs+304:fs+384]| = {np.linalg.norm(iq[fs+304:fs+384]):.3f}")

# Frame structure (CORRECTED):
#   fs+0..fs+160: L-STF (160)
#   fs+160..fs+304: L-LTF (144)  - structure: GI(16) + sym1(64) + sym2(64)
#   fs+304..fs+384: L-SIG (80)   - structure: GI(16) + DATA(64)
# L-LTF sym1: fs+176 to fs+240
# L-LTF sym2: fs+240 to fs+304
# L-SIG DATA: fs+320 to fs+384

# === Step 1: Re-extract H52 with the correct structure ===
print("\n[2] Re-extracting H52 with corrected L-LTF structure...")

# L-LTF starts at fs+160 (NO pre-GI from L-STF!)
lltf = iq[fs + 160:fs + 160 + 144]
# lltf[0:16] = GI
# lltf[16:80] = sym1
# lltf[80:144] = sym2
sym1 = lltf[16:80]
sym2 = lltf[80:144]
H1 = np.fft.fft(sym1, 64)
H2 = np.fft.fft(sym2, 64)
H_new = (H1 + H2) / 2

H_new_active = np.array([H_new[k] for k in ACTIVE_SC])
print(f"  New H52 stats:")
print(f"    magnitude: mean={np.abs(H_new_active).mean():.3f}, std={np.abs(H_new_active).std():.3f}")
print(f"    phase: mean={np.angle(H_new_active).mean():.3f}, std={np.angle(H_new_active).std():.3f}")

# Compare to Phase 24 H_v3 (which was correct)
H_v3 = np.load('/tmp/p24_H_v3.npy')
H_v3_active = np.array([H_v3[k] for k in ACTIVE_SC])
print(f"\n  Phase 24.3 H_v3 stats (52 active SC):")
print(f"    magnitude: mean={np.abs(H_v3_active).mean():.3f}, std={np.abs(H_v3_active).std():.3f}")
print(f"    phase: mean={np.angle(H_v3_active).mean():.3f}, std={np.angle(H_v3_active).std():.3f}")

# Phase24.2 H_v3 used sym1=lltf[0:64], sym2=lltf[80:144]
# But lltf[0:64] includes GI(16) + sym1(64) - so it's GI+sym1!
# Actually sym1=lltf[0:64] includes the GI as the first 16 samples, then sym1
# That extra GI doesn't matter for FFT (cyclicity).
# Let's compare both:
sym1_a = lltf[0:64]    # GI + sym1
sym2_a = lltf[80:144]
H_a = (np.fft.fft(sym1_a, 64) + np.fft.fft(sym2_a, 64)) / 2
H_a_active = np.array([H_a[k] for k in ACTIVE_SC])
print(f"\n  Phase24.2-style H_a (sym1=lltf[0:64], sym2=lltf[80:144]) stats:")
print(f"    magnitude: mean={np.abs(H_a_active).mean():.3f}, std={np.abs(H_a_active).std():.3f}")

# Time-domain correlation between sym1 and sym2
print(f"\n  |sym1 . sym2*| (correlation) = {np.abs(np.sum(sym1 * np.conj(sym2))):.3f}")
print(f"  |sym1_a . sym2_a*| = {np.abs(np.sum(sym1_a * np.conj(sym2_a))):.3f}")

np.save('/tmp/p25_H_corrected.npy', H_new)

# === Step 3: Sweep L-SIG timing offset ===
print("\n[3] L-SIG timing offset sweep...")

# Reference L-SIG DATA: fs+320 to fs+384 (64 samples)
# Sweep delta = -20 to +20 around fs+320
cfo_hz = float(np.load('/tmp/p24_cfo_hz.npy')[0])
print(f"  Using CFO correction: {cfo_hz} Hz")

# L-LTF center for CFO phase: between sym1 and sym2 midpoints
# sym1: fs+176 to fs+240, center fs+208
# sym2: fs+240 to fs+304, center fs+272
# L-LTF effective center: fs+208 (since H was derived from sym1+sym2 averaged FFT)
# Actually L-SIG center is fs+(320+384)/2 = fs+352
# Time offset from L-LTF center to L-SIG center = 352 - 272 = 80 samples = 4 us

print(f"  Reference L-SIG DATA start: fs+320 (=fs+{320})")
print(f"  Sweep delta: -20 to +20 samples")

results_cfo = []
results_nocfo = []
results_raw = []

best_ber_cfo = 1.0
best_ber_nocfo = 1.0
best_ber_raw = 1.0

for delta in range(-20, 21, 1):
    lsig_start = 320 + delta
    lsig_sym = iq[fs + lsig_start:fs + lsig_start + 64]

    # === WITH CFO correction ===
    # CFO phase at L-SIG center, relative to L-LTF center (fs+272)
    t_offset_sec = (lsig_start + 32 - 272) / F_SAMPLE
    phase_offset = 2 * np.pi * cfo_hz * t_offset_sec
    lsig_cfo = lsig_sym * np.exp(-1j * phase_offset)
    lsig_fft_cfo = np.fft.fft(lsig_cfo, 64)
    eq_cfo = np.array([lsig_fft_cfo[k] / H_new[k] for k in ACTIVE_SC])

    best_m_cfo = 0
    best_p_cfo = 0
    for phase_deg in np.arange(-180, 180, 1):
        eq_rot = eq_cfo * np.exp(-1j * np.deg2rad(phase_deg))
        hard = (eq_rot.real > 0).astype(int)
        got = ''.join(map(str, hard))
        m = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
        if m > best_m_cfo:
            best_m_cfo = m
            best_p_cfo = phase_deg
    ber_cfo = 1 - best_m_cfo / 48
    results_cfo.append((delta, ber_cfo, best_m_cfo, best_p_cfo))
    if ber_cfo < best_ber_cfo:
        best_ber_cfo = ber_cfo
        best_with_cfo = (delta, ber_cfo, lsig_cfo, eq_cfo, best_p_cfo, best_m_cfo)

    # === WITHOUT CFO correction ===
    lsig_fft_no = np.fft.fft(lsig_sym, 64)
    eq_no = np.array([lsig_fft_no[k] / H_new[k] for k in ACTIVE_SC])

    best_m_no = 0
    best_p_no = 0
    for phase_deg in np.arange(-180, 180, 1):
        eq_rot = eq_no * np.exp(-1j * np.deg2rad(phase_deg))
        hard = (eq_rot.real > 0).astype(int)
        got = ''.join(map(str, hard))
        m = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
        if m > best_m_no:
            best_m_no = m
            best_p_no = phase_deg
    ber_no = 1 - best_m_no / 48
    results_nocfo.append((delta, ber_no, best_m_no, best_p_no))
    if ber_no < best_ber_nocfo:
        best_ber_nocfo = ber_no
        best_no_cfo = (delta, ber_no, lsig_sym, eq_no, best_p_no, best_m_no)

    # === RAW ===
    hard_raw = (eq_no.real > 0).astype(int)
    got_raw = ''.join(map(str, hard_raw))
    m_raw = sum(1 for a, b in zip(got_raw, EXPECTED_BITS) if a == b)
    ber_raw = 1 - m_raw / 48
    results_raw.append((delta, ber_raw, m_raw))
    if ber_raw < best_ber_raw:
        best_ber_raw = ber_raw
        best_raw = (delta, ber_raw, hard_raw, m_raw)

    if delta % 4 == 0 or ber_cfo < 0.3 or ber_no < 0.3 or ber_raw < 0.3:
        print(f"  delta={delta:+3d}: L-SIG@{fs+lsig_start} BER_cfo={ber_cfo:.1%} BER_no_cfo={ber_no:.1%} BER_raw={ber_raw:.1%}")

# Print best
print("\n" + "=" * 70)
print("BEST RESULTS (with corrected offsets)")
print("=" * 70)

print(f"\nWITH CFO correction (phase search):")
delta_cfo, ber_cfo_b, lsig_cfo_b, eq_cfo_b, p_cfo_b, m_cfo_b = best_with_cfo
eq_rot = eq_cfo_b * np.exp(-1j * np.deg2rad(p_cfo_b))
bits = (eq_rot.real > 0).astype(int)
print(f"  delta={delta_cfo:+d} samples, BER={ber_cfo_b:.1%}")
print(f"  matches: {m_cfo_b}/48")
print(f"  bits: {''.join(map(str, bits))}")
print(f"  expected: {EXPECTED_BITS}")

print(f"\nWITHOUT CFO correction (phase search):")
delta_no, ber_no_b, lsig_no_b, eq_no_b, p_no_b, m_no_b = best_no_cfo
eq_rot = eq_no_b * np.exp(-1j * np.deg2rad(p_no_b))
bits = (eq_rot.real > 0).astype(int)
print(f"  delta={delta_no:+d} samples, BER={ber_no_b:.1%}")
print(f"  matches: {m_no_b}/48")
print(f"  bits: {''.join(map(str, bits))}")
print(f"  expected: {EXPECTED_BITS}")

print(f"\nRAW:")
delta_raw, ber_raw_b, bits_raw, m_raw_b = best_raw
print(f"  delta={delta_raw:+d} samples, BER={ber_raw_b:.1%}")
print(f"  matches: {m_raw_b}/48")
print(f"  bits: {''.join(map(str, bits_raw))}")
print(f"  expected: {EXPECTED_BITS}")

np.save('/tmp/p25_sweep_cfo.npy', np.array(results_cfo))
np.save('/tmp/p25_sweep_nocfo.npy', np.array(results_nocfo))
np.save('/tmp/p25_sweep_raw.npy', np.array(results_raw))

print("\n=== FINAL STATUS ===")
all_best_ber = min(best_ber_cfo, best_ber_nocfo, best_ber_raw)
all_best_matches = max(m_cfo_b, m_no_b, m_raw_b)
print(f"  Best BER: {all_best_ber:.1%}")
print(f"  Best matches: {all_best_matches}/48")
print(f"  Best L-SIG start sample: {320 + delta_no}")
