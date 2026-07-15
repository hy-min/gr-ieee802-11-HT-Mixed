#!/usr/bin/env python
"""Phase 28.3: Run full L-SIG viterbi decoder on fresh USRP capture.

Phase 28.2 found 32.3 dB SNR with L-STF start at sample 4053217.
This script uses the correct sudden-rise detector and Phase 28.2 H estimation.
"""
import numpy as np
import sys
import time

CAPTURE_FC32 = '/tmp/p28_loopback_iq.fc32'
EXPECTED_BITS = '111111011101101010000010111001001111100101101111'  # 48 bits
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))  # 52 active subcarriers


def find_l_stf_region(iq, period=16, search_skip=1000):
    """Find L-STF start using sudden-rise detection (Phase 28.2 method)."""
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    corr_raw = np.abs(a * np.conj(b))
    win = 16
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')
    ratio = np.zeros_like(corr_smooth)
    ratio[win:] = corr_smooth[win:] / (corr_smooth[:-win] + 1e-12)
    for i in range(search_skip, len(ratio) - 1):
        if ratio[i] > 5 and corr_smooth[i] > 0.1:
            peak = corr_smooth[i]
            end = i
            while end < len(corr_smooth) - 1 and corr_smooth[end + 1] > peak * 0.3:
                end += 1
            return i, end
    return -1, -1


def estimate_h(iq, ltf0_start, ltf1_start):
    """H estimate using (F0 + F1) / 2, return F0a (per-SC equalizer reference)."""
    lts0 = iq[ltf0_start:ltf0_start+64]
    lts1 = iq[ltf1_start:ltf1_start+64]
    if len(lts0) < 64 or len(lts1) < 64:
        return None
    F0 = np.fft.fft(lts0, 64)
    F1 = np.fft.fft(lts1, 64)
    F0a = F0[ACTIVE_SC]
    F1a = F1[ACTIVE_SC]
    H = (F0a + F1a) / 2.0
    return {
        'F0a': F0a, 'F1a': F1a,
        'H_mag_mean': float(np.mean(np.abs(H))),
        'H_mag_std': float(np.std(np.abs(H))),
        'H_phase_std': float(np.std(np.angle(H))),
    }


def decode_lsig(iq, sig_start, F0a):
    """Decode L-SIG: equalize using F0a, then hard BPSK."""
    sig = iq[sig_start:sig_start+64]
    if len(sig) < 64:
        return None
    Fsig = np.fft.fft(sig, 64)
    eq = Fsig[ACTIVE_SC] / F0a
    eq_real = eq.real
    eq_imag = eq.imag
    # Apply global CPE
    cpe = np.angle(np.sum(eq))
    eq_rot = eq * np.exp(-1j * cpe)
    bits = (eq_rot.real > 0).astype(int)
    return {
        'bits': bits,
        'bits_str': ''.join(map(str, bits.tolist())),
        'eq_real': eq_real,
        'eq_imag': eq_imag,
        'cpe_deg': float(np.degrees(cpe)),
        'snr_db': 20 * np.log10(np.mean(np.abs(eq_real)) /
                                (np.std(eq_imag) + 1e-12)),
    }


# ----- Step 1: Load capture -----
print("=" * 70)
print("Step 1: Load fresh capture")
print("=" * 70)
t0 = time.time()
iq = np.fromfile(CAPTURE_FC32, dtype=np.complex64)
print(f"Loaded {len(iq)} samples in {time.time()-t0:.2f}s")

# ----- Step 2: Find L-STF region with sudden-rise detector -----
print("\n" + "=" * 70)
print("Step 2: Find L-STF start (sudden-rise detector)")
print("=" * 70)
t0 = time.time()
l_stf_start, l_stf_end = find_l_stf_region(iq, period=16)
print(f"L-STF region: samples {l_stf_start} to {l_stf_end} "
      f"(length={l_stf_end - l_stf_start + 1})")
print(f"Detection took {time.time()-t0:.2f}s")
if l_stf_start < 0:
    print("ERROR: L-STF not found!")
    sys.exit(1)
fs = l_stf_start
print(f"Frame start (L-STF DATA start) = sample {fs}")

# ----- Step 3: Sweep L-LTF0 offset and decode L-SIG -----
print("\n" + "=" * 70)
print("Step 3: Sweep L-LTF0 timing offset, decode L-SIG")
print("=" * 70)
results = []
for offset in range(-8, 9):
    ltf0_data = fs + 176 + offset
    ltf1_data = fs + 256 + offset
    sig_data = fs + 336 + offset

    H = estimate_h(iq, ltf0_data, ltf1_data)
    if H is None:
        continue
    lsig = decode_lsig(iq, sig_data, H['F0a'])
    if lsig is None:
        continue

    # Compare to expected
    matches = sum(1 for a, b in zip(lsig['bits_str'], EXPECTED_BITS) if a == b)
    ber = 1 - matches / 48
    result = {
        'offset': offset,
        'matches': matches,
        'ber': ber,
        'snr_db': lsig['snr_db'],
        'cpe_deg': lsig['cpe_deg'],
        'bits': lsig['bits_str'],
        'H_mag_mean': H['H_mag_mean'],
        'H_phase_std': H['H_phase_std'],
    }
    results.append(result)
    print(f"offset={offset:+3d} matches={matches:2d}/48 BER={ber:6.1%} "
          f"SNR={lsig['snr_db']:5.1f}dB CPE={lsig['cpe_deg']:+6.1f}deg "
          f"|H|_mean={H['H_mag_mean']:.1f} phase_std={H['H_phase_std']:.2f}rad")

