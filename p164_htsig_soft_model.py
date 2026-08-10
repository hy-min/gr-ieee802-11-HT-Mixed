#!/usr/bin/env python3
"""p164_htsig_soft_model.py — algorithm-level TDD for HT-SIG soft-decision viterbi
(Phase 164: extend the P162-proven |H|^2-weighted LLR to the HT-SIG header decode).

Phase 163f forensics: the residual includes full-strength frames that commit a
valid frame-start but fail HT-SIG parse (avg_snr_htsig ~1.9 dB, best_metric=N/A).
HT-SIG currently decodes with HARD viterbi by default (P44/P129 soft paths are
opt-in and were REFUTED in the delta-on era). On the delta-OFF baseline, the
P162 data-path soft viterbi proved |H|^2-weighted LLR rescues band-edge fades.

This is a PURE PYTHON MODEL test (mirrors the C++ algorithm; the C++ wiring is
validated separately by loopback + replay + ABAB). HT-SIG = 48 info bits ->
rate-1/2 conv (133/171) -> 96 coded bits -> 2 symbols x 48 data SCs, QBPSK
(bits on IMAG axis). CRC8 over bits 0..33 is the pass/fail arbiter.

Pre-registered contract:
  T1 clean: hard 100% pass (scaffold sanity)
  T2 band-edge fade regime: hard pass-rate in [0.30, 0.90] (calibrated)
  T3 SAME realizations + soft-h2: rescue >= 90% of hard failures AND soft >= 0.95
"""
import numpy as np

G0, G1 = 0o133, 0o171


def crc8(bits):
    # 802.11 HT-SIG CRC8: poly 0x07, init 0xFF... use the ATM/8-bit variant the
    # project uses. For the model we only need self-consistency (encode+check
    # use the same function), so implement the standard 0x07/0x00 CRC.
    crc = 0
    for b in bits:
        crc ^= (b << 0)  # placeholder replaced below
    return crc  # unused; see crc8_htsig below


def crc8_htsig(bits0_33):
    """802.11n HT-SIG CRC-8: polynomial x^8+x^2+x+1 (0x07), init 0x00,
    computed over the 34 input bits MSB-first per the standard's bit order.
    For the model, self-consistency (encode == check) is what matters."""
    crc = 0
    for i in range(34):
        bit = int(bits0_33[i]) & 1
        feedback = ((crc >> 7) & 1) ^ bit
        crc = ((crc << 1) & 0xFF)
        if feedback:
            crc ^= 0x07
    # complement per 802.11 convention (HT-SIG uses ~crc)
    return crc ^ 0xFF


def build_htsig48(mcs=0, length=100):
    bits = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        bits[i] = (mcs >> i) & 1
    for i in range(16):
        bits[8 + i] = (length >> i) & 1
    bits[30] = 0  # BCC
    c = crc8_htsig(bits[0:34])
    for i in range(8):
        bits[34 + i] = (c >> i) & 1
    return bits


def conv_encode(bits):
    state = 0
    out = np.empty(2 * len(bits), dtype=np.uint8)
    for i in range(len(bits)):
        state = ((state << 1) & 0x7E) | int(bits[i])
        out[2 * i]     = bin(state & G0).count('1') & 1
        out[2 * i + 1] = bin(state & G1).count('1') & 1
    return out


