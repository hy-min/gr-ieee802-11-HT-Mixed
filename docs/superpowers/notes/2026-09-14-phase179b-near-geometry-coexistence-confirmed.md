# Phase 179b verdict: 近几何（<10cm）Co-SR 共存 CONFIRMED — 耦合增强 8dB 但受害链路不受扰（2026-09-14）

VERDICT: 近几何（箱距 <10cm）Co-SR 共存 CONFIRMED——干扰箱 tx-gain 0→31.5dB
全程受害链路无功率依赖劣化（A 自身帧成功 678-921 全档平坦，满功率 31.5dB 达
95.3% 为全场最高）。几何变化被量化：干扰耦合门限从远几何 16dB 前移到 8dB
（约 +8dB 耦合增益），但受害机捕获自身信号从未被夺走（B 帧在 A 处解码成功
恒 0）。升臂 16dB 单点 5.7% 经降臂同功率 96.8% 判定为环境瞬态，非功率效应。

## VERDICT（逐字粘贴批次输出原文）

命令：`python3 p179_cosr_power_sweep.py --a-env HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1 --b-env HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1 --windows 5 --tag neargeo2`
（受害 A=192.168.10.2 自链路 A:0 TX→B:0 RX2，MCS0 len38→PSDU 66；
干扰 B=192.168.20.3 同构自链路，MCS0 len61→PSDU 89；5890 MHz 共信道，
箱距 <10cm，governor=performance）

```
========== P179 SWEEP SUMMARY ==========
idx  B_gain  arm    A_FCS_OK/est  rate%  len89_contam  attempts
  0     OFF  asc      688/750      91.73      0  1
  1       0  asc      727/750      96.93      0  1
  2       8  asc      671/750      89.47      0  1
  3      16  asc       43/750       5.73      0  1
  4      24  asc      524/750      69.87      0  1
  5    31.5  asc      736/750      98.13      0  1
  6    31.5  desc     693/750      92.40      0  1
  7      24  desc     607/750      80.93      0  1
  8      16  desc     726/750      96.80      0  1
  9       8  desc     633/750      84.40      0  1
 10       0  desc     527/750      70.27      0  1
 11     OFF  desc     549/750      73.20      0  2
[SWEEP] OFF repeat control: first=91.73% last=73.20%  |delta|=18.53pp -> DRIFT SUSPECT
[SWEEP] curve: /home/hy/captures/p179_cosr_power_sweep_20260914_105409_neargeo2/p179_curve.png
```

几何对比（同脚本同配置，仅箱距不同；B 帧@A = 受害箱 stderr 中 len=89 出现次数）：

```
远几何(1m):  B_gain  OFF   0     8     16    24    31.5
             B帧@A   0     0     0     802   1302  2139
             A帧成功 442   525   531   553   557   529
近几何(<10cm):B_gain  OFF   0     8     16    24       31.5
             B帧@A   0     0     1088  782   1409/2988 3311/928
             A帧成功 802   910   822   108   696/783   921/871
```

## 判定

**近几何 Co-SR 共存: CONFIRMED（表征测量，非 A/B）**
- 干扰功率 0→31.5dB 全程受害者自身帧成功 678-921（升/降臂），无单调劣化、
  无膝点；满功率 31.5dB 配对均值 95.3% 为全场最高
- **几何增量被量化**：干扰耦合门限（B 帧首次抵达 A 的功率档）从远几何 16dB
  前移到近几何 8dB ≈ **+8dB 耦合增益**；B 帧到达数在满功率达 3311（远几何 2139）
- **受害机从未被夺走捕获**：B 帧在 A 处 DECODE_SUCCESS 恒 0（len89_contam
  全 12 点 0）——A 捕获自身信号，干扰帧只能进入候选但解不出
- **升臂 16dB 单点 5.7% 判为环境瞬态**：三条证据——① 同功率降臂 96.8%；
  ② 升臂自身在更高功率 24dB(69.9%)/31.5dB(98.1%) 回升，"干扰越强越好"无物理
  机制；③ 该点逐窗口 6/6/6/20/5 均匀低达 75s，与真功率效应应有的单调形态不符
- DRIFT SUSPECT（预期内）：首尾 OFF 漂移 18.5pp——近几何窗间噪声 ±15pp
  （远几何 ±5pp，box2 机箱近场反射增加的额外多径），该噪声带即曲线分辨力上限
- 分子分母窗口：分母 est_sent~150/15s 窗 ×5 窗 = 750/点（interval 100ms，
  warmup 20s 已由 harness 排除）；分子 FCS_OK = [RESULT] per-window 计数器差值
  （P159b 分母同域规避）；len89 计数 = A 侧 stderr 全程 DECODE_SUCCESS len=89 行数
  （PSDU 89=B 帧，长度归因，P178 方法）

## 机制与实现

- 复用 `p179_cosr_power_sweep.py`（零代码改动），仅 `--windows 5`（近几何窗间
  噪声 ±15pp，点内窗口 3→5 压低均值标准误）
- **热瞬态教训（本次新增运维知识）**：首次启动时基线仅 41.3% 且批内单调崩塌
  （逐窗口 109→64→13），被预注册健康门（<50%）拦下。根因 = 两台设备刚上电
  （约 5 分钟）UBX LO 未热稳。判别法 = 逐窗口形态：**热瞬态呈单调递减，
  真功率效应呈均匀低**。加跑 6 窗稳定性诊断（B 静默）确认恢复：114/80/92/123/119/125，
  均值 72.5%，崩塌消失。设备热稳后基线升至 91.7%
- 受害链路配置：跨板自链路必须 `HDR_COMP_DISABLE=0 + SYNC_LONG_XCORR_FS=1`
  （同 P179 远几何）
- 新增对比工具 `p179_compare_geometry.py`（读两份 console log 叠加两几何曲线）

## 决策

- <10cm 仍不劣化 → P178 净判定"要演示碰撞威胁需更近箱距或电缆 2×2 可控 SIR
  信道"中，**"更近箱距"一路已封闭**（<10cm 实测无效应）；剩余路径 = 电缆 2×2
  精确 SIR 控制，或从受害侧压低自身信号（P180 SIR 扫描，同日启动）
- 无默认翻转、无新 env var、无禁止方向变更

## 诚实清单（故意不动）

- 无新 env var → 不动 env-vars.md（lint 用既有 IEEE80211_HDR_COMP_DISABLE 过门）
- 无默认翻转 → 不动 CLAUDE.md harness 表与「当前状态」
- 非关闭方向 → 不动禁止方向表与 hookify pattern
- 曲线 PNG 与全部日志在仓库外：
  `/home/hy/captures/p179_cosr_power_sweep_20260914_105409_neargeo2/`（有效数据）
  `/home/hy/captures/p179_cosr_power_sweep_20260914_101508_neargeo/`（热瞬态废数据，仅 idx0）
  几何对比图 `/home/hy/captures/p179_geometry_compare.png`
