#!/usr/bin/env python
"""Phase 28.3b: Diagnose why SNR is high (32.3 dB) but BER is high (47.9%).

Key observation: eq_real and eq_imag have similar magnitude AND same sign.
This means there's a systematic rotation, not noise. CPE may be correct
but the L-SIG constellation might be BPSK on a different axis, OR there's
a frequency offset, OR the L-SIG vs L-LTF timing is off by a sub-sample.

Plan:
1. Show constellation scatter
2. Try multiple frame start offsets to find a low-BER config
3. Try the L-SIG DATA with 32-sample cyclic shift (HT-SIG1 starts at LSIG+64)
4. Try decoding as L-SIG in HT-greenfield mode (different location)
"""
import numpy as np
import sys
import time

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


# ----- Step 1: Load and find frame start -----
print("=" * 70)
print("Step 1: Load + find frame start")
print("=" * 70)
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
print(f"Loaded {len(iq)} samples")
l_stf_start, l_stf_end = find_l_stf_region(iq, period=16)
print(f"L-STF: {l_stf_start} to {l_stf_end}, fs={l_stf_start}")
fs = l_stf_start

# ----- Step 2: Best offset from Phase 28.2 (offset=0 was best by SNR, but we need best by BER) -----
# Try every LTF0 offset from -16 to +16, and ALSO try every LTF0 starting sample
print("\n" + "=" * 70)
print("Step 2: Exhaustive search for low-BER L-LTF0/L-SIG position")
print("=" * 70)
print("Trying offsets -32..+32 with all layouts")

best_ber = 1.0
best_cfg = None
results = []

# L-SIG is at fs + 336 by spec, L-LTF1 is at fs + 256
# L-LTF0 CP is at fs + 160-175, L-LTF0 DATA at fs + 176-239
# Try: shift LTS0_start, keep LTS1 = LTS0+80, LSIG = LTS1+80
for lts0_off in range(-32, 33):
    lts0_start = fs + 176 + lts0_off
    lts1_start = lts0_start + 80  # LTS1 immediately after LTS0 (no GI)
    sig_start = lts1_start + 80   # SIG immediately after LTS1 (no GI)

    if lts0_start < 0 or sig_start + 64 > len(iq):
        continue

    LTS0 = iq[lts0_start:lts0_start+64]
    LTS1 = iq[lts1_start:lts1_start+64]
    SIG = iq[sig_start:sig_start+64]

    F0 = np.fft.fft(LTS0, 64)
    F1 = np.fft.fft(LTS1, 64)
    Fsig = np.fft.fft(SIG, 64)

    # Use F0a (just LTS0, not averaged) as equalizer
    F0a = F0[ACTIVE_SC]
    eq = Fsig[ACTIVE_SC] / F0a
    cpe = np.angle(np.sum(eq))
    eq_rot = eq * np.exp(-1j * cpe)
    bits = (eq_rot.real > 0).astype(int)
    got = ''.join(map(str, bits.tolist()))
    matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
    ber = 1 - matches / 48
    real_mean = float(np.mean(np.abs(eq.real)))
    imag_std = float(np.std(eq.imag))
    snr_db = 20 * np.log10(real_mean / (imag_std + 1e-12))
    if matches > (1 - best_ber) * 48:
        best_ber = ber
        best_cfg = (lts0_off, lts0_start, lts1_start, sig_start, got, snr_db, cpe)
    results.append((lts0_off, matches, ber, snr_db, cpe))

# Print best 10
print("\n--- Top 10 by matches ---")
for off, m, b, s, c in sorted(results, key=lambda x: -x[1])[:10]:
    print(f"  LTS0_off={off:+3d} matches={m:2d}/48 BER={b:6.1%} "
          f"SNR={s:5.1f}dB CPE={np.degrees(c):+6.1f}deg")

if best_cfg:
    print(f"\nBest: LTS0_off={best_cfg[0]:+d} at sample {best_cfg[1]}")
    print(f"  LTS1: {best_cfg[2]}")
    print(f"  LSIG: {best_cfg[3]}")
    print(f"  Got:      {best_cfg[4]}")
    print(f"  Expected: {EXPECTED_BITS}")
    print(f"  SNR={best_cfg[5]:.1f}dB CPE={np.degrees(best_cfg[6]):.1f}deg")

