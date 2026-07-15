#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 T4 (CONFIRM TX corruption): inspect intra-burst power continuity.

If TX underflows mid-frame, the USRP stops transmitting -> power drops to the
noise floor INSIDE a frame, fragmenting it. Detect that signature per burst and
correlate with L-SIG cleanliness. Distinguishes:
  (a) TX underflow fragmentation (mid-burst power gap)  -> TX corruption
  (b) continuous-but-weak burst                          -> low SNR / gain issue
  (c) continuous-strong burst but noise-like L-SIG       -> timing/other
"""
import numpy as np
exec(open('p147_lsig_validity.py').read().split('def main')[0])  # reuse detect_bursts, lsig_bpsk_ratio

PATH='/tmp/p146_rxonly_cap.fc32'; FS=20e6
def main():
    mm = np.memmap(PATH, dtype=np.complex64, mode='r'); n = mm.shape[0]
    frames, noise = detect_bursts(mm, n)
    print(f"[T4] bursts={len(frames)} noise={noise:.5f}", flush=True)
    frag=0; cont=0; weak=0; clean=0; ratio_of=[]
    samples=[]
    for bi,b in enumerate(frames):
        s0,e0 = b[0]*BIN, min(b[1]*BIN, n)
        seg = np.array(mm[s0:e0])
        p = seg.real**2+seg.imag**2
        # smooth in 32-sample windows
        W=32; k=len(p)//W
        if k<3: continue
        ps = p[:k*W].reshape(k,W).mean(axis=1)
        pk = ps.max()
        # mid-burst gap: any window (after the first 2, before last) near noise?
        body = ps[1:-1]
        gap = (body < noise*4).sum()  # windows dropped to ~noise
        is_frag = gap >= 2
        r,_ = lsig_bpsk_ratio(np.array(mm[s0:min(e0+512,n)]))
        ratio = r if r is not None else 9.9
        ratio_of.append(ratio)
        if is_frag: frag+=1
        else: cont+=1
        if pk/noise < 20: weak+=1
        if ratio<0.3: clean+=1
        if bi<6 or (ratio<0.3 and len(samples)<8):
            samples.append((bi, len(seg), pk/noise, gap, ratio))
    ratio_of=np.array(ratio_of)
    print(f"[T4] fragmented(mid-burst power gap>=2)={frag}  continuous={cont}", flush=True)
    print(f"[T4] weak(peak<20x noise~13dB)={weak}  clean_LSIG(ratio<0.3)={clean}", flush=True)
    # cross-tab: fragmentation vs L-SIG cleanliness
    print(f"[T4] corr: ratio med={np.median(ratio_of):.2f}", flush=True)
    print("\n[T4] sample bursts (idx,len_samp,peakSNRlin,gapWins,ratio):", flush=True)
    for s in samples:
        print(f"   idx={s[0]:4d} len={s[1]:6d} pkSNR={s[2]:7.1f}x ({10*np.log10(s[2]+1e-9):4.1f}dB) gap={s[3]:3d} ratio={s[4]:.2f}", flush=True)

if __name__=='__main__':
    main()
