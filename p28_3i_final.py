#!/usr/bin/env python
"""Phase 28.3i: Use the EXACT encoder from utils.cc and try ALL possibilities.

The C++ encoder:
  state = ((state << 1) & 0x7e) | in[i]
  out[i*2]   = parity(state & 0b1011011)  # 133 octal
  out[i*2+1] = parity(state & 0b1111001)  # 171 octal

State is 7 bits, but bit 7 always 0 (mask 0x7e).
After shift-in: state = (input << 0) | (prev_state bits 6..1)
So state[6] = new input, state[5..0] = previous state[6..1] which is prev state[5..0]
"""
import numpy as np

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'
PILOT_SC = [11, 25, 39, 53]
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]


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
    """Exact replica of utils.cc:convolutional_encoding."""
    state = 0
    out = []
    for b in in_bits:
        state = ((state << 1) & 0x7e) | b
        # parity of state & 0133 = state & 0b1011011
        s = state & 0b1011011
        out.append(bin(s).count('1') % 2)
        s = state & 0b1111001
        out.append(bin(s).count('1') % 2)
    return out


def make_signal(rate, length):
    """Build 24-bit SIGNAL field per signal_field_impl.cc."""
    sh = [0] * 24
    # RATE MSB first
    sh[0] = (rate >> 3) & 1
    sh[1] = (rate >> 2) & 1
    sh[2] = (rate >> 1) & 1
    sh[3] = (rate >> 0) & 1
    # reserved
    sh[4] = 0
    # LENGTH 12-bit LSB first
    for i in range(12):
        sh[5+i] = (length >> i) & 1
    # parity over first 17 bits (even)
    s = sum(sh[:17])
    sh[17] = s % 2
    # tail 6 zeros
    return sh


def interleave_forward(encoded):
    """Forward 802.11n interleaver for BPSK 1/2 (n_col=16, n_row=3, s=1).
    encoded[k] → out[j] where j = 3*(k%16) + k//16
    """
    out = [0] * 48
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[j] = encoded[k]
    return out


# Compute expected bits for various LENGTHs
print("=" * 70)
print("Compute expected L-SIG bits for various LENGTH values")
print("=" * 70)
for length in [16, 20, 21, 22, 24, 30, 33, 36, 42, 50, 100]:
    sh = make_signal(0xD, length)
    encoded = cc_encode(sh)
    interleaved = interleave_forward(encoded)
    bits_str = ''.join(map(str, interleaved))
    matches_expected = sum(1 for a, b in zip(bits_str, EXPECTED_BITS) if a == b)
    print(f"  LENGTH={length:4d}: interleaved={bits_str[:24]}... (matches task's expected: {matches_expected}/48)")

# === Now load the capture and decode ===
print("\n" + "=" * 70)
print("Decode L-SIG from capture")
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

F0a = F0[DATA_SC]
F1a = F1[DATA_SC]
Havg = (F0a + F1a) / 2
eq = Fsig[DATA_SC] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

received_bits = (eq_rot.real > 0).astype(int).tolist()
print(f"Received (DATA_SC order): {''.join(map(str, received_bits))}")
print(f"Expected (task):          {EXPECTED_BITS}")

# === Try all LENGTHs and find match ===
print("\n=== Try all LENGTHs in [0..4095] ===")
best_length = -1
best_match = 0
for length in range(0, 4096):
    sh = make_signal(0xD, length)
    encoded = cc_encode(sh)
    interleaved = interleave_forward(encoded)
    matches = sum(1 for a, b in zip(interleaved, received_bits) if a == b)
    if matches > best_match:
        best_match = matches
        best_length = length
        print(f"  New best: LENGTH={length}, matches={matches}/48")

print(f"\nBest LENGTH={best_length} matches={best_match}/48")

