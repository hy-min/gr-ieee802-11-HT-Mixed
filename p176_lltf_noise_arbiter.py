#!/usr/bin/env python3
"""P176 裁决实验: L-LTF 时刻每-SC 相位差分布 + 离群帧形态.

裁决矛盾: 若每-SC 相位噪声 ~1.77 rad(数据符号时刻测量值), 则 L-LTF0/1
52-SC 拟合的 CFO 估计 σ 应 ~0.35 rad; 但主体帧实测 σ ~0.01. 两者差 35×.

同时判别离群帧形态: (a) 单SC尖峰 → 迭代剔除/Theil-Sen 有效;
(b) 全SC斜坡 → 定时/整帧事件, 拟合方法无效; (c) 整帧偏置 → 其他机制.
"""
import numpy as np

CAPTURE = '/home/hy/captures/p176_adjudication.fc32'
FS = 20e6
RX_SCALE = 40.0
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))  # 52 active (FFT bins)
SC_INDEX = [-26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
            -7,-6,-5,-4,-3,-2,-1, 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,
            20,21,22,23,24,25,26]  # FFT bin → SC index (natural order, DC=0 at bin 0)

def find_frames(iq, period=16, win=16):
    """boxcar period-16 autocorr detector (P89 replica)."""
    a = iq[:-period]; b = iq[period:]
    corr = np.convolve(np.abs(a * np.conj(b)), np.ones(win), mode='same')
    # trailing-window adaptive threshold (P160): p90 of last 4096 * 1.5
    frames = []
    i = 4096
    while i < len(iq) - 5000:
        w = corr[i-4096:i]
        p90 = np.percentile(w, 90)
        thr = max(p90 * 1.5, 0.2)
        # plateau detection: >=24 consecutive above 2.5*thr (P154/P159)
        j = i
        while j < i + 4000:
            if corr[j] > thr * 2.5:
                end = j
                while end < len(corr)-1 and corr[end+1] > thr:
                    end += 1
                if end - j >= 24:
                    frames.append((j, end))
                    i = end + 160  # skip past L-STF (160 samples)
                    break
            j += 1
        else:
            i += 4000
            continue
        i += 800  # don't re-detect inside same frame preamble
    return frames

def main():
    print(f"loading {CAPTURE} ...")
    iq = np.fromfile(CAPTURE, dtype=np.complex64) / RX_SCALE
    print(f"{len(iq)} samples = {len(iq)/FS:.1f}s")

    frames = find_frames(iq)
    print(f"detected {len(frames)} L-STF plateaus")

    rows = []  # per frame: cfo, sfo, resid_rms, |H| stats
    all_pd = []  # per-SC phase diffs, bulk frames only
    outlier_frames = []
    for (st, en) in frames:
        fs = en - 160 + 16  # L-STF data start ≈ plateau end - 160 + 16
        # L-LTF0: fs+176 .. fs+240 (GI 32 + 64); L-LTF1: fs+256..fs+320
        l0 = fs + 176 + 32
        l1 = fs + 256 + 32
        if l1 + 64 > len(iq):
            continue
        F0 = np.fft.fft(iq[l0:l0+64], 64)
        F1 = np.fft.fft(iq[l1:l1+64], 64)
        H0 = F0[ACTIVE_SC]
        H1 = F1[ACTIVE_SC]
        pd = np.angle(H1 * np.conj(H0))  # per-SC phase diff (rad/symbol)
        sc = np.array(SC_INDEX, dtype=float)
        # receiver's exact fit: sfo = sum(sc*pd)/sum(sc^2), cfo = mean(pd)
        sfo = np.sum(sc * pd) / np.sum(sc * sc)
        cfo = np.mean(pd)
        resid = pd - (cfo + sfo * sc)
        rms = float(np.sqrt(np.mean(resid**2)))
        hm = float(np.mean(np.abs(H0)))
        rows.append((cfo, sfo, rms, hm))
        all_pd.append(pd)

    rows = np.array(rows)
    cfo = rows[:, 0]; sfo = rows[:, 1]; rms = rows[:, 2]; hm = rows[:, 3]
    med = np.median(cfo)
    bulk = np.abs(cfo - med) < 0.02
    outl = ~bulk

    print(f"\n=== 帧统计 (N={len(rows)}) ===")
    print(f"CFO: median={med:.4f}  bulk σ={np.std(cfo[bulk]):.4f}  "
          f"range=[{np.min(cfo):.4f},{np.max(cfo):.4f}]")
    print(f"离群帧 (|cfo-med|>0.02): {np.sum(outl)}/{len(rows)} = {100*np.mean(outl):.1f}%")

    print(f"\n=== 裁决指标: 主体帧每-SC 相位差噪声 ===")
    if np.sum(bulk) > 10:
        pd_bulk = np.array(all_pd)[bulk]
        resid_all = pd_bulk - (cfo[bulk,None] + sfo[bulk,None]*np.array(SC_INDEX)[None,:])
        sigma_sc = float(np.std(resid_all))
        print(f"每-SC 残差 σ = {sigma_sc:.4f} rad  (噪声模型预言 ~1.77 rad/SC)")
        print(f"  → 若 σ≈1.8: 模型成立(1.77 rad 是 L-LTF 时刻噪声); 若 σ≪1.8: 1.77 不是 L-LTF 噪声")

    print(f"\n=== 离群帧形态 (前 5 个离群帧) ===")
    out_idx = np.where(outl)[0]
    for k in out_idx[:5]:
        print(f"frame#{k}: cfo={cfo[k]:+.4f} sfo={sfo[k]:+.6f} resid_rms={rms[k]:.4f} |H0|={hm[k]:.1f}")
        pd_k = all_pd[k]
        # 形态判别: 最大残差 SC 的占比
        resid_k = pd_k - (cfo[k] + sfo[k]*np.array(SC_INDEX))
        maxres = np.max(np.abs(resid_k)); total = np.sum(np.abs(resid_k))
        nbig = np.sum(np.abs(resid_k) > 3*sigma_sc) if np.sum(bulk)>10 else -1
        print(f"  max|resid|={maxres:.3f}  尾部占比={maxres/total:.2f}  >3σ 的 SC 数={nbig}/52")
        print(f"  形态: {'单SC尖峰' if maxres/total>0.3 else '全SC分布(斜坡/整帧事件)'}")

    print(f"\n=== |H| 对比 ===")
    print(f"主体帧 |H0| mean={np.mean(hm[bulk]):.2f}  离群帧 |H0| mean={np.mean(hm[outl]) if np.any(outl) else float('nan'):.2f}")

if __name__ == '__main__':
    main()
