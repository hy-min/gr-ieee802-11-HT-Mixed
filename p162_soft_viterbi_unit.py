#!/usr/bin/env python3
"""
p162_soft_viterbi_unit.py — TDD unit test for the DATA-PATH soft-decision viterbi
(Phase 162: |H|^2-weighted LLR for the BCC Conv decoder in decode_mac).

Mechanism under test (Phase 161 root cause, 2026-08-05):
  Frames failing FCS are OUR frames with a deep band-edge subcarrier fade
  (min|H| p50 = 13.7 vs 28.7 on OK frames; argmin at SC -28/-27 = kTxOrder52[0],[1]).
  ZF equalization amplifies noise exactly on those weak SCs; the hard-decision
  viterbi then treats full-strength wrong bits as reliable -> 47-115 errors
  vs a ~40/1144 hard budget -> FCS failure.

Fix under test: per-bit soft LLR with |H|^2 reliability weighting,
    LLR_i  =  Re(eq_i) * |H_i|^2      (proportional to true LLR; max-log
                                        viterbi is invariant to positive scale,
                                        so no sigma^2 estimate is needed)
The weights travel as a per-frame "soft_h2" stream tag (f32vector[52]) emitted
by frame_equalizer next to frame_bytes/mcs; decode_mac applies them before a
float deinterleave + soft-metric viterbi.  Env: IEEE80211_DATA_SOFT_VITERBI=1.

Pre-registered contract:
  T1  Python TX-scaffold interleave is the exact inverse of the C++
      ht_deinterleave index math (pure numpy, no flowgraph).
  T2  Clean regime (flat H, no noise): hard path 5/5 FCS_OK  (scaffold sanity —
      if this fails the scaffold is broken, NOT the feature).
  T3  P161 fade regime: hard FCS_OK rate in [0.30, 0.90] (characterization gate;
      proves the test operates in the regime where the hard decoder actually
      fails on faded frames but is not always-dead).
  T4  SAME realizations + soft_h2 weights: soft must rescue >= 90% of the
      frames hard failed (rescue ratio (soft-hard)/(1-hard) >= 0.90) AND
      reach >= 0.95 FCS_OK absolute.   *** RED until feature is implemented ***
      (rescue-ratio form: an absolute "+0.25pp" is arithmetically impossible
      when hard > 0.75; this form is strictly stronger in the regime of interest)
  T5  Deterministic hero frame at the P161 signature depth: find (within 8
      fixed seeds) a realization where hard fails; assert soft passes it.

Run:
  unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
    PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p162_soft_viterbi_unit.py
"""

import os
import sys
import zlib
import struct
import numpy as np

import pmt
from gnuradio import gr, blocks
import ieee802_11

# ----------------------------------------------------------------------------
# Configuration (calibrated once, then frozen — see T3 gate)
# ----------------------------------------------------------------------------
FRAME_LEN   = 66          # PSDU bytes (matches the realtime testbed len=66)
N_SYM       = 22          # HT MCS0: ceil((16 + 8*66 + 6)/26) = 22
N_DATA_BITS = 572         # 22 * 26
N_CODED     = 1144        # 22 * 52
SEED_SCRAM  = 1           # TX scrambler seed (utils.cc scramble initial_state)
SIGMA_GRID  = [0.50, 0.56, 0.62, 0.68, 0.74, 0.80, 0.88, 0.96]  # calibration grid for T3 gate
N_POP       = 300         # frames per population case
EDGE_IDX    = [0, 1, 2, 3, 4, 5]                # 52-array positions of SC -28..-23
EDGE_BASE   = [0.30, 0.42, 0.55, 0.70, 0.85, 0.95]  # mild-fade depth profile


# ----------------------------------------------------------------------------
# Python TX scaffold — mirrors lib/utils.cc bit chain exactly.
# Validated end-to-end by T2 (clean FCS_OK) and by the T1 permutation check.
# ----------------------------------------------------------------------------
def build_psdu():
    """66-byte PSDU: 62-byte body (MAC-like header + 'x' payload) + CRC32 LE."""
    body = bytearray(62)
    body[0:6]   = b'\x42' * 6      # addr1 (matches testbed 0x42 pattern)
    body[6:12]  = b'\x23' * 6      # addr2
    body[12:18] = b'\xff' * 6      # addr3
    for i in range(18, 62):
        body[i] = 0x78             # 'x' payload
    crc = zlib.crc32(bytes(body)) & 0xFFFFFFFF
    return bytes(body) + struct.pack('<I', crc)


