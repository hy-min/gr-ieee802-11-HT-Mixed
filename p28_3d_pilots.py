#!/usr/bin/env python
"""Phase 28.3d: The 48 expected bits are at the 48 DATA subcarriers (excluding 4 pilots).

Active SCs (52): 1..26 + 38..63
Pilots (4 of these): k = -21, -7, 7, 21 in 64-point notation
  = 64 + (-21) = 43 (NOT in 1..26)
  Wait: 64-point FFT has k = -32..-1, 0, 1..31
  k = -21 = subcarrier 11 (negative frequencies index 43, but typically referred to as -21)
  k = -7 = subcarrier 25
  k = 7 = subcarrier 39
  k = 21 = subcarrier 53
  So PILOTS at k = 11, 25, 39, 53 (1-indexed positive notation)

DATA SCs: 52 - 4 = 48 subcarriers (excluding 11, 25, 39, 53)
The 48 expected bits map 1:1 to data SCs in order k = 1, 2, ..., 10, 12, ..., 24, 26, 38, ..., 38, 40, ..., 52, 54, ..., 63
"""
import numpy as np

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'
PILOT_SC = [11, 25, 39, 53]
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]
print(f"DATA_SC ({len(DATA_SC)}): {DATA_SC}")


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


# Load
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
l_stf_start, _ = find_l_stf_region(iq, period=16)
fs = l_stf_start

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

# Use F0a (LTS0 only) as equalizer reference
F0a = F0[DATA_SC]  # 48 data SCs
F1a = F1[DATA_SC]
Havg = (F0a + F1a) / 2

eq = Fsig[DATA_SC] / Havg
# Global CPE
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

bits = (eq_rot.real > 0).astype(int)
got = ''.join(map(str, bits.tolist()))
matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
ber = 1 - matches / 48

print(f"\n=== After excluding pilot SCs (using DATA_SC = 48 SCs) ===")
print(f"Matches: {matches}/48 (BER={ber:.1%})")
print(f"  Got:      {got}")
print(f"  Expected: {EXPECTED_BITS}")

# Show per-SC details
print(f"\nPer-bit detail (data SCs only):")
for i, k in enumerate(DATA_SC):
    exp_bit = int(EXPECTED_BITS[i])
    got_bit = int(eq_rot[i].real > 0)
    correct = "OK" if exp_bit == got_bit else "ERR"
    p_deg = np.degrees(np.angle(eq_rot[i]))
    print(f"  DATA_SC[{i:2d}] k={k:2d}: real={eq_rot[i].real:+.3f} "
          f"imag={eq_rot[i].imag:+.3f} angle={p_deg:+7.1f}° "
          f"got={got_bit} exp={exp_bit} {correct}")

# Per-bit eq SNR
real_mean = np.mean(np.abs(eq_rot.real))
imag_std = np.std(eq_rot.imag)
snr_db = 20 * np.log10(real_mean / (imag_std + 1e-12))
print(f"\nL-SIG SNR (data SCs only): {snr_db:.1f} dB")
print(f"|H| mean: {np.mean(np.abs(Havg)):.2f}, std: {np.std(np.abs(Havg)):.2f}")
print(f"H phase std: {np.std(np.angle(Havg)):.3f} rad = {np.degrees(np.std(np.angle(Havg))):.1f}°")

# Best offset search on DATA_SC
print(f"\n=== Sweep offset with DATA_SC ===")
best_m = 0
best_off = 0
best_got = ''
for offset in range(-16, 17):
    lts0_s = fs + 176 + offset
    lts1_s = fs + 256 + offset
    sig_s = fs + 336 + offset
    if lts0_s < 0 or sig_s + 64 > len(iq):
        continue
    LTS0_t = iq[lts0_s:lts0_s+64]
    LTS1_t = iq[lts1_s:lts1_s+64]
    SIG_t = iq[sig_s:sig_s+64]
    F0_t = np.fft.fft(LTS0_t, 64)
    F1_t = np.fft.fft(LTS1_t, 64)
    Fsig_t = np.fft.fft(SIG_t, 64)
    F0a_t = F0_t[DATA_SC]
    F1a_t = F1_t[DATA_SC]
    Havg_t = (F0a_t + F1a_t) / 2
    eq_t = Fsig_t[DATA_SC] / Havg_t
    cpe_t = np.angle(np.sum(eq_t))
    eq_t_rot = eq_t * np.exp(-1j * cpe_t)
    bits_t = (eq_t_rot.real > 0).astype(int)
    got_t = ''.join(map(str, bits_t.tolist()))
    m_t = sum(1 for a, b in zip(got_t, EXPECTED_BITS) if a == b)
    if m_t > best_m:
        best_m = m_t
        best_off = offset
        best_got = got_t

print(f"Best offset={best_off}: {best_m}/48 matches")
print(f"  Got: {best_got}")

# Decision feedback: use F0a only
print(f"\n=== F0a only (no F1a average) ===")
eq_f0 = Fsig[DATA_SC] / F0a
cpe_f0 = np.angle(np.sum(eq_f0))
eq_f0_rot = eq_f0 * np.exp(-1j * cpe_f0)
bits_f0 = (eq_f0_rot.real > 0).astype(int)
got_f0 = ''.join(map(str, bits_f0.tolist()))
m_f0 = sum(1 for a, b in zip(got_f0, EXPECTED_BITS) if a == b)
print(f"F0a only: {m_f0}/48 matches")
print(f"  Got: {got_f0}")

# F1a only
eq_f1 = Fsig[DATA_SC] / F1a
cpe_f1 = np.angle(np.sum(eq_f1))
eq_f1_rot = eq_f1 * np.exp(-1j * cpe_f1)
bits_f1 = (eq_f1_rot.real > 0).astype(int)
got_f1 = ''.join(map(str, bits_f1.tolist()))
m_f1 = sum(1 for a, b in zip(got_f1, EXPECTED_BITS) if a == b)
print(f"F1a only: {m_f1}/48 matches")
print(f"  Got: {got_f1}")

# Try also: re-arrange order of expected bits
print(f"\n=== Maybe expected_bits are in different order (encoded-then-interleaved) ===")
print("For now, hard-decision at BPSK gives 32.3 dB SNR. Let's run the viterbi decoder.")
