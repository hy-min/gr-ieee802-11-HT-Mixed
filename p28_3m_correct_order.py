#!/usr/bin/env python
"""Phase 28.3m: Use the CORRECT kHeader48Sc order from frame_equalizer_impl.cc.

The TX SC order is NEGATIVE-FIRST:
  kHeader48Sc = [-26, -25, -24, -23, -22, -20, -19, ..., -1, 1, 2, ..., 26]
  (skipping pilots at SC -21, -7, +7, +21)

The corresponding FFT bins (kHeader48Bin):
  Negative freq (SC -26 to -1): bins 38-63
  Positive freq (SC +1 to +26): bins 1-26
"""
import numpy as np

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'

# From frame_equalizer_impl.cc
kHeader48Sc = [
    -26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26
]
kHeader48Bin = [
    38, 39, 40, 41, 42,         # SC -26 to -22
    44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,  # SC -20 to -8 (skip -7 pilot)
    58, 59, 60, 61, 62, 63,     # SC -6 to -1
    1, 2, 3, 4, 5, 6,           # SC +1 to +6
    8, 9,10,11,12,13,14,15,16,17,18,19,20,  # SC +8 to +20 (skip +7 pilot)
    22,23,24,25,26              # SC +22 to +26
]


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


def cc_encode(in_bits):
    state = 0
    out = []
    for b in in_bits:
        state = ((state << 1) & 0x7e) | b
        out.append(bin(state & 0b1011011).count('1') % 2)
        out.append(bin(state & 0b1111001).count('1') % 2)
    return out


def make_signal(rate, length):
    sh = [0] * 24
    sh[0] = (rate >> 3) & 1
    sh[1] = (rate >> 2) & 1
    sh[2] = (rate >> 1) & 1
    sh[3] = (rate >> 0) & 1
    sh[4] = 0
    for i in range(12):
        sh[5+i] = (length >> i) & 1
    s = sum(sh[:17])
    sh[17] = s % 2
    return sh


def interleave_forward(encoded):
    out = [0] * 48
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[j] = encoded[k]
    return out


# === Compute L-SIG for LENGTH=20 with neg-first SC order ===
sh = make_signal(0xD, 20)
enc = cc_encode(sh)
intl = interleave_forward(enc)
print(f"L-SIG for LENGTH=20, interleaved (encoder order):")
print(f"  interleaved[j] where j = 3*(k%16) + k//16")
print(f"  {''.join(map(str, intl))}")

# Now the TX chain in mapper:
# 1. interleaved[0..47] are mapped to FFT bins kHeader48Bin[0..47]
# 2. interleaved[0] goes to bin 38 (SC -26)
# 3. interleaved[1] goes to bin 39 (SC -25)
# ...
# So the bit at SC order index i (in kHeader48Sc order) = interleaved[i]
# Expected bits at SCs in kHeader48Sc order = interleaved (same order)
print(f"\nExpected at SCs (kHeader48Sc order): {''.join(map(str, intl))}")
print(f"EXPECTED_BITS (task):                 {EXPECTED_BITS}")
match = sum(1 for a, b in zip(intl, EXPECTED_BITS) if a == b)
print(f"Match: {match}/48")

# === Load capture and decode in correct SC order ===
print("\n" + "=" * 70)
print("Decode L-SIG with correct kHeader48Sc order")
print("=" * 70)
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
l_stf_start, _ = find_l_stf_region(iq, period=16)
fs = l_stf_start

lts0_start = fs + 176
lts1_start = fs + 256
sig_start = fs + 336
LTS0 = iq[lts0_start:lts0_start+64]
LTS1 = iq[lts1_start:lts1_start+64]
SIG = iq[sig_start:sig_start+64]

F0 = np.fft.fft(LTS0, 64)
F1 = np.fft.fft(LTS1, 64)
Fsig = np.fft.fft(SIG, 64)

# Use kHeader48Bin to extract SCs in correct order
F0a = F0[kHeader48Bin]  # 48 SCs in TX order
F1a = F1[kHeader48Bin]
Havg = (F0a + F1a) / 2
eq = Fsig[kHeader48Bin] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

