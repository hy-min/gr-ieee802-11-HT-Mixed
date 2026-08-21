# Phase 177 verdict: MCS 0-7 表征测量 + MCS 1 根因 + TX pad 对齐 CONFIRMED（2026-08-21）

## VERDICT（原文逐字粘贴）

配对 ABAB（mcs_sweep.py 交错批次，4 对，跨设备 192.168.20.3→192.168.10.2，
MCS 1 len 38，HDR_COMP_DISABLE=0 + SYNC_LONG_XCORR_FS=1）：

```
========== MCS SWEEP SUMMARY ==========
idx  MCS  len   env     modulation   FCS_OK/est_sent  rate%  FCS_FAIL  attempts
  0    1    38  -                   QPSK 1/2         88/450         19.56         0  1
  1    1    38  IEEE80211_TX_PAD_ALIGN=8  QPSK 1/2        389/450         86.44         0  1
  2    1    38  -                   QPSK 1/2         77/450         17.11         0  1
  3    1    38  IEEE80211_TX_PAD_ALIGN=8  QPSK 1/2        384/450         85.33         0  2
  4    1    38  -                   QPSK 1/2         74/450         16.44         0  1
  5    1    38  IEEE80211_TX_PAD_ALIGN=8  QPSK 1/2        380/450         84.44         0  2
  6    1    38  -                   QPSK 1/2         70/450         15.56         0  1
  7    1    38  IEEE80211_TX_PAD_ALIGN=8  QPSK 1/2        370/450         82.22         0  1
[SWEEP] MCS1/len38 repeat control: first=19.56% last=15.56%  |delta|=4.00pp -> drift ok
[SWEEP] MCS1/len38 repeat control: first=86.44% last=82.22%  |delta|=4.22pp -> drift ok
```

配对 t 检验（python，4 对 per-pair diff）：

```
N=4 pairs
  ctrl: [19.56, 17.11, 16.44, 15.56]  mean=17.17%
  exp : [86.44, 85.33, 84.44, 82.22]  mean=84.61%
  per-pair diff: ['+66.88', '+68.22', '+68.00', '+66.66']
  mean diff = +67.44 pp   sd = 0.78   t = 172.039
  paired t p = 0.000000
  VERDICT: CONFIRMED (p<0.05, mean>0)
```

## 判定

`IEEE80211_TX_PAD_ALIGN=N`（N=目标末符号真实比特上限）: **CONFIRMED**
- N=4 配对交错 ABAB，新鲜背靠背对照，governor=performance，电缆 --tx-scale 0.1，跨设备
- 预注册主终点（解码级机制 → FCS_OK/est_sent，pad 不触及到达层）：mean diff **+67.44 pp**（paired t p=0.0000，4/4 全正）
- 次终点：两臂重复漂移对照均过（对照 |Δ|=4.0pp、实验 |Δ|=4.2pp < 5pp）
- Loopback 门：OFF 臂 Final: OK=1 FAIL=0；ON 臂（PAD_ALIGN=8, MCS1 len38→41 / MCS2 len100→106）Final: OK=1 FAIL=0
- 分子分母窗口：每 run 3×15s 窗 = 450 帧 est_sent；FCS_OK 为 per-window 计数器差值（warmup 20s 已排除，P159b 分母同域规避）

## 机制与实现

- **根因（本 phase 先查明）**："MCS 1 FCS_OK 反常低于 MCS 2"（单板 43.1 vs 50.7%，
  跨设备 15.3 vs 76.2%）并非 MCS 1 代码 bug，而是两个独立因素的几何交互：
  1. **帧尾相位损伤（物理）**：残差 CFO/SFO 拟合误差×计数器外推 → 每符号相位误差随
     符号序号线性增长（跨设备 ~1°/符号，单板 ~0.5°/符号）→ 末符号恒定 ~25% 硬错误。
     证据：`[EQ_HTDATA]` 逐符号 QPSK 偏差轨迹 FAIL 帧末符号 22-34° vs 前段 9°；
     `[SYM n] deintl-vs-TX-punctured mismatches` 末符号 FAIL ~26-32 vs OK <1。
  2. **pad 几何（纯数学）**：`real_in_last = (16+8·PSDU+6) mod n_dbps` —— 末符号里
     真实数据比特数。错误落在 OFDM pad 上不验 FCS（无害），落在真实比特上即死。
- **7 配置定量吻合**：MCS0/38 (4)→97%、MCS1/38 (30)→16%、MCS1/100 (6)→57%、
  MCS2/38 (4)→75%、MCS2/100 (32)→1.6%、MCS1/41 (2)→88%、MCS1/35 (6)→87%
  （括号为 real_in_last）。阴性对照 MCS1/39 (38)→15% 纹丝不动。
- **修复**：opt-in env `IEEE80211_TX_PAD_ALIGN=N`，TX 侧自动扩展 payload 使末符号
  真实比特 ≤N。实现 `compute_padded_payload_len()`（test_usrp_rxonly_instrumented.py
  与 test_direct_loopback.py 各一份）。
- 判定为 CONFIRMED 但**保持 opt-in 默认 OFF**（跨设备 MCS 1 场景收益；单板 MCS 0
  基线 95% 无需；且 pad 改变 L-SIG length 属协议可见行为，翻默认需更多证据）。

