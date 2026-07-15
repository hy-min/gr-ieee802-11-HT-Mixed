#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 T3 (DECISIVE): per-burst L-SIG constellation validity.

The GR funnel is noisy (LSIG_DECODE OK=695 but mostly false len). DECIDE the fork:
  - If MANY bursts have clean BPSK L-SIG but GR decoded only 26 -> RX misses them.
  - If FEW bursts have clean BPSK L-SIG -> TX corruption dominates (garbage frames).

For each detected burst: anchor timing via lag-16 L-STF autocorr, take LTF1/LTF2,
estimate channel H, equalize the L-SIG symbol, and score BPSK-ness
(mean|Im(eq)| / mean|Re(eq)| over the 48 data SCs). BPSK -> ~0, noise -> ~1.
"""
import numpy as np

PATH = '/tmp/p146_rxonly_cap.fc32'
FS = 20e6
BIN = 160
HI_MULT, LO_MULT = 8.0, 3.0
MERGE_GAP_BINS, MIN_DUR_BINS = 250, 2
# data SCs (48) within 64-FFT: SC -26..-22,-20..-8,-6..-1,+1..+6,+8..+20,+22..+26
# (exclude 4 pilots at SC -21,-7,+7,+21 and DC)
DATA_SC = [s for s in range(-26, 27) if s != 0 and s not in (-21, -7, 7, 21)]
def sc_to_bin(s):  # DC at bin 0
    return s if s > 0 else (64 + s)
DATA_BINS = np.array([sc_to_bin(s) for s in DATA_SC])

def detect_bursts(mm, n):
    nbins = n // BIN
    pwr = np.empty(nbins)
    CH = 40_000_000
    idx = 0
    for start in range(0, nbins*BIN, CH):
        stop = min(start+CH, nbins*BIN)
        blk = mm[start:stop]; p = blk.real**2 + blk.imag**2
        k = len(p)//BIN
        pwr[idx:idx+k] = p[:k*BIN].reshape(k, BIN).mean(axis=1); idx += k
    pwr = pwr[:idx]
    noise = np.median(pwr)
    hi, lo = noise*HI_MULT, noise*LO_MULT
    bursts, on, s = [], False, 0
    for i in range(nbins):
        if not on and pwr[i] > hi: on, s = True, i
        elif on and pwr[i] < lo: bursts.append([s, i]); on = False
    if on: bursts.append([s, nbins-1])
    merged = []
    for b in bursts:
        if merged and b[0]-merged[-1][1] < MERGE_GAP_BINS: merged[-1][1] = b[1]
        else: merged.append(b)
    frames = [b for b in merged if (b[1]-b[0]) >= MIN_DUR_BINS]
    return frames, noise

def lsig_bpsk_ratio(seg):
    """Return (ratio, |H|mean). Low ratio => BPSK-like (valid frame)."""
    if len(seg) < 460:
        return None, 0.0
    # anchor: find L-STF end via lag-16 autocorr plateau end in first 320 samples
    head = seg[:480]
    c = head[:-16] * np.conj(head[16:])
    pw_src = (head.real**2 + head.imag**2)[:len(c)]
    ac = np.abs(np.convolve(c, np.ones(16), 'valid'))
    pw = np.convolve(pw_src, np.ones(16), 'valid') + 1e-9
    coh = ac / pw  # ~1 during L-STF
    # L-STF plateau: coh>0.5; find where it ends (~sample 160)
    above = np.where(coh[:400] > 0.5)[0]
    if len(above) < 40:
        return None, 0.0
    lstf_end = above[0] + 160  # approx end of L-STF relative to plateau start
    # refine: search L-SIG FFT window around expected position
    best = (1e9, 0.0)
    for off in range(-8, 9, 2):
        base = lstf_end + off
        ltf1 = seg[base+32: base+96]
        ltf2 = seg[base+96: base+160]
        lsig = seg[base+176: base+240]  # after LTF(160) + CP(16)
        if len(lsig) < 64:
            continue
        F1, F2, FL = np.fft.fft(ltf1), np.fft.fft(ltf2), np.fft.fft(lsig)
        H = 0.5*(F1 + F2)
        Hmag = np.abs(H[DATA_BINS]).mean()
        if Hmag < 1e-6:
            continue
        eq = FL[DATA_BINS] / H[DATA_BINS]
        ratio = np.abs(eq.imag).mean() / (np.abs(eq.real).mean() + 1e-9)
        if ratio < best[0]:
            best = (ratio, Hmag)
    return best

def main():
    mm = np.memmap(PATH, dtype=np.complex64, mode='r')
    n = mm.shape[0]
    frames, noise = detect_bursts(mm, n)
    print(f"[T3] bursts={len(frames)} noise={noise:.5f}", flush=True)
    ratios, valid, valid_lens = [], 0, []
    results = []
    for b in frames:
        seg = np.array(mm[b[0]*BIN : min(b[1]*BIN + 512, n)])
        r = lsig_bpsk_ratio(seg)
        if r[0] is None:
            results.append(('no_anchor', None, 0.0)); continue
        ratio, Hmag = r
        results.append(('ok', ratio, Hmag))
    # summarize
    okr = [r[1] for r in results if r[0] == 'ok']
    no_anchor = sum(1 for r in results if r[0] == 'no_anchor')
    okr = np.array(okr)
    print(f"[T3] anchored={len(okr)}  no_anchor(too short/weak)={no_anchor}", flush=True)
    for thr in (0.3, 0.5, 0.7):
        nv = (okr < thr).sum()
        print(f"[T3] bursts with BPSK-like L-SIG (ratio<{thr}): {nv} / {len(okr)}", flush=True)
    print(f"[T3] ratio dist: p10={np.percentile(okr,10):.2f} p25={np.percentile(okr,25):.2f} "
          f"med={np.median(okr):.2f} p75={np.percentile(okr,75):.2f} p90={np.percentile(okr,90):.2f}", flush=True)
    # histogram
    hist, edges = np.histogram(okr, bins=[0,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.5,3.0])
    print("[T3] ratio histogram:", flush=True)
    for i in range(len(hist)):
        print(f"   {edges[i]:.1f}-{edges[i+1]:.1f}: {'#'*min(hist[i],80)} {hist[i]}", flush=True)

if __name__ == '__main__':
    main()
