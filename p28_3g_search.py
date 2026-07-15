#!/usr/bin/env python
"""Phase 28.3g: Systematic search for the correct mapping.

The BPSK is clean. The bits don't match in DATA_SC order OR in standard
deinterleaved order. This suggests the SC-to-encoder mapping is wrong.

Hypotheses to test:
1. Pilot SCs are at different positions (k=±7, ±21 vs k=11,25,39,53)
2. The TX interleaver uses a different formula
3. The signal_field_impl.cc uses a different interleaver (HT 52-carrier?)
4. The DATA_SC ordering might be different (negative k first?)
5. L-STF/L-LTF offsets are different
"""
import numpy as np

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'


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


def encode_bit(input_bit, state):
    new_state = (state[1], state[2], state[3], state[4], state[5], input_bit)
    o1 = (input_bit ^ state[5] ^ state[3] ^ state[2] ^ state[1] ^ state[0]) & 1
    o2 = (input_bit ^ state[5] ^ state[4] ^ state[3] ^ state[0]) & 1
    return o1, o2, new_state


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
                o0, o1, new_state = encode_bit(bit, state)
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


def encode_signal_24(rate, length, parity, tail=0, reserved=0):
    """Build 24-bit SIGNAL field: RATE[4] | LENGTH[12] | PARITY[1] | TAIL[6] | RESERVED[1]"""
    bits = []
    # RATE 4 bits
    for i in range(3, -1, -1):
        bits.append((rate >> i) & 1)
    # LENGTH 12 bits
    for i in range(11, -1, -1):
        bits.append((length >> i) & 1)
    # PARITY 1 bit
    bits.append(parity & 1)
    # TAIL 6 bits
    for i in range(5, -1, -1):
        bits.append((tail >> i) & 1)
    # RESERVED 1 bit
    bits.append(reserved & 1)
    return bits


def bcc_encode_24(info_24):
    """BCC rate 1/2 encode 24 info bits to 48 coded bits."""
    coded = []
    state = (0, 0, 0, 0, 0, 0)
    for b in info_24:
        o0, o1, state = encode_bit(b, state)
        coded.extend([o0, o1])
    return coded


# === Compute expected coded bits ===
# Expected SIGNAL: rate=0xD=13, length=?, parity=0, tail=0, reserved=0
# We don't know exact length, but let's try common ones
print("=" * 70)
print("Compute expected encoded bits for various LENGTH values")
print("=" * 70)

# Try length=16 (0x010) as the task says
expected_info_24 = encode_signal_24(0xD, 16, parity=1)  # even parity over first 17 bits
# Verify parity
ones = sum(expected_info_24[:16])
parity_actual = 1 - (ones & 1)  # Make total even
expected_info_24[16] = parity_actual
print(f"SIGNAL field bits: {''.join(map(str, expected_info_24))}")
print(f"RATE=0xD, LENGTH=16, parity={parity_actual}")

# BCC encode
expected_coded = bcc_encode_24(expected_info_24)
print(f"BCC-encoded (48 bits): {''.join(map(str, expected_coded))}")
print(f"Expected (raw):        {EXPECTED_BITS}")

# Apply interleaver (forward)
DEINTL_INV_48 = [
    0, 16, 32,  1, 17, 33,  2, 18, 34,  3, 19, 35,  4, 20, 36,  5,
   21, 37,  6, 22, 38,  7, 23, 39,  8, 24, 40,  9, 25, 41, 10, 26,
   42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47
]

# Forward: encoded[k] → SC index j = DEINTL_INV[k]
# So interleaved[j] = encoded[k] where k=inv[j]... wait let me think again
# The TX forward is: encoded[k] → SC index j where j = 3*(k%16) + k//16
# Inverse: received[j] → encoded[k] where k is such that 3*(k%16)+k//16 == j
# So to compute expected interleaved: interleaved[j] = encoded[k] where j = 3*(k%16)+k//16
expected_interleaved = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    expected_interleaved[j] = expected_coded[k]

print(f"\nExpected interleaved (in SC order): {''.join(map(str, expected_interleaved))}")
print(f"Task's EXPECTED_BITS:                 {EXPECTED_BITS}")
print(f"Match? {''.join(map(str, expected_interleaved)) == EXPECTED_BITS}")

# Check if they match
if ''.join(map(str, expected_interleaved)) == EXPECTED_BITS:
    print("\n*** EXPECTED_BITS matches LENGTH=16, RATE=0xD with BCC + interleaver ***")
