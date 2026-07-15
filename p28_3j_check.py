#!/usr/bin/env python
"""Phase 28.3j: Final analysis - verify expected bits are for LENGTH=20.

LENGTH=20 generates expected bits that match EXPECTED_BITS at 38/48.
But received bits only match at 22/48 in DATA_SC order.
This means the EXPECTED_BITS string is in a DIFFERENT order than my DATA_SC.

Hypothesis: the expected bits might be in ENCODER ORDER (after BCC, before interleaver),
or in some other specific order. Let me test.

Also: the match of 38/48 for LENGTH=20 vs EXPECTED_BITS is suspiciously high
given random would be 24/48. So EXPECTED_BITS = L-SIG for LENGTH=20.
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
    state = 0
    out = []
    for b in in_bits:
        state = ((state << 1) & 0x7e) | b
        s = state & 0b1011011
        out.append(bin(s).count('1') % 2)
        s = state & 0b1111001
        out.append(bin(s).count('1') % 2)
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


# Compute L-SIG for LENGTH=20 (best match to EXPECTED_BITS)
sh_20 = make_signal(0xD, 20)
enc_20 = cc_encode(sh_20)
intl_20 = interleave_forward(enc_20)
print(f"L-SIG for LENGTH=20, interleaved:")
print(f"  Bits: {''.join(map(str, intl_20))}")
print(f"  EXPECTED_BITS:   {EXPECTED_BITS}")
diff_pos = [i for i, (a, b) in enumerate(zip(intl_20, EXPECTED_BITS)) if a != b]
print(f"  Diff positions: {diff_pos}")

# Maybe EXPECTED_BITS are the SIGNAL bits (24 bits padded) without proper ordering
# Let's try the SIGNAL info bits padded:
print(f"\nSIGNAL info bits for LENGTH=20: {''.join(map(str, sh_20))}")

# Maybe EXPECTED_BITS is just the encoded (BCC) but not interleaved
enc_str = ''.join(map(str, enc_20))
print(f"BCC encoded (not interleaved): {enc_str}")
print(f"Expected:                      {EXPECTED_BITS}")
diff_pos_enc = [i for i, (a, b) in enumerate(zip(enc_str, EXPECTED_BITS)) if a != b]
print(f"Diff positions: {diff_pos_enc}")

# Also check if maybe it's interleaved differently — like HT-SIG interleaver
# HT uses n_col=13, n_row=4. Let me try that.
print("\n=== Try HT interleaver with n_col=13, n_row=4 ===")
HT_INTERLEAVED_20 = [0] * 48
for k in range(48):
    i = 4 * (k % 13) + k // 13
    # Now apply second-stage permutation (BPSK s=1, so j=i)
    j = i
    if j < 48:
        HT_INTERLEAVED_20[j] = enc_20[k]
print(f"HT interleaved: {''.join(map(str, HT_INTERLEAVED_20))}")
print(f"Expected:       {EXPECTED_BITS}")

# Maybe the order is by frequency index, k = 1, 2, ..., 26, 38, ..., 63
# (negative freqs first, then positive — or vice versa)
# Let me try a different ordering: PILOT_SC[11,25,39,53], so data SCs are 1..10, 12..24, 26, 38, 40..52, 54..63
# That gives 10 + 13 + 1 + 1 + 13 + 10 = 48

# Load capture and check
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

# Compare received to L-SIG for LENGTH=20 in different orderings
print("\n=== Compare received to L-SIG for LENGTH=20 ===")
print(f"L-SIG for LENGTH=20: {''.join(map(str, intl_20))}")
print(f"Received:            {''.join(map(str, received_bits))}")

# Try different orderings
orderings = {
    'DATA_SC pos-first': DATA_SC,
    'DATA_SC neg-first': list(range(63, 37, -1)) + list(range(1, 27)),
    'k=1..26, 38..63': list(range(1, 27)) + list(range(38, 64)),
    'k=63..38, 26..1': list(range(63, 37, -1)) + list(range(26, 0, -1)),
    'sorted': sorted(DATA_SC),
}
for name, ordering in orderings.items():
    # Make sure 48 SCs
    data_sc_use = [k for k in ordering if k not in PILOT_SC and k != 0][:48]
    if len(data_sc_use) < 48:
        continue
    F0a_t = F0[data_sc_use]
    F1a_t = F1[data_sc_use]
    Havg_t = (F0a_t + F1a_t) / 2
    eq_t = Fsig[data_sc_use] / Havg_t
    cpe_t = np.angle(np.sum(eq_t))
    eq_t_rot = eq_t * np.exp(-1j * cpe_t)
    bits_t = (eq_t_rot.real > 0).astype(int).tolist()
    matches_t = sum(1 for a, b in zip(bits_t, intl_20) if a == b)
    matches_t2 = sum(1 for a, b in zip(bits_t, EXPECTED_BITS) if a == b)
    print(f"  {name:30s}: matches L=20: {matches_t}/48, matches task: {matches_t2}/48")

# === OK so we know L-SIG for LENGTH=20 closely matches EXPECTED_BITS.
# Maybe EXPECTED_BITS = L-SIG for LENGTH=20 but in a different order.
# Let me try every possible permutation of mapping DATA_SC to positions:
# No, 48! is too many. Let me check: maybe EXPECTED_BITS are in ENCODER ORDER (before interleave).
print("\n=== Compare BCC-encoded to EXPECTED_BITS ===")
enc_str = ''.join(map(str, enc_20))
print(f"BCC-encoded (encoder order): {enc_str}")
print(f"Expected:                    {EXPECTED_BITS}")
diff = sum(1 for a, b in zip(enc_str, EXPECTED_BITS) if a != b)
print(f"Diff: {diff}/48")

# Try SIGNAL bits padded with parity+tail
# signal_header is 24 bits, padded to 48 by appending 24 zeros
sig_str = ''.join(map(str, sh_20))
padded = sig_str + '0' * 24
print(f"\nSIGNAL bits padded: {padded}")
print(f"Expected:           {EXPECTED_BITS}")
diff_pad = sum(1 for a, b in zip(padded, EXPECTED_BITS) if a != b)
print(f"Diff: {diff_pad}/48")

# Try reverse encoded
enc_rev = enc_str[::-1]
print(f"\nBCC-encoded reversed: {enc_rev}")
print(f"Expected:             {EXPECTED_BITS}")
diff_rev = sum(1 for a, b in zip(enc_rev, EXPECTED_BITS) if a != b)
print(f"Diff: {diff_rev}/48")

# Try interleaved with different n_row, n_col combinations
print("\n=== Try different interleaver parameters ===")
for n_col in [12, 13, 16, 24]:
    n_row = 48 // n_col
    if n_row * n_col != 48:
        continue
    for s in [1, 2]:
        intl_test = [0] * 48
        for k in range(48):
            i = n_row * (k % n_col) + k // n_col
            j = s * (i // s) + ((i + 48 - (n_col * i) // 48) % s)
            intl_test[j] = enc_20[k]
        intl_str = ''.join(map(str, intl_test))
        matches = sum(1 for a, b in zip(intl_str, EXPECTED_BITS) if a == b)
        if matches > 32:
            print(f"  n_col={n_col} n_row={n_row} s={s}: matches={matches}/48: {intl_str[:24]}...")

# Try reverse of intl_20
intl_20_rev = intl_20[::-1]
matches_rev = sum(1 for a, b in zip(intl_20_rev, EXPECTED_BITS) if a == b)
print(f"\nIntl_20 reversed: matches={matches_rev}/48: {''.join(map(str, intl_20_rev))[:24]}...")

# Try intl_20 with even/odd split
intl_even = intl_20[::2]
intl_odd = intl_20[1::2]
matches_even = sum(1 for a, b in zip(intl_even, EXPECTED_BITS[:24]) if a == b)
matches_odd = sum(1 for a, b in zip(intl_odd, EXPECTED_BITS[24:]) if a == b)
print(f"\nIntl_20 even-indexed: matches first 24 of expected: {matches_even}/24")
print(f"Intl_20 odd-indexed: matches last 24 of expected: {matches_odd}/24")