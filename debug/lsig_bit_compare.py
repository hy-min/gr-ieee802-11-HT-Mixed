#!/usr/bin/env python3
"""
Debug script to compare TX enc48 with RX VITERBI_IN bits.
This helps identify exactly where the L-SIG decode chain fails.
"""
import sys
sys.path.insert(0, 'examples')

def expected_tx_enc48():
    """From TX debug output: [TX][LSIG] enc48=110110001010010001111101101100101000100011110111"""
    return "110110001010010001111101101100101000100011110111"

def expected_interleaved():
    """From TX debug output: [TX][LSIG] intl48=110000111110101111011010000001110010011110100101"""
    return "110000111110101111011010000001110010011110100101"

def main():
    print("=== L-SIG TX vs Expected RX Bit Comparison ===")
    print(f"TX enc48 (before convolution): {expected_tx_enc48()}")
    print(f"TX intl48 (after interleaver): {expected_interleaved()}")
    print()
    print("Expected VITERBI_IN should match TX intl48 after deinterleaving")
    print("If VITERBI_IN differs, the issue is in:")
    print("  1. FFT output extraction (wrong samples)")
    print("  2. Channel estimation (wrong H values)")
    print("  3. Equalization formula (rx/H gives wrong result)")
    print("  4. Hard bit extraction (phase mapping wrong)")

if __name__ == "__main__":
    main()