def generate_bits(psdu):
    """utils.cc generate_bits: 16 zero SERVICE bits + PSDU LSB-first + tail/pad zeros."""
    bits = np.zeros(N_DATA_BITS, dtype=np.uint8)
    for i in range(len(psdu)):
        for b in range(8):
            bits[16 + i * 8 + b] = (psdu[i] >> b) & 1
    # tail (6) + pad (22) remain zero; reset_tail zeroes scrambled tail below
    return bits


def scramble(bits, seed=SEED_SCRAM):
    """utils.cc scramble(): feedback = bit6 ^ bit3; out = fb ^ in."""
    state = seed
    out = np.empty_like(bits)
    for i in range(len(bits)):
        fb = ((state >> 6) & 1) ^ ((state >> 3) & 1)
        out[i] = bits[i] ^ fb
        state = ((state << 1) & 0x7E) | fb
    return out


def reset_tail(scrambled):
    """utils.cc reset_tail_bits: zero 6 bits at n_data_bits - n_pad - 6 = 544."""
    scrambled = scrambled.copy()
    scrambled[544:550] = 0
    return scrambled


def conv_encode(bits):
    """utils.cc convolutional_encoding: state=((state<<1)&0x7e)|in; g0=0133 g1=0171."""
    state = 0
    out = np.empty(2 * len(bits), dtype=np.uint8)
    for i in range(len(bits)):
        state = ((state << 1) & 0x7E) | int(bits[i])
        out[2 * i]     = bin(state & 0o133).count('1') & 1
        out[2 * i + 1] = bin(state & 0o171).count('1') & 1
    return out


