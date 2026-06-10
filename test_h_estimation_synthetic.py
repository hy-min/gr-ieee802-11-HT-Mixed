#!/home/hy/conda/envs/gnuradio/bin/python
"""
Synthetic test: validate the H estimation math before USRP run.

Hypothesis: If we apply the same per-subcarrier phase rotation (CFO + SFO*sc)
to L-LTF0 and L-SIG, then L-SIG / H should produce symbols whose imaginary
part is small (BPSK, real-valued) and whose real magnitude is close to 1.0.

We don't need GNU Radio here; we re-implement the same math:
- kLltf48TX is the standard 802.11n L-LTF sequence (the values used in the
  frame_equalizer's `estimate_header_channel_from_lltf52`).
- kTxOrder52 defines which subcarrier index corresponds to slot i.
- CFO+SFO model: phase[sc] = cfo_per_sym * sym_counter + sfo * sc * sym_counter
"""
import numpy as np

# Standard 802.11n L-LTF data subcarriers (TX reference, used by
# frame_equalizer_impl.cc:estimate_header_channel_from_lltf52).
# These are the +1/-1 BPSK values for SCs -26..+26 (excluding DC and
# pilots). Order matches the 48 data slots in the frame_equalizer's 52-slot
# array (slots 0..47 = data, slots 48..51 = pilots).
# Copied VERBATIM from lib/ieee80211_constants.h:kLltf48TX.
KL_LTF_48_TX = np.array([
    +1, +1, -1, -1, +1, -1,   # sc -26 to -20
    +1, -1, +1, +1, +1, +1,   # sc -19 to -14
    +1, +1, -1, -1, +1, +1,   # sc -13 to  -8
    +1, -1, +1, +1, +1, +1,   # sc  -6 to  -1
    +1, -1, -1, +1, +1, -1,   # sc  +1 to  +6
    -1, +1, -1, -1, -1, -1,   # sc  +8 to +13
    -1, +1, +1, -1, -1, +1,   # sc +14 to +19
    -1, -1, +1, +1, +1, +1,   # sc +20 to +26
], dtype=np.complex64)

# L-LTF pilot TX values (slots 48..51). Copied VERBATIM from
# lib/frame_equalizer_impl.cc:kLltfPilotTX. Order is the kPilot4Sc order:
# {-21, -7, +7, +21}.
K_PILOT_TX = np.array([1.0, -1.0, 1.0, 1.0], dtype=np.complex64)

# kScIndex52: subcarrier index for slot i (0..47=data, 48..51=pilots).
# Negative indices are wrapped; this matches kScIndex52 in general_work().
K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def make_header52_with_cfo_sfo(bpsk_symbols, sym_counter, cfo_per_sym, sfo_per_sc):
    """Apply per-subcarrier phase rotation: phase[sc] = (cfo + sfo*sc) * counter.

    bpsk_symbols: 48 complex values (BPSK, real +/-1) for L-SIG/HT-SIG.
    Returns 52 complex values (48 data + 4 pilots).
    """
    full = np.concatenate([bpsk_symbols, K_PILOT_TX]).astype(np.complex64)
    phases = (cfo_per_sym + sfo_per_sc * K_SC_INDEX_52.astype(np.float32)) * sym_counter
    return full * np.exp(1j * phases)


def estimate_h_with_compensation(lltf0_52, sym_counter,
                                 cfo_per_sym, sfo_per_sc):
    """Apply CFO+SFO compensation to L-LTF0 (mirroring the new frame_equalizer path)
    then compute H = lltf0 / tx_reference (mirroring estimate_header_channel_from_lltf52).
    """
    phases = (cfo_per_sym + sfo_per_sc * K_SC_INDEX_52.astype(np.float32)) * sym_counter
    rot = np.exp(-1j * phases)
    lltf0_comp = lltf0_52 * rot
    H = np.zeros(52, dtype=np.complex64)
    H[:48] = lltf0_comp[:48] / KL_LTF_48_TX
    H[48:] = lltf0_comp[48:] / K_PILOT_TX
    return H


def test_no_cfo_no_sfo():
    """Baseline: with CFO=SFO=0, H * symbols should equal symbols exactly."""
    np.random.seed(42)
    bpsk = (2 * (np.random.randint(0, 2, 48) > 0.5) - 1).astype(np.complex64)
    lltf0 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=0,
                                       cfo_per_sym=0.0, sfo_per_sc=0.0)
    lltf1 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=1,
                                       cfo_per_sym=0.0, sfo_per_sc=0.0)
    H = estimate_h_with_compensation(lltf0, sym_counter=0,
                                     cfo_per_sym=0.0, sfo_per_sc=0.0)
    eq = bpsk / H[:48]
    imag_max = np.max(np.abs(eq.imag))
    assert imag_max < 1e-5, f"no-CFO/SFO baseline failed: imag_max={imag_max}"
    print(f"[PASS] test_no_cfo_no_sfo imag_max={imag_max:.2e}")


