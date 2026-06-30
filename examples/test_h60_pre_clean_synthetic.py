#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 60 synthetic test: pre-clean H52 before HT-SIG equalization.

Validates that running detect_h52_nulls + interp_h52_nulls on H52 BEFORE
HT-SIG equalization produces a well-formed equalized HT-SIG constellation
on synthetic data with H52 channel nulls (|H|<0.15 at ~6/52 SCs).

SCOPE: This test validates the pre-clean algorithm in isolation on
synthetic data with adversarially constructed nulls. It does NOT
reproduce the USRP end-to-end pipeline (channel impulse response, UHD
streaming instability, RF impairments). End-to-end USRP validation
is in docs/superpowers/notes/2026-06-30-phase60-pre-clean-h52-verdict.md.

Reference: docs/superpowers/plans/2026-06-30-phase60-pre-clean-h52-before-htsig.md
"""
import argparse
import sys
import numpy as np

# Import Phase 59 helpers (reuse, no duplicate implementation)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "phase59",
    "/home/hy/gr-ieee802-11/examples/test_h52_null_interp_synthetic.py")
phase59 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase59)

K_SC_INDEX_52 = phase59.K_SC_INDEX_52


def test_pre_clean_improves_htsig_eq():
    """With H52 nulls, equalizing HT-SIG without pre-clean produces
    wild noise amplification at null SCs (50x). Pre-cleaning replaces
    nulls with neighbor mean, restoring sane equalization."""
    # Build H52 with 6 nulls (Phase 38 distribution)
    h52_dirty, _ = phase59.make_synthetic_h52(n_nulls=6, null_seed=42)
    # Pre-clean
    nulls = phase59.detect_h52_nulls(h52_dirty, thresh=0.15)
    h52_clean = phase59.interp_h52_nulls(h52_dirty, nulls, radius=2)

    # Simulate HT-SIG: 48 SCs (QBPSK on imag axis, +/- j)
    rng = np.random.default_rng(456)
    tx_htsig = np.zeros(52, dtype=np.complex64)
    for i in range(52):
        bit = rng.integers(0, 2)
        tx_htsig[i] = 1j * (1.0 if bit == 0 else -1.0)
    # HT-SIG only uses 48 data SCs (skip DC + pilots for simplicity)
    # We'll equalize all 52 and check noise amp at null SCs specifically

    # Channel: rx = tx * h52 (with nulls in air path)
    rx = tx_htsig * h52_dirty

    # Noise comparable to USRP Phase 38 finding (50x amp at null SCs)
    noise = (rng.standard_normal(52) + 1j * rng.standard_normal(52)) * 0.5
    rx = rx + noise

    # BASELINE: equalize with dirty H52
    with np.errstate(invalid='ignore', divide='ignore'):
        eq_dirty = rx / h52_dirty
    # PRE-CLEAN: equalize with cleaned H52
    with np.errstate(invalid='ignore', divide='ignore'):
        eq_clean = rx / h52_clean

    # Check: at null SCs, pre-clean should give sane constellation (|eq|~1)
    # At strong SCs, both should give sane constellation (|eq|~1)
    err_at_nulls_dirty = []
    err_at_nulls_clean = []
    for null_idx in nulls:
        err_at_nulls_dirty.append(abs(eq_dirty[null_idx] - tx_htsig[null_idx]))
        err_at_nulls_clean.append(abs(eq_clean[null_idx] - tx_htsig[null_idx]))
    # Use median (robust to outliers in 6-sample distribution; mean is
    # dragged up by 1-2 outlier SCs)
    avg_err_dirty = float(np.median(err_at_nulls_dirty))
    avg_err_clean = float(np.median(err_at_nulls_clean))

    print(f"[PRE_CLEAN_HT_SIG] median err at null SCs:")
    print(f"  baseline (rx / h52_dirty):     {avg_err_dirty:.3f}")
    print(f"  pre-clean (rx / h52_clean):    {avg_err_clean:.3f}")

    # Pass: pre-clean must reduce error at null SCs by 5x (median-based,
    # robust to small-sample variance).
    # Theoretical max: |h_clean|/|h_dirty| = 0.7/0.05 = 14x noise amp
    # reduction. Achieved ratio is bounded by structural error (h_clean is
    # an approximation, not true H) at ~6-9x. 5x is a conservative bound
    # that confirms the algorithm meaningfully helps.
    if avg_err_clean >= avg_err_dirty / 5:
        print(f"[PRE_CLEAN_HT_SIG] FAIL: pre-clean did not reduce error by 5x "
              f"({avg_err_clean:.3f} vs baseline {avg_err_dirty:.3f})")
        return False
    print(f"[PRE_CLEAN_HT_SIG] PASS (pre-clean reduced null-SC error "
          f"{avg_err_dirty / avg_err_clean:.1f}x, median)")
    return True


def test_pre_clean_preserves_strong_scs():
    """Pre-cleaning must not modify strong SCs (|H|>=0.15)."""
    h52, _ = phase59.make_synthetic_h52(n_nulls=6, null_seed=42)
    nulls = phase59.detect_h52_nulls(h52, thresh=0.15)
    h52_clean = phase59.interp_h52_nulls(h52, nulls, radius=2)

    # Check: at non-null SCs, h52_clean should equal h52
    for i in range(52):
        if i not in nulls and i != 0:
            if abs(h52_clean[i] - h52[i]) > 1e-6:
                print(f"[PRESERVE] FAIL: strong SC {i} was modified")
                return False
    print(f"[PRESERVE] PASS ({52 - len(nulls) - 1} strong SCs preserved)")
    return True


def test_no_nulls_no_change():
    """If there are no nulls (clean channel), pre-clean should be a no-op."""
    h52 = np.zeros(52, dtype=np.complex64)
    for i in range(52):
        h52[i] = 0.7 * np.exp(1j * 0.05)  # all strong
    h52[0] = 0.0  # DC
    nulls = phase59.detect_h52_nulls(h52, thresh=0.15)
    if nulls:
        print(f"[NO_NULLS] FAIL: detected {len(nulls)} nulls in clean H52")
        return False
    h52_clean = phase59.interp_h52_nulls(h52, nulls, radius=2)
    if np.any(np.abs(h52_clean - h52) > 1e-6):
        print(f"[NO_NULLS] FAIL: pre-clean modified clean H52")
        return False
    print(f"[NO_NULLS] PASS (clean channel is no-op)")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['pre_clean', 'preserve', 'no_nulls', 'all'],
                        default='all')
    args = parser.parse_args()
    results = {}
    if args.mode in ('pre_clean', 'all'):
        results['pre_clean'] = test_pre_clean_improves_htsig_eq()
    if args.mode in ('preserve', 'all'):
        results['preserve'] = test_pre_clean_preserves_strong_scs()
    if args.mode in ('no_nulls', 'all'):
        results['no_nulls'] = test_no_nulls_no_change()
    if not all(results.values()):
        print(f"\n[FAIL] modes: {results}")
        sys.exit(1)
    print(f"\n[PASS] all modes: {results}")
    sys.exit(0)


if __name__ == '__main__':
    main()