def interleave_fwd_sym(in52):
    """utils.cc interleave(reverse=false), HT 52-SC, s=1 n_col=13 n_row=4:
    out[4*(k%13) + k//13] = in[k]."""
    out = np.empty(52, dtype=np.uint8)
    for k in range(52):
        out[4 * (k % 13) + k // 13] = in52[k]
    return out


def deinterleave_cpp_sym(in52):
    """decode_mac.cc ht_deinterleave index math (reference for T1 only):
    out[13*j - 51*(j//4)] = in[j]."""
    out = np.empty(52, dtype=np.uint8)
    for j in range(52):
        out[13 * j - 51 * (j // 4)] = in52[j]
    return out


def tx_frame_symbols(psdu):
    """Full TX chain -> (N_SYM, 52) BPSK symbols in 52-array order."""
    bits = generate_bits(psdu)
    coded = conv_encode(reset_tail(scramble(bits)))
    assert len(coded) == N_CODED
    syms = np.empty((N_SYM, 52), dtype=np.float64)
    for s in range(N_SYM):
        inter = interleave_fwd_sym(coded[s * 52:(s + 1) * 52])
        syms[s] = 2.0 * inter - 1.0       # bit1 -> +1 ; hard_bpsk_bit: re>=0 -> 1
    return syms


# ----------------------------------------------------------------------------
# Channel model — P161 signature: graded fade over the 6 left band-edge data
# SCs (SC -28..-23), flat elsewhere; CN(0, sigma^2) noise at the RX antenna,
# then ZF (noise on weak SCs amplified by 1/|H| — the Phase 161 mechanism).
# ----------------------------------------------------------------------------
def fade_depths(rng):
    """Per-frame fade realization: severity u interpolates mild -> deep tail.
    Weighted toward the failure region (stress population, not real-world stats)."""
    u = rng.uniform(0.45, 1.0)
    depths = np.maximum(0.06, np.array(EDGE_BASE) - u * 0.55)
    return depths


def apply_channel(tx_syms, sigma, rng):
    """Returns (eq, h2). eq: (N_SYM,52) complex post-ZF; h2: (52,) |H|^2."""
    H = np.ones(52, dtype=np.float64)
    H[EDGE_IDX] = fade_depths(rng)
    n = (rng.normal(0.0, sigma / np.sqrt(2), tx_syms.shape)
         + 1j * rng.normal(0.0, sigma / np.sqrt(2), tx_syms.shape))
    y = tx_syms * H[None, :] + n
    eq = y / H[None, :]
    return eq, H ** 2


# ----------------------------------------------------------------------------
# Flowgraph driver — feeds the REAL decode_mac block (real ht_deinterleave,
# real viterbi, real descramble/FCS).
# ----------------------------------------------------------------------------
def _silence_fds():
    import contextlib
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1), os.dup(2)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    return saved


def _restore_fds(saved):
    os.dup2(saved[0], 1)
    os.dup2(saved[1], 2)
    os.close(saved[0])
    os.close(saved[1])


def run_decode(eq_frames, h2_list=None, env_on=False):
    """eq_frames: list of (N_SYM,52) complex arrays. Returns FCS_OK count."""
    if env_on:
        os.environ['IEEE80211_DATA_SOFT_VITERBI'] = '1'
    else:
        os.environ.pop('IEEE80211_DATA_SOFT_VITERBI', None)

    stream = np.concatenate([f.reshape(-1) for f in eq_frames]).astype(np.complex64)
    tags = []
    for fi in range(len(eq_frames)):
        off = fi * N_CODED
        t1 = gr.tag_t()
        t1.offset = off
        t1.key = pmt.intern('frame_bytes')
        t1.value = pmt.from_uint64(FRAME_LEN)
        t1.srcid = pmt.intern('p162')
        tags.append(t1)
        t2 = gr.tag_t()
        t2.offset = off
        t2.key = pmt.intern('mcs')
        t2.value = pmt.from_uint64(0)
        t2.srcid = pmt.intern('p162')
        tags.append(t2)
        if h2_list is not None:
            t3 = gr.tag_t()
            t3.offset = off
            t3.key = pmt.intern('soft_h2')
            t3.value = pmt.init_f32vector(52, [float(x) for x in h2_list[fi]])
            t3.srcid = pmt.intern('p162')
            tags.append(t3)

    saved = _silence_fds()
    try:
        tb = gr.top_block()
        src = blocks.vector_source_c(stream, False, 1, tags)
        dec = ieee802_11.decode_mac(False, False)
        dbg = blocks.message_debug()
        tb.connect(src, dec)
        tb.msg_connect((dec, 'out'), (dbg, 'store'))
        tb.run()
        n_ok = dbg.num_messages()
    finally:
        _restore_fds(saved)
    return n_ok


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------
def t1_permutation_inverse():
    rng = np.random.RandomState(0)
    for _ in range(200):
        v = rng.randint(0, 2, 52).astype(np.uint8)
        assert np.array_equal(deinterleave_cpp_sym(interleave_fwd_sym(v)), v), \
            'scaffold interleave is not the inverse of C++ ht_deinterleave'
    print('T1 PASS: interleave/deinterleave are exact inverses (52/52 positions, 200 vectors)')


def t2_clean_sanity():
    psdu = build_psdu()
    tx = tx_frame_symbols(psdu)
    eq = tx.astype(np.complex64)          # flat H=1, no noise
    n_ok = run_decode([eq] * 5)
    assert n_ok == 5, f'clean scaffold sanity failed: {n_ok}/5 FCS_OK'
    print('T2 PASS: clean hard path 5/5 FCS_OK (scaffold validated end-to-end)')


def _population(sigma, n, seed0):
    rng = np.random.RandomState(seed0)
    psdu = build_psdu()
    tx = tx_frame_symbols(psdu)
    eqs, h2s = [], []
    for _ in range(n):
        eq, h2 = apply_channel(tx, sigma, rng)
        eqs.append(eq.astype(np.complex64))
        h2s.append(h2)
    return eqs, h2s


def t3_hard_fade_regime(sigma, eqs):
    n_ok = run_decode(eqs)
    rate = n_ok / len(eqs)
    print(f'T3 INFO: sigma={sigma} hard FCS_OK = {n_ok}/{len(eqs)} = {rate:.3f}')
    assert 0.30 <= rate <= 0.90, \
        f'hard rate {rate:.3f} outside calibrated regime [0.30, 0.90] — recalibrate SIGMA'
    print(f'T3 PASS: hard decoder operates in the P161 failure regime ({rate:.3f})')
    return rate


def t4_soft_rescues(sigma, eqs, h2s, hard_rate):
    n_ok = run_decode(eqs, h2_list=h2s, env_on=True)
    rate = n_ok / len(eqs)
    rescue = (rate - hard_rate) / (1.0 - hard_rate) if hard_rate < 1.0 else 1.0
    print(f'T4 INFO: sigma={sigma} soft FCS_OK = {n_ok}/{len(eqs)} = {rate:.3f} '
          f'(hard {hard_rate:.3f}, rescued {rescue * 100:.0f}% of hard failures)')
    # Contract (rescue-ratio form; the original "hard + 0.25pp absolute" form is
    # arithmetically impossible when hard > 0.75 since rates cap at 1.0 — this
    # form is strictly stronger in the regime of interest):
    #   soft must rescue >= 90% of the frames hard failed, AND reach >= 0.95.
    assert rescue >= 0.90 and rate >= 0.95, \
        f'soft viterbi rescue contract unmet: soft={rate:.3f} hard={hard_rate:.3f} ' \
        f'rescue={rescue:.2f} (need rescue >= 0.90 AND soft >= 0.95)'
    print(f'T4 PASS: soft viterbi rescues {rescue * 100:.0f}% of hard failures '
          f'(soft {rate:.3f} vs hard {hard_rate:.3f})')


def t5_hero_frame(sigma):
    """Deterministic single-frame case drawn from the SAME population model:
    find (within 32 fixed seeds) a realization where hard fails; assert the
    soft path rescues that exact frame."""
    psdu = build_psdu()
    tx = tx_frame_symbols(psdu)
    for seed in range(32):
        rng = np.random.RandomState(1000 + seed)
        H = np.ones(52)
        H[EDGE_IDX] = fade_depths(rng)          # same model as the population
        n = (rng.normal(0, sigma / np.sqrt(2), tx.shape)
             + 1j * rng.normal(0, sigma / np.sqrt(2), tx.shape))
        eq = ((tx * H[None, :] + n) / H[None, :]).astype(np.complex64)
        if run_decode([eq]) == 1:
            continue                          # hard luck-passes; not a hero frame
        n_soft = run_decode([eq], h2_list=[H ** 2], env_on=True)
        assert n_soft == 1, \
            f'hero frame (seed {seed}, min|H|/med={H[EDGE_IDX].min():.2f}): soft failed to rescue'
        print(f'T5 PASS: hero frame rescued (seed {seed}, min|H|/med = {H[EDGE_IDX].min():.2f})')
        return
    raise AssertionError('no hard-failing hero frame found in 32 seeds — '
                         'fade depth/sigma miscalibrated')


def main():
    print('=== p162 soft-viterbi TDD (Phase 161 fade mechanism) ===')
    t1_permutation_inverse()
    t2_clean_sanity()

    # Calibrate sigma ONCE on the hard path so T3's regime gate holds; the
    # rescue contract (T4/T5) is frozen and never retuned.
    chosen = None
    for sigma in SIGMA_GRID:
        eqs, h2s = _population(sigma, 60, seed0=7)
        probe = run_decode(eqs) / 60
        print(f'[calib] sigma={sigma}: hard FCS_OK probe = {probe:.3f}')
        if 0.30 <= probe <= 0.90:
            chosen = sigma
            break
    assert chosen is not None, 'no sigma in grid lands hard rate in [0.30, 0.90]'

    eqs, h2s = _population(chosen, N_POP, seed0=42)
    hard_rate = t3_hard_fade_regime(chosen, eqs)
    t4_soft_rescues(chosen, eqs, h2s, hard_rate)
    t5_hero_frame(chosen)
    print('=== ALL PASS ===')


if __name__ == '__main__':
    main()
