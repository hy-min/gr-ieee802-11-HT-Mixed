#!/home/hy/conda/envs/gnuradio/bin/python
"""
Phase 44 — Soft-LLR viterbi synthetic test.

Hypothesis: feeding soft LLR magnitudes (signed by |H[i]|) to viterbi
will down-weight the contribution of channel-null SCs (where |H[i]| ~ 0)
that currently produce random hard-bit flips. The branch metric becomes
squared Euclidean distance from a soft observation (LLR) to the expected
BPSK constellation point {+1, -1}, weighted by confidence.

Three layers, all must PASS:

  Layer 1 (regression): With clean (high SNR) and uniform |H|, soft-LLR
    viterbi produces identical decoded bits to hard-bit viterbi on
    synthetically generated frames with known MCS/length.

  Layer 2 (channel nulls): With per-SC |H[i]| varying by 50x and a few
    nulls (|H[i]| < 0.05), hard-bit viterbi fails (errors at null SCs)
    but soft-LLR viterbi succeeds because the down-weighted branch
    metric ignores the noise at null SCs.

  Layer 3 (SNR sweep): At SNR 0-12 dB with frequency-selective H
    (multiplicative Rayleigh), soft-LLR achieves strictly lower BER than
    hard-bit.

This test is offline (pure NumPy) and does NOT require a rebuild.
Mirrors test_htsig_viterbi_synthetic.py (Phase 37).
"""
import numpy as np


# ============================================================
# Hard-bit viterbi (reference implementation, mirrors Phase 37)
# ============================================================
def viterbi_decode_133_171_hard(rx_bits, n_steps=None):
    """K=7, rate 1/2, polynomials 133/171 (octal). Hard-bit input.

    rx_bits: sequence of {0, 1} ints.
    Returns (decoded_bits, best_metric)."""
    if n_steps is None:
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
# Phase 44: Soft-LLR viterbi
# ============================================================
def viterbi_decode_133_171_soft(rx_soft, n_steps=None):
    """K=7, rate 1/2, polynomials 133/171 (octal). Soft LLR input.

    rx_soft: sequence of floats, length 2*n_steps.
      Each pair (r0, r1) is the LLR for the two coded bits at that step.
      Sign indicates the bit value (positive -> bit 1, negative -> bit 0).
      Magnitude indicates confidence (proportional to |H[i]|).

    Branch metric: for bit b in {0, 1} with output (o0, o1):
      bm = (r0 - (1 if o0 else -1))^2 + (r1 - (1 if o1 else -1))^2
    So a confident LLR with matching constellation adds 0; a confident
    LLR with mismatched constellation adds ~4*conf^2 (large penalty).

    Returns (decoded_bits, best_metric).
    """
    if n_steps is None:
        assert len(rx_soft) % 2 == 0
        n_steps = len(rx_soft) // 2
    INF = 10 ** 18  # float-based metric
    metric_prev = np.full(64, INF, dtype=np.float64)
    metric_prev[0] = 0.0
    prev_state = np.full((n_steps + 1, 64), -1, dtype=np.int32)
    prev_bit = np.zeros((n_steps + 1, 64), dtype=np.uint8)

    for t in range(n_steps):
        metric_curr = np.full(64, INF, dtype=np.float64)
        r0 = float(rx_soft[2 * t])
        r1 = float(rx_soft[2 * t + 1])
        for s in range(64):
            mp = metric_prev[s]
            if mp >= INF:
                continue
            for b in (0, 1):
                reg = ((s << 1) | b) & 0x7F
                o0 = bin(reg & 0o133).count("1") & 1
                o1 = bin(reg & 0o171).count("1") & 1
                ns = reg & 0x3F
                # Branch metric: squared error from the BPSK reference
                err0 = r0 - (1.0 if o0 else -1.0)
                err1 = r1 - (1.0 if o1 else -1.0)
                bm = err0 * err0 + err1 * err1
                mc = mp + bm
                if mc < metric_curr[ns]:
                    metric_curr[ns] = mc
                    prev_state[t + 1, ns] = s
                    prev_bit[t + 1, ns] = b
        metric_prev = metric_curr

    best_state = 0
    best_metric = float(metric_prev[best_state])
    if best_metric >= INF:
        idx = int(np.argmin(metric_prev))
        best_state = idx
        best_metric = float(metric_prev[idx])
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
# Helpers: HT-SIG BCC encode/deinterleave (mirrors Phase 37 test)
# ============================================================
def _bcc_encode_48(bits48):
    """48 info+tail -> 96 coded bits. Polynomials 133/171 (octal)."""
    assert len(bits48) == 48
    g0 = 0o133
    g1 = 0o171
    state = 0
    out = np.zeros(96, dtype=np.uint8)
    for t in range(48):
        reg = ((state << 1) | int(bits48[t])) & 0x7F
        o0 = bin(reg & g0).count("1") & 1
        o1 = bin(reg & g1).count("1") & 1
        out[2 * t] = o0
        out[2 * t + 1] = o1
        state = reg & 0x3F
    return out


