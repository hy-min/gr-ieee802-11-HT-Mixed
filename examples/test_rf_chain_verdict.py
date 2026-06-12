#!/usr/bin/env python3
"""
Aggregate verdict from Phase 5 RF chain diagnostics.

Parses the 3 diagnostic logs (CW sweep, TDD transient, LO phase noise) and
produces a single composite verdict identifying the corruption source.

Usage:
  python examples/test_rf_chain_verdict.py \
    --cw-log /tmp/rf_chain_cw_sweep.log \
    --tdd-log /tmp/tdd_transient.log \
    --lo-log /tmp/lo_phase_noise.log

Output:
  RF Chain: RF_CHAIN_FLAT (flatness=1.50 dB)
  TDD Switch: TDD_STEADY (median ratio=0.987)
  USRP LO: LO_CLEAN (total_rms=0.0234 rad)

  COMPOSITE VERDICT: RF_CHAIN_OK
  Recommended action: Look beyond RF chain (e.g., algorithm, environment)

Composite verdicts:
  - RF_CHAIN_OK:         all 3 layers clean
  - RF_CHAIN_PROBLEM:    at least one DEGRADED or BROKEN
  - INSUFFICIENT_DATA:   at least one log missing
"""

import argparse
import os
import re
import sys


# Per-layer clean verdicts (used to detect RF_CHAIN_OK)
CLEAN_VERDICTS = {
    "RF_CHAIN_FLAT",
    "TDD_STEADY",
    "LO_CLEAN",
}

# TDD transient classification thresholds (median ratio of first/steady LTF amp)
TDD_STEADY_MIN = 0.8    # >= this  -> TDD_STEADY
TDD_GLITCH_MIN = 0.5    # >= this  -> TDD_GLITCH; < this -> TDD_BROKEN

# LO phase noise classification thresholds (integrated RMS rad in band)
LO_CLEAN_MAX = 0.1      # < this   -> LO_CLEAN
LO_DEGRADED_MAX = 0.5   # < this   -> LO_DEGRADED; >= this -> LO_BROKEN

# Synthetic log paths (smoke test in __main__)
SYNTH_CW_LOG = "/tmp/cw_synth_rfchain.log"
SYNTH_TDD_LOG = "/tmp/tdd_synth_rfchain.log"
SYNTH_LO_LOG = "/tmp/lo_synth_rfchain.log"


def _read_lines(path):
    """Yield non-empty stripped lines from a log file; return [] if missing."""
    if not os.path.isfile(path):
        return []
    with open(path, "r") as f:
        return [line.rstrip() for line in f if line.strip()]


def parse_cw_sweep(log_path):
    """Parse CW sweep log. Returns (verdict, flatness_db).

    The log contains lines like:
        [CW_SWEEP] freq=5.000GHz amp=-12.34 dBFS
        VERDICT: RF_CHAIN_FLAT (flatness=1.23 dB)
    """
    verdict = None
    flatness = None
    for line in _read_lines(log_path):
        m = re.search(r"VERDICT:\s*(RF_CHAIN_[A-Z]+)", line)
        if m and verdict is None:
            verdict = m.group(1)
        m = re.search(r"flatness=([\d\.]+)\s*dB", line)
        if m and flatness is None:
            try:
                flatness = float(m.group(1))
            except ValueError:
                pass
    if verdict is None:
        return "NO_DATA", 0.0
    if flatness is None:
        # Verdict present but metric missing - degrade to NO_DATA
        return "NO_DATA", 0.0
    return verdict, flatness


