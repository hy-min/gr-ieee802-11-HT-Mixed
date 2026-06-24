#!/home/hy/conda/envs/gnuradio/bin/python
"""
Synthetic test: full HT-SIG viterbi decode pipeline on a known signal.

Mirrors `decode_htsig_from_rotated` (lib/frame_equalizer_impl.cc:2008):
1. BPSK+QBPSK modulate HT-SIG0 + HT-SIG1 (bits on IMAG axis after +j rotation)
2. Insert pilots at SCs {-21, -7, 7, 21} with kHtPilotPolarity127 polarity
3. Apply optional impairments (CFO / AWGN / SFO)
4. Equalize (eq = rx / H, with H=I for ideal)
5. Hard-bit decision on IMAG sign
6. Optional QBPSK rotation x invert_a x invert_b (16 candidates)
7. Deinterleave per 802.11n: j = 3*(k%16) + k/16
8. Concatenate HT-SIG0 + HT-SIG1 (96 bits)
9. Viterbi decode (rate 1/2, k=7, polynomials 133/171)
10. Extract MCS / length / LDPC / CRC8 / tail

We re-implement everything in NumPy so the test runs offline without GNU Radio.
"""
import numpy as np

# 802.11n subcarrier index for the 52-bin TX order
K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)

# 802.11n HT pilot polarity sequence (127 entries, from frame_equalizer_impl.cc:248)
K_HT_PILOT_POLARITY_127 = np.array([
    1, 1, 1, 1, -1, -1, -1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1, -1, 1, 1,
    -1, 1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1, 1, 1, -1, 1, 1, -1, -1, 1, 1, 1,
    -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1, -1, -1, 1, 1, 1, 1, 1, -1, -1,
    1, 1, -1, -1, 1, -1, 1, -1, 1, 1, -1, -1, -1, 1, 1, -1, -1, -1, -1, 1,
    -1, -1, 1, -1, 1, 1, 1, 1, -1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, 1, -1, -1,
    -1, -1, -1, -1, -1
], dtype=np.int32)


def make_known_htsig_bits(mcs=0, length=100, sgi=0, aggregation=0, ldpc=0,
                          num_ht_ltf=1, stbc=0, rsv_zero=True, tail_zero=True):
    """Build the 48-bit HT-SIG field per IEEE 802.11-2016 Section 18.3.5.3.

    Layout (LSB-first within each field):
      [0..6]   MCS (7 bits)
      [7]      bw40 (must be 0 for 20 MHz)
      [8..23]  PSDU length (16 bits)
      [24..26] reserved (must be 0)
      [27]     aggregation
      [28..29] STBC (2 bits)
      [30]     adv_coding (0=BCC, 1=LDPC)
      [31]     short GI
      [32..33] num_ht_ltf
      [34..41] CRC8 over bits 0..33
      [42..47] tail (must be 0)
    """
    bits = np.zeros(48, dtype=np.uint8)
    # MCS at bits 0..6
    for i in range(7):
        bits[i] = (mcs >> i) & 1
    # bw40 at bit 7 - leave 0 (20 MHz)
    # psdu_length at bits 8..23
    for i in range(16):
        bits[8 + i] = (length >> i) & 1
    # rsv at bits 24..26 - leave 0
    bits[27] = 1 if aggregation else 0
    for i in range(2):
        bits[28 + i] = (stbc >> i) & 1
    bits[30] = 1 if ldpc else 0
    bits[31] = 1 if sgi else 0
    for i in range(2):
        bits[32 + i] = (num_ht_ltf >> i) & 1
    # Compute CRC8 over bits 0..33
    bits[34:42] = _ht_crc8_compute(bits[0:34])
    # tail bits 42..47 stay 0
    return bits


def _ht_crc8_compute(bits0_33):
    """CRC8 per IEEE 802.11-2016 Section 18.3.5.3.5: polynomial x^8+x^2+x+1,
    init all ones, final invert, input bits[0..33] LSB-first.
    Returns 8-bit array, LSB-first."""
    c = [1, 1, 1, 1, 1, 1, 1, 1]
    for i in range(34):
        m = bits0_33[i] & 1
        c0, c1, c2, c3, c4, c5, c6, c7 = c
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    out = np.zeros(8, dtype=np.uint8)
    for j in range(8):
        out[j] = (c[j] ^ 1) & 1
    return out