def interleave48(in48):
    out = np.empty(48, dtype=np.uint8)
    for k in range(48):
        out[3 * (k % 16) + k // 16] = in48[k]
    return out


def deinterleave48(in48):
    out = np.empty(48)
    for k in range(48):
        out[k] = in48[3 * (k % 16) + k // 16]
    return out


def viterbi_decode(rx, soft):
    """rx: 96 values (hard 0/1 or soft float LLR>0<=bit1). Returns 48 decoded bits.
    Zero-state terminated; max-log correlation metric for both (hard uses +/-1)."""
    n_data = 48
    n_states = 64
    NEG = -1e18
    prev = np.full(n_states, NEG)
    prev[0] = 0.0
    pred_state = np.zeros((n_data, n_states), dtype=np.int8)
    pred_input = np.zeros((n_data, n_states), dtype=np.int8)
    for t in range(n_data):
        l0 = rx[2 * t]
        l1 = rx[2 * t + 1]
        if not soft:
            l0 = 1.0 if l0 else -1.0
            l1 = 1.0 if l1 else -1.0
        nxt = np.full(n_states, NEG)
        for ps in range(n_states):
            if prev[ps] == NEG:
                continue
            for ib in (0, 1):
                reg = ((ps << 1) | ib) & 0x7F
                ns = reg & 0x3F
                e0 = bin(reg & G0).count('1') & 1
                e1 = bin(reg & G1).count('1') & 1
                bm = (l0 if e0 else -l0) + (l1 if e1 else -l1)
                cand = prev[ps] + bm
                if cand > nxt[ns]:
                    nxt[ns] = cand
                    pred_state[t, ns] = ps
                    pred_input[t, ns] = ib
        prev = nxt
    state = 0
    if prev[state] == NEG:
        state = int(np.argmax(prev))
    dec = np.zeros(n_data, dtype=np.uint8)
    for t in range(n_data - 1, -1, -1):
        dec[t] = pred_input[t, state]
        state = pred_state[t, state]
    return dec


def tx_htsig_symbols(bits48):
    """48 bits -> conv -> 96 coded -> 2x48 interleaved -> QBPSK on imag axis."""
    coded = conv_encode(bits48)
    a = interleave48(coded[0:48])
    b = interleave48(coded[48:96])
    # QBPSK: bit on IMAG axis. bit1 -> +j, bit0 -> -j
    sym_a = 1j * (2.0 * a - 1.0)
    sym_b = 1j * (2.0 * b - 1.0)
    return sym_a, sym_b


def apply_channel(sym, sigma, edge_depths, rng):
    H = np.ones(48)
    H[:len(edge_depths)] = edge_depths
    n = (rng.normal(0, sigma / np.sqrt(2), 48) + 1j * rng.normal(0, sigma / np.sqrt(2), 48))
    y = sym * H + n
    eq = y / H
    return eq, H ** 2


def decode_frame(sym_a, sym_b, sigma, edge_depths, rng, soft):
    eq_a, h2_a = apply_channel(sym_a, sigma, edge_depths, rng)
    eq_b, h2_b = apply_channel(sym_b, sigma, edge_depths, rng)
    if soft:
        la = eq_a.imag * h2_a
        lb = eq_b.imag * h2_b
        la = deinterleave48(la)
        lb = deinterleave48(lb)
        rx = np.concatenate([la, lb])
    else:
        ba = (eq_a.imag >= 0).astype(np.uint8)
        bb = (eq_b.imag >= 0).astype(np.uint8)
        ba = deinterleave48(ba).astype(np.uint8)
        bb = deinterleave48(bb).astype(np.uint8)
        rx = np.concatenate([ba, bb])
    dec = viterbi_decode(rx, soft)
    return crc8_htsig(dec[0:34]) == pack_crc(dec)


def pack_crc(dec):
    c = 0
    for i in range(8):
        c |= (int(dec[34 + i]) << i)
    return c


def run(sigma, n, seed0, soft):
    rng = np.random.RandomState(seed0)
    ok = 0
    for _ in range(n):
        bits = build_htsig48()
        sym_a, sym_b = tx_htsig_symbols(bits)
        u = rng.uniform(0.45, 1.0)
        depths = np.maximum(0.06, np.array([0.30, 0.42, 0.55, 0.70, 0.85, 0.95]) - u * 0.55)
        if decode_frame(sym_a, sym_b, sigma, depths, rng, soft):
            ok += 1
    return ok / n


def main():
    print('=== p164 HT-SIG soft-viterbi model TDD ===')
    # T1 clean
    rng = np.random.RandomState(1)
    clean_ok = 0
    for _ in range(50):
        bits = build_htsig48()
        sa, sb = tx_htsig_symbols(bits)
        if decode_frame(sa, sb, 1e-9, np.ones(6), rng, soft=False):
            clean_ok += 1
    assert clean_ok == 50, f'T1 clean scaffold failed: {clean_ok}/50'
    print('T1 PASS: clean hard 50/50 (scaffold validated)')

    # calibrate sigma so hard lands in [0.30, 0.90]
    chosen = None
    for sigma in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2]:
        r = run(sigma, 100, 7, soft=False)
        print(f'[calib] sigma={sigma}: hard pass = {r:.3f}')
        if 0.30 <= r <= 0.90:
            chosen = sigma
            break
    assert chosen is not None, 'no sigma lands hard in regime'

    hard = run(chosen, 400, 42, soft=False)
    assert 0.30 <= hard <= 0.90, f'T2 regime off: {hard}'
    print(f'T2 PASS: hard regime {hard:.3f} at sigma={chosen}')

    soft = run(chosen, 400, 42, soft=True)
    rescue = (soft - hard) / (1 - hard) if hard < 1 else 1.0
    print(f'T3 INFO: sigma={chosen} hard={hard:.3f} soft={soft:.3f} rescue={rescue*100:.0f}%')
    assert rescue >= 0.90 and soft >= 0.95, \
        f'T3 FAIL: soft rescue contract unmet (soft={soft:.3f} hard={hard:.3f} rescue={rescue:.2f})'
    print(f'T3 PASS: soft-h2 rescues {rescue*100:.0f}% of hard failures')
    print('=== ALL PASS ===')


if __name__ == '__main__':
    main()