received_bits = (eq_rot.real > 0).astype(int).tolist()
print(f"Received (kHeader48Bin order): {''.join(map(str, received_bits))}")
print(f"Expected (L=20):              {''.join(map(str, intl))}")

matches = sum(1 for a, b in zip(received_bits, intl) if a == b)
print(f"Matches: {matches}/48")

# === Try all LENGTHs with correct SC order ===
print("\n=== Sweep LENGTH with correct SC order ===")
best_length = -1
best_match = 0
for length in range(0, 4096):
    sh = make_signal(0xD, length)
    enc = cc_encode(sh)
    intl = interleave_forward(enc)
    matches = sum(1 for a, b in zip(intl, received_bits) if a == b)
    if matches > best_match:
        best_match = matches
        best_length = length
        if matches > 32:
            print(f"  LENGTH={length}: matches={matches}/48")
print(f"\nBest LENGTH={best_length}, matches={best_match}/48")

# === Show per-bit detail ===
print(f"\n=== Per-bit detail (best config) ===")
sh = make_signal(0xD, best_length)
enc = cc_encode(sh)
intl = interleave_forward(enc)
for i in range(48):
    sc = kHeader48Sc[i]
    bin_idx = kHeader48Bin[i]
    eq_val = eq_rot[i]
    got = int(eq_val.real > 0)
    exp = intl[i]
    mark = "OK" if got == exp else "ERR"
    print(f"  i={i:2d} SC={sc:+3d} bin={bin_idx:2d}: "
          f"real={eq_val.real:+.3f} imag={eq_val.imag:+.3f} "
          f"got={got} exp={exp} {mark}")

# === Run viterbi decoder ===
print(f"\n=== Viterbi decode ===")
def viterbi_decode_hard(rx_bits, n_steps=24):
    INF = float('inf')
    n_states = 64
    pm = np.full(n_states, INF)
    pm[0] = 0.0
    prev_state = np.zeros((n_steps, n_states), dtype=int)
    prev_bit = np.zeros((n_steps, n_states), dtype=int)
    for t in range(n_steps):
        new_pm = np.full(n_states, INF)
        r0 = rx_bits[2*t]
        r1 = rx_bits[2*t+1]
        for s in range(n_states):
            if pm[s] == INF:
                continue
            state = tuple((s >> (5-i)) & 1 for i in range(6))
            for bit in [0, 1]:
                new_state = (state[1], state[2], state[3], state[4], state[5], bit)
                o1 = (bit ^ state[5] ^ state[4] ^ state[2] ^ state[1] ^ state[0]) & 1
                o2 = (bit ^ state[5] ^ state[4] ^ state[3] ^ state[0]) & 1
                metric = (o1 != r0) + (o2 != r1)
                new_metric = pm[s] + metric
                new_s = (new_state[0] << 5) | (new_state[1] << 4) | (new_state[2] << 3) | \
                        (new_state[3] << 2) | (new_state[4] << 1) | new_state[5]
                if new_metric < new_pm[new_s]:
                    new_pm[new_s] = new_metric
                    prev_state[t][new_s] = s
                    prev_bit[t][new_s] = bit
        pm = new_pm
    best_state = int(np.argmin(pm))
    decoded = np.zeros(n_steps, dtype=int)
    s = best_state
    for t in range(n_steps - 1, -1, -1):
        decoded[t] = prev_bit[t][s]
        s = prev_state[t][s]
    return decoded, best_state

# Deinterleave received bits using DEINTL_INV_48
DEINTL_INV = [
    0, 16, 32, 1, 17, 33, 2, 18, 34, 3, 19, 35, 4, 20, 36, 5,
    21, 37, 6, 22, 38, 7, 23, 39, 8, 24, 40, 9, 25, 41, 10, 26,
    42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47
]
received_deintl = [0] * 48
for k in range(48):
    received_deintl[k] = received_bits[DEINTL_INV[k]]
print(f"Received deinterleaved: {''.join(map(str, received_deintl))}")

decoded, _ = viterbi_decode_hard(received_deintl)
rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
length = 0
for i in range(12):
    length |= decoded[4+i] << i
print(f"Decoded SIGNAL: RATE=0x{rate:X}, LENGTH={length} bytes")
print(f"  Bits: {''.join(map(str, decoded))}")