def test_make_known_htsig_bits_case_a():
    """Case A: len=100, MCS=0, SGI=0, BCC, no agg, no LDPC."""
    bits = make_known_htsig_bits(mcs=0, length=100, sgi=0, ldpc=0)
    # Extract MCS
    mcs = sum(int(bits[i]) << i for i in range(7))
    assert mcs == 0, f"MCS mismatch: got {mcs}"
    # Extract length
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert length == 100, f"Length mismatch: got {length}"
    # bw40 must be 0
    assert bits[7] == 0, f"bw40 must be 0, got {bits[7]}"
    # rsv must be 0
    assert bits[24] == 0 and bits[25] == 0 and bits[26] == 0
    # sgi=0, ldpc=0, agg=0
    assert bits[27] == 0, "agg"
    assert bits[30] == 0, "ldpc"
    assert bits[31] == 0, "sgi"
    # num_ht_ltf=1 (default)
    num_ltf = (int(bits[32]) << 0) | (int(bits[33]) << 1)
    assert num_ltf == 1, f"num_ht_ltf mismatch: got {num_ltf}"
    # tail bits must be 0
    assert all(bits[42:48] == 0), f"tail bits not zero: {bits[42:48]}"
    print(f"[PASS] test_make_known_htsig_bits_case_a: bits[0..7]={bits[:8].tolist()}, "
          f"length_field={length}, mcs={mcs}, crc[0..3]={bits[34:38].tolist()}")


def test_make_known_htsig_bits_case_b():
    """Case B: len=1000, MCS=7, SGI=1, agg=1, BCC."""
    bits = make_known_htsig_bits(mcs=7, length=1000, sgi=1, aggregation=1)
    mcs = sum(int(bits[i]) << i for i in range(7))
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert mcs == 7, f"MCS mismatch: got {mcs}"
    assert length == 1000, f"Length mismatch: got {length}"
    assert bits[27] == 1, "agg"
    assert bits[31] == 1, "sgi"
    assert all(bits[42:48] == 0), "tail must be 0"
    print(f"[PASS] test_make_known_htsig_bits_case_b: mcs={mcs} length={length} "
          f"agg={bits[27]} sgi={bits[31]} crc[0..3]={bits[34:38].tolist()}")


def test_make_known_htsig_bits_case_c():
    """Case C: len=10, MCS=0, LDPC=1 (boundary)."""
    bits = make_known_htsig_bits(mcs=0, length=10, ldpc=1)
    mcs = sum(int(bits[i]) << i for i in range(7))
    length = sum(int(bits[8 + i]) << i for i in range(16))
    assert mcs == 0, f"MCS mismatch: got {mcs}"
    assert length == 10, f"Length mismatch: got {length}"
    assert bits[30] == 1, "ldpc must be 1"
    assert all(bits[42:48] == 0), "tail must be 0"
    print(f"[PASS] test_make_known_htsig_bits_case_c: mcs={mcs} length={length} "
          f"ldpc={bits[30]} crc[0..3]={bits[34:38].tolist()}")


if __name__ == "__main__":
    test_make_known_htsig_bits_case_a()
    test_make_known_htsig_bits_case_b()
    test_make_known_htsig_bits_case_c()
    print("\nHT-SIG bit synthesis tests passed (3/3).")


