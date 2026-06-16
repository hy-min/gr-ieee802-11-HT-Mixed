#!/home/hy/conda/envs/gnuradio/bin/python
"""Debug helper: load the captured IQ and print correlation values + frame search."""
import sys
import numpy as np

iq = np.fromfile('/tmp/p28_loopback_iq.fc32', dtype=np.complex64)
print(f"Loaded {len(iq)} samples")

# 16-sample auto-correlation
period = 16
a = iq[:-period]
b = iq[period:]
corr = np.abs(a * np.conj(b)) / (np.abs(a) * np.abs(b) + 1e-12)

# Histogram of correlation values
print(f"\ncorr min={corr.min():.3f} max={corr.max():.3f} "
      f"mean={corr.mean():.3f} median={np.median(corr):.3f}")
for thresh in [0.3, 0.5, 0.6, 0.7, 0.8, 0.9]:
    n_above = (corr > thresh).sum()
    print(f"  corr > {thresh}: {n_above} samples "
          f"({n_above/len(corr)*100:.1f}%)")

# 80-sample auto-correlation (CP check)
period80 = 80
a80 = iq[:-period80]
b80 = iq[period80:]
corr80 = np.abs(a80 * np.conj(b80)) / (np.abs(a80) * np.abs(b80) + 1e-12)
print(f"\ncorr80 min={corr80.min():.3f} max={corr80.max():.3f} "
      f"mean={corr80.mean():.3f}")
for thresh in [0.5, 0.7, 0.9]:
    n_above = (corr80 > thresh).sum()
    print(f"  corr80 > {thresh}: {n_above} samples "
          f"({n_above/len(corr80)*100:.1f}%)")

# Power
power = np.abs(iq)**2
print(f"\npower min={power.min():.3e} max={power.max():.3e} "
      f"mean={power.mean():.3e}")
# Find samples with high power
for thresh in [0.001, 0.01, 0.1, 0.5]:
    n_above = (power > thresh).sum()
    print(f"  power > {thresh}: {n_above} samples "
          f"({n_above/len(power)*100:.1f}%)")

# Find high-power region
power_smooth = np.convolve(power, np.ones(100)/100, mode='same')
high_pwr = power_smooth > 0.001
print(f"\nhigh-power region (smoothed > 0.001):")
runs = []
in_run = False
start = 0
for i, hp in enumerate(high_pwr):
    if hp and not in_run:
        start = i
        in_run = True
    elif not hp and in_run:
        runs.append((start, i - 1, i - start))
        in_run = False
if in_run:
    runs.append((start, len(high_pwr) - 1, len(high_pwr) - start))
print(f"Found {len(runs)} high-power runs")
for s, e, l in runs[:10]:
    print(f"  {s}-{e} (len={l})")

# Check first few hundred samples
print(f"\nFirst 200 samples (i, real, imag, |i|^2, corr16):")
for i in range(0, 200, 10):
    if i < len(iq):
        c = corr[i] if i < len(corr) else 0
        print(f"  {i}: {iq[i].real:+.3f} {iq[i].imag:+.3f} "
              f"|i|²={power[i]:.3e} corr16={c:.3f}")
