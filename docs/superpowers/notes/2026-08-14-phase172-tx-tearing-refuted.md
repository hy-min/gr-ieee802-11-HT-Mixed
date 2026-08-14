# Phase 172 verdict: TX 撕裂机制——MTU/缓冲包装族 REFUTED + episode 归属设备模拟域（2026-08-14）

## VERDICT（逐字粘贴测量输出原文）

**H3（B/C 族=捕获伪影假设）证据——网格残差（episode 前后轨道连续，无压缩）：**
```
=== fate capture episode 465..480 ===
burst[463] resid=+139237 / burst[464] resid=+136524   (pre)
burst[475] resid=+130645 / burst[476] resid=+136850 / burst[477] resid=+134281  (post)
=== p170b_rx episode 545..551 ===
pre  ~+30k track ; post burst[554/556/557] = +23309/+25310/+25322  (same track)
```

**H1（MTU/首包边界假设）证据——交错臂洞扫描：**
```
A1 mtu1500: burst[19]  L-SIG start=376 dur=152 peak=2.96   ([uhd] Maximum frame size: 1472 bytes)
A2 mtu1500: burst[41]  L-SIG start=377 dur=323 peak=2.92   (frame size 1472)
B2 mtu9000: burst[517] L-SIG start=345 dur=174 peak=2.72   ([uhd] Maximum frame size: 8000 bytes)
            burst[685] L-SIG start=345 dur=154 peak=2.84
            burst[554] HT-LTF start=708 dur=206 peak=2.80
撕裂率: A1=1/754, A2=1/745, B2=2(+1)/734 —— 洞位 345 不动，率不降
```

**P173（episode TX/RF 定位）证据——同跑双捕获（90s, DS=693, 0 overflow/0 underflow）：**
```
RX 捕获 episode 帧: slot 741 (peak 2.69, 1 洞), slot 841 簇 (peak 1.41-1.76 弱幅+碎洞),
                    slot 853/854 (peak 2.19-2.57, 各 1 洞)
TX 数字链聚合扫描（p173_tx1.fc32, 695 帧）:
  TX frames=695  peak: min=0.2490 median=0.2841
  minroll: min=0.0567 median=0.0671
  hole frames (minroll < 0.0142): 0
  weak frames (peak < 0.8*median): 0
VERDICT: H1 REFUTED（洞位与 MTU 无关）; H3 REFUTED（episode 非捕获伪影）;
         P173 episode = 设备模拟域事件（TX 数字链同跑全干净 → 主机软件无杠杆）
```

## 判定

- **TX 缓冲/MTU/jumbo/tsb 包装族：REFUTED**（机制级）。
  - 交错 A/B/A 设计（1500/9000/1500，新鲜背靠背，governor=performance，电缆 --tx-scale 0.1）；
    b1 臂 DS=0 系 P152 RFNoC init 崩（b2 同 MTU 正常），按规程单独计数不进统计。
  - 预注册主终点（机制级 → 洞位置+A 族撕裂率）：jumbo 下洞位 345 不变、率不降 → 假设证伪。
    （"345+检测滞后≈364=首包边界"的算术为诱巧合；A1/A2 实测 376/377 本就不符。）
- **B/C episode 捕获伪影假设：REFUTED**——episode 是真实空口事件。
- **episode 主机软件杠杆：机制级证伪**——同跑 TX 数字链 695/695 帧完美（0 洞 0 弱幅），
  RX 空口同时弱幅/碎裂 → 退化发生在 UHD/USRP 模拟侧（UBX TX 或 RX 增益路径瞬态），
  且 0 underflow/0 overflow 伴随。RX 解码侧同样无解（弱幅=SNR 问题）。
- **分子分母窗口**：A 族率 = 各臂全捕获（20s warmup + 2×30s 测量窗，~600-750 bursts/臂，
  含 warmup 帧）；episode 计数同窗。TX 聚合扫描 = 同跑全部 695 个产出帧（TX 捕获压缩，
  缺帧无占位，故用聚合比对而非逐帧索引对齐）。

## 机制与实现

- A 族（位置锁定 L-SIG 撕裂 ~345，满强度，dur 124-323 样本）约束集：固定起点 + 可变时长
  + MTU 无关 + TX 数字链干净 → 最佳拟合模型 = host↔device 投递边界起点 stall +
  固定排水深度。定时突发（姊妹篇 phase172b）是对症形状但效力未确认。
- B/C 族（多帧弱幅碎裂/截断 episode，~1-2 次/60s）= 设备模拟域，软件轴关闭。
- 工具：`p172_fullframe_hole_scan.py`（全帧洞扫描，p170b 只看前导 +250..520 的盲区补齐）、
  `p173_episode_localize.py`（TX/RF 双捕获定位）、`p172_mtu_ab*.sh`（交错驱动，含 ≥5s
  MTU 稳定 + df 磁盘守卫）。
- 运维教训（已入 memory）：MTU 切换后须 ≥5s 再 probe；磁盘满挂死签名 = futex 等待 +
  文件停长；捕获批次前查 df（≥20G）。

## 决策

- 禁止方向表新增两行：TX 缓冲/MTU/jumbo/tsb 包装族；B/C episode 主机软件修复。
- hookify pattern 同步 `TX_BURST_TSB`（P170 的 tsb env 从未归档，本次补登记 env-vars.md
  并入护栏）。
- 残余 0.45% 最终分解维持：A 族（投递边界 stall，率 ~0.1-0.5% 波动）+ episode（设备模拟域）。
  99.9% 唯一原理路径不变：外部 10 MHz 参考/GPSDO。