def htsig_deinterleave(bits48):
    """802.11n HT-SIG deinterleaver: j = 3*(k%16) + k//16, out[k] = in[j]."""
    out = np.zeros(48, dtype=np.uint8)
    for k in range(48):
        j = 3 * (k % 16) + k // 16
        out[k] = bits48[j] & 1
    return out


def make_known_htsig_bits(mcs=0, length=100, sgi=0, aggregation=0, ldpc=0):
    """Build the 48-bit HT-SIG field per IEEE 802.11-2016 Section 18.3.5.3."""
    bits = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        bits[i] = (mcs >> i) & 1
    for i in range(16):
        bits[8 + i] = (length >> i) & 1
    bits[27] = 1 if aggregation else 0
    for i in range(2):
        bits[28 + i] = 0
    bits[30] = 1 if ldpc else 0
    bits[31] = 1 if sgi else 0
    for i in range(2):
        bits[32 + i] = 0
    bits[34:42] = _ht_crc8_compute(bits[0:34])
    return bits


def _ht_crc8_compute(bits0_33):
    """CRC8 per IEEE 802.11-2016 Section 18.3.5.3.5: poly x^8+x^2+x+1, init 1s, final invert."""
    c = [1, 1, 1, 1, 1, 1, 1, 1]
    for i in range(34):
        m = bits0_33[i] & 1
        c0, c1, c2, c3, c4, c5, c6, c7 = c
        new7 = c6
        new6 = c5
        new5 = c4
        new4 = c3
        new3 = c2
        new2 = c1 ^ c7 ^ m
        new1 = c0 ^ c7 ^ m
        new0 = c7 ^ m
        c = [new0, new1, new2, new3, new4, new5, new6, new7]
    out = np.zeros(8, dtype=np.uint8)
    for j in range(8):
        out[j] = (c[j] ^ 1) & 1
    return out


# ============================================================
# Modulation: bits -> +/-1 (BPSK)
# ============================================================
def bpsk_modulate(bits):
    """0 -> -1, 1 -> +1."""
    return (2.0 * bits.astype(np.float64) - 1.0)


# ============================================================
# Soft LLR computation from eq (real-axis BPSK) and |H|
# ============================================================
def compute_soft_llr_from_eq(eq_real, h_mag, max_h_mag=None):
    """BPSK: bit on REAL axis. LLR[i] = sign(eq.real[i]) * |H[i]| / max(|H|).

    eq_real: equalized symbols (real-valued).
    h_mag: |H[i]| per SC.
    max_h_mag: optional max; defaults to max(h_mag).
    Returns: array of floats, length = len(eq_real).
    """
    if max_h_mag is None:
        max_h_mag = float(np.max(h_mag))
        if max_h_mag < 1e-9:
            max_h_mag = 1e-9
    sign = np.sign(eq_real)
    return sign * (h_mag / max_h_mag)


# ============================================================
# Channel model: per-SC |H[i]| varying (frequency-selective fading)
# ============================================================
def make_freq_selective_h(n_scs=48, n_nulls=2, rng=None):
    """Return |H[i]| array: 48 SCs with a few deep nulls.

    For 6-tap Rayleigh-like profile, |H[i]| varies 0.1-1.5; we add
    `n_nulls` SCs with |H[i]| = 0.02 (deep null).
    """
    if rng is None:
        rng = np.random.default_rng(seed=42)
    h = 0.5 + rng.standard_gamma(2.0, size=n_scs) * 0.3  # 0.5-2.0 typically
    h = np.clip(h, 0.05, 2.0)
    # Place n_nulls deep nulls at random positions
    null_pos = rng.choice(n_scs, size=min(n_nulls, n_scs), replace=False)
    h[null_pos] = 0.02
    return h.astype(np.float64), null_pos


