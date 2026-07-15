#!/usr/bin/env python3
"""Phase 112 R1 + T7e D1: H52(sc) 时间序列 + 相位噪声根因诊断

R1:  诊断 per-symbol 相位方差根因 (per-symbol / per-frame / per-second)
     横比 Phase 25 报告的 1.77 rad 残差 (≈101°)

T7e D1: 收集多 symbol H52(sc),为 D2-D4 的 decision-directed 提供数据

测量:
  1. 检测 L-STF plateau 找到每帧起始
  2. 抽 L-LTF / HT-SIG0 / HT-SIG1 / DATA[0..N-1] / HT-LTF
  3. 用 L-LTF H52 当参考,算每个 symbol 的 argH drift per SC
  4. 输出 per-symbol / per-frame / per-second 漂移统计
"""
import numpy as np
import sys
import os

# === Constants ===
PILOT_BINS = [7, 21, 43, 57]  # +7, +21, -7(=-56%64=8... wait recheck)
# 802.11n subcarrier mapping for HT-Mixed 20MHz:
#   Subcarrier -26 to +26 in 64-point FFT
#   Pilot SCs: -21, -7, +7, +21 → FFT bins 43, 57, 7, 21
# But the project uses neg-first order, so data SCs active at:
#   1..26 (neg freq SCs -26..-1) + 38..63 (pos freq SCs +1..+26)
#   Pilots: bins 7 (+7), 21 (+21), 43 (-21), 57 (-7)
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))  # 52 SCs
DATA_SC = [k for k in ACTIVE_SC if k not in PILOT_BINS]  # 48 data SCs
N_FFT = 64

# OFDM symbol timing (20 MHz, 80 samples/symbol + 16 sample GI):
#   L-STF:        0-159   (160 samples = 8 µs, 10 periods of 16)
#   L-LTF0:       160-239
#   L-LTF1:       240-319
#   L-SIG:        320-399
#   HT-SIG0:      400-479
#   HT-SIG1:      480-559
#   HT-STF:       560-639
#   HT-LTF0:      640-719
#   HT-LTF1:      720-799
#   DATA[0]:      800-879
#   DATA[k]:      800 + 80*k
L_LTF0_OFFSET = 160
OFDM_SYM_LEN = 80


def find_l_stf_regions(iq, period=16, search_skip=1000, min_gap=20000):
    """Find L-STF starts using period-16 autocorrelation (Phase 89 algorithm)."""
    n = len(iq) - period
    a = iq[:-period]
    b = iq[period:]
    corr_raw = np.abs(a * np.conj(b))
    win = 16
    kern = np.ones(win) / win
    corr_smooth = np.convolve(corr_raw, kern, mode='same')

    threshold = 0.1
    min_plateau = 32
    starts = []
    i = search_skip
    while i < len(corr_smooth) - min_plateau:
        if corr_smooth[i] > threshold:
            end = i
            while end < len(corr_smooth) and corr_smooth[end] > threshold * 0.3:
                end += 1
            if end - i >= min_plateau:
                starts.append(i)
                i = end + min_gap
                continue
        i += 1
    return starts


def compute_h52(ltf0_samples, ltf1_samples):
    """Compute H52 from L-LTF0 + L-LTF1 averaging. Returns 64-pt complex."""
    win = np.hanning(64)
    F0 = np.fft.fft(ltf0_samples[:64] * win, 64)
    F1 = np.fft.fft(ltf1_samples[:64] * win, 64)
    return (F0 + F1) / 2.0


def compute_h52_at_offset(iq, frame_start, symbol_offset, win=None):
    """Compute FFT at given OFDM symbol offset from frame start.

    symbol_offset=0 → L-LTF0
    symbol_offset=1 → L-LTF1
    symbol_offset=2 → L-SIG
    symbol_offset=3 → HT-SIG0
    symbol_offset=4 → HT-SIG1
    symbol_offset=5 → HT-STF (or HT-LTF0 if no STF)
    """
    if win is None:
        win = np.hanning(64)
    sym_start = frame_start + L_LTF0_OFFSET + symbol_offset * OFDM_SYM_LEN
    if sym_start + 64 > len(iq):
        return None
    samples = iq[sym_start:sym_start + 64]
    return np.fft.fft(samples * win, 64)