def parse_tdd_transient(log_path):
    """Parse TDD transient log. Returns (verdict, median_ratio).

    Log lines look like:
        [TDD_TRANSIENT] rx_idx=1 first_ltf_amp=0.450 steady_ltf_amp=0.456 ratio=0.987
    """
    ratios = []
    for line in _read_lines(log_path):
        m = re.search(r"ratio=([\d\.]+)", line)
        if m:
            try:
                ratios.append(float(m.group(1)))
            except ValueError:
                pass
    if not ratios:
        return "NO_DATA", 0.0
    sorted_ratios = sorted(ratios)
    median = sorted_ratios[len(sorted_ratios) // 2]
    if median >= TDD_STEADY_MIN:
        verdict = "TDD_STEADY"
    elif median >= TDD_GLITCH_MIN:
        verdict = "TDD_GLITCH"
    else:
        verdict = "TDD_BROKEN"
    return verdict, median


def parse_lo_phase_noise(log_path):
    """Parse LO phase noise log. Returns (verdict, total_rms_rad).

    Log lines look like:
        [LO_PN] center=5.180GHz total_rms_rad=0.0044
        VERDICT: LO_CLEAN (total_rms=0.0044 rad)
    """
    total_rms = None
    for line in _read_lines(log_path):
        # Prefer the metric from a VERDICT line if present
        m = re.search(r"VERDICT:\s*(LO_[A-Z]+).*total_rms=([\d\.]+)", line)
        if m:
            try:
                return m.group(1), float(m.group(2))
            except ValueError:
                pass
        m = re.search(r"total_rms_rad=([\d\.]+)", line)
        if m and total_rms is None:
            try:
                total_rms = float(m.group(1))
            except ValueError:
                pass
    if total_rms is None:
        return "NO_DATA", 0.0
    if total_rms < LO_CLEAN_MAX:
        verdict = "LO_CLEAN"
    elif total_rms < LO_DEGRADED_MAX:
        verdict = "LO_DEGRADED"
    else:
        verdict = "LO_BROKEN"
    return verdict, total_rms


def composite_verdict(cw_v, tdd_v, lo_v):
    """Aggregate 3 layer verdicts into a single composite."""
    layers = [cw_v, tdd_v, lo_v]
    # All data missing -> INSUFFICIENT_DATA
    if all(v == "NO_DATA" for v in layers):
        return "INSUFFICIENT_DATA", "Re-run all 3 diagnostics"
    # Any single missing layer -> INSUFFICIENT_DATA (composite is suspect)
    if "NO_DATA" in layers:
        missing_idx = [i for i, v in enumerate(layers) if v == "NO_DATA"]
        names = ["CW", "TDD", "LO"]
        missing = ", ".join(names[i] for i in missing_idx)
        return "INSUFFICIENT_DATA", f"Re-run missing diagnostic(s): {missing}"
    # All 3 layers explicitly clean -> OK
    if all(v in CLEAN_VERDICTS for v in layers):
        return (
            "RF_CHAIN_OK",
            "Look beyond RF chain (e.g., algorithm, environment, multipath)",
        )
    # Otherwise at least one DEGRADED or BROKEN
    problem = [v for v in layers if v not in CLEAN_VERDICTS]
    return (
        f"RF_CHAIN_PROBLEM ({', '.join(problem)})",
        f"Investigate: {', '.join(problem)}",
    )


def format_report(cw, tdd, lo):
    """Format the 3-tuple (verdict, metric) and the composite verdict."""
    cw_v, cw_x = cw
    tdd_v, tdd_x = tdd
    lo_v, lo_x = lo

    lines = []
    # Layer reports
    if cw_v == "NO_DATA":
        lines.append("RF Chain: NO_DATA")
    else:
        lines.append(f"RF Chain: {cw_v} (flatness={cw_x:.2f} dB)")
    if tdd_v == "NO_DATA":
        lines.append("TDD Switch: NO_DATA")
    else:
        lines.append(f"TDD Switch: {tdd_v} (median ratio={tdd_x:.3f})")
    if lo_v == "NO_DATA":
        lines.append("USRP LO: NO_DATA")
    else:
        lines.append(f"USRP LO: {lo_v} (total_rms={lo_x:.4f} rad)")
    lines.append("")

    composite, action = composite_verdict(cw_v, tdd_v, lo_v)
    lines.append(f"COMPOSITE VERDICT: {composite}")
    lines.append(f"Recommended action: {action}")
    return "\n".join(lines)


def main():
    is_self_test = "--self-test" in sys.argv
    ap = argparse.ArgumentParser(
        description="Aggregate 3 RF chain diagnostic logs into a composite verdict."
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="Run a smoke test with synthetic logs (ignores --cw-log etc.)",
    )
    ap.add_argument(
        "--cw-log", required=not is_self_test, default=None,
        help="Path to CW sweep log",
    )
    ap.add_argument(
        "--tdd-log", required=not is_self_test, default=None,
        help="Path to TDD transient log (placeholder; missing is OK)",
    )
    ap.add_argument(
        "--lo-log", required=not is_self_test, default=None,
        help="Path to LO phase noise log",
    )
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()

    cw = parse_cw_sweep(args.cw_log)
    tdd = parse_tdd_transient(args.tdd_log)
    lo = parse_lo_phase_noise(args.lo_log)

    print(format_report(cw, tdd, lo))
    # Exit code 0 = composite OK or problem identified, 2 = insufficient data
    composite, _ = composite_verdict(cw[0], tdd[0], lo[0])
    if composite == "INSUFFICIENT_DATA":
        return 2
    return 0


def run_self_test():
    """Smoke test with synthetic logs covering happy path, missing TDD, and broken LO."""
    import tempfile

    print("=== Self-test 1: all 3 layers clean ===")
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("[CW_SWEEP] freq=5.000GHz amp=-12.34 dBFS\n")
        f.write("[CW_SWEEP] freq=5.180GHz amp=-12.50 dBFS\n")
        f.write("VERDICT: RF_CHAIN_FLAT (flatness=0.30 dB)\n")
        cw_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("[TDD_TRANSIENT] rx_idx=1 first_ltf_amp=0.450 steady_ltf_amp=0.456 ratio=0.987\n")
        f.write("[TDD_TRANSIENT] rx_idx=2 first_ltf_amp=0.452 steady_ltf_amp=0.456 ratio=0.991\n")
        tdd_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("[LO_PN] center=5.180GHz total_rms_rad=0.0234\n")
        f.write("[LO_PN] floor_db=-95.20\n")
        f.write("VERDICT: LO_CLEAN (total_rms=0.0234 rad)\n")
        lo_path = f.name
    print(format_report(parse_cw_sweep(cw_path), parse_tdd_transient(tdd_path), parse_lo_phase_noise(lo_path)))
    print()

    print("=== Self-test 2: missing TDD log ===")
    print(format_report(
        parse_cw_sweep(cw_path),
        parse_tdd_transient("/tmp/does_not_exist_rfchain.log"),
        parse_lo_phase_noise(lo_path),
    ))
    print()

    print("=== Self-test 3: broken LO ===")
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("[LO_PN] center=5.180GHz total_rms_rad=1.2345\n")
        f.write("VERDICT: LO_BROKEN (total_rms=1.2345 rad)\n")
        lo_broken_path = f.name
    print(format_report(
        parse_cw_sweep(cw_path),
        parse_tdd_transient(tdd_path),
        parse_lo_phase_noise(lo_broken_path),
    ))
    print()

    print("=== Self-test 4: degraded CW ===")
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("VERDICT: RF_CHAIN_DEGRADED (flatness=4.50 dB)\n")
        cw_deg_path = f.name
    print(format_report(
        parse_cw_sweep(cw_deg_path),
        parse_tdd_transient(tdd_path),
        parse_lo_phase_noise(lo_path),
    ))
    print()

    # Cleanup
    for p in (cw_path, tdd_path, lo_path, lo_broken_path, cw_deg_path):
        try:
            os.unlink(p)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
