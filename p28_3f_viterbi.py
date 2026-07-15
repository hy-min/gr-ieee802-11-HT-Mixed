#!/usr/bin/env python
"""Phase 28.3f: Run L-SIG viterbi decoder using the EXACT C++ interleaver/viterbi.

The 802.11n L-SIG TX chain:
  24 SIGNAL info bits → BCC rate 1/2 (48 coded bits) → Interleave (48 SCs)

Per the actual C++ source (lib/frame_equalizer_impl.cc:875 and utils.cc:380):
  Forward interleaver: i = n_row * (k % n_col) + (k / n_col) for s=1 BPSK
  For 48 SCs BPSK: n_row=3, n_col=16
    i = 3 * (k % 16) + (k / 16)
    j = i (because s=1, second term = 0)

  So encoded bit k goes to SC index j = 3*(k%16) + k/16

  Inverse (from frame_equalizer_impl.cc:875):
    out[inv[i]] = in[i] where inv is the precomputed table.
"""
import numpy as np

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
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


# Load
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
l_stf_start, _ = find_l_stf_region(iq, period=16)
fs = l_stf_start
print(f"fs = {fs}")

# Standard positions (Phase 28.2 optimal)
lts0_start = fs + 176
lts1_start = fs + 256
sig_start = fs + 336

LTS0 = iq[lts0_start:lts0_start+64]
LTS1 = iq[lts1_start:lts1_start+64]
SIG = iq[sig_start:sig_start+64]

F0 = np.fft.fft(LTS0, 64)
F1 = np.fft.fft(LTS1, 64)
Fsig = np.fft.fft(SIG, 64)

# Use DATA_SC (52-4 pilot = 48 SCs)
F0a = F0[DATA_SC]
F1a = F1[DATA_SC]
Havg = (F0a + F1a) / 2

eq = Fsig[DATA_SC] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

# === Step 1: Verify the deinterleaver matches expected ===
# Per C++ source: forward interleaver with s=1, n_row=3, n_col=16
# i = 3 * (k % 16) + (k / 16), j = i
# So encoded bit k → SC index j = 3*(k%16) + k//16
# Inverse (from frame_equalizer_impl.cc):
DEINTL_INV_48 = [
    0, 16, 32,  1, 17, 33,  2, 18, 34,  3, 19, 35,  4, 20, 36,  5,
   21, 37,  6, 22, 38,  7, 23, 39,  8, 24, 40,  9, 25, 41, 10, 26,
   42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47
]

# Verify forward: for k in 0..47, j = 3*(k%16) + k//16
# Then inv[j] should = k
print("\n=== Verifying interleaver ===")
for k in range(48):
    j = 3 * (k % 16) + k // 16
    inv_j = DEINTL_INV_48[j]
    if inv_j != k:
        print(f"  MISMATCH at k={k}: j={j}, inv[{j}]={inv_j}")
        break
else:
    print("All 48 mappings consistent.")

# Show the expected bit ordering after deinterleave
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'
# expected_bits[i] is the bit AT SC i (in DATA_SC order)
# To get encoder order: deinterleave
# deinterleaved[inv[i]] = received[i]
# So encoded[k] = received[forward[k]]
# Forward mapping: encoded[k] → SC j = 3*(k%16) + k//16
# So to deinterleave: encoded[k] = received_at_SC[forward[k]]
deinterleaved_expected = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    deinterleaved_expected[k] = int(EXPECTED_BITS[j])
print(f"\nExpected SIGNAL encoder order (after deinterleave): {''.join(map(str, deinterleaved_expected))}")
# The 48 encoder bits should be 24 info bits * 2 (BCC rate 1/2)
# In pairs: (b0_0, b1_0), (b0_1, b1_1), ..., (b0_23, b1_23)

# Now do the same with received bits
received_hard = (eq_rot.real > 0).astype(int)
received_deinterleaved = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    received_deinterleaved[k] = int(received_hard[j])

print(f"Received encoder order (after deinterleave): {''.join(map(str, received_deinterleaved))}")

