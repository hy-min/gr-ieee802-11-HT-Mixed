#!/home/hy/conda/envs/gnuradio/bin/python
"""Look at IQ structure more carefully."""
import numpy as np
iq = np.fromfile('/tmp/p28_loopback_iq.fc32', dtype=np.complex64)
print(f"Loaded {len(iq)} samples")

# Look at samples 0 to 300 in detail
print("\nSamples 0-300 every 8:")
for i in range(0, 300, 8):
    print(f"  {i:4d}: re={iq[i].real:+.3f} im={iq[i].imag:+.3f} |i|={np.abs(iq[i]):.3f}")

# Look at samples 1000-1300
print("\nSamples 1000-1300 every 8:")
for i in range(1000, 1300, 8):
    print(f"  {i:4d}: re={iq[i].real:+.3f} im={iq[i].imag:+.3f} |i|={np.abs(iq[i]):.3f}")

# Look at 50000-50300
print("\nSamples 50000-50300 every 8:")
for i in range(50000, 50300, 8):
    print(f"  {i:4d}: re={iq[i].real:+.3f} im={iq[i].imag:+.3f} |i|={np.abs(iq[i]):.3f}")

# Look at 200000-200300
print("\nSamples 200000-200300 every 8:")
for i in range(200000, 200300, 8):
    print(f"  {i:4d}: re={iq[i].real:+.3f} im={iq[i].imag:+.3f} |i|={np.abs(iq[i]):.3f}")

# Power profile over time
print("\nMean |i|^2 over 10000-sample windows:")
for w in range(0, len(iq), 100000):
    if w + 100000 <= len(iq):
        win = iq[w:w+100000]
        p = np.mean(np.abs(win)**2)
        print(f"  samples {w:7d}-{w+100000:7d}: pwr={p:.4e}")
