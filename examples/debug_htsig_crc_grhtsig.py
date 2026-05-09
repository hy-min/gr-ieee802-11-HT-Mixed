#!/usr/bin/env python3
"""Debug gr-htsig HT-SIG CRC computation.

From gr-htsig/lib/ht_sig_field_impl.cc lines 9-22:
 *   bit  0..6   : MCS (LSB-first)
 *   bit  7      : BW40 (0 = 20MHz, 1 = 40MHz)
 *   bit  8..23  : LENGTH (16 bit, LSB-first) = PSDU length in bytes
 *   bit 24..26  : Reserved = 0
 *   bit 27      : Aggregation (A-MPDU) flag
 *   bit 28..29  : STBC (2 bit, LSB-first)
 *   bit 30      : Advanced coding (LDPC) flag
 *   bit 31      : Short GI flag
 *   bit 32..33  : Num HT-LTF (LSB-first)
 *   bit 34..41  : 8-bit CRC over bit 0..33
 *   bit 42..47  : Tail bits = 0

CRC input: bits 0-33 (34 bits total)
"""

def crc8_grhtsig(bits):
    """gr-htsig style: init=[1,1,1,1,1,1,1,1], G(x)=x^8+x^2+x+1, final=c[j]^1"""
    c = [1]*8
    for i in range(34):
        m = bits[i] & 0x1
        c0, c1, c2, c3, c4, c5, c6, c7 = c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    # Final: c[j] ^ 1
    crc = 0
    for j in range(8):
        bit = (c[j] ^ 1) & 0x1
        crc |= bit << j
    return crc

# Test case: mcs=0, len=96, all other fields=0 (from user description)
# Bits 0-6: MCS=0 -> "0000000"
# Bit 7: BW40=0
# Bits 8-23: Length=96 -> "01100000" in LSB-first (string "00000110")
# Bits 24-26: Reserved=0 -> "000"
# Bit 27: Aggregation=0
# Bits 28-29: STBC=0 -> "00"
# Bit 30: Advanced coding=0
# Bit 31: Short GI=0
# Bits 32-33: Num HT-LTF=0 -> "00"

# LSB-first: length 96 = 0x60 = binary 01100000
# In LSB-first: bit0=0, bit1=0, bit2=0, bit3=0, bit4=0, bit5=1, bit6=1, bit7=0
length_bits_lsb = [0,0,0,0,0,1,1,0,0,0,0,0,0,0,0,0]  # 96 in LSB-first

bits = [0,0,0,0,0,0,0,  # MCS bits 0-6 (LSB first)
        0,               # BW40 bit 7
        ] + length_bits_lsb + [  # Length bits 8-23
        0,0,0,  # Reserved bits 24-26
        0,      # Aggregation bit 27
        0,0,    # STBC bits 28-29
        0,      # Advanced coding bit 30
        0,      # Short GI bit 31
        0,0]    # Num HT-LTF bits 32-33

crc = crc8_grhtsig(bits)
print(f"CRC computed: 0x{crc:02X}")
print(f"TX output:   0x41")
print(f"Match: {crc == 0x41}")

# Show the bit sequence
print(f"\nBit sequence (0-33):")
for i in range(34):
    print(f"  bit {i:2d} = {bits[i]}")
