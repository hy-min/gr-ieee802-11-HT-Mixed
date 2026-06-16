#!/home/hy/conda/envs/gnuradio/bin/python
"""Debug helper: load captured IQ and visualize the L-STF/L-LTF structure."""
import numpy as np
iq = np.fromfile('/tmp/p28_loopback_iq.fc32', dtype=np.complex64)
print(f"Loaded {len(iq)} samples")
print(f"Power: min={np.abs(iq).min():.4e} max={np.abs(iq).max():.4e} "
      f"mean={np.mean(np.abs(iq)**2):.4e}")

# 16-sample auto-correlation
period = 16
a = iq[:-period]
b = iq[period:]
corr = np.abs(a * np.conj(b)) / (np.abs(a) * np.abs(b) + 1e-12)

# Print correlation values around sample 0
print("\ncorr16 around sample 0 (i, corr):")
for i in [0, 100, 200, 500, 1000, 1500, 2000, 3000, 4000, 5000, 10000]:
    if i < len(corr):
        print(f"  i={i:6d}: corr={corr[i]:.3f}")

# Find first high-power region
power = np.abs(iq)**2
power_smooth = np.convolve(power, np.ones(100)/100, mode='same')
print(f"\nPower smoothed max: {power_smooth.max():.4e}")
print("Power smoothed at various samples:")
for i in [0, 100, 500, 1000, 1500, 2000, 3000, 4000, 5000, 10000, 50000]:
    if i < len(power_smooth):
        print(f"  i={i:6d}: pwr_smooth={power_smooth[i]:.4e} corr={corr[i] if i < len(corr) else 0:.3f}")

# Find first place where both power and correlation are high
print("\nSearching for first L-STF-like region (power > 1e-5 AND corr > 0.7)...")
for i in range(0, min(100000, len(corr))):
    if power[i] > 1e-5 and corr[i] > 0.7:
        print(f"  First match at i={i}: pwr={power[i]:.3e} corr={corr[i]:.3f}")
        # Show next 200 values
        for j in range(i, min(i + 250, len(corr)), 16):
            print(f"    j={j:6d} pwr={power[j]:.3e} corr={corr[j]:.3f}")
        break

# Find boundary of first high-power region
hp = power_smooth > 1e-3
print(f"\nFirst high-power region (smoothed > 1e-3):")
runs = []
in_run = False
start = 0
for i, h in enumerate(hp):
    if h and not in_run:
        start = i
        in_run = True
    elif not h and in_run:
        runs.append((start, i - 1, i - start))
        in_run = False
print(f"Found {len(runs)} high-power runs")
for s, e, l in runs[:5]:
    print(f"  {s}-{e} (len={l})")
    # Check correlation at start, middle, end
    for j in [s, s+50, s+100, s+150, s+200, e]:
        if j < len(corr):
            print(f"    j={j}: corr={corr[j]:.3f} pwr={power[j]:.3e}")
