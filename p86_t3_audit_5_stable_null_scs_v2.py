#!/usr/bin/env python3
"""Phase 86 T3 v2: per-frame correlation between H52 SCs and rate=0x9/0xD.

The C++ dump formats are too sparse (7 SCs out of 52 in LTF0_FFT_PRECOMP, 5 in HHDR52).
Fall back to Python: read the IQ capture directly, compute H52 per frame, then
correlate with the C++'s per-frame rate=0x9/0xD decision from the log.

Method:
  1. Read the same 5s slice via p68_replay_offline.py, but with TIMING_OFFSET_APPLY=0
     and HT_SIG_CAND diagnostic to get per-frame rate.
  2. Read /tmp/p28_loopback_iq.fc32 in Python, find L-STF starts (same as Phase 82).
  3. Compute H52 for each frame.
  4. Build per-frame feature vectors: |H[sc]| and arg(H[sc]) for all 52 SCs.
  5. Compare rate=0x9 frames vs rate=0xD frames: which SCs differ most?
"""
import re
import sys
import numpy as np


CAPTURE_FILE = '/tmp/p28_loopback_iq.fc32'
C_LOG = '/tmp/p86_full_dump.log'

ACTIVE_SC_INDICES = list(range(1, 27)) + list(range(38, 64))
assert len(ACTIVE_SC_INDICES) == 52

# SC indices in TX order (L-SIG data SCs only, 48 of 52)
SC_INDEX_52 = np.array([
    -26,-25,-24,-23,-22, -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1, 1,2,3,4,5,6, 8,9,10,11,12,13, 14,15,16,17,18,19,
    20,22,23,24,25,26, -21,-7,7,21
], dtype=np.int32)

KLTF64 = np.array([
    0,    1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
    -1, -1, 1, 1, -1, 1, 1, -1, 1, -1, 1, 1,
    0, 0, 0, 0, 0,
    0,
    0, 0, 0, 0, 0,
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1,
    1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1,
], dtype=np.complex64)
LTF_ACTIVE = KLTF64[ACTIVE_SC_INDICES]

# L-LTF0/L-LTF1 positions per C++: counter=0 at fs+176, counter=1 at fs+256
# (per Phase 82 + p68 output)
def estimate_h52(iq, fs):
    lts0_start = fs + 176
    lts1_start = fs + 256
    if lts1_start + 64 > len(iq):
        return None
    LTS0 = iq[lts0_start:lts0_start + 64]
    LTS1 = iq[lts1_start:lts1_start + 64]
    F0 = np.fft.fft(LTS0, 64)
    F1 = np.fft.fft(LTS1, 64)
    F0a = F0[ACTIVE_SC_INDICES]
    F1a = F1[ACTIVE_SC_INDICES]
    avg = (F0a + F1a) / 2.0
    H = np.zeros_like(avg)
    valid = np.abs(LTF_ACTIVE) > 1e-6
    H[valid] = avg[valid] / LTF_ACTIVE[valid]
    return H.astype(np.complex64)


def find_l_stf_starts(iq, chunk_size=10_000_000, min_distance=2_000_000):
    n = len(iq)
    period = 16; win = 16; starts = []
    last_peak_pos = -min_distance
    for chunk_start in range(0, n - period, chunk_size):
        chunk_end = min(chunk_start + chunk_size + period, n)
        chunk = np.array(iq[chunk_start:chunk_end], dtype=np.complex64)
        a = chunk[:-period]; b = chunk[period:]
        corr_raw = np.abs(a * np.conj(b))
        kern = np.ones(win) / win
        corr_smooth = np.convolve(corr_raw, kern, mode='same')
        median_corr = float(np.median(corr_smooth))
        threshold = max(median_corr * 10.0, 0.01)
        above = corr_smooth > threshold
        rising_edges = np.where(np.diff(above.astype(np.int32)) == 1)[0]
        for r in rising_edges:
            abs_pos = chunk_start + int(r)
            if abs_pos - last_peak_pos >= min_distance:
                starts.append(abs_pos)
                last_peak_pos = abs_pos
        del chunk, a, b, corr_raw, corr_smooth, above
    return starts


def parse_rate_per_frame(log_path):
    """Returns list of (frame_idx, rate_value) from C++ log lines.

    C++ log format: 'lsig_rate=0xD' or similar.
    """
    rates = []
    with open(log_path) as f:
        for line in f:
            m = re.search(r'lsig_rate=0x([0-9A-Fa-f]+)', line)
            if m:
                rates.append(int(m.group(1), 16))
    return rates


def main():
    print("[P86-T3] Loading capture as memmap...")
    iq = np.memmap(CAPTURE_FILE, dtype=np.complex64, mode='r')
    print(f"[P86-T3] Total samples: {len(iq)}")

    print("[P86-T3] Finding L-STF starts...")
    starts = find_l_stf_starts(iq)
    print(f"[P86-T3] Found {len(starts)} L-STF starts (5s slice)")

    # Compute H52 for each frame
    H52_per_frame = []
    for fs in starts:
        H = estimate_h52(iq, fs)
        if H is not None:
            H52_per_frame.append(H)

    H52_arr = np.array(H52_per_frame)  # (n_frames, 52)
    print(f"[P86-T3] Computed H52 for {len(H52_arr)} frames")

    # Per-SC magnitude statistics
    print("\n[P86-T3] === Per-SC |H| statistics (H52 from L-LTF0+L-LTF1)/2 ===")
    print(f"  {'SC':>4} {'type':>20} {'mean':>8} {'std':>8} {'min':>8} {'max':>8}")
    for j in range(52):
        sc = int(SC_INDEX_52[j])
        mags = np.abs(H52_arr[:, j])
        if sc in (-21, -7, 7, 21):
            sc_type = "PILOT"
        else:
            sc_type = ""
        print(f"  {sc:>4} {sc_type:>20} {mags.mean():>8.3f} {mags.std():>8.3f} "
              f"{mags.min():>8.3f} {mags.max():>8.3f}")

    # Find the actual "5 stable null" SCs from THIS capture
    # Stable null = low mean |H| AND low std |H|
    mean_mag = np.mean(np.abs(H52_arr), axis=0)
    std_mag = np.std(np.abs(H52_arr), axis=0)
    cv = std_mag / np.maximum(mean_mag, 1e-9)
    # Sort by mean magnitude
    sorted_idx = np.argsort(mean_mag)
    print("\n[P86-T3] === Top 10 lowest |H| SCs (potential null candidates) ===")
    for j in sorted_idx[:10]:
        sc = int(SC_INDEX_52[j])
        sc_type = "PILOT" if sc in (-21, -7, 7, 21) else ""
        print(f"  SC {sc:>4} ({sc_type:>5}): mean={mean_mag[j]:.3f}, std={std_mag[j]:.3f}, "
              f"cv={cv[j]:.3f}")


if __name__ == '__main__':
    main()