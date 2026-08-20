#!/usr/bin/env python3
"""P176 离群帧物理来源定位(Phase 1 补证).

三个互斥候选, 同一捕获一次判别:
  (a) RX 窗错位: 离群帧的最优 L-LTF 窗 dt≠0 且 resid 回落到主体水平 → P166a 领域
  (b) TX 完整性: 离群帧前导区有功率洞(USB 网卡突发抖动污染) → TX 侧问题
  (c) 信道/LO 物理噪声: 两者都不成立 → 无软件杠杆确认

另查: CFO 分布是否多模(多发射机/TX 不稳 vs 单模重尾噪声).
"""
import numpy as np

CAPTURE = '/home/hy/captures/p176_adjudication.fc32'
ACTIVE_SC = list(range(1, 27)) + list(range(38, 64))
SC = np.array([-26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
               -7,-6,-5,-4,-3,-2,-1, 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,
               20,21,22,23,24,25,26], dtype=float)

def wrap(x): return (x + np.pi) % (2*np.pi) - np.pi

def fit_resid(iq, l0, l1):
    H0 = np.fft.fft(iq[l0:l0+64], 64)[ACTIVE_SC]
    H1 = np.fft.fft(iq[l1:l1+64], 64)[ACTIVE_SC]
    pd = np.angle(H1 * np.conj(H0))
    sfo = np.sum(SC * pd) / np.sum(SC**2)
    cfo = np.mean(pd)
    r = pd - (cfo + sfo*SC)
    return cfo, sfo, float(np.sqrt(np.mean(r**2))), float(np.mean(np.abs(H0)))

def main():
    iq = np.fromfile(CAPTURE, dtype=np.complex64)
    p = np.abs(iq)**2
    thr = 0.1; above = p > thr
    edges = []; i = 0; n = len(p)
    while i < n - 3000:
        if above[i]:
            j = i
            while j < n-1 and (p[j] > thr*0.3 or p[j+1] > thr*0.3): j += 1
            if j - i > 300: edges.append((i, j)); i = j + 1000
            else: i += 1
        else: i += 1
    print(f"frames: {len(edges)}")

    rows = []
    for (s, e) in edges:
        l0 = s + 192; l1 = l0 + 80
        cfo, sfo, rms0, h0 = fit_resid(iq, l0, l1)
        # (a) 窗扫描
        best_dt, best_rms = 0, rms0
        for dt in range(-8, 9, 2):
            if dt == 0: continue
            _, _, r, _ = fit_resid(iq, l0+dt, l1+dt)
            if r < best_rms: best_rms, best_dt = r, dt
        # (b) 前导洞扫描: L-STF(160) + L-LTF(160) + L-SIG(80) = 前 400 样本, 40 样本窗
        frame_pow = p[s:s+400]
        peak = np.max(frame_pow)
        win_min = min(np.mean(frame_pow[k:k+40]) for k in range(0, 360, 40))
        hole_ratio = win_min / peak if peak > 0 else 1.0
        rows.append((cfo, sfo, rms0, best_dt, best_rms, h0, hole_ratio))
    rows = np.array(rows)
    cfo = rows[:,0]; rms0 = rows[:,2]; bdt = rows[:,3]; brms = rows[:,4]; hole = rows[:,6]

    print("\n=== CFO 分布(多模检查, rad/symbol) ===")
    hist, bedges = np.histogram(cfo, bins=15)
    for k in range(15):
        print(f"  [{bedges[k]:+.3f},{bedges[k+1]:+.3f}): {'█'*hist[k]} {hist[k]}")

    outl = rms0 > 0.5
    print(f"\n=== 离群帧(resid_rms>0.5): {np.sum(outl)}/{len(rows)} = {100*np.mean(outl):.1f}% ===")
    print(f"\n--- 候选(a) RX 窗错位 ---")
    print(f"主体帧: 最优dt |mean|={np.mean(np.abs(bdt[~outl])):.2f}  离群帧: {np.mean(np.abs(bdt[outl])):.2f}")
    print(f"离群帧 resid@最优dt: mean={np.mean(brms[outl]):.3f} (dt=0 时 {np.mean(rms0[outl]):.3f})")
    rec = np.sum((brms[outl] < 0.5))
    print(f"离群帧在最优窗恢复到主体水平的: {rec}/{np.sum(outl)}")
    print(f"\n--- 候选(b) TX 前导洞 ---")
    print(f"主体帧 hole_ratio: mean={np.mean(hole[~outl]):.3f} min={np.min(hole[~outl]):.3f}")
    if np.any(outl):
        print(f"离群帧 hole_ratio: mean={np.mean(hole[outl]):.3f} min={np.min(hole[outl]):.3f}")
        deep = np.sum(hole[outl] < 0.15)
        print(f"离群帧有深洞(<0.15): {deep}/{np.sum(outl)}")
        bulk_deep = np.sum(hole[~outl] < 0.15)
        print(f"主体帧有深洞(<0.15): {bulk_deep}/{np.sum(~outl)}  (对照)")
    print(f"\n--- 判定 ---")
    if np.any(outl) and np.mean(brms[outl]) < 0.5*np.mean(rms0[outl]):
        print("→ (a) RX 窗错位是主因: 离群帧 resid 在移动窗后大幅回落")
    elif np.any(outl) and np.mean(hole[outl]) < 0.5*np.mean(hole[~outl]):
        print("→ (b) TX 前导洞是主因: 离群帧前导有显著功率洞")
    else:
        print("→ (c) 两者都不成立: 离群帧 = 信道/LO 物理噪声实现, 软件杠杆确认不存在")

if __name__ == '__main__':
    main()
