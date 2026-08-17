#!/usr/bin/env python3
"""Phase 174: episode frame forensics — foreign-WiFi (H5) vs device transient (H6).

Tests on a live capture:
1. CFO fingerprint: our frames share the board LO (TX and RX same UBX) -> CFO ~ 0.
   Foreign AP frames -> ppm-level offset (tens-hundreds kHz).
   CFO = angle(mean(x[n+16] * conj(x[n]))) over L-STF / (2*pi*16*Ts).
2. Noise floor: inter-frame silence amplitude inside vs outside episode windows
   (RX gain sag -> floor drops too; TX-side or foreign -> floor flat).
3. Content extent: contiguous above-threshold length vs our 2481 samples.
"""
import sys
import numpy as np

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from p172_fullframe_hole_scan import detect_bursts

FS = 20e6
FL = 2481

def analyze(path, max_bursts=None):
    pos = detect_bursts(path)
    if max_bursts:
        pos = pos[:max_bursts]
    d = np.diff(pos)
    P = np.median(d[(d > 1.5e6) & (d < 2.5e6)])
    print(f"bursts={len(pos)} P={P:.0f}")

    rows = []
    for bi, p in enumerate(pos):
        seg = np.fromfile(path, dtype=np.complex64, count=FL+200, offset=int(p)*8)
        if len(seg) < 600:
            continue
        mag = np.abs(seg)
        peak = mag.max()
        # CFO from first 144 samples (9 short symbols), lag-16
        lstf = seg[:144]
        prod = lstf[16:] * np.conj(lstf[:-16])
        cfo = np.angle(prod.mean()) / (2 * np.pi * 16 / FS)
        # content extent: last index above 10% peak within frame window
        above = np.where(mag[:FL] > 0.1 * peak)[0]
        extent = int(above[-1] - above[0]) if len(above) else 0
        rows.append(dict(bi=bi, pos=int(p), peak=float(peak), cfo=float(cfo),
                         extent=extent, minroll=float(np.convolve(mag[:FL], np.ones(24)/24, 'valid').min())))
    return pos, P, rows

def main(path):
    pos, P, rows = analyze(path)
    strong = [r for r in rows if r['peak'] > 2.0]
    weak = [r for r in rows if r['peak'] <= 2.0]
    def cfo_stats(rs):
        if not rs: return "n=0"
        c = np.array([r['cfo'] for r in rs])
        return f"n={len(rs)} CFO median={np.median(c)/1e3:+.1f}kHz p5={np.percentile(c,5)/1e3:+.1f} p95={np.percentile(c,95)/1e3:+.1f}"
    print(f"STRONG frames: {cfo_stats(strong)}")
    print(f"WEAK   frames: {cfo_stats(weak)}")
    print("\nweak/torn frames detail:")
    for r in weak:
        print(f"  burst[{r['bi']}] peak={r['peak']:.2f} CFO={r['cfo']/1e3:+.1f}kHz extent={r['extent']} minroll={r['minroll']:.4f}")
    # strong-frame CFO reference sample
    print("\nstrong-frame CFO sample (first 10):")
    for r in strong[:10]:
        print(f"  burst[{r['bi']}] peak={r['peak']:.2f} CFO={r['cfo']/1e3:+.1f}kHz extent={r['extent']}")
    # noise floor: silence midpoints between consecutive grid slots, inside vs
    # outside weak-frame index ranges
    weak_idx = set(r['bi'] for r in weak)
    floors_in, floors_out = [], []
    for k in range(len(pos) - 1):
        gap = pos[k+1] - pos[k]
        if not (1.5e6 < gap < 2.5e6):
            continue
        mid = int(pos[k] + gap // 2)
        sil = np.fromfile(path, dtype=np.complex64, count=20000, offset=mid*8)
        if len(sil) < 20000:
            continue
        fl = float(np.median(np.abs(sil)))
        (floors_in if k in weak_idx else floors_out).append(fl)
    fi = np.array(floors_in); fo = np.array(floors_out)
    print(f"\nnoise floor near weak frames:  n={len(fi)} median={np.median(fi) if len(fi) else 0:.4f}")
    print(f"noise floor elsewhere:         n={len(fo)} median={np.median(fo) if len(fo) else 0:.4f}")

if __name__ == '__main__':
    main(sys.argv[1])
