#!/usr/bin/env python3
"""Analyze 30s USRP capture for gaps, underflows, and per-frame quality."""
import numpy as np
import sys

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from p145b_delta_sign_analysis import detect_l_stf_starts, fft52, LTF_52, SC_INDEX_52

CAPTURE = '/tmp/p145c_30s.fc32'


def main():
    print(f'Loading {CAPTURE} ...')
    iq = np.memmap(CAPTURE, dtype=np.complex64, mode='r')
    duration = len(iq) / 20e6
    print(f'  {len(iq)} samples ({duration:.2f}s @ 20MHz)')

    # 1. Detect frames
    print('\nDetecting L-STF starts ...')
    starts = detect_l_stf_starts(iq)
    print(f'  found {len(starts)} frames')

    if len(starts) < 2:
        print('Not enough frames for gap analysis')
        return

    # 2. Gap analysis: check inter-frame spacing
    print('\n=== Inter-frame spacing ===')
    spacings = np.diff(starts)
    print(f'  min={spacings.min()} max={spacings.max()} mean={spacings.mean():.0f} std={spacings.std():.0f}')
    # Expected: packet interval ~100ms = 2M samples at 20MHz
    expected = 2_000_000
    large_gaps = np.where(spacings > expected * 1.5)[0]
    small_gaps = np.where(spacings < expected * 0.5)[0]
    print(f'  gaps > 1.5x expected ({expected*1.5/1e6:.1f}M): {len(large_gaps)}')
    print(f'  gaps < 0.5x expected ({expected*0.5/1e6:.1f}M): {len(small_gaps)}')

    # 3. Check for signal gaps (zero/near-zero regions)
    print('\n=== Signal gap detection ===')
    # Compute energy in 1ms windows (20000 samples)
    win = 20000
    n_win = len(iq) // win
    energy = np.array([np.mean(np.abs(iq[i*win:(i+1)*win])**2) for i in range(n_win)])
    median_e = np.median(energy)
    gap_windows = np.where(energy < median_e * 0.01)[0]
    print(f'  windows with energy < 1% of median: {len(gap_windows)} / {n_win}')
    if len(gap_windows) > 0:
        print(f'  first 10 gap window indices: {gap_windows[:10]}')
        print(f'  gap window times (s): {gap_windows[:10] * win / 20e6}')

    # 4. Per-frame quality analysis
    print('\n=== Per-frame quality ===')
    lltf0 = 176
    lsig = lltf0 + 160
    results = []
    for idx, fs in enumerate(starts):
        H0 = fft52(iq, fs + lltf0)
        L = fft52(iq, fs + lsig)
        if H0 is None or L is None:
            continue
        H = H0 / LTF_52
        eq = np.zeros(48, dtype=np.complex64)
        for j in range(48):
            if abs(H[j]) > 1e-3:
                eq[j] = L[j] / H[j]
        phases = np.angle(eq)
        phase_mod = (phases + np.pi/2) % np.pi - np.pi/2
        near_zero = np.mean(np.abs(phase_mod) < np.pi/6)
        im_var = np.var(eq.imag)
        mean_h = np.mean(np.abs(H))
        results.append({
            'idx': idx, 'start': fs, 'mean_h': mean_h,
            'im_var': im_var, 'near_0': near_zero
        })

    # Classify frames
    clean = [r for r in results if r['near_0'] > 0.7 and r['im_var'] < 0.5]
    marginal = [r for r in results if 0.4 <= r['near_0'] <= 0.7 or 0.5 <= r['im_var'] < 1.5]
    noisy = [r for r in results if r['near_0'] < 0.4 or r['im_var'] >= 1.5]

    print(f'  clean (near_0>0.7, im_var<0.5): {len(clean)}')
    print(f'  marginal: {len(marginal)}')
    print(f'  noisy: {len(noisy)}')

    print('\n  Detailed:')
    for r in results[:20]:
        tag = 'CLEAN' if r in clean else ('MARG' if r in marginal else 'NOISY')
        print(f"    frame {r['idx']:3d} start={r['start']:8d} |H|={r['mean_h']:6.2f} "
              f"im_var={r['im_var']:6.3f} near_0={r['near_0']:.2f} [{tag}]")

    # 5. Underflow correlation
    print('\n=== Underflow correlation ===')
    # If there are gap windows, check if any frames fall in them
    if len(gap_windows) > 0:
        gap_samples = set()
        for gw in gap_windows:
            gap_samples.update(range(gw * win, (gw + 1) * win))
        frames_in_gap = [r for r in results if r['start'] in gap_samples]
        print(f'  frames starting in gap windows: {len(frames_in_gap)}')
        for r in frames_in_gap[:5]:
            print(f"    frame {r['idx']} start={r['start']} |H|={r['mean_h']:.2f} near_0={r['near_0']:.2f}")
    else:
        print('  no significant gap windows detected')

    # 6. Summary verdict
    print('\n=== VERDICT ===')
    if len(gap_windows) > n_win * 0.05:
        print('  SIGNIFICANT GAPS DETECTED: TX underflow is likely corrupting the signal.')
        print('  Next: attack TX side (interval, buffer, USRP config).')
    elif len(clean) >= len(results) * 0.5:
        print('  CAPTURE IS CLEAN: most frames have good quality.')
        print('  Realtime failure is in RX chain processing, not IQ quality.')
        print('  Next: compare realtime RX chain vs file replay chain.')
    else:
        print('  MIXED QUALITY: some frames clean, some noisy.')
        print('  Need to understand why realtime decoder fails on clean frames.')
        print('  Next: compare C++ decode vs p145b on identical clean frames.')


if __name__ == '__main__':
    main()
