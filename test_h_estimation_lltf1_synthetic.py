#!/home/hy/conda/envs/gnuradio/bin/python
"""
Synthetic test: compare H estimation from L-LTF0 (counter=0) vs L-LTF1 (counter=1)
under a time-varying channel.

Hypothesis: L-LTF0 is 8us away from L-SIG (counter=0 -> counter=2 in the
frame_equalizer). If the channel rotates over time (CFO + per-SC phase
slope from SFO is fully compensated, but residual time-varying channel
rotation is NOT), then H estimated from L-LTF0 will have a larger residual
phase at L-SIG than H estimated from L-LTF1 (which is only 4us away).

This test simulates the time-varying channel by directly rotating H_chan
between the L-LTF and L-SIG moments, then verifies:
1. With no rotation, both H choices give identical eq_lsig (sanity check).
2. With rotation, L-LTF0 H leaves a large residual phase on eq_lsig.
3. With rotation, L-LTF1 H leaves a SMALLER residual phase than L-LTF0 H.

Mirrors `estimate_header_channel_from_lltf52` (lib/frame_equalizer_impl.cc:1146).
"""
import numpy as np

# Standard 802.11n L-LTF data subcarriers (TX reference). Copied VERBATIM
# from lib/ieee80211_constants.h:kLltf48TX. Order matches the 48 data slots
# in the frame_equalizer's 52-slot array (slots 0..47 = data).
K_LLTF_48_TX = np.array([
    +1, +1, -1, -1, +1, -1,   # sc -26 to -20
    +1, -1, +1, +1, +1, +1,   # sc -19 to -14
    +1, +1, -1, -1, +1, +1,   # sc -13 to  -8
    +1, -1, +1, +1, +1, +1,   # sc  -6 to  -1
    +1, -1, -1, +1, +1, -1,   # sc  +1 to  +6
    -1, +1, -1, -1, -1, -1,   # sc  +8 to +13
    -1, +1, +1, -1, -1, +1,   # sc +14 to +19
    -1, -1, +1, +1, +1, +1,   # sc +20 to +26
], dtype=np.complex64)

# kScIndex52: subcarrier index for slot i (0..47=data, 48..51=pilots).
# Copied VERBATIM from lib/frame_equalizer_impl.cc:2180-2189.
K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)


def rotate_per_symbol(H_chan_t0, rotation_rad_per_us, t_us):
    """Simulate a time-varying channel that rotates uniformly between
    L-LTF0 (t=0) and a target time t_us.

    H_chan_t0:        48 complex channel values at t=0 (the L-LTF0 moment).
    rotation_rad_per_us:  rotation rate in rad/us (uniform per-subcarrier).
    t_us:             time delta in microseconds from t=0.

    Returns H_chan_t0 with per-SC phase rotated by
        rotation_rad_per_us * t_us * K_SC_INDEX_52[:48]
    """
    phases = rotation_rad_per_us * t_us * K_SC_INDEX_52[:48].astype(np.float32)
    return H_chan_t0 * np.exp(1j * phases.astype(np.complex64))


def estimate_h_from_lltf(lltf_rx, lltf_tx=K_LLTF_48_TX):
    """Estimate H = rx / tx for the 48 data subcarriers (mirrors
    estimate_header_channel_from_lltf52 in frame_equalizer_impl.cc)."""
    return lltf_rx / lltf_tx


def test_no_rotation_lltf0_equals_lltf1():
    """Sanity check: when the channel is constant (no time variation), H
    built from L-LTF0 (counter=0) and H built from L-LTF1 (counter=1) give
    the same H estimate, so eq_lsig is identical (imag_max < 1e-5)."""
    np.random.seed(42)
    bpsk = np.array([1 if (i % 2 == 0) else -1 for i in range(48)], dtype=np.complex64)

    # Random channel (constant in time)
    H_chan_t0 = (np.random.randn(48) + 1j * np.random.randn(48)).astype(np.complex64) * 2.0

    # L-LTF0 and L-LTF1 (both are the TX sequence, channel is the same since
    # the channel is constant in time). The frame_equalizer estimates H from
    # L-LTF0 or L-LTF1 (per the synthesis-test baseline, L-LTF0 is at
    # sym_counter=0 and L-LTF1 at sym_counter=1).
    lltf0_rx = K_LLTF_48_TX * H_chan_t0
    lltf1_rx = K_LLTF_48_TX * H_chan_t0

    H_lltf0 = estimate_h_from_lltf(lltf0_rx)
    H_lltf1 = estimate_h_from_lltf(lltf1_rx)

    # L-SIG at counter=2, no channel rotation (channel is constant)
    lsig_rx = bpsk * H_chan_t0
    eq_lltf0 = lsig_rx / H_lltf0
    eq_lltf1 = lsig_rx / H_lltf1

    imag_max_lltf0 = np.max(np.abs(eq_lltf0.imag))
    imag_max_lltf1 = np.max(np.abs(eq_lltf1.imag))
    print(f"[NO-ROTATION] imag_max={imag_max_lltf0:.2e} (expect ~0)")

    assert imag_max_lltf0 < 1e-5, (
        f"no-rotation sanity: imag_max={imag_max_lltf0} (want <1e-5)")
    assert imag_max_lltf1 < 1e-5, (
        f"no-rotation sanity (L-LTF1): imag_max={imag_max_lltf1} (want <1e-5)")
    print("[PASS] test_no_rotation_lltf0_equals_lltf1")


