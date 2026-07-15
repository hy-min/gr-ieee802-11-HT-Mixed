#!/usr/bin/env python
"""Phase 28.3l: The BPSK is perfect (phase std 0.85°). The issue is the SC-to-bit mapping.

Per-SC phase error shows:
  Mean: -1.07° (close to 0)
  Std:  0.85°
  Linear fit: 0.018 deg/k, basically zero.

So phase is NOT the issue. The bit mapping must be different.

Key observation: The TASK says expected is 48 interleaved bits for L-SIG.
But the TX path is: 24 SIGNAL → BCC rate 1/2 → 48 coded → interleave → 48 SCs.
The interleaved bits go to SCs in order: SC[0..47] which is some mapping of DATA_SC.

If we get hard-decision bits at DATA_SC in order k=1,2,...,10,12,...,26,38,...
But the EXPECTED_BITS are the interleaved TX bits in encoder order k=0,1,2,...

So EXPECTED_BITS[0] should be at SC[DATA_SC[?]]. Let me figure out the mapping.
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


# Load and equalize
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

# Now find the mapping from received to expected
# received[i] is at DATA_SC[i] (in pos-first order)
# expected[k] is the TX interleaved bit at SC[?]
#
# The TX interleaver: encoded[k] → SC index j = 3*(k%16) + k//16
# But which SC index? It's relative to DATA_SC or to all 64 FFT bins?
# Let's check: in 802.11n, the interleaver outputs 48 bits that get mapped
# to the 48 data subcarriers (after pilot removal).
# The 48-bit interleaver output indices 0..47 correspond to DATA_SC indices 0..47.

# So EXPECTED_BITS[k] is at DATA_SC[?]
# where ? = the interleaver mapping for encoded bit k
# For BPSK 802.11 L-SIG interleaver: j = 3*(k%16) + k//16
# But wait, j here is the OUTPUT position in the interleaved sequence.
# So interleaved[j] = encoded[k] where j = 3*(k%16) + k//16

# So the bit AT DATA_SC position j is encoded[k] where j = 3*(k%16) + k//16
# In other words, expected_bits[j] = encoded[k]
# But this is the same as saying: expected_bits IS the interleaved output.

# So expected_bits[k] = encoded[k'] where k' = inv_perm[k]
# inv_perm: k = 3*(k'%16) + k'//16, find k'
# We have DEINTL_INV_48[i] = k where j = 3*(k%16) + k//16 means deintl_inv[i] = k
# Wait, that's the deinterleaver inverse — it goes from interleaved position i
# back to encoder position k.

# Let me re-derive: forward interleaver in 802.11n for BPSK (s=1, n_col=16, n_row=3):
# i = n_row * (k % n_col) + k // n_col
# j = s * (i // s) + ((i + n_cbps - (n_col * i) // n_cbps) % s)
# For s=1: j = i
# So: j = 3 * (k % 16) + k // 16
#
# Therefore: forward[k] = 3 * (k % 16) + k // 16 (interleaved position j)
#             deintl_inv[j] = k (encoder position)
#
# DEINTL_INV_48 = [0, 16, 32, 1, 17, 33, 2, 18, 34, 3, 19, 35, 4, 20, 36, 5,
#                  21, 37, 6, 22, 38, 7, 23, 39, 8, 24, 40, 9, 25, 41, 10, 26,
#                  42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47]
# For j=0: k=0; j=1: k=16; j=2: k=32; j=3: k=1; ...
# These ARE correct.

# So the EXPECTED_BITS at position j (interleaved index) = encoded[DEINTL_INV_48[j]]
# To go from received at DATA_SC[j] (in DATA_SC order, j=0..47) to expected:
# received[DATA_SC[j]] is the bit at interleaved position j.
# Compare to expected[j] which is the bit at interleaved position j.
# So they SHOULD match if DATA_SC order matches interleaved order.

# But they don't match. So either:
# 1. DATA_SC is in the wrong order
# 2. The TX uses a different interleaver (HT-SIG uses different formula)
# 3. There's a pilot I haven't accounted for

# Let me test hypothesis 3: maybe pilot positions are different
# In 802.11n HT-mixed, L-SIG uses the same pilot positions as L-LTF? Or different?
# Per 802.11n-2009 17.3.5.3: SIGNAL field uses 48 SCs (no pilots).
# Per 802.11n-2009 17.3.5.4: HT-SIG uses 52 SCs (4 pilots at -21, -7, +7, +21)
# So L-SIG pilots: NONE. All 52 SCs carry data?
# No wait, in 802.11n HT-mixed, the L-SIG is sent on the legacy OFDM symbol
# which uses 4 pilots at -21, -7, +7, +21 (same as 802.11a/g).
# So L-SIG has 48 data + 4 pilot = 52 SCs total.

# OK so 4 pilots at k=11,25,39,53. DATA_SC excludes those = 48 SCs.
# The interleaver output 48 bits go to DATA_SC in some order.
# If DATA_SC order matches interleaver output order: bits should match.

# Hypothesis: maybe the TX uses a DIFFERENT interleaver for L-SIG than data.
# Or: maybe the order in which SCs are filled is different.

# Let me try: assign EXPECTED_BITS in some specific order to DATA_SC indices
# and see which order gives a match.

# Brute force: try all 48! permutations? Too many.
# Instead: assume the relationship is a simple shuffle (e.g., reverse, rotate)

print("=" * 70)
print("Try shuffles of EXPECTED_BITS to match received bits")
print("=" * 70)

exp = list(EXPECTED_BITS)
exp_rev = exp[::-1]
for k in range(48):
    exp_rot = exp[k:] + exp[:k]

# Show all
print(f"Expected:                 {''.join(exp)}")
print(f"Expected reversed:        {''.join(exp_rev)}")
print(f"Received:                 {''.join(map(str, received_bits))}")

# Direct match
direct_match = sum(1 for a, b in zip(received_bits, exp) if a == b)
print(f"\nDirect match: {direct_match}/48")
rev_match = sum(1 for a, b in zip(received_bits, exp_rev) if a == b)
print(f"Reversed match: {rev_match}/48")

# Try rotating
print("\nRotation matches:")
best_rot_match = 0
best_rot = 0
for k in range(48):
    exp_rot = exp[k:] + exp[:k]
    matches = sum(1 for a, b in zip(received_bits, exp_rot) if a == b)
    if matches > best_rot_match:
        best_rot_match = matches
        best_rot = k
print(f"Best rotation: {best_rot} with {best_rot_match}/48 matches")

# Try swapping 2 halves
exp_swap = exp[24:] + exp[:24]
swap_match = sum(1 for a, b in zip(received_bits, exp_swap) if a == b)
print(f"Halves swap match: {swap_match}/48")

# Try 4 quarters
exp_q = exp[12:24] + exp[:12] + exp[36:48] + exp[24:36]
q_match = sum(1 for a, b in zip(received_bits, exp_q) if a == b)
print(f"Quarter swap match: {q_match}/48")

# === Now try matching received to ENCODER ORDER (before interleave) ===
# deinterleave expected and received using DEINTL_INV_48
DEINTL_INV = [
    0, 16, 32, 1, 17, 33, 2, 18, 34, 3, 19, 35, 4, 20, 36, 5,
    21, 37, 6, 22, 38, 7, 23, 39, 8, 24, 40, 9, 25, 41, 10, 26,
    42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47
]

# Deinterleave expected: expected_bits[interleaved_pos] → encoder_pos
exp_deintl = [0] * 48
for k in range(48):
    exp_deintl[k] = int(exp[DEINTL_INV[k]])
print(f"\nExpected deinterleaved (encoder order): {''.join(map(str, exp_deintl))}")

# Deinterleave received
rec_deintl = [0] * 48
for k in range(48):
    rec_deintl[k] = received_bits[DEINTL_INV[k]]
print(f"Received deinterleaved (encoder order): {''.join(map(str, rec_deintl))}")

deintl_match = sum(1 for a, b in zip(exp_deintl, rec_deintl) if a == b)
print(f"Deinterleaved match: {deintl_match}/48")

# === KEY INSIGHT: maybe DATA_SC order doesn't match the interleaver output order ===
# Let me try: assign each received bit to a different EXPECTED_BITS position
# to find the maximum match.

# Greedy assignment: for each received bit at position i, find the closest
# matching expected bit at position j, and remember the mapping.

# Actually, simpler: try all 48! permutations is too many.
# But the expected bits have a strong pattern: 6 ones, 1 zero, ... etc.
# Let me just try specific shuffles that make sense:

# Maybe the order is: SC index from low to high, with k=0 DC in the middle.
# In 802.11n, the SCs are mapped to FFT bins:
# Negative freqs first: -26, -25, ..., -1, then +1, ..., +26
# In FFT bin indices (1..63): -26 corresponds to bin 38 (since 64-26=38)
# So negative-first order: bin 38, 39, ..., 63, then 1, 2, ..., 37
# But we exclude pilots at -21 (bin 43), -7 (bin 57), +7 (bin 7), +21 (bin 21)
# Wait, +7 is bin 7 in positive (since 1..26 are positive freqs, 38..63 are negative)
# Actually: bin index k=1..26 are positive freqs (1=+1, 26=+26)
# bin index k=38..63 are negative freqs (38=-26, 63=-1)
# Pilots at -21=-21, -7=-7, +7=+7, +21=+21 → bins 43, 57, 7, 21

# Neg-first DATA_SC: start from bin 38 (-26), up to 63 (-1), then bin 1 (+1) up to 26 (+26)
# But exclude pilots: 7, 21, 43, 57
NEG_FIRST = []
for k in range(38, 64):
    if k not in PILOT_SC:
        NEG_FIRST.append(k)
for k in range(1, 27):
    if k not in PILOT_SC:
        NEG_FIRST.append(k)
print(f"\nNEG_FIRST DATA_SC: {NEG_FIRST}")

F0a_nf = F0[NEG_FIRST]
F1a_nf = F1[NEG_FIRST]
Havg_nf = (F0a_nf + F1a_nf) / 2
eq_nf = Fsig[NEG_FIRST] / Havg_nf
cpe_nf = np.angle(np.sum(eq_nf))
eq_nf_rot = eq_nf * np.exp(-1j * cpe_nf)
rec_nf = (eq_nf_rot.real > 0).astype(int).tolist()
nf_match = sum(1 for a, b in zip(rec_nf, exp) if a == b)
print(f"Neg-first DATA_SC match against EXPECTED_BITS: {nf_match}/48")
print(f"  Received neg-first: {''.join(map(str, rec_nf))}")

# Also try: positive-first, by index in DATA_SC
POS_FIRST = [k for k in range(1, 27) if k not in PILOT_SC] + \
            [k for k in range(38, 64) if k not in PILOT_SC]
print(f"POS_FIRST DATA_SC: {POS_FIRST}")
# That's our current ordering

# Try: pos-first then neg-first separately (i.e., +1..+26 then -26..-1)
# But in DATA_SC order this is what we already have.

# Try grouping by signal SCs vs the rest
# In 802.11n HT-SIG, the SC ordering might be:
# +1..+26 (excluding pilots), then -26..-1 (excluding pilots)
# That's our DATA_SC order

# Maybe the expected bits are in a different convention:
# Perhaps interleaved with pilots: the 48 bits + 4 pilot bits = 52 bits
# And the bits come out at ACTIVE_SC (52) in pos-first order
ALL_SC_ORDER = list(range(1, 27)) + list(range(38, 64))
print(f"\n=== Try ALL 52 SCs as the bit stream ===")
F0a_all = F0[ALL_SC_ORDER]
F1a_all = F1[ALL_SC_ORDER]
Havg_all = (F0a_all + F1a_all) / 2
eq_all = Fsig[ALL_SC_ORDER] / Havg_all
cpe_all = np.angle(np.sum(eq_all))
eq_all_rot = eq_all * np.exp(-1j * cpe_all)
rec_all = (eq_all_rot.real > 0).astype(int).tolist()
print(f"Received (52 SCs): {''.join(map(str, rec_all))}")

# Try matching 48-char EXPECTED_BITS to positions [0..47] of rec_all
m_0_47 = sum(1 for a, b in zip(rec_all[:48], exp) if a == b)
print(f"rec_all[0..47] vs expected: {m_0_47}/48")
# Try [4..51] (skip first 4 which could be pilots)
m_4_51 = sum(1 for a, b in zip(rec_all[4:52], exp) if a == b)
print(f"rec_all[4..51] vs expected: {m_4_51}/48")
# Try every possible window
best_window = 0
best_window_start = 0
for start in range(5):
    for end in range(52, start, -1):
        if end - start != 48:
            continue
        m = sum(1 for a, b in zip(rec_all[start:end], exp) if a == b)
        if m > best_window:
            best_window = m
            best_window_start = start
            best_window_end = end
print(f"Best window [{best_window_start}..{best_window_end}]: {best_window}/48")