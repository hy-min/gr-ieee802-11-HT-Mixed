#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 79 Stage 2: USRP capture replay HT-SIG test.

Loads the Phase 78b USRP capture dump (/tmp/p78b_per_frame.json, 8-10 frames
at 5250 MHz with HT-SIG FFT bins and post-equalization values) and re-runs
the HT-SIG viterbi decoder pipeline in offline Python.

The Python decoder mirrors the C++ redesign in
``lib/frame_equalizer_impl.cc:decode_htsig_from_rotated``:

  1. Estimate per-symbol delta from the 4 HT pilots (QBPSK grid search)
     using the channel estimate Hhdr52 derived from bin/eq in the dump.
  2. Apply per-SC delta correction to the equalized data SCs.
  3. Hard-bit decision on the IMAG axis (QBPSK).
  4. Try all 16 QBPSK rotation/inversion candidates.
  5. Deinterleave + viterbi decode (rate 1/2 K=7 polynomials 133/171).
  6. Validate tail + CRC8 (HT_SIG_PARSE_OK iff both pass).

The dump only carries post-equalization HT-SIG bins, not raw IQ, so the
Python decoder re-derives Hhdr52 as H = bin / eq and re-equalizes the
captured bins. This validates the same code path the C++ redesign runs
inside frame_equalizer, but does not require USRP hardware.

Baseline (Phase 78b): 0 HT_SIG_PARSE_OK / 8 frames.
Target:               HT_SIG_PARSE_OK > 0 (any improvement validates
                      redesign).

Usage:
    python examples/test_usrp_capture_replay_htsig.py --mode off
    python examples/test_usrp_capture_replay_htsig.py --mode on
    python examples/test_usrp_capture_replay_htsig.py --mode both