# ----- Step 3: Try non-equal-spaced LTS0/LTS1/LSIG positions -----
print("\n" + "=" * 70)
print("Step 3: Try different LTS0/LTS1/LSIG spacings")
print("=" * 70)
# Standard 802.11n: L-STF(160) + LTS0(64) + LTS1(64) + LSIG(64) = 352
# But there are 16-sample GIs between them:
# L-STF(160) + GI(16) + LTS0(64) + GI(16) + LTS1(64) + GI(16) + LSIG(64) = 400
# So LTS0 at fs+176, LTS1 at fs+256 (with GIs), LSIG at fs+336 (with GI)
# OR without GIs: LTS0 at fs+160, LTS1 at fs+224, LSIG at fs+288

# Try multiple spacings
spacings = {
    'spec_with_GI (176,256,336)': (176, 256, 336),
    'no_GI (160,224,288)': (160, 224, 288),
    'spec_alternate (176,240,304)': (176, 240, 304),  # 64-sample GI
    'LTS0+16, LTS1+96, LSIG+176': (16, 96, 176),  # from Phase 28.2
}

for sname, (s0, s1, slsig) in spacings.items():
    for off in range(-4, 5):
        lts0_start = fs + s0 + off
        lts1_start = fs + s1 + off
        sig_start = fs + slsig + off
        if lts0_start < 0 or sig_start + 64 > len(iq):
            continue
        LTS0 = iq[lts0_start:lts0_start+64]
        LTS1 = iq[lts1_start:lts1_start+64]
        SIG = iq[sig_start:sig_start+64]
        F0 = np.fft.fft(LTS0, 64)
        F1 = np.fft.fft(LTS1, 64)
        Fsig = np.fft.fft(SIG, 64)
        F0a = F0[ACTIVE_SC]
        F1a = F1[ACTIVE_SC]
        # Try F0a, F1a, and (F0a+F1a)/2
        for H_name, H_used in [('F0a', F0a), ('F1a', F1a), ('avg', (F0a+F1a)/2)]:
            eq = Fsig[ACTIVE_SC] / H_used
            cpe = np.angle(np.sum(eq))
            eq_rot = eq * np.exp(-1j * cpe)
            bits = (eq_rot.real > 0).astype(int)
            got = ''.join(map(str, bits.tolist()))
            matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
            if matches >= 35:  # only print if reasonable
                print(f"  {sname} off={off:+d} {H_name}: matches={matches}/48 "
                      f"got={got[:24]}...")

# ----- Step 4: Show constellation for best config -----
print("\n" + "=" * 70)
print("Step 4: Show constellation for best config (visualize issue)")
print("=" * 70)
if best_cfg:
    _, lts0_start, lts1_start, sig_start, got, snr_db, cpe = best_cfg
    LTS0 = iq[lts0_start:lts0_start+64]
    SIG = iq[sig_start:sig_start+64]
    F0 = np.fft.fft(LTS0, 64)
    Fsig = np.fft.fft(SIG, 64)
    F0a = F0[ACTIVE_SC]
    eq = Fsig[ACTIVE_SC] / F0a
    print(f"Raw eq values (no CPE):")
    print(f"  Mean real: {eq.real.mean():.3f}, mean imag: {eq.imag.mean():.3f}")
    print(f"  Std real:  {eq.real.std():.3f}, std imag:  {eq.imag.std():.3f}")
    print(f"  CPE angle: {np.degrees(np.angle(np.sum(eq))):.1f} deg")
    # Now rotate by 90, 180, 270 deg
    for deg in [0, 30, 45, 60, 90, 135, 180]:
        eq_rot = eq * np.exp(-1j * np.radians(deg))
        bits = (eq_rot.real > 0).astype(int)
        got = ''.join(map(str, bits.tolist()))
        matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
        print(f"  Rotate {deg:3d}°: matches={matches}/48, got={got[:24]}...")

