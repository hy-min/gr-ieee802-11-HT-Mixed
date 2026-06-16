#!/home/hy/conda/envs/gnuradio/bin/python
"""Examine the first 200K samples carefully to find the L-STF/L-LTF boundary."""
import numpy as np
iq = np.fromfile('/tmp/p28_loopback_iq.fc32', dtype=np.complex64)
N = min(300000, len(iq))
x = iq[:N]

# Mean power over 100-sample windows
print("Power over 100-sample windows (showing first 3000 samples):")
for i in range(0, 3000, 100):
    win = x[i:i+100]
    p = np.mean(np.abs(win)**2)
    print(f"  {i:5d}: pwr={p:.4e}  |sample|={np.abs(x[i]):.3f}")

print("\n--- Around the L-STF end (typically sample 160) ---")
# 16-sample auto-correlation
period = 16
a = x[:-period]
b = x[period:]
corr = np.abs(a * np.conj(b)) / (np.abs(a) * np.abs(b) + 1e-12)

print("corr16 in first 500 samples:")
for i in range(0, 500, 16):
    p = np.mean(np.abs(x[i:i+16])**2)
    c = corr[i] if i < len(corr) else 0
    print(f"  i={i:4d}: pwr={p:.4e} corr={c:.3f}")

# Check where corr drops
print("\nFind where corr first drops below 0.5 (L-STF end):")
for i in range(0, 2000, 16):
    c = corr[i] if i < len(corr) else 0
    if c < 0.5:
        print(f"  i={i}: corr={c:.3f} (below 0.5)")
        break
else:
    print("  No drop below 0.5 found in first 2000 samples")

# Find where the 80-sample-period correlation is high (CP correlation)
period80 = 80
a80 = x[:-period80]
b80 = x[period80:]
corr80 = np.abs(a80 * np.conj(b80)) / (np.abs(a80) * np.abs(b80) + 1e-12)
print("\ncorr80 in first 500 samples:")
for i in range(0, 500, 16):
    c = corr80[i] if i < len(corr80) else 0
    print(f"  i={i:4d}: corr80={c:.3f}")

# 64-sample correlation (L-LTF period)
period64 = 64
a64 = x[:-period64]
b64 = x[period64:]
corr64 = np.abs(a64 * np.conj(b64)) / (np.abs(a64) * np.abs(b64) + 1e-12)
print("\ncorr64 in samples 100-500 (potential L-LTF region):")
for i in range(100, 500, 16):
    c = corr64[i] if i < len(corr64) else 0
    print(f"  i={i:4d}: corr64={c:.3f}")
