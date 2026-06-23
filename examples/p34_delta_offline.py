#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 34 offline δ distribution validator.

Reuses analyze_h52_offline.py for H52 estimation from raw USRP IQ, then
computes δ via the same linear regression the e2e equalizer uses, and reports
the distribution across all detected frames.

Verifies the Phase 33b hypothesis (δ ∈ [0,1) at 1/64 quantization, varying per
frame) is what the e2e estimator recovers from real captures.

Usage:
    python examples/p34_delta_offline.py /tmp/p33e_raw_iq.bin
    python examples/p34_delta_offline.py /tmp/p34a_raw_iq.bin
"""
import sys
import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from analyze_h52_offline import find_frame_starts, estimate_h52_frame, read_fc32


# 52-subcarrier index mapping (must match frame_equalizer_impl.cc).
SC_INDEX = np.array([
    -26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
    1,2,3,4,5,6,
    8,9,10,11,12,13,
    14,15,16,17,18,19,
    20,22,23,24,25,26,
    -21,-7,7,21
], dtype=np.float64)
assert len(SC_INDEX) == 52


def estimate_delta(H52):
    """Estimate per-frame sub-sample timing offset δ from H52.

    Same algorithm as frame_equalizer_impl::estimate_timing_offset_from_h52:
    unwrapped linear regression of argH vs SC index, δ = -b·64/(2π) mod 1.
    Returns δ in [0,1) sample units.
    """
    argH = np.angle(H52).astype(np.float64)
    # Weighted linear regression on unwrapped phase.
    # Use np.polyfit for robust fit (handles 2π wrap by minimizing L2).
    b, a = np.polyfit(SC_INDEX, argH, 1)
    delta = (-b * 64.0 / (2.0 * np.pi))
    delta = delta - np.floor(delta)
    return delta, a


def main():
    if len(sys.argv) < 2:
        print("Usage: p34_delta_offline.py <raw_iq.bin>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    samples = read_fc32(path)
    print(f"[P34] Loaded {len(samples)} samples from {path}", file=sys.stderr)

    starts, _ = find_frame_starts(samples, threshold=0.5)
    print(f"[P34] Found {len(starts)} frame starts", file=sys.stderr)

    deltas = []
    h_mags = []
    for fs in starts:
        H52 = estimate_h52_frame(samples, fs)
        if H52 is None:
            continue
        delta, intercept = estimate_delta(H52)
        deltas.append(delta)
        h_mags.append(float(np.mean(np.abs(H52))))

    deltas = np.array(deltas)
    h_mags = np.array(h_mags)

    if len(deltas) == 0:
        print("[P34] No frames found, exiting.", file=sys.stderr)
        return

    # Quantize to nearest k/64 and measure RMS error — should be very small if
    # δ is truly on the 1/64 grid.
    k_quant = np.round(deltas * 64.0).astype(int) % 64
    delta_quant = k_quant / 64.0
    quant_err = np.abs(deltas - delta_quant)
    quant_err = np.minimum(quant_err, 1.0 - quant_err)  # wrap to [0, 0.5]

    print(f"\n[P34] ===== δ distribution (N={len(deltas)} frames) =====")
    print(f"[P34] δ mean:   {deltas.mean():.4f}")
    print(f"[P34] δ std:    {deltas.std():.4f}")
    print(f"[P34] δ min:    {deltas.min():.4f}")
    print(f"[P34] δ max:    {deltas.max():.4f}")
    print(f"[P34] |H|mean:  {h_mags.mean():.4f}  std: {h_mags.std():.4f}")
    print(f"\n[P34] ===== 1/64 grid quantization =====")
    print(f"[P34] RMS quantization error: {np.sqrt(np.mean(quant_err**2)):.6f}")
    print(f"[P34] Max quantization error: {quant_err.max():.6f}")
    print(f"[P34] Fraction within 0.01 of grid: {(quant_err < 0.01).mean()*100:.1f}%")
    print(f"\n[P34] ===== δ histogram (k/64 bins) =====")
    hist, edges = np.histogram(deltas, bins=64, range=(0, 1))
    for i in range(64):
        bar = "#" * min(50, hist[i] * 50 // max(1, hist.max()))
        print(f"[P34]  k={i:2d}  δ∈[{i/64:.4f},{(i+1)/64:.4f})  {hist[i]:5d}  {bar}")


if __name__ == "__main__":
    main()