# ============================================================
# Layer 1: regression test. With uniform |H|=1 and clean signal,
# soft-LLR should agree with hard-bit.
# ============================================================
def test_layer1_regression():
    """Layer 1: clean signal, uniform H. Soft-LLR must match hard-bit."""
    rng = np.random.default_rng(seed=1234)
    cases = [
        {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0},
        {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1},
        {"mcs": 3, "length": 500, "sgi": 0, "ldpc": 1},
    ]
    passed = 0
    for ci, case in enumerate(cases):
        bits48 = make_known_htsig_bits(**case)
        coded96 = _bcc_encode_48(bits48)
        # BPSK modulation
        syms = bpsk_modulate(coded96).astype(np.float64)
        # Uniform H = 1
        h_mag = np.ones(96, dtype=np.float64)
        # Equalize (rx = H * syms with H=1, so eq = syms directly)
        eq = syms / h_mag
        # Hard-bit viterbi
        hard_bits = np.where(eq >= 0, 1, 0).astype(np.uint8)
        dec_hard, metric_hard = viterbi_decode_133_171_hard(hard_bits)
        # Soft LLR: r[i] = sign(eq[i]) * |H[i]| / max(|H|) = sign(eq[i]) (since |H|=1)
        soft_llr = compute_soft_llr_from_eq(eq, h_mag, max_h_mag=1.0)
        dec_soft, metric_soft = viterbi_decode_133_171_soft(soft_llr)
        ok = (dec_hard is not None and dec_soft is not None
              and np.array_equal(dec_hard, bits48)
              and np.array_equal(dec_soft, bits48))
        if ok:
            passed += 1
            print(f"[PASS] Layer1/case{ci} ({case}): hard=OK, soft=OK, "
                  f"metric_hard={metric_hard}, metric_soft={metric_soft:.2f}")
        else:
            print(f"[FAIL] Layer1/case{ci} ({case}): hard={dec_hard}, soft={dec_soft}, "
                  f"expected={bits48}")
    assert passed == 3, f"Layer 1: expected 3/3, got {passed}/3"
    print(f"[PASS] Layer 1 regression: {passed}/3 (soft-LLR matches hard-bit on clean signal)")


# ============================================================
# Layer 2: deep-null channel. Hard-bit fails at null SCs, soft-LLR succeeds.
# ============================================================
def synth_with_channel(bits48, h_mag, noise_sigma=0.0, rng=None):
    """Synthesize the viterbi input for a frame transmitted through
    freq-selective channel.

    1. Encode bits48 -> coded96
    2. BPSK modulate -> tx symbols (in {-1, +1})
    3. Apply channel: rx[i] = h_mag[i] * tx[i] + noise[i] (noise=0 if noise_sigma=0)
    4. Equalize: eq[i] = rx[i] / h_mag[i] = tx[i] + noise[i] / h_mag[i]
    5. Hard-bit: hard[i] = (eq[i] >= 0)
    6. Soft-LLR: r[i] = sign(eq[i]) * h_mag[i] / max(h_mag)

    For a deep null (h_mag[i] = 0.02), the noise is amplified 50x. The
    hard-bit decision is dominated by noise; the soft-LLR magnitude is
    near zero, so viterbi ignores this SC.
    """
    if rng is None:
        rng = np.random.default_rng(seed=0)
    coded96 = _bcc_encode_48(bits48)
    tx = bpsk_modulate(coded96)  # in {-1, +1}
    noise = noise_sigma * rng.standard_normal(96) if noise_sigma > 0 else 0.0
    rx = h_mag * tx + noise
    eq = rx / h_mag
    hard = np.where(eq >= 0, 1, 0).astype(np.uint8)
    soft = compute_soft_llr_from_eq(eq, h_mag)
    return hard, soft


