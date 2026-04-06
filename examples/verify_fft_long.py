#!/usr/bin/env python
"""验证 FFT_LONG 计算是否正确"""
import numpy as np

# LONG from sync_long.cc (64 samples of L-LTF time domain)
LONG = np.array([
    -0.0455-1.0679j, 0.3528-0.9865j, 0.8594+0.7348j, 0.1874+0.2475j,
    0.5309-0.7784j, -1.0218-0.4897j, -0.3401-0.9423j, 0.8657-0.2298j,
    0.4734+0.0362j, 0.0088-1.0207j, -1.2142-0.4205j, 0.2172-0.5195j,
    0.5207-0.1326j, -0.1995+1.4259j, 1.0583-0.0363j, 0.5547-0.5547j,
    0.3277+0.8728j, -0.5077+0.3488j, -1.1650+0.5789j, 0.7297+0.8197j,
    0.6173+0.1253j, -0.5353+0.7214j, -0.5011-0.1935j, -0.3110-1.3392j,
    -1.0818-0.1470j, -1.1300-0.1820j, 0.6663-0.6571j, -0.0249+0.4773j,
    -0.8155+1.0218j, 0.8140+0.9396j, 0.1090+0.8662j, -1.3868+0.0000j,
    0.1090-0.8662j, 0.8140-0.9396j, -0.8155-1.0218j, -0.0249-0.4773j,
    0.6663+0.6571j, -1.1300+0.1820j, -1.0818+0.1470j, -0.3110+1.3392j,
    -0.5011+0.1935j, -0.5353-0.7214j, 0.6173-0.1253j, 0.7297-0.8197j,
    -1.1650-0.5789j, -0.5077-0.3488j, 0.3277-0.8728j, 0.5547+0.5547j,
    1.0583+0.0363j, -0.1995-1.4259j, 0.5207+0.1326j, 0.2172+0.5195j,
    -1.2142+0.4205j, 0.0088+1.0207j, 0.4734-0.0362j, 0.8657+0.2298j,
    -0.3401+0.9423j, -1.0218+0.4897j, 0.5309+0.7784j, 0.1874-0.2475j,
    0.8594-0.7348j, 0.3528+0.9865j, -0.0455+1.0679j, 1.3868-0.0000j
], dtype=np.complex64)

# FFT_LONG from ls.cc
FFT_LONG_expected = np.array([
    -0.0002+0.0000j, 8.8326+0.8699j, -8.7047-1.7315j, -8.4932-2.5764j,
    8.1998+3.3965j, 7.8277+4.1840j, -7.3798-4.9310j, 6.8608+5.6305j,
    -6.2757-6.2757j, 5.6303+6.8605j, -4.9308-7.3795j, -4.1839-7.8275j,
    -3.3964-8.1997j, -2.5763-8.4929j, -1.7313-8.7038j, 0.8700+8.8329j,
    0.0000+8.8750j, 0.8699-8.8325j, 1.7314-8.7045j, -2.5765+8.4934j,
    3.3965-8.1998j, -4.1837+7.8272j, 4.9307-7.3793j, -5.6305+6.8608j,
    -6.2757+6.2757j, -6.8603+5.6301j, -7.3795+4.9308j, 0.0003-0.0001j,
    0.0006-0.0002j, -0.0004+0.0001j, -0.0001+0.0000j, -0.0004+0.0000j,
    -0.0006+0.0000j, -0.0002-0.0000j, -0.0005-0.0001j, -0.0001-0.0000j,
    -0.0002-0.0001j, -0.0004-0.0002j, -7.3794-4.9308j, -6.8609-5.6306j,
    6.2757+6.2757j, 5.6305+6.8608j, -4.9311-7.3799j, -4.1838-7.8273j,
    3.3963+8.1993j, -2.5764-8.4933j, 1.7314+8.7045j, -0.8699-8.8323j,
    0.0000-8.8750j, 0.8699-8.8324j, 1.7314-8.7041j, 2.5764-8.4933j,
    3.3963-8.1994j, -4.1838+7.8273j, -4.9311+7.3799j, 5.6305-6.8607j,
    6.2757-6.2757j, -6.8602+5.6301j, 7.3790-4.9305j, -7.8273+4.1838j,
    8.2000-3.3965j, 8.4929-2.5763j, 8.7044-1.7314j, 8.8326-0.8699j
], dtype=np.complex64)

# Compute FFT of LONG
fft_result = np.fft.fft(LONG)

print("=" * 60)
print("FFT_LONG Verification")
print("=" * 60)

print("\nComparison of FFT_LONG (first 16 bins):")
print(f"{'Bin':>4} {'Computed':>20} {'Expected':>20} {'Diff Mag':>10}")
print("-" * 60)
max_diff = 0.0
for i in range(16):
    diff = abs(fft_result[i] - FFT_LONG_expected[i])
    max_diff = max(max_diff, diff)
    print(f"{i:>4} {fft_result[i]:>20.4f} {FFT_LONG_expected[i]:>20.4f} {diff:>10.6f}")

print("\nFull 64-bin comparison (non-zero bins only):")
print(f"{'Bin':>4} {'Computed':>20} {'Expected':>20} {'Diff Mag':>10}")
print("-" * 60)
for i in range(64):
    diff = abs(fft_result[i] - FFT_LONG_expected[i])
    if diff > 0.01 or i < 6 or i > 58:
        print(f"{i:>4} {fft_result[i]:>20.4f} {FFT_LONG_expected[i]:>20.4f} {diff:>10.6f}")

print("\n" + "=" * 60)
print(f"Maximum difference: {max_diff:.6f}")
if max_diff < 0.001:
    print("STATUS: FFT_LONG matches computed FFT (diff < 0.001)")
else:
    print(f"STATUS: MISMATCH - FFT_LONG differs from computed FFT (max diff = {max_diff:.4f})")

# Check magnitude consistency
print("\n" + "=" * 60)
print("Magnitude check (should be ~8.875 at data subcarriers):")
for i in [6, 7, 8, 9, 10, 11, 25, 39, 53]:
    print(f"  bin {i}: computed mag={np.abs(fft_result[i]):.4f}, expected mag={np.abs(FFT_LONG_expected[i]):.4f}")

# Phase analysis
print("\n" + "=" * 60)
print("Phase analysis for bins 6-10:")
for i in range(6, 11):
    computed_phase = np.angle(fft_result[i])
    expected_phase = np.angle(FFT_LONG_expected[i])
    phase_diff = abs(computed_phase - expected_phase)
    if phase_diff > np.pi:
        phase_diff = 2 * np.pi - phase_diff
    print(f"  bin {i}: computed={computed_phase:.4f} rad ({np.degrees(computed_phase):.1f} deg), expected={expected_phase:.4f} rad ({np.degrees(expected_phase):.1f} deg), diff={phase_diff:.4f} rad")