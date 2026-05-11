#!/usr/bin/env python3
"""Verify deinterleaver with known L-SIG bits"""

# TX L-SIG interleaved bits (from debug output)
tx_intl48 = [int(b) for b in "110000111110101111011010000001110010011110100101"]
tx_enc48 = [int(b) for b in "110110001010010001111101101100101000100011110111"]

# Deinterleaver formula from frame_equalizer_impl.cc:
# j = 16 * (k % 3) + k / 3
deintl = [0]*48
for k in range(48):
    j = 16 * (k % 3) + k // 3
    deintl[k] = tx_intl48[j]

result = ''.join(str(b) for b in deintl)
expected = ''.join(str(b) for b in tx_enc48)

print(f"Deinterleaved: {result}")
print(f"Expected:      {expected}")
print(f"Match: {result == expected}")

# Also print first 12 bits for comparison
print(f"\nFirst 12 bits:")
print(f"  Deinterleaved: {result[:12]}")
print(f"  Expected:      {expected[:12]}")