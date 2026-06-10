#!/home/hy/conda/envs/gnuradio/bin/python
"""
Offline classification of equalized L-SIG constellation dumps from USRP.

Parses `[LSIG_EQ_FULL]` lines (atomic dump from commit cac6fff) and
classifies each frame as one of:
  OK              -- magnitude ~1, phase ~0 or pi, margin > 0.5
  PHASE_ROTATION  -- magnitude ~1, phase consistently off-axis
  MAGNITUDE_ERROR -- |H| misestimated, eq_lsig scaled wrong
  NOISE_LIKE      -- points scattered uniformly (BPSK boundary)
  MIXED           -- multiple failure modes in same frame

Heuristics:
  - mean magnitude: should be ~1.0 for BPSK; <0.3 or >3.0 = magnitude error
  - BPSK hard-decision margin: |Re(eq)| - |Im(eq)|; >0.5 = good
  - phase spread: low spread at non-zero phase = rotation; high spread = noise

Lines with TRUNC suffix or unmatched regex are skipped.
"""
import re
import sys
import numpy as np


RE_FULL = re.compile(
    r"\[LSIG_EQ_FULL\] is_ht=(\d+) H_mag=([\d\.,\-]+) "
    r"rx=([\d\.,\-]+) eq=([\d\.,\-]+)"
)


def parse_dump_line(line):
    """Return (is_ht, H_mag[52], rx[52], eq[52] complex) or None."""
    if " TRUNC" in line:
        return None
    m = RE_FULL.search(line)
    if not m:
        return None
    is_ht = int(m.group(1))
    H_mag_str = m.group(2).rstrip(",")
    rx_str = m.group(3).rstrip(",")
    eq_str = m.group(4).rstrip(",")
    H_mag = [float(x) for x in H_mag_str.split(",")]
    rx = [float(x) for x in rx_str.split(",")]
    eq_flat = [float(x) for x in eq_str.split(",")]
    if len(H_mag) != 52 or len(rx) != 52 or len(eq_flat) != 104:
        return None
    eq = np.array(eq_flat[0::2]) + 1j * np.array(eq_flat[1::2])
    return is_ht, np.array(H_mag), np.array(rx), eq


def classify_frame(eq, H_mag, rx):
    """Return (verdict, stats_dict)."""
    mag = np.abs(eq)
    margin = np.abs(eq.real) - np.abs(eq.imag)
    phase_deg = np.angle(eq, deg=True)
    # Wrap phases to [-180, 180]
    phase_deg = (phase_deg + 180.0) % 360.0 - 180.0

    mean_mag = float(np.mean(mag))
    std_mag = float(np.std(mag))
    mean_margin = float(np.mean(margin))
    std_margin = float(np.std(margin))
    # Phase: compute "concentration" — std of phases
    # For BPSK, phases are bimodal (clustered near 0 and pi);
    # the std in degrees will be ~90° (random across both clusters).
    # For phase rotation, all phases cluster near a single value,
    # so std should be small.
    # For noise-like, std should be near 104° (uniform).
    phase_spread = float(np.std(phase_deg))
    # Detect bimodal: if 25-75% of phases are at +ve Re vs -ve Re
    pos_re_frac = float(np.mean(eq.real > 0))
    is_bimodal = 0.3 < pos_re_frac < 0.7

    stats = {
        "mean_mag": mean_mag,
        "std_mag": std_mag,
        "mean_margin": mean_margin,
        "std_margin": std_margin,
        "phase_spread": phase_spread,
        "pos_re_frac": pos_re_frac,
        "is_bimodal": is_bimodal,
    }

    # Classification heuristics
    if mean_mag < 0.3 or mean_mag > 3.0:
        return "MAGNITUDE_ERROR", stats
    if std_mag > 1.5 * mean_mag:
        return "MAGNITUDE_ERROR", stats
    if mean_margin < 0.2 and phase_spread > 70:
        return "NOISE_LIKE", stats
    if mean_margin > 0.5 and phase_spread < 30 and is_bimodal:
        return "OK", stats
    if mean_margin > 0.3 and phase_spread < 60 and not is_bimodal:
        return "PHASE_ROTATION", stats
    if is_bimodal and mean_margin > 0.3:
        return "OK", stats
    return "MIXED", stats