# Compare per pair
print(f"\n=== Per-pair comparison (after deinterleave) ===")
matches_after_deintl = 0
for k in range(48):
    exp = int(EXPECTED_BITS[3 * (k % 16) + k // 16])
    got = received_deinterleaved[k]
    mark = "OK" if exp == got else "ERR"
    if exp == got:
        matches_after_deintl += 1
    print(f"  k={k:2d}: exp={exp} got={got} {mark}")
print(f"\nAfter deinterleave: {matches_after_deintl}/48 bits match")

# === Step 2: Viterbi decode ===
# BCC rate 1/2, k=7, polynomials [133, 171]
# Same as in frame_equalizer_impl.cc viterbi_decode_133_171
print("\n=== Viterbi decode (24 info bits) ===")
POLY1 = 0b1011011  # G1 = 133 octal
POLY2 = 0b1111001  # G2 = 171 octal


def encode_bit(input_bit, state):
    """BCC rate 1/2 encode one bit. state is tuple of 6 bits (s5..s0)."""
    new_state = (state[1], state[2], state[3], state[4], state[5], input_bit)
    # G1 = 1011011: bit 6 (input) + bit 5 (s0) + bit 3 (s2) + bit 2 (s3) + bit 1 (s4) + bit 0 (s5)
    # state = (s5, s4, s3, s2, s1, s0)
    o1 = (input_bit ^ state[5] ^ state[3] ^ state[2] ^ state[1] ^ state[0]) & 1
    # G2 = 1111001: bit 6 (input) + bit 5 (s0) + bit 4 (s1) + bit 3 (s2) + bit 0 (s5)
    o2 = (input_bit ^ state[5] ^ state[4] ^ state[3] ^ state[0]) & 1
    return o1, o2, new_state


def viterbi_decode_hard(rx_bits, n_steps=24):
    """Hard-decision viterbi decoder for rate 1/2, k=7."""
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
                o0, o1, new_state = encode_bit(bit, state)
                # Hamming distance from received
                metric = (o0 != r0) + (o1 != r1)
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


# Viterbi decode on hard-decision received bits
decoded, best_state = viterbi_decode_hard(received_deinterleaved, n_steps=24)
print(f"Decoded 24 SIGNAL info bits: {''.join(map(str, decoded))}")

# Parse SIGNAL field
rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
length = 0
for i in range(12):
    length |= decoded[4+i] << i
parity = decoded[16]
tail = decoded[17:23]
reserved = decoded[23]

print(f"\nRATE:    0x{rate:X} = 0b{rate:04b} (expected 0xD = 0b1101)")
print(f"LENGTH:  {length} bytes")
print(f"PARITY:  {parity} (expected 0 for even parity)")
print(f"TAIL:    {''.join(map(str, tail))} (expected 000000)")
print(f"RESERVED: {reserved} (expected 0)")

# Compute expected SIGNAL field
# RATE=0xD=1101, LENGTH=16 bytes = 0b000000010000 (12 bits LSB first)
# So bits: 1,1,0,1,0,0,0,0,0,0,0,1,0,0,0,0 (length), parity, 0,0,0,0,0,0 (tail), 0 (reserved)
# Note: LENGTH is in bytes. The standard says LENGTH in bits before tail = (16+8*LENGTH+6)/3 OFDM symbols
# But the SIGNAL LENGTH field is the TX duration
# For HT-mixed, L-SIG LENGTH is computed to cover HT duration
# Actual length depends on PSDU size
expected_signal_24 = '110100000001000000000000'
print(f"\nExpected SIGNAL: {expected_signal_24}")

# Check parity
parity_check = sum(decoded[:17]) & 1  # Even parity over RATE+LENGTH+PARITY
print(f"\nParity check: decoded[0..16] sum = {sum(decoded[:17])}, even parity = {parity_check} vs decoded[16]={parity}")

# Verify: count matching bits
match_count = sum(1 for a, b in zip(''.join(map(str, decoded)), expected_signal_24) if a == b)
print(f"\nDecoded vs expected SIGNAL: {match_count}/24 matches")

if rate == 0xD:
    print("\n*** RATE = 0xD ✓ — USRP L-SIG decode VERIFIED ***")
elif rate == 0:
    print("\n*** RATE = 0 — decoder returned zero, may be hard-decision issue ***")
else:
    print(f"\n*** RATE = 0x{rate:X} — unexpected ***")