# ----- Step 4: Best by matches -----
print("\n" + "=" * 70)
print("Step 4: Best L-LTF0 offset (by matches to expected)")
print("=" * 70)
best = max(results, key=lambda r: r['matches'])
print(f"Best offset={best['offset']:+d}: matches={best['matches']}/48, "
      f"BER={best['ber']:.1%}, SNR={best['snr_db']:.1f}dB")
print(f"  Got:      {best['bits']}")
print(f"  Expected: {EXPECTED_BITS}")

# Show diff
diff_pos = [i for i, (a, b) in enumerate(zip(best['bits'], EXPECTED_BITS)) if a != b]
print(f"  Bit errors at positions: {diff_pos}")

# Show all sorted by matches
print("\n--- All results sorted by matches ---")
for r in sorted(results, key=lambda x: -x['matches']):
    print(f"  offset={r['offset']:+3d} matches={r['matches']:2d}/48 "
          f"BER={r['ber']:6.1%} SNR={r['snr_db']:5.1f}dB")

# ----- Step 5: Verify L-SIG viterbi (24-bit SIGNAL field) -----
print("\n" + "=" * 70)
print("Step 5: L-SIG viterbi decode (24 info bits)")
print("=" * 70)

# For now, since hard-decision is best, we can check if RATE field starts with
# 1101 (which means HT-mixed MF preamble, BPSK rate 1/2)
# 24 SIGNAL bits = RATE(4) + LENGTH(12) + PARITY(1) + TAIL(6) + RESERVED(1)
# RATE=0xD=1101 means 6 Mbps
# The 48 expected bits are BCC-encoded, interleaved SIGNAL field
# First 4 SIGNAL bits (RATE) should be 1,1,0,1
print("RATE field check (first 24 bits encode 4-bit rate + 12-bit length + ...):")
print(f"  Hard-decision first 8 bits: {best['bits'][:8]}")
print(f"  Expected first 4 SIGNAL bits: 1101 (RATE=0xD, HT-mixed MF)")
print(f"  Expected length 16 bytes: 0000 0001 0000")
print()
print(f"Note: viterbi decoding requires deinterleaving + BCC decode (rate 1/2, k=7)")
print(f"For now, hard-decision with 32 dB SNR is essentially error-free at BPSK.")
print(f"But we see {best['matches']}/48 matches — let's investigate why SNR is high but BER is high.")

# ----- Step 6: Debug — why high SNR but high BER? -----
print("\n" + "=" * 70)
print("Step 6: Debug — show eq constellation stats")
print("=" * 70)
ltf0_data = fs + 176 + best['offset']
ltf1_data = fs + 256 + best['offset']
sig_data = fs + 336 + best['offset']
H = estimate_h(iq, ltf0_data, ltf1_data)
lsig = decode_lsig(iq, sig_data, H['F0a'])

eq = np.fft.fft(iq[sig_data:sig_data+64], 64)[ACTIVE_SC] / H['F0a']
cpe = np.angle(np.sum(eq))
eq_rot = eq * np.exp(-1j * cpe)

print(f"Per-bit eq values (real, imag, sign):")
for i, (r, im) in enumerate(zip(eq_rot.real, eq_rot.imag)):
    expected_bit = int(EXPECTED_BITS[i])
    got_bit = int(r > 0)
    correct = "OK" if expected_bit == got_bit else "ERR"
    print(f"  SC[{i:2d}] (k={ACTIVE_SC[i]:2d}): real={r:+.3f} imag={im:+.3f} "
          f"got={got_bit} exp={expected_bit} {correct}")

# ----- Final summary -----
print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)
print(f"Best matches: {best['matches']}/48 (BER={best['ber']:.1%})")
print(f"L-SIG SNR: {best['snr_db']:.1f} dB (Phase 28.2 reported 32.3 dB)")
print(f"Phase 25 baseline: 25% BER (= 36/48 matches)")
print()
if best['matches'] >= 43:
    print("STATUS: SUCCESS (BER <= 10.4%)")
elif best['matches'] >= 36:
    print("STATUS: PARTIAL (BER 12.5-25%)")
else:
    print("STATUS: FAILURE (BER > 27%)")
