#!/usr/bin/env python3
"""Generate IEEE 802.11 L-LTF matched filter taps matching TX mixed_mode_carrier_allocator"""

import numpy as np

# TX LTF sequence from mixed_mode_carrier_allocator.py (IFFT natural/memory order)
# DC@0, +f bins 1-26, guards 27-37, -f bins 38-63
LEGACY_LTF = (
    0,
    1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1
)

# Build 64-bin frequency array
freq_seq = np.zeros(64, dtype=complex)
freq_seq[1:27] = LEGACY_LTF[1:27]
freq_seq[38:64] = LEGACY_LTF[38:64]

# Apply same window as TX IFFT: 1/sqrt(52)
window = 1.0 / np.sqrt(52)
freq_seq *= window

# IFFT (numpy ifft divides by N=64, matching GNU Radio behavior)
time_seq = np.fft.ifft(freq_seq)

# Matched filter: time-reversed + conjugate (NO arbitrary scaling!)
taps = np.conj(time_seq[::-1])

# Print C++ format
print("// IEEE 802.11 L-LTF Matched Filter Taps")
print("// Generated from mixed_mode_carrier_allocator.py LEGACY_LTF")
print("// Window: 1/sqrt(52), IFFT: 64-point")
print("const std::vector<gr_complex> sync_long_impl::LONG = {")
for i, t in enumerate(taps):
    if i % 4 == 0:
        print(f"    // taps[{i:02d}:{min(i+4, 64):02d}]")
    comma = "," if i < 63 else ""
    print(f"    gr_complex({t.real:+.10f}, {t.imag:+.10f}){comma}")
print("};")

print(f"\n// Peak: {np.max(np.abs(taps)):.6f}")
print(f"// RMS:  {np.sqrt(np.mean(np.abs(taps)**2)):.6f}")

# Verification: check imaginary parts
print(f"// Max imaginary: {np.max(np.abs(np.imag(taps))):.10f}")