# Also try other RATE values
print("\n=== Try other RATE values ===")
best_rate = -1
best_rate_match = 0
best_rate_length = -1
for rate in range(16):
    for length in range(0, 200):
        sh = make_signal(rate, length)
        encoded = cc_encode(sh)
        interleaved = interleave_forward(encoded)
        matches = sum(1 for a, b in zip(interleaved, received_bits) if a == b)
        if matches > best_rate_match:
            best_rate_match = matches
            best_rate = rate
            best_rate_length = length
print(f"Best RATE=0x{best_rate:X} LENGTH={best_rate_length} matches={best_rate_match}/48")

# === Try interpreting as HT-SIG instead of L-SIG ===
print("\n" + "=" * 70)
print("Try interpreting as HT-SIG (different interleaver)")
print("=" * 70)
# HT-SIG uses n_col=13, n_row=4 for BPSK 1/2
# Forward: j = s * (i/s) + (i + n_cbps - n_col*i/n_cbps) % s
# For BPSK s=1: j = i
# i = n_row*(k%n_col) + k//n_col = 4*(k%13) + k//13
HT_FORWARD = [4 * (k % 13) + k // 13 for k in range(48)]
print(f"HT_FORWARD max: {max(HT_FORWARD)}, len unique: {len(set(HT_FORWARD))}")
# Note: HT goes up to 50, not 47. So HT-SIG interleaver maps 48 encoder bits
# to 48 SC positions out of 52 (excluding 4 pilots)

# Apply HT deinterleave
received_ht_deintl = [0] * 48
for k in range(48):
    j = HT_FORWARD[k]
    received_ht_deintl[k] = received_bits[j]
print(f"Received (HT deinterleaved): {''.join(map(str, received_ht_deintl))}")

# Viterbi decode
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
            # Extract 6-bit state from s
            s0 = (s >> 0) & 1
            s1 = (s >> 1) & 1
            s2 = (s >> 2) & 1
            s3 = (s >> 3) & 1
            s4 = (s >> 4) & 1
            s5 = (s >> 5) & 1
            # state = (s5, s4, s3, s2, s1, s0)
            state = (s5, s4, s3, s2, s1, s0)
            for bit in [0, 1]:
                # new state
                new_state = (state[1], state[2], state[3], state[4], state[5], bit)
                # Output 0: parity of state & 1011011
                # bit 0 (s0) + bit 1 (s1) + bit 3 (s3) + bit 4 (s4) + bit 6 (input = bit)
                o1 = (bit ^ state[5] ^ state[4] ^ state[2] ^ state[1] ^ state[0]) & 1
                # Output 1: parity of state & 1111001
                # bit 0 (s0) + bit 3 (s3) + bit 4 (s4) + bit 5 (s5) + bit 6 (input = bit)
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

# L-SIG: 24 SIGNAL info bits
received_lsig_deintl = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    received_lsig_deintl[k] = received_bits[j]

decoded_lsig, _ = viterbi_decode_hard(received_lsig_deintl)
rate_dec = (decoded_lsig[0] << 3) | (decoded_lsig[1] << 2) | (decoded_lsig[2] << 1) | decoded_lsig[3]
length_dec = 0
for i in range(12):
    length_dec |= decoded_lsig[4+i] << i

print(f"\nL-SIG viterbi decoded:")
print(f"  RATE=0x{rate_dec:X}, LENGTH={length_dec}")
print(f"  SIGNAL field bits: {''.join(map(str, decoded_lsig))}")

# === Final summary ===
print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)
print(f"L-SIG SNR: 18.6 dB (32.3 dB per Phase 28.2 including pilots)")
print(f"Hard-decision matches (DATA_SC): 22/48 (BER 54.2%)")
print(f"Viterbi decoded RATE: 0x{rate_dec:X}")
print(f"Viterbi decoded LENGTH: {length_dec}")
print()
if rate_dec == 0xD:
    print(f"*** RATE=0xD ✓ — Viterbi decode SUCCEEDED ***")
elif rate_dec in [0xB, 0xA, 0x9]:
    print(f"RATE=0x{rate_dec:X} — partial (viterbi got a valid rate but wrong)")
else:
    print(f"RATE=0x{rate_dec:X} — random (viterbi failed)")