def test_cfo_only():
    """With CFO=0.3 rad/sym (typical USRP) and L-LTF0 counter=1, L-SIG counter=3,
    the L-SIG relative to L-LTF0 has a 2*cfo = 0.6 rad rotation. After
    compensating L-LTF0 with the same per-SC phase and compensating L-SIG
    (line 2141 of frame_equalizer_impl.cc), eq should be ~real +/-1."""
    cfo = 0.3  # rad per OFDM symbol (typical USRP)
    sfo = 0.0
    np.random.seed(42)
    bpsk = (2 * (np.random.randint(0, 2, 48) > 0.5) - 1).astype(np.complex64)
    lltf0 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=1,
                                       cfo_per_sym=cfo, sfo_per_sc=sfo)
    lltf1 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=2,
                                       cfo_per_sym=cfo, sfo_per_sc=sfo)
    # L-SIG is at counter=3: 3*counter=6 = 3*cfo rad of extra rotation vs L-LTF0.
    lsig = make_header52_with_cfo_sfo(bpsk, sym_counter=3,
                                      cfo_per_sym=cfo, sfo_per_sc=sfo)
    H = estimate_h_with_compensation(lltf0, sym_counter=1,
                                     cfo_per_sym=cfo, sfo_per_sc=sfo)
    # L-SIG compensation (mirrors line 2141 of frame_equalizer_impl.cc):
    phases_lsig = (cfo + sfo * K_SC_INDEX_52.astype(np.float32)) * 3
    lsig_comp = lsig * np.exp(-1j * phases_lsig)
    eq = lsig_comp[:48] / H[:48]
    imag_max = np.max(np.abs(eq.imag))
    assert imag_max < 1e-3, f"CFO-only compensated: imag_max={imag_max} (want <1e-3)"
    print(f"[PASS] test_cfo_only imag_max={imag_max:.2e} (was 20.6 weighted avg in USRP)")


def test_cfo_and_sfo():
    """With CFO=0.3, SFO=0.0005 rad/SC (well below the 0.001 clamp threshold)."""
    cfo = 0.3
    sfo = 0.0005
    np.random.seed(42)
    bpsk = (2 * (np.random.randint(0, 2, 48) > 0.5) - 1).astype(np.complex64)
    lltf0 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=1,
                                       cfo_per_sym=cfo, sfo_per_sc=sfo)
    lltf1 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=2,
                                       cfo_per_sym=cfo, sfo_per_sc=sfo)
    lsig = make_header52_with_cfo_sfo(bpsk, sym_counter=3,
                                      cfo_per_sym=cfo, sfo_per_sc=sfo)
    H = estimate_h_with_compensation(lltf0, sym_counter=1,
                                     cfo_per_sym=cfo, sfo_per_sc=sfo)
    # L-SIG compensation (mirrors line 2141 of frame_equalizer_impl.cc):
    phases_lsig = (cfo + sfo * K_SC_INDEX_52.astype(np.float32)) * 3
    lsig_comp = lsig * np.exp(-1j * phases_lsig)
    eq = lsig_comp[:48] / H[:48]
    imag_max = np.max(np.abs(eq.imag))
    assert imag_max < 1e-3, f"CFO+SFO compensated: imag_max={imag_max} (want <1e-3)"
    print(f"[PASS] test_cfo_and_sfo imag_max={imag_max:.2e}")


def test_uncompensated_baseline_fails():
    """Show the bug: if we DON'T compensate L-LTF0, the eq has huge imag."""
    cfo = 0.3
    sfo = 0.0
    np.random.seed(42)
    bpsk = (2 * (np.random.randint(0, 2, 48) > 0.5) - 1).astype(np.complex64)
    # L-LTF0 is at sym_counter=1 (after L-LTF1 timing baseline), L-SIG at 2.
    # This mirrors the real frame_equalizer where L-LTF0 has counter=0 and
    # L-SIG counter=2, but the L-SIG-compensation at line 2141 uses
    # phase_diff*2 which fully cancels the 2*cfo rotation, leaving a
    # residual equal to the uncompensated L-LTF0 phase (here 1*cfo).
    lltf0 = make_header52_with_cfo_sfo(KL_LTF_48_TX, sym_counter=1,
                                       cfo_per_sym=cfo, sfo_per_sc=sfo)
    lsig = make_header52_with_cfo_sfo(bpsk, sym_counter=2,
                                      cfo_per_sym=cfo, sfo_per_sc=sfo)
    # H estimated from RAW (uncompensated) L-LTF0, like the current code does.
    H_uncomp = np.zeros(52, dtype=np.complex64)
    H_uncomp[:48] = lltf0[:48] / KL_LTF_48_TX
    H_uncomp[48:] = lltf0[48:] / K_PILOT_TX
    # L-LTF0 at counter=1 has a 1*cfo rotation that L-LTF0 H (uncompensated)
    # carries through. L-SIG at counter=2 is compensated by 2*cfo, so after
    # HDR_COMP it has no rotation. The uncompensated H therefore contributes
    # a 1*cfo residual that the L-SIG compensation does not cancel.
    # This is the domain-mismatch bug: H and the L-SIG-compensated symbols
    # are in different phase domains.
    lsig_comp = lsig * np.exp(-1j * cfo * 2)
    eq = lsig_comp[:48] / H_uncomp[:48]
    imag_max = np.max(np.abs(eq.imag))
    print(f"[INFO] test_uncompensated_baseline_fails imag_max={imag_max:.3f} "
          f"(this is the bug: uncompensated H leaves {imag_max:.2f} rad residual)")
    assert imag_max > 0.1, "Test setup wrong: uncompensated case should be bad"
    print(f"[PASS] test_uncompensated_baseline_fails confirms bug exists")


if __name__ == "__main__":
    test_no_cfo_no_sfo()
    test_cfo_only()
    test_cfo_and_sfo()
    test_uncompensated_baseline_fails()
    print("\nAll synthetic H estimation tests passed.")
