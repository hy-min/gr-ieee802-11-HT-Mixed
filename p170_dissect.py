#!/usr/bin/env python3
"""Phase 170: offline dissection of real chain-loss / terminal-fail frames.

Given a raw IQ capture + DECODE_SEQ fate log from the SAME run:
1. Detect every L-STF burst (ground-truth lattice).
2. Parse DECODE_SEQ from the fate log -> missing seqs (chain loss) and
   DECODE_FAIL positions (terminal fail).
3. For each lost frame: extract its IQ segment and run the FULL offline
   decode battery — window offset sweep x H-source x rotation x hard/soft —
   checking whether ANY variant recovers a valid L-SIG (rate=0xD, len=72,
   parity ok).

If nothing recovers a frame: physical limit (evidence-based).
If something does: there IS an untapped software lever.
"""
import numpy as np
import re
import sys

F = '/home/hy/captures/p170_fate_iq.fc32'
LOG = '/tmp/p170_fate.err'
FS = 20e6

# ---------------- L-SIG constants ----------------
DATA_SC = np.array([-26,-25,-24,-23,-22,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,-6,-5,-4,-3,-2,-1,
                     1,2,3,4,5,6,8,9,10,11,12,13,14,15,16,17,18,19,20,22,23,24,25,26])
LTF_VAL = np.array([1,1,-1,-1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,1,-1,1,1,1,1,
                    1,-1,-1,1,1,-1,-1,1,-1,-1,-1,-1,-1,1,1,-1,-1,1,-1,-1,1,1,1,1], dtype=complex)
PILOTS = {sc2b: v for sc2b, v in [(11,1),(25,-1),(39,1),(53,1)]}  # bins of SC -21,-7,7,21
def sc2bin(k): return k if k >= 0 else 64 + k

# ---------------- viterbi (133,171) rate-1/2 K=7, zero-tail ----------------
G0, G1 = 0o133, 0o171
def parity8(x): return bin(x).count('1') & 1
NEXT = []   # (next_state, out0, out1) for input 0/1
for s in range(64):
    row = []
    for b in (0, 1):
        reg = ((s << 1) | b) & 0x7F
        row.append((reg & 0x3F, parity8(reg & G0), parity8(reg & G1)))
    NEXT.append(row)

def viterbi_hard(coded):
    """coded: 48 hard bits (0/1). Returns 24 decoded bits."""
    n = 24
    INF = float('inf')
    M = [[INF]*64 for _ in range(n+1)]
    P = [[0]*64 for _ in range(n+1)]   # predecessor state
    PI = [[0]*64 for _ in range(n+1)]  # input bit
    M[0][0] = 0
    for t in range(n):
        r0, r1 = coded[2*t], coded[2*t+1]
        for s in range(64):
            if M[t][s] == INF: continue
            for b in (0, 1):
                ns, e0, e1 = NEXT[s][b]
                cost = (r0 != e0) + (r1 != e1)
                if M[t][s] + cost < M[t+1][ns]:
                    M[t+1][ns] = M[t][s] + cost
                    P[t+1][ns] = s
                    PI[t+1][ns] = b
    st = 0  # zero-terminated
    out = [0]*n
    for t in range(n, 0, -1):
        out[t-1] = PI[t][st]
        st = P[t][st]
    return out, M[n][0]

def viterbi_soft_metric(coded_llr):
    """coded_llr: 48 floats, sign=bit, magnitude=reliability.
    Correlation metric (P162-style, scale-invariant). Returns bits + metric."""
    n = 24
    NEG = -1e30
    M = [[NEG]*64 for _ in range(n+1)]
    P = [[0]*64 for _ in range(n+1)]
    PI = [[0]*64 for _ in range(n+1)]
    M[0][0] = 0.0
    for t in range(n):
        r0, r1 = coded_llr[2*t], coded_llr[2*t+1]
        for s in range(64):
            if M[t][s] == NEG: continue
            for b in (0, 1):
                ns, e0, e1 = NEXT[s][b]
                # correlation: reward agreement
                gain = (r0 if e0 else -r0) + (r1 if e1 else -r1)
                if M[t][s] + gain > M[t+1][ns]:
                    M[t+1][ns] = M[t][s] + gain
                    P[t+1][ns] = s
                    PI[t+1][ns] = b
    st = 0
    out = [0]*n
    for t in range(n, 0, -1):
        out[t-1] = PI[t][st]
        st = P[t][st]
    return out, M[n][0]