def analyze_log(log_path):
    """Walk log file, parse dumps, classify each frame, aggregate."""
    with open(log_path, encoding="utf-8", errors="replace") as f:
        raw_lines = f.readlines()

    raw_count = sum(1 for l in raw_lines if "[LSIG_EQ_FULL]" in l)
    parsed = [parse_dump_line(l) for l in raw_lines if "[LSIG_EQ_FULL]" in l]
    parsed = [p for p in parsed if p is not None]
    shredded = raw_count - len(parsed)
    print(f"Total [LSIG_EQ_FULL] tags: {raw_count}")
    print(f"  well-formed: {len(parsed)}")
    print(f"  shredded/truncated: {shredded}")
    if raw_count > 0 and shredded * 2 > raw_count:
        print("WARNING: >50% lines shredded. Check USRP_LOG atomicity.")
        return

    if not parsed:
        print("No valid dumps to analyze.")
        return

    verdicts = []
    all_stats = []
    for is_ht, H_mag, rx, eq in parsed:
        v, stats = classify_frame(eq, H_mag, rx)
        verdicts.append(v)
        all_stats.append(stats)

    from collections import Counter
    counts = Counter(verdicts)
    total = len(verdicts)
    print(f"\n=== Verdict distribution (n={total} frames) ===")
    for v, c in counts.most_common():
        print(f"  {v:18s}: {c:3d} ({100*c/total:.0f}%)")

    # Aggregate stats across all frames
    all_eq = np.concatenate([p[3] for p in parsed])
    all_mag = np.abs(all_eq)
    all_margin = np.abs(all_eq.real) - np.abs(all_eq.imag)
    print("\n=== Aggregate stats (across all frames x 52 sc) ===")
    print(f"  mean |eq|:    {np.mean(all_mag):.3f}  (BPSK expect ~1.0)")
    print(f"  std |eq|:     {np.std(all_mag):.3f}")
    print(f"  mean margin:  {np.mean(all_margin):.3f}  (BPSK expect >0.5)")
    print(f"  std margin:   {np.std(all_margin):.3f}")
    print(f"  mean H_mag:   {np.mean([np.mean(p[1]) for p in parsed]):.3f}")
    print(f"  mean rx_mag:  {np.mean([np.mean(p[2]) for p in parsed]):.3f}")

    # Final verdict + recommendation
    if not counts:
        return
    dominant = counts.most_common(1)[0][0]
    dominant_pct = 100 * counts[dominant] / total
    print(f"\n=== DOMINANT FAILURE MODE: {dominant} ({dominant_pct:.0f}%) ===")
    if dominant == "OK":
        print("Constellation looks OK. Issue is downstream of equalization.")
        print("Next: investigate viterbi metric threshold / deinterleaver.")
    elif dominant == "PHASE_ROTATION":
        print("Consistent phase offset across subcarriers. Check:")
        print("  - d_phase_diff_per_sc[i] * counter compensation direction")
        print("  - Common CFO applied to L-LTF0 before H estimation")
        print("  - Sample clock offset (SFO magnitude)")
    elif dominant == "MAGNITUDE_ERROR":
        print(f"Mean |eq| = {np.mean(all_mag):.2f}, expected ~1.0. Check:")
        print("  - H estimation magnitude in estimate_header_channel_from_lltf52")
        print("  - kFftNormalize scaling")
        print("  - L-LTF template normalization")
    elif dominant == "NOISE_LIKE":
        print("Constellation scattered uniformly. Check:")
        print("  - FFT window timing (sub-sample misalignment -> ISI)")
        print("  - sync_long frame_start accuracy")
        print("  - ht_symbol_splitter symbol alignment")
    elif dominant == "MIXED":
        print("Mixed failure mode. Per-frame analysis needed.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_path>")
        sys.exit(1)
    analyze_log(sys.argv[1])