### 机制深化（2026-08-21 同日后续）：帧尾损伤根因 = 二维独立随机 LO 相位噪声

Phase 1 根因调查（systematic-debugging）补三个证据，确认损伤**不可预测、无处根治**：

| 维度 | 证据（`mcs1_phase_traj.py`，跨设备交错批次 4 日志）| 判定 |
|------|------------------------------------------------|------|
| 符号间 | Δφ 每符号相位增量 lag-1 自相关 ≈0（-0.04~-0.09）| **白噪声**，随机游走累积 ~√n×6° |
| 符号内 | 同符号 4 SC signed_dev std ~9°（前段）→ 21-23°（末符号）| **SC 独立散开**，非整体旋转 |
| 帧间 | P168 已直接测量（SC 相关 \|r\|≈0.17, ~1.8 rad/SC）| 每帧每 SC 独立实现 |

**物理图景**：LO 相位噪声在 符号×SC 二维独立随机实现，帧尾超标 = 随机游走累积
（线性斜率仅 ~0.6-1.3°/符号，R² 0.24-0.62，确定性分量占比小）。

**根因级解释历史失败**（非参数问题，是原理性不匹配）：
- P176 EMA（Δφ 白噪声 → EMA 假设增量相关，天然失效）
- P35/P36/P161 CPE 修正族、P141 Wiener（平滑/外推一个纯随机场，天然失效）
- P168 统计补偿（无结构可平滑，已证）

**修正 P177 结论**：verdict 初版"pad 对齐是掩盖而非根治"应改为——**损伤不可预测
（二维随机场），不存在可根治的确定性根；pad 对齐是结构性正确的软件应对**。
物理唯一出路 = 外部 10 MHz 参考/GPSDO（与 CLAUDE.md 当前状态一致）。
工具：`mcs1_phase_traj.py`（逐符号相位轨迹 + 确定性检验，本 commit 提交）。

## 附带成果（本 phase 非假设检验，但交付）

### MCS 0-7 FCS_OK 表征（每档 450 帧，单板 + 跨设备）

| MCS | 调制 | 单板 | 跨设备 |
|-----|------|------|--------|
| 0 | BPSK 1/2 | 95.3% | 97.1% |
| 1 | QPSK 1/2 | 43.1% | 15.3% |
| 2 | QPSK 3/4 | 50.7% | 76.2% |
| 3 | 16QAM 1/2 | 1.8% | 0.4% |
| 4 | 16QAM 3/4 | 4.9% | 0.0% |
| 5-7 | 64QAM | 0% | 0% |

- 帧头链路各档无差异（LSIG_WIN/DECODE_TAG 与健康无异）；失败帧零 FCS_FAIL PDU
  （解码链内终止）。16QAM/64QAM 崩零 = LO 相位噪声墙（P169 同构，功率族无效）。
- 跨设备 MCS 2 > MCS 1 且 > 单板 MCS 2：HDR_COMP_DISABLE=0 逐符号补偿对 QPSK 数据
  符号收益显著（推断非 CONFIRMED——单/跨对比混淆拓扑与 env）。

### 交付物（本 phase 代码改动）
- `--mcs N` 参数：test_usrp_rxonly_instrumented.py + test_direct_loopback.py
  （HT MCS 0-7 → Encoding enum {0,2,3,4,5,6,7,8}，映射见 mapper_impl.cc）
- `mcs_sweep.py`：MCS 扫描驱动（漂移对照、P152 重试、per-run env 注入支持配对 ABAB）
- **RX 5/6 打孔补齐**（lib/viterbi_decoder/*：`PUNCTURE_5_6`，MCS 7 合成门抓出的
  既有实现缺口——RX 侧 64QAM 5/6 去打孔从未实现，补前 loopback MCS7 OK=0）
- `mcs1_frame_fate.py` / `mcs1_symerr_profile.py`：根因分析工具

## 决策

- pad 对齐 **CONFIRMED 但保持 opt-in OFF**：单板 MCS 0 基线 95% 已稳；收益集中在
  跨设备 MCS 1（17→85%）与一般"末符号真实比特多"的帧长；翻默认需 ABAB 覆盖
  MCS 0/2 + 空口 + 更长帧后再定。
- 帧尾相位损伤本身（CFO/SFO 外推误差）是更根本的靶点，但 P176 已判 EMA 方向
  NOT CONFIRMED（DS -42.2 p=0.021）——本 phase 不重开该轴。
- 16QAM/64QAM 轴不重开（LO 相位噪声墙，P169/P177 双证）。

## 诚实清单（故意不动）

- 未翻 CLAUDE.md「Harness 默认环境」表（pad 对齐 opt-in 保持 OFF）
- 未进「禁止方向」表（非 REFUTED；P176 EMA 已在 P176 归档时登记）
- 未动 wifi_phy_hier.py（pad 在 harness TX 层实现，未改 PHY 结构）
- 未动 CLAUDE.md 顶部「当前状态」（单板 99.55% 基线不受本 phase 影响）

## 日志

- 扫描日志：`/home/hy/captures/mcs_sweep_20260820_154637_v1`（单板）
  `/home/hy/captures/mcs_sweep_20260820_160206_xdev_v1`（跨设备）
  `/home/hy/captures/mcs_sweep_20260821_113210_xdev_abab_pad`（配对 ABAB）
