#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 78c-2: Test force-to-zero at 5 USRP-identified null SCs in Python.

Re-runs the 78a Layer 4 model with the option to force 5 specific SCs
to 0 at the decoder input. Compares 3 conditions:
  - Baseline (no forcing)
  - Force 5 USRP-identified SCs to 0
  - Force 5 RANDOM SCs to 0 (control)
"""
import json
import sys
import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')
from test_htsig_viterbi_synthetic import (
    make_known_htsig_bits, _bcc_encode_48,
    htsig_interleave, bpsk_qbpsk_modulate, insert_ht_pilots,
    apply_usrp_like_channel, decode_htsig_attempt,
)

# Load identified null SCs
with open('/tmp/p78c_null_scs.json') as f:
    null_sc_data = json.load(f)
force_scs = null_sc_data['top5_null_scs_data_order']
print(f"Forcing 5 SCs to 0 at decoder: {force_scs} (data array indices)")
print(f"  actual SC indices: {null_sc_data['top5_null_scs_actual_index']}")

# Also pick 5 random SCs for control (seeded for determinism)
np.random.seed(42)
random_scs = sorted(np.random.choice(48, size=5, replace=False).tolist())
print(f"Control: 5 random SCs to 0: {random_scs}")


def synth_and_decode_layer4_force(case_name, force_scs=None, frame_seed=0, **case_kwargs):
    """Layer 4 with optional force-to-zero at specific SCs (data array indices)."""
    bits48_tx = make_known_htsig_bits(**case_kwargs)
    coded96 = _bcc_encode_48(bits48_tx)
    coded0 = coded96[0:48]
    coded1 = coded96[48:96]
    intl0 = htsig_interleave(coded0)
    intl1 = htsig_interleave(coded1)
    syms0 = bpsk_qbpsk_modulate(intl0)
    syms1 = bpsk_qbpsk_modulate(intl1)
    sc52_0 = insert_ht_pilots(syms0, 0)
    sc52_1 = insert_ht_pilots(syms1, 1)

    sc52_0_rx, sc52_1_rx, H, delta, n_nulls = apply_usrp_like_channel(
        sc52_0, sc52_1, frame_seed=frame_seed)
    eq52_0 = sc52_0_rx / H
    eq52_1 = sc52_1_rx / H

    # Force specific SCs to 0
    if force_scs is not None:
        for sc in force_scs:
            eq52_0[sc] = 0
            eq52_1[sc] = 0

    eq48_a = eq52_0[0:48]
    eq48_b = eq52_1[0:48]
    dec48, info = decode_htsig_attempt(eq48_a, eq48_b)
    info["case"] = case_name
    info["frame_seed"] = frame_seed
    info["force_scs"] = list(force_scs) if force_scs is not None else None
    if dec48 is not None:
        info["bit_match"] = bool(np.array_equal(dec48, bits48_tx))
    return info


def run_test(force_scs, label, n_frames=100):
    cases = [
        ("A", {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0}),
        ("B", {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1}),
        ("C", {"mcs": 0, "length": 10, "ldpc": 1}),
    ]
    results = {}
    for name, kwargs in cases:
        passed = 0
        for seed in range(n_frames):
            info = synth_and_decode_layer4_force(name, force_scs=force_scs,
                                                 frame_seed=seed, **kwargs)
            if info.get("crc_ok") and info.get("bit_match"):
                passed += 1
        results[name] = passed
        print(f"  [{label}] Layer4/{name}: {passed}/{n_frames}")
    total = sum(results.values())
    total_possible = 3 * n_frames
    pct = 100.0 * total / total_possible
    print(f"  [{label}] Total: {total}/{total_possible} ({pct:.1f}%)")
    return results, pct, total


def main():
    print("=== Layer 4: Baseline (no forcing) ===")
    res_baseline, pct_baseline, total_baseline = run_test(force_scs=None, label="baseline")

    print("\n=== Layer 4: Force 5 USRP-identified null SCs to 0 ===")
    res_force_usrp, pct_force_usrp, total_force_usrp = run_test(
        force_scs=force_scs, label="force-usrp-nulls")

    print("\n=== Layer 4: Force 5 RANDOM SCs to 0 (control) ===")
    res_force_random, pct_force_random, total_force_random = run_test(
        force_scs=random_scs, label="force-random")

    print()
    print("=" * 60)
    print("COMPARISON")
    print("=" * 60)
    print(f"  baseline:           {pct_baseline:.1f}%  ({total_baseline}/300)")
    print(f"  force-USRP-nulls:   {pct_force_usrp:.1f}%  ({total_force_usrp}/300)  "
          f"(Δ {pct_force_usrp - pct_baseline:+.1f})")
    print(f"  force-random:       {pct_force_random:.1f}%  ({total_force_random}/300)  "
          f"(Δ {pct_force_random - pct_baseline:+.1f})")
    print()

    print("INTERPRETATION:")
    if pct_force_usrp > pct_baseline + 5:
        print("  Forcing USRP-identified null SCs IMPROVES success rate.")
        print("  → Approach is valid in principle; the wall is in those specific SCs.")
    elif pct_force_usrp < pct_baseline - 2:
        print("  Forcing USRP-identified null SCs HURTS success rate.")
        print("  → Synthetic nulls are ROTATING each frame; USRP nulls are STABLE.")
        print("  → Force-to-zero at fixed SCs is a mismatch; different approach needed.")
    else:
        print("  Forcing USRP-identified null SCs has no meaningful effect.")
        print("  → The wall is not in those 5 SCs specifically; check equalization.")

    if pct_force_random < pct_baseline - 5:
        print("  → Control confirms: forcing arbitrary SCs hurts (validation criterion).")
    elif pct_force_random > pct_baseline:
        print("  → Control shows forcing HELPS generally (suspicious — may indicate")
        print("     the model itself has too many bad SCs and arbitrary removal helps).")
    else:
        print("  → Control effect within ±5% — neither helps nor hurts overall.")


if __name__ == "__main__":
    main()
