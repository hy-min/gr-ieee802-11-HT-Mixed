#!/usr/bin/env python3
"""
Test HT deinterleaving algorithm.
Verify that interleaving and deinterleaving are inverse operations.
"""

import numpy as np

def ht_n_bpsc_from_mcs(mcs):
    """Return number of coded bits per subcarrier for HT MCS."""
    return [1, 2, 2, 4, 4, 6, 6, 6][mcs]

def ht_n_cbps_from_mcs(mcs):
    """Return number of coded bits per OFDM symbol for HT MCS."""
    return [52, 104, 104, 208, 208, 312, 312, 312][mcs]

def ht_interleave(in_bits, n_sym, mcs, reverse=False):
    """
    HT interleaving/deinterleaving algorithm.
    Based on utils.cc interleave() function.

    Parameters:
    - in_bits: 1D array of bits (n_sym * n_cbps)
    - n_sym: number of OFDM symbols
    - mcs: MCS index (0-7)
    - reverse: False for interleaving, True for deinterleaving

    Returns: interleaved/deinterleaved bits
    """
    n_bpsc = ht_n_bpsc_from_mcs(mcs)
    n_cbps = ht_n_cbps_from_mcs(mcs)
    s = max(n_bpsc // 2, 1)
    n_col = 13  # HT 20MHz: 13 columns
    n_row = n_cbps // n_col  # 4 * n_bpsc

    # Verify dimensions
    assert n_row * n_col == n_cbps, f"Invalid dimensions: n_row={n_row}, n_col={n_col}, n_cbps={n_cbps}"
    assert len(in_bits) == n_sym * n_cbps, f"Input length mismatch: got {len(in_bits)}, expected {n_sym * n_cbps}"

    out_bits = np.zeros_like(in_bits)

    for sym in range(n_sym):
        in_sym = in_bits[sym * n_cbps:(sym + 1) * n_cbps]
        out_sym = out_bits[sym * n_cbps:(sym + 1) * n_cbps]

        if not reverse:
            # Interleaving: in[k] -> out[j]
            for k in range(n_cbps):
                i = n_row * (k % n_col) + (k // n_col)
                j = s * (i // s) + ((i + n_cbps - ((n_col * i) // n_cbps)) % s)
                out_sym[j] = in_sym[k]
        else:
            # Deinterleaving: in[j] -> out[k]
            for j in range(n_cbps):
                i = s * (j // s) + ((j + (n_col * j) // n_cbps) % s)
                k = n_col * i - (n_cbps - 1) * (i // n_row)
                out_sym[k] = in_sym[j]

    return out_bits

def test_roundtrip():
    """Test that interleaving followed by deinterleaving returns original bits."""
    print("Testing interleaving/deinterleaving roundtrip...")

    # Test different MCS values
    test_cases = [
        (0, 1),   # MCS0: BPSK, 1 symbol
        (1, 1),   # MCS1: QPSK, 1 symbol
        (2, 2),   # MCS2: QPSK 3/4, 2 symbols
        (3, 1),   # MCS3: 16-QAM 1/2, 1 symbol
        (4, 2),   # MCS4: 16-QAM 3/4, 2 symbols
        (5, 1),   # MCS5: 64-QAM 2/3, 1 symbol
        (6, 2),   # MCS6: 64-QAM 3/4, 2 symbols
        (7, 1),   # MCS7: 64-QAM 5/6, 1 symbol
    ]

    all_passed = True

    for mcs, n_sym in test_cases:
        n_bpsc = ht_n_bpsc_from_mcs(mcs)
        n_cbps = ht_n_cbps_from_mcs(mcs)

        # Generate random test bits
        total_bits = n_sym * n_cbps
        original = np.random.randint(0, 2, total_bits, dtype=np.uint8)

        # Interleave
        interleaved = ht_interleave(original, n_sym, mcs, reverse=False)

        # Deinterleave
        deinterleaved = ht_interleave(interleaved, n_sym, mcs, reverse=True)

        # Check if we get back original
        if np.array_equal(original, deinterleaved):
            print(f"  ✓ MCS{mcs} (n_bpsc={n_bpsc}, n_cbps={n_cbps}, n_sym={n_sym}): PASS")
        else:
            print(f"  ✗ MCS{mcs} (n_bpsc={n_bpsc}, n_cbps={n_cbps}, n_sym={n_sym}): FAIL")
            print(f"    Mismatch count: {np.sum(original != deinterleaved)} / {total_bits}")
            all_passed = False

            # Debug first few mismatches
            for idx in range(min(10, total_bits)):
                if original[idx] != deinterleaved[idx]:
                    print(f"    Bit {idx}: original={original[idx]}, deinterleaved={deinterleaved[idx]}")
                    break

    return all_passed

def test_specific_pattern():
    """Test with a specific pattern for debugging."""
    print("\nTesting with specific pattern...")

    mcs = 0  # BPSK
    n_sym = 1
    n_cbps = ht_n_cbps_from_mcs(mcs)

    # Create a simple pattern: 010101...
    original = np.array([(i % 2) for i in range(n_cbps)], dtype=np.uint8)

    print(f"  Original bits (first 20): {original[:20]}")

    interleaved = ht_interleave(original, n_sym, mcs, reverse=False)
    print(f"  Interleaved bits (first 20): {interleaved[:20]}")

    deinterleaved = ht_interleave(interleaved, n_sym, mcs, reverse=True)
    print(f"  Deinterleaved bits (first 20): {deinterleaved[:20]}")

    if np.array_equal(original, deinterleaved):
        print("  ✓ Pattern test: PASS")
        return True
    else:
        print("  ✗ Pattern test: FAIL")
        return False

def main():
    print("=" * 60)
    print("HT Deinterleaving Algorithm Test")
    print("=" * 60)

    # Test roundtrip for all MCS
    if not test_roundtrip():
        print("\n✗ Roundtrip test FAILED")
        return 1

    # Test specific pattern
    if not test_specific_pattern():
        print("\n✗ Pattern test FAILED")
        return 1

    print("\n" + "=" * 60)
    print("All tests PASSED ✓")
    print("=" * 60)
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())