def compute_argh_drift_per_sc(h52_ref, h52_sym):
    """Per-SC arg(H_sym / H_ref). Returns array of 64 complex argdiffs."""
    ratio = h52_sym / (h52_ref + 1e-12)
    return np.angle(ratio)


def main():
    capture = '/tmp/p110_t10_capture.fc32'
    if len(sys.argv) > 1:
        capture = sys.argv[1]

    print(f"[R1+T7e D1] Analyzing {capture}")
    if not os.path.exists(capture):
        print(f"  File not found! Abort.")
        return
    iq = np.fromfile(capture, dtype=np.complex64)
    print(f"  Samples: {len(iq)} = {len(iq)/20e6:.1f}s @ 20 MHz")

    # Find L-STF starts
    l_stf_starts = find_l_stf_regions(iq, period=16)
    print(f"  Found {len(l_stf_starts)} L-STF plateaus")

    if not l_stf_starts:
        print("  No L-STF found.")
        return

    n_data_symbols = 6
    # Offset for each OFDM symbol (from frame_start):
    # L-LTF0=160/80=2, L-LTF1=240/80=3, L-SIG=4, HT-SIG0=5, HT-SIG1=6,
    # HT-STF=7, HT-LTF0=8, HT-LTF1=9, DATA[k]=10+k
    sym_offsets = {
        'L-LTF0': 2,
        'L-LTF1': 3,
        'L-SIG': 4,
        'HT-SIG0': 5,
        'HT-SIG1': 6,
        'HT-STF': 7,
        'HT-LTF0': 8,
        'HT-LTF1': 9,
    }
    for k in range(n_data_symbols):
        sym_offsets[f'DATA[{k}]'] = 10 + k

    # Collect per-frame argH (per SC) for each symbol
    argh_per_sym = {name: [] for name in sym_offsets}  # list of (52,) arrays
    valid_frames = 0
    max_frames = 30
    for fs in l_stf_starts[:max_frames]:
        # Compute L-LTF reference H52
        sym0 = compute_h52_at_offset(iq, fs, sym_offsets['L-LTF0'])
        sym1 = compute_h52_at_offset(iq, fs, sym_offsets['L-LTF1'])
        if sym0 is None or sym1 is None:
            continue
        h52_ref = (sym0 + sym1) / 2.0  # 64-pt complex

        frame_ok = True
        frame_data = {}
        for name, offset in sym_offsets.items():
            h = compute_h52_at_offset(iq, fs, offset)
            if h is None:
                frame_ok = False
                break
            # argH per active SC
            argh = compute_argh_drift_per_sc(h52_ref[ACTIVE_SC], h[ACTIVE_SC])
            frame_data[name] = argh
        if frame_ok:
            valid_frames += 1
            for name, argh in frame_data.items():
                argh_per_sym[name].append(argh)

    print(f"\n  Valid frames analyzed: {valid_frames}")
    print(f"  OFDM symbols per frame: {len(sym_offsets)}")

    # === R1 Analysis: per-symbol argH drift statistics ===
    print(f"\n{'='*70}")
    print(f"[R1] Per-symbol argH drift statistics (vs L-LTF reference)")
    print(f"     Frames={valid_frames}, Active SCs={len(ACTIVE_SC)}")
    print(f"{'='*70}")
    print(f"  {'Symbol':<10} {'mean_per_SC':>12} {'std_per_SC':>12} "
          f"{'min_max_per_SC':>20} {'Phase_25_ref':>15}")
    print(f"  {'':─<70}")

    for name in sym_offsets:
        if not argh_per_sym[name]:
            continue
        data = np.array(argh_per_sym[name])  # (n_frames, 52)
        # Per-SC std averaged across SCs and frames
        per_sc_std = data.std(axis=0).mean()
        per_sc_mean = data.mean(axis=0).mean()
        per_frame_mean_std = data.mean(axis=1).std()  # std of per-frame means
        max_std = data.std(axis=0).max()
        max_mean = np.abs(data.mean(axis=0)).max()
        print(f"  {name:<10} {per_sc_mean:>+10.3f} rad {per_sc_std:>10.3f} rad  "
              f"max_mean={max_mean:>5.2f} max_std={max_std:>5.2f}   "
              f"({per_sc_std*180/np.pi:>5.1f}°)")

    # === R1: Time-scale decomposition ===
    print(f"\n{'='*70}")
    print(f"[R1] Time-scale decomposition of per-SC phase drift")
    print(f"{'='*70}")

    # For DATA[0..N], compute per-frame argH change
    if 'DATA[0]' in sym_offsets and len(argh_per_sym.get('DATA[0]', [])) > 1:
        # Per-symbol drift (within a frame): argH(DATA[k]) - argH(DATA[0])
        for k in range(1, n_data_symbols):
            name = f'DATA[{k}]'
            if not argh_per_sym[name]:
                continue
            data0 = np.array(argh_per_sym['DATA[0]'])
            datak = np.array(argh_per_sym[name])
            # Per-frame diff
            per_frame_diff = datak - data0  # (n_frames, 52)
            std = per_frame_diff.std(axis=0).mean()
            print(f"  Δφ(DATA[{k}] - DATA[0]): std = {std:.3f} rad "
                  f"({std*180/np.pi:.1f}°) across {valid_frames} frames")

        # Per-frame drift: average |argH| per frame
        # This tells us how argH changes from one frame to next
        d0 = np.array(argh_per_sym['DATA[0]'])  # (n_frames, 52)
        if len(d0) > 1:
            frame_to_frame_diff = np.diff(d0, axis=0)  # (n_frames-1, 52)
            f2f_std = frame_to_frame_diff.std(axis=0).mean()
            print(f"  Per-frame drift (DATA[0] across frames): std = "
                  f"{f2f_std:.3f} rad ({f2f_std*180/np.pi:.1f}°)")
            print(f"    (Phase 25 reported 1.77 rad residual; if this matches, "
                  "upstream analog phase noise confirmed)")

    # === T7e D1: data export for offline analysis ===
    print(f"\n{'='*70}")
    print(f"[T7e D1] Per-frame argH time series (first 3 frames shown)")
    print(f"{'='*70}")

    if valid_frames >= 1:
        # Show first 3 frames, all symbols, all SCs (mean argH across SCs)
        frame_argh_table = []
        for name in sym_offsets:
            if argh_per_sym[name]:
                # Mean argH across all 52 SCs (one number per frame)
                arr = np.array(argh_per_sym[name])  # (n_frames, 52)
                mean_per_frame = arr.mean(axis=1)  # (n_frames,)
                frame_argh_table.append((name, mean_per_frame))

        print(f"  {'Frame':<6} {'Symbol':<10} {'mean_argH(rad)':>15} "
              f"{'mean_argH(°)':>15}")
        for frame_idx in range(min(3, valid_frames)):
            for name, arr in frame_argh_table:
                print(f"  {frame_idx:<6} {name:<10} {arr[frame_idx]:>+13.4f} "
                      f"{arr[frame_idx]*180/np.pi:>+13.2f}")
            print(f"  {'':─<6}")

    # === Summary verdict for R1 ===
    print(f"\n{'='*70}")
    print(f"[R1 SUMMARY]")
    print(f"{'='*70}")
    print(f"  If DATA[k] - DATA[0] std ≈ 108°: per-symbol drift dominates →")
    print(f"    channel H changes significantly between DATA symbols.")
    print(f"    T7e decision-directed tracking should help.")
    print(f"  If frame-to-frame std >> 1.77 rad: per-frame drift dominates →")
    print(f"    UHD/buffer/driver issue (analog chain stable but interface unstable).")
    print(f"  If per-second drift dominates: thermal or oscillator issue.")


if __name__ == '__main__':
    main()