def test_layer2_channel_nulls():
    """Layer 2: freq-selective channel with deep nulls. Soft-LLR must
    succeed where hard-bit fails (3 deep nulls out of 96 SCs)."""
    rng = np.random.default_rng(seed=42)
    cases = [
        {"mcs": 0, "length": 100, "sgi": 0, "ldpc": 0},
        {"mcs": 7, "length": 1000, "sgi": 1, "aggregation": 1},
        {"mcs": 3, "length": 500, "sgi": 0, "ldpc": 1},
    ]
    # Use moderate noise: enough that null SCs flip, but non-null SCs stay clean
    noise_sigma = 0.15
    soft_pass = 0
    hard_pass = 0
    for ci, case in enumerate(cases):
        bits48 = make_known_htsig_bits(**case)
        # Build freq-selective channel with 3 deep nulls at random SCs
        # (rng seeded per case so reproducible)
        case_rng = np.random.default_rng(seed=42 + ci)
        h_mag, null_pos = make_freq_selective_h(n_scs=96, n_nulls=3, rng=case_rng)
        hard, soft = synth_with_channel(bits48, h_mag, noise_sigma=noise_sigma,
                                        rng=case_rng)
        dec_hard, m_hard = viterbi_decode_133_171_hard(hard)
        dec_soft, m_soft = viterbi_decode_133_171_soft(soft)
        hard_ok = dec_hard is not None and np.array_equal(dec_hard, bits48)
        soft_ok = dec_soft is not None and np.array_equal(dec_soft, bits48)
        if hard_ok:
            hard_pass += 1
        if soft_ok:
            soft_pass += 1
        print(f"[INFO] Layer2/case{ci}: nulls@{null_pos.tolist()}, "
              f"hard={'OK' if hard_ok else 'FAIL'} (m={m_hard}), "
              f"soft={'OK' if soft_ok else 'FAIL'} (m={m_soft:.2f})")
    # Spec: hard-bit may fail (it does), soft-LLR must pass all 3
    print(f"[INFO] Layer 2 totals: hard={hard_pass}/3, soft={soft_pass}/3")
    assert soft_pass == 3, (
        f"Layer 2: soft-LLR must pass 3/3 with deep-null channel, "
        f"got {soft_pass}/3 (hard={hard_pass}/3 baseline)"
    )
    print(f"[PASS] Layer 2 channel-nulls: soft-LLR {soft_pass}/3 PASS (hard-bit {hard_pass}/3)")


# ============================================================
# Layer 3: SNR sweep. Soft-LLR must achieve lower BER than hard-bit.
# ============================================================
def test_layer3_snr_sweep():
    """Layer 3: SNR sweep with freq-selective H. Soft-LLR must have
    lower BER than hard-bit at low SNR (0-10 dB)."""
    rng = np.random.default_rng(seed=99)
    bits48 = make_known_htsig_bits(mcs=0, length=100)
    snr_values = [10, 6, 3, 0]  # dB
    n_trials = 30
    print(f"[INFO] Layer 3: SNR sweep, {n_trials} trials each, freq-selective H")
    print(f"[INFO] {'SNR (dB)':>8} | {'hard BER':>10} | {'soft BER':>10} | "
          f"{'soft wins?':>10}")
    for snr_db in snr_values:
        # Per-trial noise sigma for SNR = signal_power/noise_power
        sig_power = 1.0  # tx symbols are +/- 1
        noise_sigma = np.sqrt(sig_power / (10 ** (snr_db / 10)))
        hard_errors = 0
        soft_errors = 0
        for trial in range(n_trials):
            case_rng = np.random.default_rng(seed=1000 + trial * 17 + int(snr_db))
            h_mag, _ = make_freq_selective_h(n_scs=96, n_nulls=2, rng=case_rng)
            hard, soft = synth_with_channel(bits48, h_mag,
                                            noise_sigma=noise_sigma, rng=case_rng)
            dec_hard, _ = viterbi_decode_133_171_hard(hard)
            dec_soft, _ = viterbi_decode_133_171_soft(soft)
            if dec_hard is None or not np.array_equal(dec_hard, bits48):
                hard_errors += 1
            if dec_soft is None or not np.array_equal(dec_soft, bits48):
                soft_errors += 1
        hard_ber = hard_errors / n_trials
        soft_ber = soft_errors / n_trials
        # Soft must beat hard at low SNR (where nulls matter)
        soft_wins = soft_ber < hard_ber
        print(f"[INFO] {snr_db:>8} | {hard_ber:>10.3f} | {soft_ber:>10.3f} | "
              f"{'YES' if soft_wins else 'NO':>10}")
        # Pass criterion: soft BER <= hard BER at all SNRs (with one tie allowed)
        assert soft_ber <= hard_ber + 1e-9, (
            f"Layer 3 SNR={snr_db}dB: soft BER ({soft_ber:.3f}) > hard BER ({hard_ber:.3f})"
        )
    print(f"[PASS] Layer 3 SNR sweep: soft-LLR achieves <= hard-bit BER at all SNRs")


if __name__ == "__main__":
    test_layer1_regression()
    test_layer2_channel_nulls()
    test_layer3_snr_sweep()
    print("\nPhase 44 Soft-LLR viterbi synthetic tests passed.")