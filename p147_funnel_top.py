#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 T1c: measure the TRUE burst structure of the capture.

Stop assuming the TX frame rate. Detect TX-on bursts via power with hysteresis
(enter > hi, exit < lo), merge, and report burst count / duration / gaps. This
is the ground-truth top of funnel: how many distinct transmissions exist.
"""
import numpy as np

PATH = '/tmp/p146_rxonly_cap.fc32'
FS = 20e6
BIN = 160          # 8 us bins (one L-STF symbol)
HI_MULT = 8.0      # enter burst when bin power > 8x noise
LO_MULT = 3.0      # exit burst when bin power < 3x noise
MERGE_GAP_BINS = 250   # merge bursts < 250 bins (2 ms) apart
MIN_DUR_BINS = 2       # keep bursts >= 2 bins (16 us)

def main():
    mm = np.memmap(PATH, dtype=np.complex64, mode='r')
    n = mm.shape[0]
    nbins = n // BIN
    print(f"[T1c] samples={n} ({n/FS:.3f}s) bins={nbins} ({BIN}samp={BIN/FS*1e6:.1f}us)", flush=True)

    pwr = np.empty(nbins, np.float64)
    CH = 40_000_000
    idx = 0
    for start in range(0, nbins*BIN, CH):
        stop = min(start+CH, nbins*BIN)
        blk = mm[start:stop]
        p = blk.real**2 + blk.imag**2
        k = len(p)//BIN
        pwr[idx:idx+k] = p[:k*BIN].reshape(k, BIN).mean(axis=1)
        idx += k
    pwr = pwr[:idx]
    noise = np.median(pwr)
    print(f"[T1c] noise_floor={noise:.5f} max={pwr.max():.1f} ({10*np.log10(pwr.max()/noise):.1f}dB)", flush=True)

    hi, lo = noise*HI_MULT, noise*LO_MULT
    # hysteresis burst detection
    bursts = []
    on = False
    s = 0
    for i in range(nbins):
        if not on and pwr[i] > hi:
            on = True; s = i
        elif on and pwr[i] < lo:
            bursts.append([s, i]); on = False
    if on: bursts.append([s, nbins-1])
    print(f"[T1c] raw hysteresis bursts={len(bursts)}", flush=True)

    # merge close
    merged = []
    for b in bursts:
        if merged and b[0]-merged[-1][1] < MERGE_GAP_BINS:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    frames = [b for b in merged if (b[1]-b[0]) >= MIN_DUR_BINS]
    print(f"[T1c] after merge(gap<{MERGE_GAP_BINS} bins)={len(merged)}  kept(>= {MIN_DUR_BINS} bins)={len(frames)}", flush=True)

    if frames:
        durs = np.array([(b[1]-b[0])*BIN/FS*1e6 for b in frames])  # us
        starts = np.array([b[0]*BIN for b in frames])
        gaps_ms = np.diff(starts)/FS*1000
        print(f"[T1c] burst duration(us): med={np.median(durs):.0f} p10={np.percentile(durs,10):.0f} p90={np.percentile(durs,90):.0f} max={durs.max():.0f}", flush=True)
        print(f"[T1c] inter-burst gap(ms): med={np.median(gaps_ms):.1f} min={gaps_ms.min():.1f} max={gaps_ms.max():.1f}", flush=True)
        # SNR per burst
        snrs = np.array([pwr[b[0]:b[1]].max()/noise for b in frames])
        print(f"[T1c] per-burst peak SNR: med={10*np.log10(np.median(snrs)):.1f}dB min={10*np.log10(snrs.min()):.1f} max={10*np.log10(snrs.max()):.1f}", flush=True)
        print(f"[T1c] bursts SNR>10dB: {(snrs>10).sum()}/{len(snrs)}  >20dB: {(snrs>100).sum()}/{len(snrs)}", flush=True)
        print(f"[T1c] first 12 burst times(s): {np.round(starts[:12]/FS,3)}", flush=True)
        # expected frame count if interval were 100ms
        print(f"[T1c] TX count if interval=100ms over 30s = ~300; measured bursts = {len(frames)}", flush=True)

if __name__ == '__main__':
    main()
