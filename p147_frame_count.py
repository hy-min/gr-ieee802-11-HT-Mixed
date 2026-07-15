#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 T5 (definitive frame census): count L-STF plateaus = true frame count.

Each 802.11 frame has exactly ONE L-STF (10x period-16). A lag-16 coherence
plateau is therefore a robust one-per-frame marker, immune to burst-merging and
within-frame power dips. For each plateau, decode L-SIG validity (BPSK ratio)
with timing anchored ON the plateau (no mis-anchor confound).
"""
import numpy as np

PATH = '/tmp/p146_rxonly_cap.fc32'; FS = 20e6
LAG = 16; W = 32; COH_THR = 0.5
PLATEAU_MIN = 48          # L-STF plateau should be >= ~48 coherent samples
FRAME_GUARD = 1200        # samples; distinct frames are >1200 apart (60us)
DATA_SC = [s for s in range(-26, 27) if s != 0 and s not in (-21,-7,7,21)]
DATA_BINS = np.array([s if s > 0 else 64+s for s in DATA_SC])

def main():
    mm = np.memmap(PATH, dtype=np.complex64, mode='r'); n = mm.shape[0]
    # pass 1: coherence plateaus
    CH = 40_000_000
    plateaus = []  # global sample index of plateau center
    in_p = False; pstart = 0
    for start in range(0, n - LAG - W, CH):
        stop = min(start + CH, n - LAG - W)
        blk = np.array(mm[start:stop + LAG + W])
        c = blk[:-LAG] * np.conj(blk[LAG:])
        p = (blk.real**2 + blk.imag**2)[:len(c)]
        num = np.abs(np.convolve(c, np.ones(W), 'valid'))
        den = np.convolve(p, np.ones(W), 'valid') + 1e-9
        coh = num / den
        hi = coh > COH_THR
        for i in range(len(hi)):
            g = start + i
            if hi[i] and not in_p:
                in_p = True; pstart = g
            elif not hi[i] and in_p:
                if g - pstart >= PLATEAU_MIN:
                    plateaus.append((pstart + g)//2)
                in_p = False
        del blk, c, p, num, den, coh, hi
    plateaus = np.array(plateaus)
    # guard: keep first plateau per frame (merge < FRAME_GUARD apart)
    if len(plateaus):
        keep = [plateaus[0]]
        for x in plateaus[1:]:
            if x - keep[-1] > FRAME_GUARD:
                keep.append(x)
        frames = np.array(keep)
    else:
        frames = np.array([])
    print(f"[T5] raw plateaus={len(plateaus)}  frames(guard>{FRAME_GUARD})={len(frames)}", flush=True)
    if len(frames) > 1:
        gaps = np.diff(frames)/FS*1000
        print(f"[T5] inter-frame gap(ms): med={np.median(gaps):.1f} min={gaps.min():.1f} max={gaps.max():.1f}", flush=True)

    # pass 2: L-SIG validity at each frame (anchor = L-STF plateau start)
    valid = 0; ratios = []; lsigs = []
    for s in frames:
        seg = np.array(mm[s-16 : s-16+560])  # a bit before L-STF plateau
        if len(seg) < 560: continue
        # L-STF ends ~160 samples after plateau start; search LTF/L-SIG window
        best = (9.9, 0.0)
        for ltf_off in range(150, 175, 2):  # LTF1 search around plateau+160+32
            ltf1 = seg[ltf_off: ltf_off+64]; ltf2 = seg[ltf_off+64: ltf_off+128]
            lsig = seg[ltf_off+144: ltf_off+208]
            if len(lsig) < 64: continue
            H = 0.5*(np.fft.fft(ltf1)+np.fft.fft(ltf2))
            if np.abs(H[DATA_BINS]).mean() < 1e-6: continue
            eq = np.fft.fft(lsig)[DATA_BINS] / H[DATA_BINS]
            ratio = np.abs(eq.imag).mean()/(np.abs(eq.real).mean()+1e-9)
            if ratio < best[0]: best = (ratio, np.abs(H[DATA_BINS]).mean())
        ratios.append(best[0]); lsigs.append(best[1])
        if best[0] < 0.35: valid += 1
    ratios = np.array(ratios)
    print(f"\n[T5] frames with clean BPSK L-SIG (ratio<0.35): {valid} / {len(ratios)}", flush=True)
    print(f"[T5] L-SIG ratio: p10={np.percentile(ratios,10):.2f} med={np.median(ratios):.2f} p90={np.percentile(ratios,90):.2f}", flush=True)
    for thr in (0.3,0.4,0.5,0.6):
        print(f"[T5]   ratio<{thr}: {(ratios<thr).sum()}", flush=True)

if __name__ == '__main__':
    main()