# ---------------- 802.11 BPSK deinterleaver (N_CBPS=48) ----------------
def deinterleave48(bits):
    N = 48
    out = [0]*N
    for k in range(N):
        i = (N//16)*(k % 16) + k//16          # first permutation
        # second permutation s=1 for BPSK -> identity
        out[i] = bits[k]                       # deinterleave: inverse mapping
    # fix: deinterleave is inverse of interleave
    inv = [0]*N
    for k in range(N):
        i = (N//16)*(k % 16) + k//16
        inv[k] = bits[i]
    return inv

def deinterleave48_soft(vals):
    N = 48
    inv = [0.0]*N
    for k in range(N):
        i = (N//16)*(k % 16) + k//16
        inv[k] = vals[i]
    return inv

# ---------------- L-SIG field check ----------------
def parse_lsig(bits24):
    rate = bits24[0]*8 + bits24[1]*4 + bits24[2]*2 + bits24[3]
    length = sum(bits24[5+j] << j for j in range(12))
    par = bits24[17]
    tail = bits24[18:24]
    even_parity = (sum(bits24[0:17]) & 1) == par
    return rate, length, even_parity, all(t == 0 for t in tail)

# ---------------- frame battery ----------------
def try_decode(iq, lstf_pos, off, hsrc, rot, soft):
    """One battery cell: window offset + H source + rotation + hard/soft.
    Returns (rate, length, parity_ok, tail_ok, metric) or None."""
    s = lstf_pos + 160 + 32 + off   # L-STF end + GI2 ... careful: lstf_pos from detector
    # lstf_pos = START of L-STF plateau; preamble layout from lstf_pos:
    #   L-STF: 160 samples, GI2: 32, L-LTF T1: 64, T2: 64, L-SIG GI: 16, L-SIG: 64
    t1 = lstf_pos + 160 + 32 + off
    if t1 < 0 or t1 + 64*5 > len(iq):
        return None
    f1 = np.fft.fft(iq[t1:t1+64], 64)
    f2 = np.fft.fft(iq[t1+64:t1+128], 64)
    lsig = np.fft.fft(iq[t1+128+16:t1+128+16+64], 64)

    # H per source
    H = np.zeros(64, dtype=complex)
    for j in range(48):
        b = sc2bin(DATA_SC[j])
        h0 = f1[b] / LTF_VAL[j]
        h1 = f2[b] / LTF_VAL[j]
        if hsrc == 0: H[b] = h0
        elif hsrc == 1: H[b] = h1
        else: H[b] = (h0 + h1) / 2

    eq = np.zeros(48, dtype=complex)
    for j in range(48):
        b = sc2bin(DATA_SC[j])
        if abs(H[b]) > 1e-3:
            eq[j] = lsig[b] / H[b]
    # rotation candidate
    eq = eq * np.exp(1j * rot * np.pi / 2)

    if soft:
        llr = np.array([eq[j].real * abs(H[sc2bin(DATA_SC[j])])**2 for j in range(48)])
        # deinterleave soft
        coded = deinterleave_soft48(llr)
        bits, metric = viterbi_soft_metric(coded)
    else:
        hard = [1 if eq[j].real > 0 else 0 for j in range(48)]
        coded = deinterleave48(hard)
        bits, metric = viterbi_hard(coded)

    rate, length, par_ok, tail_ok = parse_lsig(bits)
    return rate, length, par_ok, tail_ok, metric

def deinterleave_soft48(vals):
    return deinterleave48_soft(vals)

def dissect(iq, pos, label):
    print(f"\n===== {label} at sample {pos} =====")
    recovered = []
    base_results = []
    for off in range(-32, 33, 4):
        for hsrc in (0, 1, 2):
            for rot in (0, 1, 2, 3):
                for soft in (False, True):
                    r = try_decode(iq, pos, off, hsrc, rot, soft)
                    if r is None: continue
                    rate, length, par_ok, tail_ok, metric = r
                    good = (rate == 0xD and length == 72 and par_ok and tail_ok)
                    if off == 0 and hsrc == 2 and rot == 0 and not soft:
                        base_results.append((rate, length, par_ok, tail_ok))
                    if good:
                        recovered.append((off, hsrc, rot, soft, metric))
    print(f"  baseline (off=0,2way,rot0,hard): {base_results}")
    if recovered:
        print(f"  *** RECOVERED by {len(recovered)} variants:")
        for r in recovered[:8]:
            print(f"      off={r[0]:+3d} hsrc={r[1]} rot={r[2]} soft={r[3]} metric={r[4]:.1f}")
    else:
        print(f"  NOT recoverable by any of ~832 variants")

# ---------------- main ----------------
def main():
    # 1. parse fate log
    seqs = []
    with open(LOG) as f:
        for line in f:
            m = re.search(r'DECODE_SEQ.*seq=(\d+)', line)
            if m:
                seqs.append(int(m.group(1)))
    missing = []
    for i in range(len(seqs)-1):
        for k in range(seqs[i]+1, seqs[i+1]):
            missing.append(k)
    print(f"[fate] decoded seqs: {len(seqs)}, range {seqs[0]}-{seqs[-1]}, missing: {missing}")

    # 2. detect bursts (chunked)
    CHUNK = 50_000_000
    pos_list = []
    offset = 0
    tail = np.zeros(0, dtype=np.complex64)
    thr = None
    while True:
        raw = np.fromfile(F, dtype=np.complex64, count=CHUNK, offset=offset*8)
        if len(raw) == 0: break
        x = np.concatenate([tail, raw])
        mult = np.abs(x[16:] * np.conj(x[:-16]))
        box = np.convolve(mult, np.ones(16), mode='valid')
        if thr is None:
            thr = max(np.percentile(box, 90) * 200, 1e-3)
        idx = np.where(box > thr)[0]
        if len(idx):
            grp = np.split(idx, np.where(np.diff(idx) > 2000)[0]+1)
            for g in grp:
                if len(g) < 10: continue
                pos_list.append(offset - 16 + int(g[0]))
        tail = x[-32:]
        offset += len(raw)
    pos_arr = np.array(pos_list)
    keep = np.concatenate([[True], np.diff(pos_arr) > 2000])
    pos_arr = pos_arr[keep]
    print(f"[gt] bursts: {len(pos_arr)} (span {pos_arr[-1]/FS:.1f}s)")

    # 3. map: burst index == seq (seq starts at 0, every sent frame bursts)
    # anchor check
    if len(pos_arr) >= seqs[-1] + 1:
        print(f"[map] bursts cover seq range: OK")
    else:
        print(f"[map] WARN: bursts {len(pos_arr)} < max seq {seqs[-1]+1}")

    # 4. dissect each missing frame
    for k in missing:
        if k < len(pos_arr):
            p = pos_arr[k]
            seg = np.fromfile(F, dtype=np.complex64, count=6000,
                              offset=max(0, (p-1000))*8)
            dissect(seg, min(1000, p), f"LOST seq={k}")

if __name__ == '__main__':
    main()
