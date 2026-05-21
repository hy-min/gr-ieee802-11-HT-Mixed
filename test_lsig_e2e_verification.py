#!/usr/bin/env python3
"""
End-to-end L-SIG verification: TX -> RX simulation -> decoded bits
"""
import numpy as np

# L-SIG original 24 bits (rate=0xD, len=45, parity=1)
TX_LSIG_24 = [1,1,0,1,0,1,0,1,1,0,1,0,0,0,0,0,0,1,0,0,0,0,0,0]

# Conv encoder (G0=0x5B=octal0133, G1=0x79=octal0171)
def ones_local(n):
    return bin(n).count('1')

def conv_encode_133_171(bits24):
    state = 0
    out = []
    for b in bits24:
        state = ((state << 1) & 0x7e) | b
        o0 = ones_local(state & 0x5B) % 2  # octal 0133 = 0x5B
        o1 = ones_local(state & 0x79) % 2  # octal 0171 = 0x79
        out.extend([o0, o1])
    return out

# Interleaver: k -> i = 3*(k%16) + k//16
def interleave_48(bits48):
    out = [0]*48
    for k in range(48):
        i = 3*(k%16) + k//16
        out[i] = bits48[k]
    return out

# Deinterleaver inverse mapping
DEINT_INV = [0,16,32,1,17,33,2,18,34,3,19,35,4,20,36,5,21,37,
             6,22,38,7,23,39,8,24,40,9,25,41,10,26,42,11,27,43,
             12,28,44,13,29,45,14,30,46,15,31,47]

def deinterleave_48(bits48):
    out = [0]*48
    for i in range(48):
        out[DEINT_INV[i]] = bits48[i]
    return out

# Run
tx_enc = conv_encode_133_171(TX_LSIG_24)
tx_int = interleave_48(tx_enc)
rx_deintl = deinterleave_48(tx_int)

print("TX L-SIG 24 bits:", ''.join(map(str, TX_LSIG_24)))
print("TX encoded 48 bits:", ''.join(map(str, tx_enc)))
print("TX interleaved:", ''.join(map(str, tx_int)))
print("RX deintl (should=tx_enc):", ''.join(map(str, rx_deintl)))
print("Match:", rx_deintl == tx_enc)
print()
print("Expected VITERBI_IN:", ''.join(map(str, tx_int)))
print("Actual test output VITERBI_IN: 010100110101111010001100110011001000111100111000")
print("Python TX int matches test VITERBI_IN? NO - problem is upstream of deinterleaver")