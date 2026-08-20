#!/usr/bin/env python3
"""P176 HT-LTF 相位假设检验: HT-LTF 相对 L-LTF 的相位结构.

新假设(来自 implementer 拦截): HT-LTF 在第 6 符号,比 L-LTF0 晚 6 个符号,
携带 6×(cfo + sfo·sc) 的相位旋转;3-way H 平均 / CFO 精化若不 derotate 就用
会引入系统性偏置(实验 F: cfo -0.15 vs L-LTF +0.04 的根因候选).

检验: 对捕获里每帧, 算 r_ht0 = arg(H_HTLTF · conj(H_LLTF0)) per SC,
与预言 6×(cfo_L + sfo_L·sc) 比较:
  - 残差≈0 (常数与斜坡都≈0) → 假设成立, 简单 derotate 即可用
  - 残差有常数偏置 → HT-LTF 有额外未知相位
  - 残差有斜坡 → HT-LTF FFT 窗错位
"""
import numpy as np

CAPTURE = '/home/hy/captures/p176_adjudication.fc32'
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
SC_INDEX = np.array([-26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
                     -7,-6,-5,-4,-3,-2,-1, 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,
                     20,21,22,23,24,25,26], dtype=float)
# HT-LTF = L-LTF sequence on legacy SCs (verified vs insert_ht_training_impl.cc)
# → 归一化参考与 L-LTF 相同; H 比值时参考相消, 不需要显式参考.

def wrap(x):
    return (x + np.pi) % (2*np.pi) - np.pi

def main():
    iq = np.fromfile(CAPTURE, dtype=np.complex64)
    p = np.abs(iq)**2
    thr = 0.1
    above = p > thr
    edges = []
    i = 0; n = len(p)
    while i < n - 3000:
        if above[i]:
            j = i
            while j < n-1 and (p[j] > thr*0.3 or p[j+1] > thr*0.3):
                j += 1
            if j - i > 300:
                edges.append((i, j))
                i = j + 1000
            else:
                i += 1
        else:
            i += 1
    print(f"frames: {len(edges)}")

    stats = []
    for (s, e) in edges:
        l0 = s + 192            # L-LTF0 data
        l1 = l0 + 80            # L-LTF1 data
        lht = s + 192 + 6*80    # HT-LTF data (counter 6)
        if lht + 64 > n:
            continue
        H0 = np.fft.fft(iq[l0:l0+64], 64)[ACTIVE_SC]
        H1 = np.fft.fft(iq[l1:l1+64], 64)[ACTIVE_SC]
        HT = np.fft.fft(iq[lht:lht+64], 64)[ACTIVE_SC]
        if np.mean(np.abs(HT)) < 1.0:   # HT-LTF 不存在(非 HT 帧或短帧)则跳过
            continue
        pd = np.angle(H1 * np.conj(H0))                    # cfo + sfo·sc
        sfo = np.sum(SC_INDEX * pd) / np.sum(SC_INDEX**2)
        cfo = np.mean(pd)
        r_ht0 = wrap(np.angle(HT * np.conj(H0)))           # 实测 HT-LTF 相位(相对 L-LTF0)
        pred = 6.0 * (cfo + sfo * SC_INDEX)                # 预言
        resid = wrap(r_ht0 - pred)
        # 残差结构: 常数项 + 斜坡项
        slope_r = np.sum(SC_INDEX * resid) / np.sum(SC_INDEX**2)
        offset_r = np.mean(resid)
        rms_r = float(np.sqrt(np.mean(resid**2)))
        stats.append((cfo, sfo, offset_r, slope_r, rms_r, float(np.mean(np.abs(H0)))))

    stats = np.array(stats)
    cfo = stats[:,0]
    med = np.median(cfo)
    bulk = np.abs(cfo - med) < 0.02
    print(f"HT-LTF 可读帧: {len(stats)}")
    print(f"\n=== 假设检验: r_ht0 vs 6×(cfo+sfo·sc) 残差 ===")
    for name, m in [('主体帧', bulk), ('离群帧', ~bulk)]:
        if not np.any(m):
            continue
        print(f"{name} (N={np.sum(m)}):")
        print(f"  残差常数项 mean = {np.mean(stats[m,2]):+.4f} rad   (应≈0; ≠0 = HT-LTF 有额外相位偏置)")
        print(f"  残差斜坡   mean = {np.mean(stats[m,3]):+.6f} rad/SC (应≈0; ≠0 = HT-LTF 窗错位)")
        print(f"  残差 rms   mean = {np.mean(stats[m,4]):.4f} rad    (小 = 简单 derotate 可用)")
    print(f"\n=== 判定 ===")
    off = np.mean(stats[bulk,2]); sl = np.mean(stats[bulk,3]); r = np.mean(stats[bulk,4])
    if abs(off) < 0.05 and abs(sl) < 0.001 and r < 0.6:
        print("✅ 假设成立: HT-LTF 相位 = 6×(cfo+sfo·sc) + 小噪声 → derotate 后可直接用于 3-way / CFO 精化")
    elif abs(off) >= 0.05:
        print(f"❌ HT-LTF 有常数相位偏置 {off:+.4f} rad → derotate 之外还有未知机制, 先查偏置来源")
    elif abs(sl) >= 0.001:
        print(f"❌ HT-LTF 有斜坡 {sl:+.6f} rad/SC → FFT 窗错位, 先修窗")
    else:
        print(f"⚠️ 残差 rms={r:.4f} 偏大 → derotate 可行但噪声稀释收益打折")

if __name__ == '__main__':
    main()
