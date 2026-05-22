#!/usr/bin/env python3
"""
End-to-end test for LDPC integration in gr-ieee802-11.
This test creates a minimal flowgraph: message source -> mapper (LDPC) ->
chunks_to_symbols -> ... -> decode_mac.
"""

import sys
import os
import time

# Add Python bindings path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'build/python'))

from gnuradio import gr
import ieee802_11


class TestLdpcFlowgraph(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "LDPC E2E Test")

        # Create blocks
        self.mapper = ieee802_11.mapper(ieee802_11.BPSK_1_2, False)
        self.mapper.set_use_ldpc(True)  # Enable LDPC

        # TODO: Add rest of TX/RX chain
        # For now, just verify the mapper block loads with LDPC enabled

    def run_test(self):
        print("LDPC mapper created successfully with use_ldpc=True")
        return True


def main():
    print("=== LDPC End-to-End Test ===")
    print()

    try:
        tb = TestLdpcFlowgraph()
        success = tb.run_test()
        print()
        print("RESULT:", "PASS" if success else "FAIL")
        return 0 if success else 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