# ============================================================
# BCC encoder: rate 1/2, K=7, polynomials 133 (octal) and 171 (octal)
# Mirrors `viterbi_decode_133_171` companion in C++.
# ============================================================
def bcc_encode_24(bits24):
    """Encode 24 input bits into 48 coded bits using K=7 rate 1/2 BCC.

    Polynomials: G0 = 133 (octal) = 1011011, G1 = 171 (octal) = 1111001
    Trellis starts in state 0 and is forced to state 0 at the end via
    6 tail bits (input forcing). Caller is responsible for ensuring the
    input already contains the 6 tail bits at positions 18..23 (already
    the case in our 48-bit HT-SIG layout — bits 42..47 are tail, which
    are 6 zeros, so the last 6 input bits to BCC are also zeros).
    """
    assert len(bits24) == 24
    # Convert octal polynomials to bit masks
    g0 = 0o133  # = 91
    g1 = 0o171  # = 121
    state = 0   # K=7 shift register, 6-bit state
    out = np.zeros(48, dtype=np.uint8)
    for t in range(24):
        # Shift in current input bit as MSB
        reg = ((state << 1) | int(bits24[t])) & 0x7F
        o0 = bin(reg & g0).count("1") & 1
        o1 = bin(reg & g1).count("1") & 1
        out[2 * t] = o0
        out[2 * t + 1] = o1
        state = reg & 0x3F  # keep last 6 bits
    return out


# ============================================================
# HT-SIG interleaver per IEEE 802.11n Table 18-6 (depth-2, BPSK)
# Forward (TX side):  j = 16*(k%3) + k//3
# Deinterleaver (RX): j = 3*(k%16)  + k//16
# These ARE inverses (round-trip identity). The 2nd permutation step in the
# IEEE spec is omitted here because it is identity for BPSK (N_BPSC=1).
# ============================================================
def htsig_interleave(bits48):
    """Apply the 802.11n HT-SIG forward interleaver permutation.

    Per IEEE 802.11n Table 18-6 (N_COL=16, N_ROW=3, BPSK, N_CBPS=48):
        j = 16 * (k % 3) + k // 3
    """
    assert len(bits48) == 48
    out = np.zeros(48, dtype=np.uint8)
    for k in range(48):
        j = 16 * (k % 3) + k // 3
        out[j] = bits48[k] & 1
    return out


def htsig_deinterleave(bits48):
    """Inverse of htsig_interleave (per IEEE 802.11n Table 18-6).

    Per IEEE 802.11n Table 18-6 (N_COL=16, N_ROW=3):
        j = 3 * (k % 16) + k // 16

    Note: the 2nd permutation step in the IEEE spec is omitted for BPSK
    (the second permutation is identity for N_BPSC=1).
    """
    assert len(bits48) == 48
    out = np.zeros(48, dtype=np.uint8)
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[j] = bits48[k] & 1
    return out


# ============================================================
# BPSK + QBPSK modulation: bits 0 -> -j, bits 1 -> +j
# (mirrors line 2054 in frame_equalizer_impl.cc: bit on IMAG axis)
# ============================================================
def bpsk_qbpsk_modulate(coded_bits):
    """Map coded bits to complex symbols with QBPSK rotation.

    bit 0 -> -j (imag < 0)
    bit 1 -> +j (imag >= 0)
    Returns 48-element complex64 array.
    """
    assert len(coded_bits) == 48
    syms = np.empty(48, dtype=np.complex64)
    for i in range(48):
        syms[i] = 1j if coded_bits[i] else -1j
    return syms


# ============================================================
# Pilot insertion at SCs {-21, -7, 7, 21} (bins 48..51 in 52-order)
# Pilot sign: kHtPilotPolarity127[data_sym_idx % 127], flipped for pilot_idx==3
# Pilots are on IMAG axis (QBPSK): pilot_value = sign * j
# ============================================================
def insert_ht_pilots(data48_syms, data_sym_idx):
    """Insert 4 pilots into a 52-SC array. data48_syms[i] for i in 0..47.
    Pilots occupy bins 48..51 (SC indices -21, -7, +7, +21)."""
    assert len(data48_syms) == 48
    p = int(K_HT_PILOT_POLARITY_127[data_sym_idx % 127])
    out = np.empty(52, dtype=np.complex64)
    out[:48] = data48_syms
    # Pilot polarity: pilots 0,1,2 use +p, pilot 3 uses -p
    out[48] = 1j * p       # SC -21
    out[49] = 1j * p       # SC -7
    out[50] = 1j * p       # SC +7
    out[51] = -1j * p      # SC +21
    return out
