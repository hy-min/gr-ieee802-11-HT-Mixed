# Phase 159: 到达损失预算分解 + 触发强度边际 — CONFIRMED 突破

**日期：** 2026-08-04
**状态：** ✅ **CONFIRMED** — N=8 交错 ABAB：DECODE_SUCCESS mean diff
**+55.8/45s**（176.8→232.5，+31.5%），t(7)=7.74，**paired t p=0.0001**，
Wilcoxon p=0.0078；arrival +46.8（p=0.0002）。8/8 对差值全为正。
harness 默认已翻转到 margin=2.5（C++ 默认 1.0 不变，env 可覆盖）。

---

## 1. 到达损失预算（新测量方法 + 最终数字）

**方法（本 phase 新建）**：给 P158-DIAG 的 `episode_end` 加 episode 起始
绝对位置（`d_episode_start`，sync_short.cc），TX 频闪周期格点拟合
（圆统计，period=100.088ms，concentration=0.993），把每个强 episode
（max_cor>10 = 真帧，2500× 阈值 vs 陷阱 1.3-1.8×）映射到 TX 槽位。
格点容差 ±3ms（触发点 jitter 在 ±2ms 内饱和）。

**预算（65s DIAG 轮，649 槽）：**

| 阶段 | 数值 | 备注 |
|---|---|---|
| 检测（真帧触发强 episode） | 506/649 = **78%** | 漏检 22%（弱帧撑不满 25 样本平台） |
| sync_short 陷阱致盲 | **0.15% 占空比** | 死轴（M24 后陷阱短至 ~280µs） |
| **链成功率**（episode→好 L-SIG） | 177/506 = **35%** | **主瓶颈** |
| 到达后解码（好 L-SIG→FCS） | 168/177 = **95%** | LO 墙仅 ~5% |

**链失败机制**：sync_short SEARCH 态丢样本 → episode 体背对背进
sync_long（100ms 帧间隔被压缩成 ~3ms）→ 真帧标签到达时 sync_long
几乎总在上一个 episode 的 COPY 中 → 本批 687 次 FAST_SYNC 重启 +
136 次 HT_MIXED 直接忽略。而 sync_long 饲料的 **46% 是噪声陷阱
episode**（分离带数据：陷阱 max_cor ≤0.36，真帧 ≥500，0.4-10 全空）。

## 2. P132 Schmidl-Cox REFUTED（附带结论）

S&C 度量替换（`IEEE80211_SYNC_SHORT_FUSED_SCHMIDL_COX=1`）sanity 轮
71661 tags/45s（35× boxcar）、DS=0。离线 IQ 根因（p159_sc_offline_diag.py）：
**幅度归一化使任意弱窄带 tone 的 |P|/R→1**（ms 级长平台，实测最长
13323 样本），S&C>0.2 占用 5.27% → 标签洪水。与 P88 MA-ratio 同构：
合成有效（6-40× 余量），真实结构化噪声崩塌。boxcar 的幅度依赖性反而是
保护（弱 spur 躺在自适应阈值下）。**S&C 作为度量替换轴关闭。**

## 3. 触发强度边际（trigger margin）— 本 phase 主结果

实现：`IEEE80211_SYNC_SHORT_TRIGGER_MARGIN`（opt-in，默认 1.0），平台
计数门 `cor > margin × effective_threshold`。TDD 单测 4/4 GREEN
（p159_margin_unit.py：margin=2.5 拒陷阱收真帧、margin=1.0 基线不变）；
loopback 回归 OK=1（默认配置不受影响）。

**N=8 交错 ABAB（p158_abab_batch.py，双臂 DIAG=1）：**

| 臂 | DS 值 | 均值 |
|---|---|---|
| A (margin 1.0) | 166,162,169,190,170,169,216,172 | 176.8 |
| B (margin 2.5) | 235,238,229,226,238,242,235,217 | **232.5** |

差值 [69,76,60,36,68,73,19,45]，mean **+55.8**，std 20.4，t(7)=7.74，
**p=0.0001**（Wilcoxon 0.0078）。arrival +46.8（p=0.0002）。

**机制核对（预注册，全中）：**
- 陷阱 episode：A 臂 5015 → B 臂 **18**（**-99.6%**）
- 强 episode：4275 → 3718（-13%；部分是同样被拒的强噪声突发 + 少量
  边缘真帧，arrival 净 +24% 证明链纯化收益远超此成本）
- 链成功率（arrival/强episode）：**36% → 51%**

## 4. 操作与配置变更

- **harness 默认翻转**：`test_usrp_rxonly_instrumented.py` setdefault
  `IEEE80211_SYNC_SHORT_TRIGGER_MARGIN=2.5`（P154 MIN_PLATEAU=24 先例：
  env 可覆盖，C++ 默认 1.0 不变）。今后 A/B 的对照臂运行在 margin=2.5
  基线上。
- 新仪表：ABAB harness 每轮提取 arrival（enc=0 len=72）；episode
  位置日志（DIAG）+ 格点分析 `p159_lattice_analysis.py`。

## 5. 下一步（按新预算）

链成功率 51% 后，残余损失重新分布：
- **~49% 链失败**：真→真压缩流重启 + L-SIG viterbi @ 噪声地板。候选：
  sync_long 对重启帧的 preamble 截断修复 / L-SIG 阶段鲁棒性。
- **22% 漏检**（弱帧平台断裂）：双阈值迟滞（高进低出）捕获边缘真帧。
- margin 微调（2.5 vs 3/4）：13% 强 episode 成本里有多少边缘真帧，
  可用 DIAG + 格点分析定量后再定。

**产物：** `p159_margin_unit.py`（TDD 4/4）、`p159_lattice_analysis.py`、
`p159_sc_offline_diag.py`、`batch_results/p159_margin/20260804_185522/`、
`/tmp/p159_diag{,2,3}.rt.err`
**相关：** [[Phase 158-ABAB]]（方法论）、[[Phase 157 refractory]]、
[[Phase 154 MIN_PLATEAU]]、[[Phase 153 funnel]]