# ----- Step 5: Per-SC phase correction (BPSK rotated to real axis) -----
print("\n" + "=" * 70)
print("Step 5: Per-SC phase correction (real axis = BPSK)")
print("=" * 70)
# In BPSK, all symbols are on real axis. After CPE, residuals can still
# exist. Try: project eq onto real axis (sign(real)), or rotate each
# eq point to nearest real axis.
if best_cfg:
    _, lts0_start, lts1_start, sig_start, got, snr_db, cpe = best_cfg
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

    # Method 1: BPSK with common rotation
    cpe = np.angle(np.sum(eq))
    eq_rot = eq * np.exp(-1j * cpe)
    bits = (eq_rot.real > 0).astype(int)
    got = ''.join(map(str, bits.tolist()))
    matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
    print(f"Common CPE: matches={matches}/48")

    # Method 2: BPSK per-SC fine phase correction
    # Each eq point should be on real axis. Rotate each by -angle(eq) so it's real.
    eq_persc = np.abs(eq) * np.sign(eq.real * np.cos(np.angle(eq)) + eq.imag * np.sin(np.angle(eq)))
    bits_persc = (eq_persc.real > 0).astype(int)
    got_persc = ''.join(map(str, bits_persc.tolist()))
    matches_persc = sum(1 for a, b in zip(got_persc, EXPECTED_BITS) if a == b)
    print(f"Per-SC phase: matches={matches_persc}/48")

    # Method 3: BPSK with decision-directed phase (use known bits)
    # If we knew the bits, we could re-rotate. We don't, but try voting:
    # For each eq point, if real > 0, set sign(real), else -real
    eq_decision = np.where(eq.real >= 0, np.abs(eq), -np.abs(eq))
    eq_decision_imag = np.zeros_like(eq_decision)  # zero imag (BPSK)
    bits_dd = (eq_decision.real > 0).astype(int)
    got_dd = ''.join(map(str, bits_dd.tolist()))
    matches_dd = sum(1 for a, b in zip(got_dd, EXPECTED_BITS) if a == b)
    print(f"Decision-directed: matches={matches_dd}/48")

    # Method 4: use F0a only, no F1a
    eq_f0 = Fsig[ACTIVE_SC] / F0a
    cpe_f0 = np.angle(np.sum(eq_f0))
    eq_f0_rot = eq_f0 * np.exp(-1j * cpe_f0)
    bits_f0 = (eq_f0_rot.real > 0).astype(int)
    got_f0 = ''.join(map(str, bits_f0.tolist()))
    matches_f0 = sum(1 for a, b in zip(got_f0, EXPECTED_BITS) if a == b)
    print(f"F0a only CPE: matches={matches_f0}/48")

    # Method 5: use F1a only
    eq_f1 = Fsig[ACTIVE_SC] / F1a
    cpe_f1 = np.angle(np.sum(eq_f1))
    eq_f1_rot = eq_f1 * np.exp(-1j * cpe_f1)
    bits_f1 = (eq_f1_rot.real > 0).astype(int)
    got_f1 = ''.join(map(str, bits_f1.tolist()))
    matches_f1 = sum(1 for a, b in zip(got_f1, EXPECTED_BITS) if a == b)
    print(f"F1a only CPE: matches={matches_f1}/48")

# ----- Step 6: Try assuming LTS0=LTS1 (cyclic prefix) -----
# In 802.11n, L-LTF is two repetitions of 64-sample LTS with 16-sample GI.
# If we treat LTS0+LTS1 as a single 128-sample (LTS0 at 0..63, LTS1 at 64..127),
# then the second LTS is a COPY of the first.
# So |F0| should equal |F1| (same magnitude spectrum).
print("\n" + "=" * 70)
print("Step 6: Check if LTS0 ≈ LTS1 (cyclic repetition)")
print("=" * 70)
lts0_start = fs + 176
lts1_start = fs + 256
LTS0 = iq[lts0_start:lts0_start+64]
LTS1 = iq[lts1_start:lts1_start+64]
F0 = np.fft.fft(LTS0, 64)
F1 = np.fft.fft(LTS1, 64)
print(f"|F0 - F1| mean: {np.abs(F0 - F1).mean():.3f}")
print(f"|F0| mean: {np.abs(F0).mean():.3f}, |F1| mean: {np.abs(F1).mean():.3f}")
print(f"Correlation F0,F1: {np.abs(np.corrcoef(F0.real, F1.real)[0,1]):.3f}")
# Per-SC phase difference
phase_diff = np.angle(F0 * np.conj(F1))
print(f"Phase diff mean: {np.degrees(phase_diff[ACTIVE_SC].mean()):.1f} deg")
print(f"Phase diff std:  {np.degrees(phase_diff[ACTIVE_SC].std()):.1f} deg")