def test_lltf0_h_residual_phase():
    """H from L-LTF0 (8us gap to L-SIG). Simulate a time-varying channel that
    rotates 0.01 rad/us (large enough to expose a clear residual, small
    enough to keep per-SC phases in (-pi, pi)).
    Verify imag_max > 0.3 (test setup is valid -- large residual expected)."""
    np.random.seed(42)
    bpsk = np.array([1 if (i % 2 == 0) else -1 for i in range(48)], dtype=np.complex64)

    H_chan_t0 = (np.random.randn(48) + 1j * np.random.randn(48)).astype(np.complex64) * 2.0

    # L-LTF0 at t=0
    lltf0_rx = K_LLTF_48_TX * H_chan_t0
    H_lltf0 = estimate_h_from_lltf(lltf0_rx)

    # L-SIG at t=8us (counter=2, but for this test we only care about the
    # channel rotation gap, not the CFO/SFO compensation).
    H_chan_t_lsig = rotate_per_symbol(H_chan_t0, 0.01, t_us=8.0)
    lsig_rx = bpsk * H_chan_t_lsig
    eq_lltf0 = lsig_rx / H_lltf0

    imag_max_lltf0 = np.max(np.abs(eq_lltf0.imag))
    print(f"[L-LTF0 H] imag_max={imag_max_lltf0:.4f} (expect LARGE for slow channel drift)")

    # Also report the L-LTF0 reference residual (with L-LTF0 H + 8us channel):
    # this is the exact test setup. Should be large.
    assert imag_max_lltf0 > 0.3, (
        f"Test setup wrong: L-LTF0 H with 8us gap should have large residual, "
        f"got imag_max={imag_max_lltf0}")
    print(f"[L-LTF0 H reference] imag_max={imag_max_lltf0:.4f}")
    print("[PASS] test_lltf0_h_residual_phase")


def test_lltf1_h_residual_phase():
    """H from L-LTF1 (4us gap to L-SIG). Simulate the same time-varying
    channel as test_lltf0_h_residual_phase. Assert imag_max_lltf1 <
    imag_max_lltf0 (the fix attempt should produce smaller residual phase)."""
    np.random.seed(42)
    bpsk = np.array([1 if (i % 2 == 0) else -1 for i in range(48)], dtype=np.complex64)

    H_chan_t0 = (np.random.randn(48) + 1j * np.random.randn(48)).astype(np.complex64) * 2.0

    # L-LTF1 at t=4us (i.e. halfway between L-LTF0 and L-SIG)
    H_chan_t_lltf1 = rotate_per_symbol(H_chan_t0, 0.01, t_us=4.0)
    lltf1_rx = K_LLTF_48_TX * H_chan_t_lltf1
    H_lltf1 = estimate_h_from_lltf(lltf1_rx)

    # L-SIG at t=8us
    H_chan_t_lsig = rotate_per_symbol(H_chan_t0, 0.01, t_us=8.0)
    lsig_rx = bpsk * H_chan_t_lsig
    eq_lltf1 = lsig_rx / H_lltf1

    imag_max_lltf1 = np.max(np.abs(eq_lltf1.imag))
    print(f"[L-LTF1 H] imag_max={imag_max_lltf1:.4f} (expect < L-LTF0)")

    # Recompute L-LTF0 residual for direct comparison (same seed = same channel)
    lltf0_rx = K_LLTF_48_TX * H_chan_t0
    H_lltf0 = estimate_h_from_lltf(lltf0_rx)
    eq_lltf0 = lsig_rx / H_lltf0
    imag_max_lltf0 = np.max(np.abs(eq_lltf0.imag))

    assert imag_max_lltf1 < imag_max_lltf0, (
        f"L-LTF1 H should leave smaller residual than L-LTF0 H, "
        f"got imag_max_lltf1={imag_max_lltf1}, imag_max_lltf0={imag_max_lltf0}")
    print(f"[L-LTF0 H reference] imag_max={imag_max_lltf0:.4f}")
    print("[PASS] test_lltf1_h_residual_phase")


if __name__ == "__main__":
    test_no_rotation_lltf0_equals_lltf1()
    test_lltf0_h_residual_phase()
    test_lltf1_h_residual_phase()
    print("\nAll synthetic H-estimation tests passed.")
