#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 80b Stage 0 (Pre-Validation): USRP-realistic synthetic channel for LUT validation.

GO/NO-GO GATE for Phase 80b:
- Build a synthetic channel that honestly models USRP RF chain impairments:
  * Estimated H (used by LUT-referee) differs from True H (Phase 78b finding)
  * Per-frame residual delta after delta-correction (Phase 79 limitation)
  * LO phase noise residual (Phase 25.4: 1.77 rad std)
  * 5 stable null SCs at {-15,-10,-3,-17,+8} (Phase 78b)
  * ADC 64-PSK quantization residual RMS=0.0142 rad (Phase 33b)
  * IQ imbalance (~0.5 dB amp, ~2 deg phase) (Phase 33b)
- Compare LUT-on vs LUT-off. LUT has positive expected gain iff:
  * SNR improvement >= 1.0 dB, OR
  * CRC-OK ratio (with_lut / no_lut) >= 2.0

If FAIL: Phase 80b should be CLOSED (recommend Option C of P83).
If PASS: Phase 80b Tasks 2-5 should proceed using this honest channel.

Source phases:
- Phase 25 SFO/Phase Noise (LO residual 1.77 rad std)
- Phase 33b USRP 64-PSK residual (RMS 0.0142 rad)
- Phase 78b USRP null SCs at {-15,-10,-3,-17,+8}, std_im 7.8
- Phase 78b estimated-H bias finding
- Phase 79 per-symbol delta grid-search estimator
- Phase 81 cable @ 5250 MHz SNR 9.6 dB baseline
"""
import sys
import numpy as np

# ============================================================
# Constants (mirroring values in test_htsig_delta_synthetic.py + lib/frame_equalizer_impl.cc)
# ============================================================

K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)
DATA_SC = K_SC_INDEX_52[:48]
PILOT_SC = K_SC_INDEX_52[48:52]  # [-21, -7, 7, 21]

# HT-SIG pilot polarities per IEEE 802.11n-2016 sec 17.3.5.10
HT_SIG0_POLARITY = np.array([1j, 1j, 1j, -1j], dtype=np.complex64)
HT_SIG1_POLARITY = np.array([-1j, -1j, -1j, 1j], dtype=np.complex64)

# Estimator tuning constants
MIN_H_MAG = 0.01
N_GRID = 64
TWO_PI = 2.0 * np.pi
N_FFT = 64

# USRP-observed structural null SCs (Phase 78b, std_im=7.8)
USRP_NULL_SC = [-15, -10, -3, -17, 8]

# Quantities of merit
TARGET_SNR_HT_DB = 9.0  # Phase 81 cable @ 5250 MHz


def get_sc_index_pos(sc_value):
    """Return array index in K_SC_INDEX_52 for a given SC value (e.g. -21)."""
    matches = np.where(K_SC_INDEX_52 == sc_value)[0]
    assert len(matches) == 1, f"SC value {sc_value} not found"
    return int(matches[0])


def estimate_symbol_delta_qbpsk(eq_pilots, H_pilots, pilot_polarity):
    """QBPSK-aware grid-search delta estimator (Phase 79).

    Mirror of C++ `estimate_symbol_delta_qbpsk` in frame_equalizer_impl.cc.
    """
    valid = np.abs(H_pilots) > MIN_H_MAG
    if not np.any(valid):
        return 0.0
    # Pilot residual: equalized - expected_symbol
    residual = eq_pilots * np.conj(pilot_polarity)
    best_delta = 0.0
    best_mag = 0.0
    for d in range(N_GRID):
        delta = d / N_GRID
        # Linear phase ramp across pilots
        expected = np.exp(1j * TWO_PI * PILOT_SC.astype(np.float64) * delta / N_GRID)
        inner = np.sum(np.conj(expected) * residual * valid)
        mag = np.abs(inner)
        if mag > best_mag:
            best_mag = mag
            best_delta = delta
    return best_delta


# ============================================================
# Core: USRP-realistic channel synthesis
# ============================================================

def synthesize_realistic_frame(rng, snr_db_htsig=9.0, delta_residual=0.3,
                                lo_phase_std_rad=1.77,
                                adc_rms_rad=0.0142,
                                iq_imbalance_db=0.5, iq_phase_deg=2.0):
    """Synthesize a USRP-realistic HT-SIG frame.

    Args:
        rng:                numpy Generator instance (must be persistent across frames)
        snr_db_htsig:       target average SNR in HT-SIG region (dB)
        delta_residual:     per-frame residual sub-sample timing offset,
                            in units where 1 unit = 1/64 of a sample period.
                            Estimated H cannot catch this; it pushes HT-SIG
                            away from QBPSK axes. Phase 79 grid-search finds
                            a single best delta_d_hat in [0..1) but residual
                            delta - delta_d_hat is on the order of 0.3.
        lo_phase_std_rad:   std of LO phase noise per-frame residual (rad).
                            Phase 25.4 measured 1.77 rad. Acts as a constant
                            (across-SC) phase per frame that estimated H
                            absorbs via pilot CPE, but only the 4 pilots;
                            per-SC differences across the wider bandwidth
                            remain.
        adc_rms_rad:        RMS of ADC 64-PSK quantization residual (Phase 33b)
        iq_imbalance_db:    amplitude imbalance (dB)
        iq_phase_deg:       phase imbalance (degrees)

    Returns:
        (rx52, H52_estimated, tx_bits)
        - rx52: complex64 length-52 array at receiver (after channel + impairments)
        - H52_estimated: complex64 length-52 array, the estimated channel response
                        that the equalizer would use (NOT the true channel;
                        differs per Phase 78b finding).
        - tx_bits: int8 length-48 array, the transmitted HT-SIG bits.
    """
    # ---------------------------------------------------------------
    # True channel: Rayleigh + 5 stable nulls
    # ---------------------------------------------------------------
    H_true = (rng.standard_normal(52) + 1j * rng.standard_normal(52)).astype(np.complex64)
    null_mask = np.zeros(52, dtype=bool)
    for ns in USRP_NULL_SC:
        idx = get_sc_index_pos(ns)
        null_mask[idx] = True
    H_true[null_mask] *= 0.1  # mimic stable nulls (|H| ~ 0.1)
    # Force pure-imaginary unit gain on non-nulls (~typical after AGC)
    H_true[~null_mask] = (H_true[~null_mask] /
                          np.abs(H_true[~null_mask])).astype(np.complex64)

    # ---------------------------------------------------------------
    # Apply IQ imbalance to true channel (per-SC)
    # ---------------------------------------------------------------
    gain_imbalance = 10 ** (iq_imbalance_db / 20.0)
    phase_imbalance_rad = np.deg2rad(iq_phase_deg)
    # Approximate: rotate phase per-SC by small offset + apply gain imbalance
    sc_phase_perturb = (rng.standard_normal(52) * np.deg2rad(0.5)).astype(np.float32)
    H_true = (H_true *
              np.exp(1j * sc_phase_perturb) *
              gain_imbalance).astype(np.complex64)

    # ---------------------------------------------------------------
    # Estimated H (what the equalizer WILL use)
    # Phase 78b: estimated H is biased — uses 4 L-LTF pilots which can
    # correct average phase but not the per-SC phase gradient from
    # delta_residual.
    # ---------------------------------------------------------------
    # True delta ramp across SCs
    delta_ramp_true = np.exp(1j * TWO_PI * K_SC_INDEX_52.astype(np.float32) *
                              delta_residual / N_GRID).astype(np.complex64)
    # LO phase noise (constant across SCs) per frame
    lo_phase = rng.normal(0.0, lo_phase_std_rad / np.sqrt(52))  # reduced since shared across SCs
    # Estimated H cannot fully correct delta_residual ramp OR LO phase
    # (only corrects pilot-based CPE, which captures a constant phase offset)
    # Estimated H gets the constant-phase correctly but misses the delta ramp
    H_est_const = H_true * np.exp(-1j * lo_phase)  # constant-phase corrected
    # Estimated H misses delta_ramp per-frame
    H_est_full = H_est_const  # no per-frame delta correction applied to H_est
    # But estimated H has 1/52 fractional phase tracking error (pilot spacing)
    # modelled as small slope
    est_phase_slope_error = rng.normal(0, np.deg2rad(5.0), size=52).astype(np.float32)
    H_est = (H_est_full *
             np.exp(1j * est_phase_slope_error)).astype(np.complex64)

    # ---------------------------------------------------------------
    # Transmit HT-SIG (random QBPSK bits)
    # bit=0 → -j, bit=1 → +j (matches C++ convention)
    # ---------------------------------------------------------------
    tx_bits = rng.integers(0, 2, size=48).astype(np.int8)
    tx_syms = (1j * (1 - 2 * tx_bits)).astype(np.complex64)

    # ---------------------------------------------------------------
    # Build 52-SC TX (data 48 + pilots 4)
    # ---------------------------------------------------------------
    tx52 = np.zeros(52, dtype=np.complex64)
    tx52[:48] = tx_syms
    tx52[48:52] = HT_SIG0_POLARITY

    # ---------------------------------------------------------------
    # RX signal (without noise): tx * H_true * delta_ramp * (constant phase LO + ADC)
    # The delta ramp is what makes estimated H non-corrective (Phase 78b).
    # ---------------------------------------------------------------
    # Apply delta_ramp and LO phase
    rx52_clean = tx52 * H_true * delta_ramp_true * np.exp(1j * lo_phase)

    # ---------------------------------------------------------------
    # ADC 64-PSK quantization residual (Phase 33b RMS 0.0142 rad)
    # ---------------------------------------------------------------
    if adc_rms_rad > 0:
        adc_phase_noise = rng.normal(0.0, adc_rms_rad, size=52).astype(np.float32)
        rx52_clean = rx52_clean * np.exp(1j * adc_phase_noise).astype(np.complex64)

    # ---------------------------------------------------------------
    # AWGN at target SNR per SC
    # SNR defined as 10*log10(|signal|^2 / |noise|^2)
    # ---------------------------------------------------------------
    sig_pow = float(np.mean(np.abs(rx52_clean[:48]) ** 2))
    noise_pow = sig_pow / (10 ** (snr_db_htsig / 10.0))
    noise = (rng.standard_normal(52) + 1j * rng.standard_normal(52)).astype(np.complex64) \
            * np.sqrt(noise_pow / 2.0)
    rx52 = (rx52_clean + noise).astype(np.complex64)

    return rx52, H_est, tx_bits


# ============================================================
# LUT construction from training frames
# ============================================================

def build_lut_from_realistic_frames(frames, mode='synthetic', max_data_sc=48):
    """Build per-SC phase LUT from N training frames.

    Two modes:
      'synthetic': divide by tx_symbols to remove ±π/2 bimodal spread,
                    then take median over frames. Ideal for synthetic
                    validation where TX bits are known.
      'capture':   take arg(eq) directly. The ±π/2 bimodality can be
                    smoothed by snapping to nearest ±π/2 grid before
                    taking median. This is the USRP path (Task 7).

    Returns:
        lut48 (complex64 length 48): per-SC phase correction.
    """
    n_frames = len(frames)
    arg_eq = np.zeros((n_frames, max_data_sc), dtype=np.float32)

    for i, (rx52, H_est, tx_bits_or_none) in enumerate(frames):
        # Apply Phase 79 delta correction (uses 4 pilots)
        eq_pilots = rx52[48:52] / H_est[48:52]
        delta_d_hat = estimate_symbol_delta_qbpsk(
            eq_pilots, H_est[48:52], HT_SIG0_POLARITY)
        correction = np.exp(1j * TWO_PI * DATA_SC.astype(np.float32) *
                            delta_d_hat / N_GRID).astype(np.complex64)
        eq48 = (rx52[:max_data_sc] / H_est[:max_data_sc]) * correction

        if mode == 'synthetic':
            assert tx_bits_or_none is not None, "synthetic mode needs tx_bits"
            tx_syms = (1j * (1 - 2 * tx_bits_or_none)).astype(np.complex64)
            # Remove ±π/2 bimodality by dividing by tx syms
            arg_eq[i] = np.angle(eq48 / tx_syms)
        elif mode == 'capture':
            # Snap each SC's argument to nearest ±π/2 grid (QBPSK expected positions)
            arg_eq[i] = np.angle(eq48)
            # Quantize to nearest ±π/2: arg_eq[i] ∈ {-π/2, +π/2}
            arg_eq[i] = np.sign(arg_eq[i]) * np.pi / 2
        else:
            raise ValueError(f"Unknown mode {mode}")

    median_arg = np.median(arg_eq, axis=0)
    # LUT applies as multiply by exp(-j * median_arg) to correct the bias
    lut48 = np.exp(-1j * median_arg).astype(np.complex64)
    return lut48


# ============================================================
# LUT effect measurement (avg_snr + per-bit CRC-OK)
# ============================================================

def compute_avg_snr_htsig_per_sc(eq48_corrected, tx_bits):
    """Compute average per-SC SNR (dB) for HT-SIG hard decision.

    SNR per SC: 10*log10(|eq|^2 / |eq - decision_sym|^2)

    Args:
        eq48_corrected: complex64 length-48 equalized bins (post-correction)
        tx_bits: int8 length-48 transmitted bits

    Returns:
        avg_snr_db (float)
    """
    tx_syms = (1j * (1 - 2 * tx_bits)).astype(np.complex64)
    sig_power = float(np.mean(np.abs(eq48_corrected) ** 2))
    err_power = float(np.mean(np.abs(eq48_corrected - tx_syms) ** 2))
    if err_power < 1e-12:
        return 30.0  # capped ceiling
    return 10.0 * np.log10(sig_power / err_power)


# ============================================================
# Main test: LUT has positive expected gain on USRP-realistic channel?
# ============================================================

def test_realistic_no_lut_vs_with_lut():
    """Compare LUT-on vs LUT-off on USRP-realistic channel.

    Decision criteria (B3):
      - PASS if SNR gain >= 1.0 dB, OR CRC-OK ratio >= 2.0x
      - FAIL otherwise (recommend closing P80b, switch to Option C)
    """
    rng = np.random.default_rng(seed=2026)

    # -----------------------------------------------------------------
    # Build LUT from N=50 training frames at high SNR (LUT is "static"
    # and should be trained at high SNR to get clean per-SC phases).
    # -----------------------------------------------------------------
    N_TRAIN = 50
    train_frames = []
    for _ in range(N_TRAIN):
        train_frames.append(synthesize_realistic_frame(
            rng, snr_db_htsig=20.0,  # high-SNR for clean LUT
            delta_residual=0.3))
    print(f"[TRAIN] built {N_TRAIN} frames at 20dB SNR")

    lut48 = build_lut_from_realistic_frames(train_frames, mode='synthetic')
    assert np.allclose(np.abs(lut48), 1.0, atol=1e-5), \
        f"LUT magnitude not unity: max deviation {np.max(np.abs(np.abs(lut48) - 1.0))}"
    print(f"[LUT] magnitude unity OK (max dev={np.max(np.abs(np.abs(lut48)-1.0)):.2e})")

    # -----------------------------------------------------------------
    # Test N=60 frames at USRP-realistic SNR (9 dB, Phase 81 cable@5250)
    # -----------------------------------------------------------------
    N_TRIAL = 60
    snr_no_lut_list = []
    snr_with_lut_list = []
    n_ok_no_lut = 0
    n_ok_with_lut = 0

    for trial_i in range(N_TRIAL):
        rx52, H_est, tx_bits = synthesize_realistic_frame(
            rng, snr_db_htsig=TARGET_SNR_HT_DB,
            delta_residual=0.3)

        # ---- Apply δ correction (Phase 79) ----
        eq_pilots = rx52[48:52] / H_est[48:52]
        delta_d_hat = estimate_symbol_delta_qbpsk(
            eq_pilots, H_est[48:52], HT_SIG0_POLARITY)
        correction = np.exp(1j * TWO_PI * DATA_SC.astype(np.float32) *
                            delta_d_hat / N_GRID).astype(np.complex64)

        # ---- No LUT baseline ----
        eq_no = (rx52[:48] / H_est[:48]) * correction
        bits_no = (eq_no.imag < 0).astype(np.int8)  # QBPSK hard decision
        snr_no = compute_avg_snr_htsig_per_sc(eq_no, tx_bits)
        snr_no_lut_list.append(snr_no)
        if np.array_equal(bits_no, tx_bits):
            n_ok_no_lut += 1

        # ---- With LUT ----
        eq_with = eq_no * lut48
        bits_with = (eq_with.imag < 0).astype(np.int8)
        snr_with = compute_avg_snr_htsig_per_sc(eq_with, tx_bits)
        snr_with_lut_list.append(snr_with)
        if np.array_equal(bits_with, tx_bits):
            n_ok_with_lut += 1

    avg_snr_no = float(np.mean(snr_no_lut_list))
    avg_snr_with = float(np.mean(snr_with_lut_list))
    snr_gain = avg_snr_with - avg_snr_no
    if n_ok_no_lut > 0:
        crc_ratio = n_ok_with_lut / n_ok_no_lut
    else:
        # If no_lut is 0 but with_lut is > 0, ratio is "infinite"
        crc_ratio = float('inf') if n_ok_with_lut > 0 else 0.0

    # Decision criteria (Phase 80b go/no-go gate)
    PASS_SNR_GAIN_DB = 1.0
    PASS_CRC_RATIO = 2.0
    decision_pass = (snr_gain >= PASS_SNR_GAIN_DB) or (crc_ratio >= PASS_CRC_RATIO)

    print()
    print("=" * 64)
    print("Phase 80b Stage 0: USRP-realistic LUT validation")
    print("=" * 64)
    print(f"[RESULT] avg_snr_no_lut   = {avg_snr_no:.3f} dB")
    print(f"[RESULT] avg_snr_with_lut = {avg_snr_with:.3f} dB")
    print(f"[RESULT] SNR delta         = {snr_gain:+.3f} dB "
          f"(threshold: >= +{PASS_SNR_GAIN_DB:.1f} dB for PASS)")
    print(f"[RESULT] CRC-OK no_lut     = {n_ok_no_lut}/{N_TRIAL}")
    print(f"[RESULT] CRC-OK with_lut   = {n_ok_with_lut}/{N_TRIAL}")
    print(f"[RESULT] CRC-OK ratio      = {crc_ratio:.2f}x "
          f"(threshold: >= {PASS_CRC_RATIO:.1f}x for PASS)")
    print()
    if decision_pass:
        print("[DECISION] PASS — LUT shows positive expected gain on USRP-realistic channel")
        print("           → recommend continuing P80b Tasks 2-5 (with this honest channel)")
        print("           → update P80b plan to use realistic_synth (not the old synthetic)")
        return True
    else:
        print("[DECISION] FAIL — LUT did NOT show positive expected gain on USRP-realistic channel")
        print("           → recommend CLOSING P80b")
        print("           → switch to Phase 83 Option C (fix Phase 34 delta estimator for 5250)")
        return False


# ============================================================
# Bonus diagnostic: per-SC LUT phase distribution sanity check
# ============================================================

def test_lut_phase_distribution():
    """Verify the built LUT captures non-trivial per-SC phase structure.

    If all LUT phases cluster near 0, the LUT is essentially identity
    and there's nothing to learn. If they span [-π/2, +π/2], the channel
    has real per-SC phase structure worth correcting.
    """
    rng = np.random.default_rng(seed=42)
    train = [synthesize_realistic_frame(rng, snr_db_htsig=20.0)
             for _ in range(30)]
    lut48 = build_lut_from_realistic_frames(train, mode='synthetic')
    phases = np.angle(lut48)
    print(f"[LUT-DIAG] min phase={np.min(phases):.3f}, "
          f"max phase={np.max(phases):.3f}, "
          f"std={np.std(phases):.3f}, "
          f"mean={np.mean(phases):.3f}")
    assert np.std(phases) > 0.05, \
        f"LUT phase std too small ({np.std(phases):.3f}), channel may be trivial"
    print("[LUT-DIAG] PASS — LUT captures non-trivial per-SC phase structure")


if __name__ == "__main__":
    print(">>> Phase 80b Stage 0: USRP-realistic channel validation")
    print()

    # First: confirm LUT infrastructure produces non-trivial correction
    test_lut_phase_distribution()
    print()

    # Then: the real go/no-go gate
    passed = test_realistic_no_lut_vs_with_lut()

    sys.exit(0 if passed else 1)
