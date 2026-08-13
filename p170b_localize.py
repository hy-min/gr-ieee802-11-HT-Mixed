#!/usr/bin/env python3
"""Phase 170b: localize the mid-preamble stall — GR TX chain vs UHD/USRP.

If the silence hole found in RX captures of lost frames ALSO appears in the
TX-chain capture, the GR TX hier produced it (mapper/mux/allocator emitting
zeros). If TX is clean but RX has holes, the stall is in UHD/USRP (underflow
sending silence mid-burst).
"""
import numpy as np
import sys

FS = 20e6

def find_frames_and_holes(path, label, is_tx):
    """Detect frame bursts and check each for a mid-preamble silence hole."""
    # burst detection via boxcar on period-16 autocorr
    CHUNK = 100_000_000
    pos_list = []
    offset = 0
    tail = np.zeros(0, dtype=np.complex64)
    thr = None
    while True:
        raw = np.fromfile(path, dtype=np.complex64, count=CHUNK, offset=offset*8)
        if len(raw) == 0: break
        x = np.concatenate([tail, raw])
        mult = np.abs(x[16:] * np.conj(x[:-16]))
        box = np.convolve(mult, np.ones(16), mode='valid')
        if thr is None:
            p90 = np.percentile(box, 90)
            thr = max(p90 * 50, 1e-6) if is_tx else max(p90 * 200, 1e-3)
        idx = np.where(box > thr)[0]
        if len(idx):
            grp = np.split(idx, np.where(np.diff(idx) > 2000)[0]+1)
            for g in grp:
                if len(g) >= 10:
                    pos_list.append(offset - 16 + int(g[0]))
        tail = x[-32:]
        offset += len(raw)
    pos_arr = np.array(pos_list)
    if len(pos_arr) > 1:
        keep = np.concatenate([[True], np.diff(pos_arr) > 2000])
        pos_arr = pos_arr[keep]
    print(f"[{label}] bursts: {len(pos_arr)}")

    # For each burst, measure min rolling |sample| in the preamble region
    # (L-LTF T2 end .. L-SIG .. HT-SIG0) = roughly +250..+500 from L-STF start
    holes = []
    for bi, p in enumerate(pos_arr):
        seg = np.fromfile(path, dtype=np.complex64, count=4000, offset=max(0,(p-200))*8)
        base = min(200, p)
        # preamble region relative to L-STF plateau start: +250..+500
        region = seg[base+250:base+520]
        if len(region) < 100: continue
        mag = np.abs(region)
        w = 24
        rm = np.convolve(mag, np.ones(w)/w, mode='valid')
        if len(rm) == 0: continue
        min_roll = rm.min()
        # TX file: inter-frame is zeros, so threshold relative to peak
        peak = np.abs(seg).max()
        thresh = 0.05 * peak
        if min_roll < thresh:
            holes.append((bi, p, min_roll, peak))
    print(f"[{label}] frames with preamble hole: {len(holes)}")
    for bi, p, mr, pk in holes[:20]:
        print(f"    burst[{bi}] pos={p} min_roll={mr:.4f} (peak={pk:.2f})")
    return pos_arr, holes

if __name__ == '__main__':
    tx_path = '/home/hy/captures/p170b_tx.fc32'
    rx_path = '/home/hy/captures/p170b_rx.fc32'
    tx_pos, tx_holes = find_frames_and_holes(tx_path, "TX", True)
    rx_pos, rx_holes = find_frames_and_holes(rx_path, "RX", False)

    print("\n=== VERDICT ===")
    if len(tx_holes) > 0 and len(rx_holes) > 0:
        print("Holes in BOTH TX and RX -> GR TX CHAIN produces the silence (mapper/mux/allocator bug)")
    elif len(tx_holes) == 0 and len(rx_holes) > 0:
        print("Holes in RX only, TX clean -> UHD/USRP layer (underflow sending silence mid-burst)")
    elif len(tx_holes) > 0 and len(rx_holes) == 0:
        print("Holes in TX only (unexpected)")
    else:
        print("No holes this run (need a run that captures lost frames)")