else:
    # Try different lengths
    print("\n--- Trying different LENGTHs ---")
    for length in range(0, 50):
        info = encode_signal_24(0xD, length, parity=1)
        ones = sum(info[:16])
        info[16] = 1 - (ones & 1)
        coded = bcc_encode_24(info)
        interleaved = [0] * 48
        for k in range(48):
            j = 3 * (k % 16) + k // 16
            interleaved[j] = coded[k]
        if ''.join(map(str, interleaved)) == EXPECTED_BITS:
            print(f"  MATCH! LENGTH={length}")
            break
    else:
        print(f"  No LENGTH in 0..49 matches. Trying other rates...")
        for rate in range(16):
            if rate == 0xD:
                continue
            info = encode_signal_24(rate, 16, parity=1)
            ones = sum(info[:16])
            info[16] = 1 - (ones & 1)
            coded = bcc_encode_24(info)
            interleaved = [0] * 48
            for k in range(48):
                j = 3 * (k % 16) + k // 16
                interleaved[j] = coded[k]
            if ''.join(map(str, interleaved)) == EXPECTED_BITS:
                print(f"  MATCH with RATE=0x{rate:X}, LENGTH=16")
                break

# === Try length=4 ===
print("\n--- Trying common HT-mixed LENGTH values ---")
# HT-mixed LENGTH = (16 + 8*LENGTH_psdu + 6) / 3
# For PSDU of various sizes...
# Let's compute the SIGNAL field for some standard lengths
for length in [4, 8, 16, 32, 64, 100, 500, 1000, 1500]:
    info = encode_signal_24(0xD, length, parity=1)
    ones = sum(info[:16])
    info[16] = 1 - (ones & 1)
    coded = bcc_encode_24(info)
    interleaved = [0] * 48
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        interleaved[j] = coded[k]
    print(f"  LENGTH={length:4d}: interleaved={''.join(map(str, interleaved))[:24]}...")

# === Now load the capture and try all the same LENGTHs ===
print("\n" + "=" * 70)
print("Load capture and decode L-SIG")
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

# Standard 4 pilots at k=11,25,39,53
# But let's try ALL 52 active SCs first (including pilots)
# The pilots carry known bits: p0,p1,p2,p3 (data, ±1) but we need to figure out
# their position in the 48-bit interleaved stream.

# For L-SIG, ALL 48 SCs are data (no pilots in L-SIG)
# So L-SIG uses 48 SCs out of 52. Which 4 are skipped? Pilots at k=±21,±7.
# That's k=11,25,39,53. So DATA_SC excludes these.
PILOT_SC = [11, 25, 39, 53]
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_SC]

F0a = F0[DATA_SC]
F1a = F1[DATA_SC]
Havg = (F0a + F1a) / 2
eq = Fsig[DATA_SC] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

received_hard = (eq_rot.real > 0).astype(int)

# Now compare to expected (with various LENGTHs)
print(f"\nReceived (SC order, hard):  {''.join(map(str, received_hard))}")
print(f"Expected (task):            {EXPECTED_BITS}")

# Direct match (no deinterleave)
direct_matches = sum(1 for a, b in zip(received_hard, EXPECTED_BITS) if a == b)
print(f"Direct match: {direct_matches}/48")

# Match after deinterleave
received_deintl = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    received_deintl[k] = int(received_hard[j])
print(f"Received deintl (encoder): {''.join(map(str, received_deintl))}")

# Try all LENGTHs to find one that matches
print("\n--- Trying all LENGTHs to find what TX sent ---")
best_length = None
best_match = 0
for length in range(0, 4096):
    info = encode_signal_24(0xD, length, parity=1)
    ones = sum(info[:16])
    info[16] = 1 - (ones & 1)
    coded = bcc_encode_24(info)
    interleaved = [0] * 48
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        interleaved[j] = coded[k]
    if ''.join(map(str, interleaved)) == EXPECTED_BITS:
        print(f"  MATCH: LENGTH={length}")
        best_length = length
        break

if best_length is None:
    print(f"  No LENGTH in 0..4095 matches the EXPECTED_BITS pattern")
    print(f"  This means EXPECTED_BITS is NOT a simple rate=0xD + length + BCC + interleave")
    print(f"  Maybe the expected_bits are given in a different order")