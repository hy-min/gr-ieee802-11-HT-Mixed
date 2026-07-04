"""USRP-realistic channel modeler for offline testing.

Mirrors Phase 78b's observed impairments on USRP @ 5250 MHz cable:
  - 5 STABLE null subcarriers (same SCs every frame, max std_im 7.8)
  - 64-PSK ADC quantization residual (Phase 33b)
  - Per-frame sub-sample timing δ (Phase 34 / Phase 82)
  - AWGN at configurable SNR (Phase 78a baseline)

NumPy-only — no GNU Radio. Designed to be wrapped around a synthetic
HT-SIG signal in test_htsig_viterbi_synthetic_layer5.py, and around the
C++ frame_equalizer output in p84_replay_compare_usrp_synthetic.py.

Why this exists: the equalizer-layer attack surface is closed after 20+
REFUTED hypotheses (Phase 82 verdict 2026-07-04). Before spending more
cable runs on upstream-attack hypotheses, we need a deterministic
synthetic-vs-USRP comparison framework. This module gives us the
synthetic half.
"""
import numpy as np


# Phase 78b observed 5 STABLE null SCs on USRP @ 5250 MHz (1 of which is the
# -21 pilot). Same SCs are null in every frame. Synthetic channels have ROTATING
# nulls (Phase 78c random null attack failed at -13.7pp) which is the structural
# gap this modeler addresses.
USRP_STABLE_NULL_SCS = np.array([-21, -7, 7, 21, -13], dtype=np.int32)


def apply_5_stable_null_scs(eq52, sc_index_52, null_depth=0.02):
    """Force 5 stable SCs to be near-zero magnitude (Phase 78b structural finding).

    Parameters
    ----------
    eq52 : np.ndarray, complex64, shape (52,)
        Equalized 52-SC signal in TX order.
    sc_index_52 : np.ndarray, int32, shape (52,)
        Subcarrier index of each element (e.g. SC_INDEX_52).
    null_depth : float
        Multiplier for the 5 null SCs (1.0 = pass-through, 0.0 = force to 0).
        Phase 78b observed null magnitude ratio of 0.02 relative to mean.
    Returns
    -------
    np.ndarray, complex64, shape (52,)
    """
    eq = eq52.copy().astype(np.complex64)
    sc_set = set(sc_index_52.tolist())
    for sc in USRP_STABLE_NULL_SCS:
        if sc not in sc_set:
            continue  # SC not present in this 52-bin layout
        idx = int(np.where(sc_index_52 == sc)[0][0])
        eq[idx] = eq52[idx] * null_depth
    return eq


def apply_64psk_residual(rx, n_bins=64):
    """ADC quantization residual rounds phase to nearest 1/n_bins of unit circle (Phase 33b).

    USRP NCO + UBX-160 ADC + DAC exhibit ~64-PSK quantization with RMS 0.0142 (Phase 33b).
    Apply ONLY to the phase, preserving magnitude (magnitude normalization is separate).
    """
    rx = np.asarray(rx, dtype=np.complex64)
    mag = np.abs(rx)
    phases = np.angle(rx)
    bin_width = 2.0 * np.pi / n_bins
    quant_phases = np.round(phases / bin_width) * bin_width
    return (mag * np.exp(1j * quant_phases)).astype(np.complex64)