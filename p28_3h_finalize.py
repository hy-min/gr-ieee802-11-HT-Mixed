#!/usr/bin/env python
"""Phase 28.3h: Try multiple orderings to find the correct mapping.

Key insight: BPSK is clean (18.6 dB SNR after equalization), but the bits
don't match the provided EXPECTED_BITS in DATA_SC order. So the EXPECTED_BITS
are in a different order (or are actually HT-SIG bits mislabeled as L-SIG).

This script tries:
1. Different SC orderings (negative-first, by-frequency, etc.)
2. Different pilot positions
3. Different interleavers (HT 52-carrier formula)
4. Reverse the bit string
5. Try treating as HT-SIG field
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


# Load
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

# === Try multiple SC orderings ===
print("=" * 70)
print("Try multiple SC orderings")
print("=" * 70)

# Different pilot sets (802.11a/g uses 4 pilots; 802.11n HT also uses 4)
PILOT_OPTIONS = [
    [11, 25, 39, 53],  # standard
    [7, 21, 43, 57],   # 802.11a/g? (sometimes seen)
    [11, 39, 25, 53],  # reordered
]
ACTIVE_SC_ALL = list(range(1, 27)) + list(range(38, 64))

for pilot_sc in PILOT_OPTIONS:
    data_sc = [k for k in ACTIVE_SC_ALL if k not in pilot_sc]
    F0a = F0[data_sc]
    F1a = F1[data_sc]
    Havg = (F0a + F1a) / 2
    eq = Fsig[data_sc] / Havg
    cpe = np.angle(np.sum(eq))
    eq_rot = eq * np.exp(-1j * cpe)
    bits = (eq_rot.real > 0).astype(int)
    got = ''.join(map(str, bits))
    matches = sum(1 for a, b in zip(got, EXPECTED_BITS) if a == b)
    print(f"  Pilots={pilot_sc}: matches={matches}/48, got[:24]={got[:24]}...")

# === Try negative-first SC ordering ===
print("\n=== Try negative-first SC ordering (k=-26..-1, 1..26) ===")
# In 802.11n, logical subcarrier order is -26..-1, +1..+26
# Mapping to FFT bins 1..64: k=1 → SC=-26 (since -32+1=-31, no wait)
# Actually FFT bins 0..63 correspond to frequencies f = -32..31
# So bin 0 = DC, bin 1 = -31, bin 32 = 0
# Wait, 64-point FFT: bins 0..63. Frequencies = k*Fs/64 for k=0..63.
# Negative frequencies: k=63 is -Fs/64, k=32 is DC, k=31 is +Fs/64
# Hmm, this depends on fftshift convention.
# Standard: bin k → frequency k*Fs/64. Negative freqs are at high k values.
# So bin 1 = +Fs/64, bin 63 = -Fs/64
# Active SCs (excluding DC bin 0, pilots, and null at edges):
# Bin 1..26 (positive freqs) and bin 38..63 (negative freqs)
# Negative-first order: 63, 62, ..., 38, 1, 2, ..., 26
NEG_FIRST_SC = list(range(63, 37, -1)) + list(range(1, 27))
PILOT_SC = [11, 25, 39, 53]
# Note: in negative-first order, 53 (positive freq) becomes position 53-38+1=16, 39 (negative) is at position 1
DATA_SC_NEG_FIRST = [k for k in NEG_FIRST_SC if k not in PILOT_SC]
print(f"DATA_SC_NEG_FIRST ({len(DATA_SC_NEG_FIRST)}): {DATA_SC_NEG_FIRST}")

F0a = F0[DATA_SC_NEG_FIRST]
F1a = F1[DATA_SC_NEG_FIRST]
Havg = (F0a + F1a) / 2
eq = Fsig[DATA_SC_NEG_FIRST] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)
bits = (eq_rot.real > 0).astype(int)
got_neg_first = ''.join(map(str, bits))
matches_neg_first = sum(1 for a, b in zip(got_neg_first, EXPECTED_BITS) if a == b)
print(f"Negative-first order: matches={matches_neg_first}/48")
print(f"  Got: {got_neg_first}")

# === Try HT-SIG interleaver (52-carrier) ===
print("\n=== Try HT-SIG interleaver (52-carrier) ===")
# HT-SIG uses n_col=13, n_row=4 for BPSK (n_bpsc=1)
# Forward: i = n_row*(k % n_col) + k / n_col = 4*(k%13) + k//13
# j = s * (i/s) + (i + n_cbps - n_col*i/n_cbps) % s
# For s=1: j = i + (i + 52 - 13*i/52) % 1 = i + 0 = i
# So for HT: encoded[k] → SC index j = 4*(k%13) + k//13
HT_FORWARD = [4 * (k % 13) + k // 13 for k in range(48)]
HT_INV = [0] * 48
for k, j in enumerate(HT_FORWARD):
    HT_INV[j] = k
print(f"HT forward[0..20]: {HT_FORWARD[:20]}")
# Use standard DATA_SC and apply HT deinterleave
DATA_SC_STD = [k for k in ACTIVE_SC_ALL if k not in PILOT_SC]
F0a = F0[DATA_SC_STD]
F1a = F1[DATA_SC_STD]
Havg = (F0a + F1a) / 2
eq = Fsig[DATA_SC_STD] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)
bits = (eq_rot.real > 0).astype(int)
# Apply HT deinterleave
bits_ht = [0] * 48
for k in range(48):
    j = HT_FORWARD[k]
    bits_ht[k] = int(bits[j])
got_ht = ''.join(map(str, bits_ht))
matches_ht = sum(1 for a, b in zip(got_ht, EXPECTED_BITS) if a == b)
print(f"HT deinterleave: matches={matches_ht}/48")
print(f"  Got: {got_ht}")

# === Try reversing expected_bits ===
print("\n=== Try reversed EXPECTED_BITS ===")
exp_rev = EXPECTED_BITS[::-1]
PILOT_SC = [11, 25, 39, 53]
DATA_SC = [k for k in ACTIVE_SC_ALL if k not in PILOT_SC]
F0a = F0[DATA_SC]
F1a = F1[DATA_SC]
Havg = (F0a + F1a) / 2
eq = Fsig[DATA_SC] / Havg
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)
bits = (eq_rot.real > 0).astype(int)
got = ''.join(map(str, bits))
matches_rev = sum(1 for a, b in zip(got, exp_rev) if a == b)
print(f"Reversed expected: matches={matches_rev}/48")

# === Try assuming EXPECTED_BITS is in DATA_SC order but using ALL 52 SCs ===
# (so 4 of the expected bits are pilots, not data — they wouldn't match)
print("\n=== Try ALL 52 SCs (no pilot exclusion) ===")
F0a_all = F0[ACTIVE_SC_ALL]
F1a_all = F1[ACTIVE_SC_ALL]
Havg_all = (F0a_all + F1a_all) / 2
eq_all = Fsig[ACTIVE_SC_ALL] / Havg_all
cpe_all = np.angle(np.sum(eq_all))
eq_all_rot = eq_all * np.exp(-1j * cpe_all)
bits_all = (eq_all_rot.real > 0).astype(int)
got_all = ''.join(map(str, bits_all))
matches_all = sum(1 for a, b in zip(got_all, EXPECTED_BITS) if a == b)
print(f"All 52 SCs: matches={matches_all}/48 (vs 52 chars)")
print(f"  Got (52 chars): {got_all}")
print(f"  Expected (48 chars): {EXPECTED_BITS}")
# Try comparing only 48 chars
got_all_48 = got_all[:48]
matches_all_48 = sum(1 for a, b in zip(got_all_48, EXPECTED_BITS) if a == b)
print(f"  Got[:48] vs Expected: matches={matches_all_48}/48")

# === Try: extract 48 bits at positions [0..25, 27..38, 39..51] (skip pilot positions in array index) ===
# Where pilot positions in ACTIVE_SC_ALL are at indices 10 (k=11), 24 (k=25), 37 (k=39), 51 (k=53)
print("\n=== Skip pilots at array indices ===")
pilots_idx = [ACTIVE_SC_ALL.index(k) for k in PILOT_SC]
print(f"Pilot indices in ACTIVE_SC_ALL: {pilots_idx}")
data_idx = [i for i in range(52) if i not in pilots_idx]
bits_data_idx = [int(bits_all[i]) for i in data_idx]
got_data_idx = ''.join(map(str, bits_data_idx))
matches_data_idx = sum(1 for a, b in zip(got_data_idx, EXPECTED_BITS) if a == b)
print(f"Data indices: matches={matches_data_idx}/48")
print(f"  Got: {got_data_idx}")

# === Final: print the received bits in multiple orderings ===
print("\n" + "=" * 70)
print("FINAL: received bits in multiple orderings")
print("=" * 70)
print(f"  All 52 SCs (k order):         {got_all}")
print(f"  DATA_SC only (52-4 pilots):   {got}")
print(f"  Negative-first DATA_SC:       {got_neg_first}")
print(f"  HT-deinterleaved DATA_SC:     {got_ht}")
print(f"  Expected:                     {EXPECTED_BITS}")

# === Now do the actual viterbi decode with the best SC ordering ===
print("\n" + "=" * 70)
print("Viterbi decode with best SC ordering")
print("=" * 70)

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

# Use DATA_SC (standard)
bits_int = [int(b) for b in got]
# Apply standard 802.11a/g deinterleave
received_deintl = [0] * 48
for k in range(48):
    j = 3 * (k % 16) + k // 16
    received_deintl[k] = bits_int[j]
print(f"Received deintl: {''.join(map(str, received_deintl))}")

decoded, _ = viterbi_decode_hard(received_deintl)
print(f"Viterbi decoded 24 SIGNAL bits: {''.join(map(str, decoded))}")
rate = (decoded[0] << 3) | (decoded[1] << 2) | (decoded[2] << 1) | decoded[3]
length = 0
for i in range(12):
    length |= decoded[4+i] << i
print(f"RATE=0x{rate:X}, LENGTH={length} bytes")