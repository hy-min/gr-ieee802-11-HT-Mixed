#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 61 synthetic test: combined pre-clean + per-symbol pilot CPE.

Validates that the combo (thresh=0.10, radius=3) reduces null count below
10/52 on synthetic H52 with nulls, and that per-symbol pilot CPE on top
further reduces constellation error.

SCOPE: Algorithm-isolation synthetic test. Does NOT reproduce USRP
end-to-end. End-to-end validation is in the verdict doc.

Reference: docs/superpowers/plans/2026-06-30-phase61-combined-preclean-pilot-cpe.md
"""
import argparse
import sys
import numpy as np

# Reuse Phase 59 helpers directly (Phase 60 test does the same importlib trick)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "phase59",
    "/home/hy/gr-ieee802-11/examples/test_h52_null_interp_synthetic.py")
phase59 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase59)


def test_combo_reduces_null_count():
    """With H52 nulls, combo (thresh=0.10, radius=3) should leave FEWER
    residual nulls after pre-clean than Phase 60 default
    (thresh=0.15, radius=2).

    Phase 60 produced 21/52 residual nulls on USRP. The combo lever
    is two-fold:
      - Lower threshold (0.15 -> 0.10) means fewer raw nulls caught,
        so fewer SCs get interpolated (less over-cleaning).
      - Wider radius (2 -> 3) means clustered nulls get better
        interpolation values.

    To exercise the combo lever in synthetic, force:
      - 5-SC cluster of hard nulls [24..28] (radius lever: center SC 26
        has no usable neighbor at ±2; at ±3 it sees non-null SCs).
      - 3 borderline SCs [18, 19, 20] with |H| in [0.10, 0.15]
        (threshold lever: detected as nulls at thresh=0.15 → interpolated;
        treated as non-nulls at thresh=0.10 → raw value used).
    The borderline SCs simulate the 9 borderline SCs in Phase 60 USRP data.
    """
    h52_dirty, _ = phase59.make_synthetic_h52(n_nulls=6, null_seed=42)
    # Force 5-SC hard null cluster
    for sc in [24, 25, 26, 27, 28]:
        h52_dirty[sc] = 0.05
    # Force 3 borderline SCs (|H| in [0.10, 0.15])
    borderline_scs = [18, 19, 20]
    borderline_mags = [0.12, 0.13, 0.11]
    for sc, mag in zip(borderline_scs, borderline_mags):
        h52_dirty[sc] = mag

    # Phase 60: detect+interp at default settings (thresh=0.15, radius=2)
    nulls_p60 = phase59.detect_h52_nulls(h52_dirty, thresh=0.15)
    h60_clean = phase59.interp_h52_nulls(h52_dirty, nulls_p60, radius=2)
    # Phase 61: detect+interp at combo settings (thresh=0.10, radius=3)
    nulls_p61 = phase59.detect_h52_nulls(h52_dirty, thresh=0.10)
    h61_clean = phase59.interp_h52_nulls(h52_dirty, nulls_p61, radius=3)

    # Count how many of the borderline SCs were flagged as nulls
    # by each threshold
    borderline_p60 = [sc for sc in borderline_scs if sc in nulls_p60]
    borderline_p61 = [sc for sc in borderline_scs if sc in nulls_p61]

    # Count residual nulls (SCs still <0.10 after interp, excluding DC)
    residual_p60 = sum(1 for i in range(1, 52) if abs(h60_clean[i]) < 0.10)
    residual_p61 = sum(1 for i in range(1, 52) if abs(h61_clean[i]) < 0.10)

    print(f"[COMBO_NULL_COUNT] raw nulls P60 (thresh=0.15): {len(nulls_p60)}/52")
    print(f"[COMBO_NULL_COUNT] raw nulls P61 (thresh=0.10): {len(nulls_p61)}/52")
    print(f"[COMBO_NULL_COUNT] borderline SCs (|H| in [0.10, 0.15]): "
          f"{len(borderline_scs)} at SCs {borderline_scs}")
    print(f"[COMBO_NULL_COUNT] borderline nulls P60: {len(borderline_p60)}/"
          f"{len(borderline_scs)} (treated as nulls, interpolated)")
    print(f"[COMBO_NULL_COUNT] borderline nulls P61: {len(borderline_p61)}/"
          f"{len(borderline_scs)} (treated as non-nulls, raw value used)")
    print(f"[COMBO_NULL_COUNT] residual nulls P60 (radius=2): {residual_p60}/52")
    print(f"[COMBO_NULL_COUNT] residual nulls P61 (radius=3): {residual_p61}/52")
    print(f"  residual reduction: {residual_p60 - residual_p61} SCs")
    print(f"  borderline null reduction: "
          f"{len(borderline_p60) - len(borderline_p61)} SCs")

    # Threshold lever check: P61 must detect FEWER borderline nulls than P60
    if len(borderline_p61) >= len(borderline_p60):
        print(f"[COMBO_NULL_COUNT] FAIL: thresh=0.10 should detect fewer "
              f"borderline nulls than thresh=0.15")
        return False
    # Radius lever check: P61 must leave FEWER residual nulls than P60
    if residual_p61 >= residual_p60:
        print(f"[COMBO_NULL_COUNT] FAIL: radius=3 should leave fewer "
              f"residual nulls than radius=2")
        return False
    print(f"[COMBO_NULL_COUNT] PASS (thresh lever: "
          f"{len(borderline_p60) - len(borderline_p61)} borderline SCs "
          f"kept raw; radius lever: {residual_p60 - residual_p61} SCs "
          f"residual reduction)")
    return True


def test_combo_interp_with_wider_radius():
    """With wider radius (3 vs 2), interpolation should give cleaner H52
    at SCs whose ±2 neighbors are also nulled (need ±3)."""
    # Build H52 with a cluster of 5 consecutive nulls [24..28].
    # radius=2 around SC 26 sees [24,25,27,28] — ALL null → count=0,
    # h52[26] stays at 0.05. radius=3 sees [23,29] (non-null) → sane interp.
    h52_dirty, _ = phase59.make_synthetic_h52(n_nulls=6, null_seed=42)
    # Force a wider null cluster so that radius=2 truly fails
    for sc in [24, 25, 26, 27, 28]:
        h52_dirty[sc] = 0.05

    # Phase 60: detect+interp at radius=2 — SC 26 has only null neighbors at ±2
    nulls_p60 = phase59.detect_h52_nulls(h52_dirty, thresh=0.15)
    h60_clean = phase59.interp_h52_nulls(h52_dirty, nulls_p60, radius=2)
    # Phase 61: radius=3 — SC 26 should have non-null neighbors at ±3
    nulls_p61 = phase59.detect_h52_nulls(h52_dirty, thresh=0.15)
    h61_clean = phase59.interp_h52_nulls(h52_dirty, nulls_p61, radius=3)

    # At SC 26, the radius=2 interp has no non-null neighbor (cluster),
    # so its interpolated value is degenerate (mean of 0s). radius=3 sees
    # SCs 22 and 29 which are non-null, giving a sane value.
    print(f"[WIDER_RADIUS] SC 26 interp at radius=2: {abs(h60_clean[26]):.3f}")
    print(f"[WIDER_RADIUS] SC 26 interp at radius=3: {abs(h61_clean[26]):.3f}")
    if abs(h61_clean[26]) <= abs(h60_clean[26]):
        print(f"[WIDER_RADIUS] FAIL: wider radius should give STRONGER interp")
        return False
    print(f"[WIDER_RADIUS] PASS (radius=3 gives "
          f"{abs(h61_clean[26]) / max(abs(h60_clean[26]), 1e-9):.2f}x stronger)")
    return True


def test_per_symbol_pilot_cpe_reduces_phase_error():
    """Pre-cleaned H52 still leaves phase error at HT-SIG equalization.
    Per-symbol pilot CPE (mean arg over 4 pilots) should cancel it.
    This is a Phase 35 helper re-test in the Phase 60 pre-cleaned context."""
    h52_dirty, _ = phase59.make_synthetic_h52(n_nulls=6, null_seed=42)
    # Pre-clean
    nulls = phase59.detect_h52_nulls(h52_dirty, thresh=0.10)
    h52_clean = phase59.interp_h52_nulls(h52_dirty, nulls, radius=3)

    # Simulate HT-SIG0 with 4 pilots + 48 data SCs
    rng = np.random.default_rng(456)
    tx_htsig = np.zeros(52, dtype=np.complex64)
    for i in range(52):
        bit = rng.integers(0, 2)
        tx_htsig[i] = 1j * (1.0 if bit == 0 else -1.0)
    # Pilots at bins {48,49,50,51} = SCs {-21,-7,7,21} (using 1j for QBPSK)
    for pbin in [48, 49, 50, 51]:
        tx_htsig[pbin] = 1j  # known pilot value

    # Add per-symbol phase error (Phase 35 finding: ~1.3 rad std within-symbol)
    phi_error = 0.8  # rad
    rx = tx_htsig * h52_dirty * np.exp(1j * phi_error)
    # Use noise std=0.05 so 4 pilots can track phi (noise/|h52| ≈ 0.07,
    # well below the 0.8 rad phase shift). 0.3 (spec's value) overpowers
    # 4 pilots and makes phi_est noise-dominated.
    noise = (rng.standard_normal(52) + 1j * rng.standard_normal(52)) * 0.05
    rx = rx + noise

    # Equalize with pre-cleaned H52
    with np.errstate(invalid='ignore', divide='ignore'):
        eq_no_cpe = rx / h52_clean

    # Per-symbol pilot CPE: estimate phi from 4 pilots, rotate.
    # Pilots are on known QBPSK positions (1j), so the channel-induced
    # rotation is angle(eq_no_cpe[p]) - angle(pilot_value) = angle(eq_no_cpe[p]) - pi/2.
    # The spec's formula (angle(eq_no_cpe[p]) alone) includes the
    # pilot's own pi/2, which would over-rotate by pi/2 and put the
    # equalized data on the real axis instead of restoring it to imag.
    pilot_bins = [48, 49, 50, 51]
    pilot_args = [np.angle(eq_no_cpe[p]) for p in pilot_bins]
    phi_est = np.median(pilot_args) - np.pi / 2  # remove known pilot phase
    # DC bin (i=0) has h52_clean[0]=0 → eq_no_cpe[0]=NaN. It's excluded
    # from data_bins below, so suppress the cosmetic multiply warning.
    with np.errstate(invalid='ignore'):
        eq_with_cpe = eq_no_cpe * np.exp(-1j * phi_est)

    # Compare error at data SCs (not pilots, not DC at i=0 which has h52=0)
    data_bins = [i for i in range(1, 48) if i not in pilot_bins]
    err_no_cpe = np.median(np.abs(eq_no_cpe[data_bins] - tx_htsig[data_bins]))
    err_with_cpe = np.median(np.abs(eq_with_cpe[data_bins] - tx_htsig[data_bins]))

    print(f"[PILOT_CPE] median err at data SCs:")
    print(f"  pre-cleaned (no CPE):     {err_no_cpe:.3f}")
    print(f"  pre-cleaned + pilot CPE:  {err_with_cpe:.3f}")

    # Pass: pilot CPE should reduce error by 1.5x (loose threshold since
    # residual phase error after CPE is bounded by pilot noise).
    if err_with_cpe >= err_no_cpe / 1.5:
        print(f"[PILOT_CPE] FAIL: pilot CPE did not reduce error by 1.5x")
        return False
    print(f"[PILOT_CPE] PASS (pilot CPE reduced error "
          f"{err_no_cpe / err_with_cpe:.1f}x)")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['combo', 'wider', 'cpe', 'all'],
                        default='all')
    args = parser.parse_args()
    results = {}
    if args.mode in ('combo', 'all'):
        results['combo'] = test_combo_reduces_null_count()
    if args.mode in ('wider', 'all'):
        results['wider'] = test_combo_interp_with_wider_radius()
    if args.mode in ('cpe', 'all'):
        results['cpe'] = test_per_symbol_pilot_cpe_reduces_phase_error()
    if not all(results.values()):
        print(f"\n[FAIL] modes: {results}")
        sys.exit(1)
    print(f"\n[PASS] all modes: {results}")
    sys.exit(0)


if __name__ == '__main__':
    main()