"""
import argparse
import json
import os
import sys

import numpy as np


CAPTURE_PATH = "/tmp/p78b_per_frame.json"


# ============================================================
# 802.11n 52-bin subcarrier index (TX order). Last 4 entries are
# the pilot SCs at indices {-21, -7, +7, +21}.
# ============================================================
K_SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)

# Pilot SCs in array-index space and SC-index space.
PILOT_IDX = np.array([48, 49, 50, 51], dtype=np.int32)
PILOT_SC = np.array([-21, -7, 7, 21], dtype=np.int32)

# HT-SIG pilot polarities per IEEE 802.11n-2016 sec 17.3.5.10.
HT_SIG0_POLARITY = np.array([1, 1, 1, -1])
HT_SIG1_POLARITY = np.array([-1, -1, -1, 1])

# Estimator tuning constants.
MIN_H_MAG = 0.01
N_GRID = 64


# ============================================================
# CRC8 (IEEE 802.11-2016 sec 18.3.5.3.5)
# Polynomial x^8+x^2+x+1, init all ones, final invert.
# ============================================================
def _ht_crc8_compute(bits0_33):
    """CRC8 over bits[0..33] LSB-first. Returns 8-bit array, LSB-first."""
    c = [1, 1, 1, 1, 1, 1, 1, 1]
    for i in range(34):
        m = int(bits0_33[i]) & 1
        c0, c1, c2, c3, c4, c5, c6, c7 = c
        c = [
            c7 ^ m,
            c0 ^ c7 ^ m,
            c1 ^ c7 ^ m,
            c2,
            c3,
            c4,
            c5,
            c6,
        ]
    return np.array([(c[j] ^ 1) & 1 for j in range(8)], dtype=np.uint8)


# ============================================================
# Viterbi decoder (hard, K=7, rate 1/2, polynomials 133/171)
# Mirrors C++ viterbi_decode_133_171 in lib/frame_equalizer_impl.cc
# ============================================================
def viterbi_decode_133_171(rx_bits):
    """Decode rx_bits (length N, even). Returns (decoded, metric)."""
    assert len(rx_bits) % 2 == 0
    n_steps = len(rx_bits) // 2
    INF = 10 ** 9
    metric_prev = np.full(64, INF, dtype=np.int64)
    metric_prev[0] = 0
    prev_state = np.full((n_steps + 1, 64), -1, dtype=np.int32)
    prev_bit = np.zeros((n_steps + 1, 64), dtype=np.uint8)

    for t in range(n_steps):
        metric_curr = np.full(64, INF, dtype=np.int64)
        r0 = int(rx_bits[2 * t])
        r1 = int(rx_bits[2 * t + 1])
        for s in range(64):
            mp = metric_prev[s]
            if mp >= INF:
                continue
            for b in (0, 1):
                reg = ((s << 1) | b) & 0x7F
                o0 = bin(reg & 0o133).count("1") & 1
                o1 = bin(reg & 0o171).count("1") & 1
                ns = reg & 0x3F
                bm = (o0 != r0) + (o1 != r1)
                mc = mp + bm
                if mc < metric_curr[ns]:
                    metric_curr[ns] = mc
                    prev_state[t + 1, ns] = s
                    prev_bit[t + 1, ns] = b
        metric_prev = metric_curr

    best_state = 0
    best_metric = int(metric_prev[best_state])
    if best_metric >= INF:
        idx = int(np.argmin(metric_prev))
        best_state = idx
        best_metric = int(metric_prev[idx])
        if best_metric >= INF:
            return None, INF

    decoded = np.zeros(n_steps, dtype=np.uint8)
    cur = best_state
    for t in range(n_steps, 0, -1):
        decoded[t - 1] = prev_bit[t, cur]
        cur = int(prev_state[t, cur])
        if cur < 0 and t > 1:
            return None, INF
    return decoded, best_metric


# ============================================================
# HT-SIG deinterleaver per 802.11n Table 18-6 (depth-2, BPSK)
# Mirrors C++ RX in lib/frame_equalizer_impl.cc:2159-2166
# ============================================================
def htsig_deinterleave(bits48):
    """Inverse of 802.11n HT-SIG forward interleaver."""
    out = np.zeros(48, dtype=np.uint8)
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[k] = bits48[j] & 1
    return out


# ============================================================
# Per-symbol delta estimator (QBPSK grid search)
# Mirrors C++ estimate_symbol_delta_qbpsk in
# lib/frame_equalizer_impl.cc:2101
# ============================================================
def estimate_symbol_delta(eq_pilots, H_pilots, pilot_polarity):
    """Grid-search delta in {0, 1/64, ..., 63/64} maximizing
    inner product of equalized-pilot residual with expected ramp."""
    TWO_PI = 2.0 * np.pi
    eq_pilots = np.asarray(eq_pilots, dtype=np.complex64)
    H_pilots = np.asarray(H_pilots, dtype=np.complex64)
    pol = np.asarray(pilot_polarity, dtype=np.float32)

    valid = np.abs(H_pilots) > MIN_H_MAG
    if not np.any(valid):
        return 0.0
    # QBPSK: pilot on IMAG axis, +j or -j. Strip known polarity by
    # multiplying by conj(pol * 1j) = -1j * pol, which rotates the pilot
    # back to the REAL axis. With C++ definition polarity is +j or -j
    # (1j*+1 or 1j*-1), so conj(1j*pol) = -1j*pol. We use the
    # interpretation that matches the C++ test_htsig_delta_synthetic.py
    # reference: residual = eq * conj(pol) where pol is a real +/-1
    # representing the pilot's imag-sign.
    residual = eq_pilots * np.conj(pol.astype(np.complex64))
    residual_valid = residual * valid.astype(np.complex64)

    best_delta = 0.0
    best_mag = 0.0
    for d in range(N_GRID):
        delta = d / N_GRID
        expected = np.exp(1j * TWO_PI * PILOT_SC * delta / 64.0).astype(np.complex64)
        inner = np.sum(expected * residual_valid)
        mag = np.abs(inner)
        if mag > best_mag:
            best_mag = mag
            best_delta = delta
    return float(best_delta)


# ============================================================
# Per-SC delta correction
# Mirrors C++ apply_delta_correction_to_eq in
# lib/frame_equalizer_impl.cc:2164
# ============================================================
def apply_delta_correction_to_eq(eq, sc_index, delta):
    """Multiply eq by exp(+j*2*pi*sc_index*delta/64)."""
    return eq * np.exp(1j * 2.0 * np.pi * sc_index * delta / 64.0)


# ============================================================
# HT-SIG viterbi decode: try 16 QBPSK rotation/inversion candidates
# Mirrors C++ decode_htsig_from_rotated in
# lib/frame_equalizer_impl.cc:2621
# ============================================================
def decode_htsig_from_capture(bin_htsig0, bin_htsig1, eq_htsig0, eq_htsig1,
                              apply_delta=False, log_delta=False):
    """Run the full HT-SIG viterbi decode on captured bins.

    Returns: (parse_ok, info_dict) where info_dict contains
    parsed MCS/length, chosen rot/inv, best metric, and per-symbol
    deltas (if apply_delta is True).
    """
    bin_htsig0 = np.asarray(bin_htsig0, dtype=np.complex64)
    bin_htsig1 = np.asarray(bin_htsig1, dtype=np.complex64)
    eq_htsig0 = np.asarray(eq_htsig0, dtype=np.complex64)
    eq_htsig1 = np.asarray(eq_htsig1, dtype=np.complex64)

    # Derive Hhdr52 from the captured bins and equalized values.
    # eq = bin / H, so H = bin / eq. This gives the channel that the
    # C++ equalizer actually used. We treat this as the ground truth H.
    eps = 1e-6
    H52_a = np.where(np.abs(eq_htsig0) > eps, bin_htsig0 / eq_htsig0, 0.0)
    H52_b = np.where(np.abs(eq_htsig1) > eps, bin_htsig1 / eq_htsig1, 0.0)

    # Per-symbol delta from the four HT pilots (Phase 79 redesign).
    delta_a = 0.0
    delta_b = 0.0
    if apply_delta:
        # Use bin / H = eq as the equalized pilots (the "rx" prior to
        # the redesign correction; the C++ re-divides by H in
        # decode_htsig_from_rotated, so this is the same equalized
        # signal that the C++ estimator sees).
        eq_pilots_a = np.array([eq_htsig0[i] for i in PILOT_IDX], dtype=np.complex64)
        eq_pilots_b = np.array([eq_htsig1[i] for i in PILOT_IDX], dtype=np.complex64)
        H_pilots_a = np.array([H52_a[i] for i in PILOT_IDX], dtype=np.complex64)
        H_pilots_b = np.array([H52_b[i] for i in PILOT_IDX], dtype=np.complex64)
        delta_a = estimate_symbol_delta(eq_pilots_a, H_pilots_a, HT_SIG0_POLARITY)
        delta_b = estimate_symbol_delta(eq_pilots_b, H_pilots_b, HT_SIG1_POLARITY)

    info = {
        "delta_htsig0": delta_a,
        "delta_htsig1": delta_b,
    }
    if log_delta:
        print(f"[HTSIG_DELTA] delta_htsig0={delta_a:.4f} delta_htsig1={delta_b:.4f}",
              flush=True)

    # Slice pilots out: 48 data SCs per symbol.
    bin48_a = bin_htsig0[:48]
    bin48_b = bin_htsig1[:48]
    H48_a = H52_a[:48]
    H48_b = H52_b[:48]

    # Try all 16 QBPSK rotation/inversion candidates.
    # rot in {0,1,2,3} mapping (matches apply_htsig_rotation in C++):
    #   0: rotate by -90 deg  (mult by +j)
    #   1: rotate by   0 deg  (mult by  1)
    #   2: rotate by +90 deg  (mult by -j)
    #   3: rotate by 180 deg  (mult by -1)
    rot_phases = {0: 1j, 1: 1.0 + 0j, 2: -1j, 3: -1.0 + 0j}

    best_decoded = None
    best_metric = 10 ** 9  # best metric across CRC-passing candidates
    best_choice = None
    best_fail = "init"
    best_metric_overall = 10 ** 9  # best metric across ALL candidates
    best_overall_choice = None
    best_overall_fail = "init"

    for rot_idx in range(4):
        rot = rot_phases[rot_idx]
        rot_htsig0 = bin48_a * np.conj(rot)
        rot_htsig1 = bin48_b * np.conj(rot)

        for inv_a in (False, True):
            for inv_b in (False, True):
                # Equalize: eq = bin / H.
                eps = 1e-6
                eq48_a = np.where(np.abs(H48_a) > eps, rot_htsig0 / H48_a, 0.0)
                eq48_b = np.where(np.abs(H48_b) > eps, rot_htsig1 / H48_b, 0.0)

                # Phase 79: per-symbol delta correction.
                if apply_delta:
                    for i in range(48):
                        sc = K_SC_INDEX_52[i]
                        eq48_a[i] = apply_delta_correction_to_eq(
                            eq48_a[i], sc, delta_a)
                        eq48_b[i] = apply_delta_correction_to_eq(
                            eq48_b[i], sc, delta_b)

                # Hard-bit decision on IMAG axis (QBPSK).
                bits_a = (eq48_a.imag >= 0).astype(np.uint8)
                bits_b = (eq48_b.imag >= 0).astype(np.uint8)
                if inv_a:
                    bits_a ^= 1
                if inv_b:
                    bits_b ^= 1

                # Deinterleave each half.
                deint_a = htsig_deinterleave(bits_a)
                deint_b = htsig_deinterleave(bits_b)
                enc96 = np.concatenate([deint_a, deint_b])

                decoded, metric = viterbi_decode_133_171(enc96)
                if decoded is None or len(decoded) != 48:
                    continue

                # Track best metric across ALL candidates (for diagnostic).
                if metric < best_metric_overall:
                    best_metric_overall = metric
                    best_overall_choice = (rot_idx, inv_a, inv_b)
                    tail_ok = np.all(decoded[42:48] == 0)
                    crc_calc = _ht_crc8_compute(decoded[0:34])
                    crc_match = np.array_equal(crc_calc, decoded[34:42])
                    field_ok = (decoded[7] == 0 and np.all(decoded[24:27] == 0))
                    if tail_ok and crc_match and field_ok:
                        best_overall_fail = "OK"
                    else:
                        # Identify the specific failure for diagnostic.
                        if not tail_ok:
                            best_overall_fail = "tail_fail"
                        elif not crc_match:
                            best_overall_fail = "crc_fail"
                        else:
                            best_overall_fail = "field_fail"

                # Validate tail + CRC8 + field check for parse_ok.
                tail_ok = np.all(decoded[42:48] == 0)
                crc_calc = _ht_crc8_compute(decoded[0:34])
                crc_match = np.array_equal(crc_calc, decoded[34:42])
                field_ok = (decoded[7] == 0 and np.all(decoded[24:27] == 0))

                if tail_ok and crc_match and field_ok and metric < best_metric:
                    best_metric = metric
                    best_decoded = decoded
                    best_choice = (rot_idx, inv_a, inv_b)
                    best_fail = "OK"

    if best_decoded is None:
        info["parse_ok"] = False
        info["fail_reason"] = best_overall_fail
        info["best_metric"] = best_metric_overall
        info["best_rot"] = best_overall_choice[0] if best_overall_choice else None
        info["best_inv_a"] = best_overall_choice[1] if best_overall_choice else None
        info["best_inv_b"] = best_overall_choice[2] if best_overall_choice else None
        return False, info

    info["parse_ok"] = True
    info["mcs"] = sum(int(best_decoded[i]) << i for i in range(7))
    info["length"] = sum(int(best_decoded[8 + i]) << i for i in range(16))
    info["sgi"] = int(best_decoded[31])
    info["agg"] = int(best_decoded[27])
    info["ldpc"] = int(best_decoded[30])
    info["rot"] = best_choice[0]
    info["inv_a"] = best_choice[1]
    info["inv_b"] = best_choice[2]
    info["best_metric"] = best_metric
    info["fail_reason"] = "OK"
    return True, info


# ============================================================
# Loader
# ============================================================
def load_capture(path):
    with open(path, "r") as f:
        data = json.load(f)
    print(f"[LOAD] {path}: {len(data)} entries", flush=True)
    # Filter entries that have the full bin+eq quartet.
    usable = [d for d in data
              if "bin_htsig0" in d and "eq_htsig0" in d
              and "bin_htsig1" in d and "eq_htsig1" in d]
    print(f"[LOAD] {len(usable)}/{len(data)} entries have bin+eq data", flush=True)
    return usable


def to_complex(d_list):
    """Convert a JSON list of {re, im} dicts to a numpy complex64 array."""
    return np.array([c["re"] + 1j * c["im"] for c in d_list], dtype=np.complex64)


# ============================================================
# Replay runner
# ============================================================
def run_replay(frames, apply_delta, log_delta_dump):
    """Run the redesigned HT-SIG viterbi on the captured frames.

    Returns: count of HT_SIG_PARSE_OK events.
    """
    n_ok = 0
    n_total = 0
    parse_results = []
    for f in frames:
        frame_id = f.get("frame_id", "?")
        bin0 = to_complex(f["bin_htsig0"])
        bin1 = to_complex(f["bin_htsig1"])
        eq0 = to_complex(f["eq_htsig0"])
        eq1 = to_complex(f["eq_htsig1"])

        ok, info = decode_htsig_from_capture(
            bin0, bin1, eq0, eq1,
            apply_delta=apply_delta,
            log_delta=log_delta_dump,
        )
        n_total += 1
        if ok:
            n_ok += 1
            print(
                f"[PARSE_OK] frame={frame_id} rot={info['rot']} "
                f"inv_a={info['inv_a']} inv_b={info['inv_b']} "
                f"mcs={info['mcs']} length={info['length']} "
                f"sgi={info['sgi']} agg={info['agg']} ldpc={info['ldpc']} "
                f"metric={info['best_metric']} "
                f"delta_a={info['delta_htsig0']:.4f} "
                f"delta_b={info['delta_htsig1']:.4f}",
                flush=True,
            )
        else:
            print(
                f"[PARSE_FAIL] frame={frame_id} fail={info['fail_reason']} "
                f"metric={info.get('best_metric', 'n/a')} "
                f"delta_a={info.get('delta_htsig0', 0):.4f} "
                f"delta_b={info.get('delta_htsig1', 0):.4f}",
                flush=True,
            )
        parse_results.append((frame_id, ok, info))

    print(f"[STAGE2] HT_SIG_PARSE_OK = {n_ok}/{n_total}", flush=True)
    return n_ok, parse_results


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Phase 79 Stage 2 USRP replay")
    parser.add_argument("--capture", default=CAPTURE_PATH,
                        help="Path to capture JSON")
    parser.add_argument("--apply-delta", action="store_true",
                        help="Enable per-symbol delta correction (Phase 79 redesign)")
    parser.add_argument("--log-delta", action="store_true",
                        help="Log per-symbol delta values per frame")
    parser.add_argument("--mode", choices=["off", "on", "both"], default="both",
                        help="Run baseline (off), redesigned (on), or both")
    args = parser.parse_args()

    capture = load_capture(args.capture)
    n_frames = len(capture)

    result_off = None
    result_on = None
    if args.mode in ("off", "both"):
        print(f"\n[STAGE2-OFF] Running baseline (delta=OFF) on {n_frames} frames...",
              flush=True)
        result_off, _ = run_replay(capture, apply_delta=False,
                                    log_delta_dump=args.log_delta)
        print(f"[STAGE2-OFF] HT_SIG_PARSE_OK = {result_off}", flush=True)

    if args.mode in ("on", "both"):
        print(f"\n[STAGE2-ON] Running redesigned (delta=ON) on {n_frames} frames...",
              flush=True)
        result_on, _ = run_replay(capture, apply_delta=True,
                                   log_delta_dump=args.log_delta)
        print(f"[STAGE2-ON] HT_SIG_PARSE_OK = {result_on}", flush=True)

    if args.mode == "both":
        baseline = result_off if result_off is not None else 0
        if result_on is not None and result_on > baseline:
            print(f"\n[PASS] Stage 2: redesign improved HT_SIG_PARSE_OK "
                  f"({baseline} -> {result_on})", flush=True)
            sys.exit(0)
        elif result_on is not None and result_on == baseline:
            print(f"\n[NEUTRAL] Stage 2: redesign did not change "
                  f"HT_SIG_PARSE_OK ({baseline} -> {result_on})", flush=True)
            sys.exit(2)
        else:
            print(f"\n[FAIL] Stage 2: redesign did not improve "
                  f"({baseline} vs {result_on})", flush=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
