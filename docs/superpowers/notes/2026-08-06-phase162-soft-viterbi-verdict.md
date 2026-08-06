# Phase 162: 数据路径软判决 viterbi（|H|² 加权 LLR）— 攻残余 1% 的 LDPC 终端失败

**日期：** 2026-08-06
**状态：** ✅ **CONFIRMED on USRP**（N=8 交错 ABAB：终端失败 5.25→2.0/45s，
−62%，paired t p=0.0047；TDD 5/5 + loopback 回归 + 真实捕获回放全过）。
当前默认 OFF（opt-in）；端到端推算 ≈ 99.3%（开启后）。

---

## 1. 动机与机制（承接 Phase 161）

Phase 161 定案：当前真实端到端 98.9%（PDU 2968/3000），残余 ~32/3000 中
**21 帧是 LDPC 终端失败** —— 失败帧全是我方帧（FAIL_PSDU mac 头完好），
**min|H| p50 = 13.7 vs OK 帧 28.7，argmin 恒在 SC −28/−27**（20MHz 频带
边缘滚降区）。机制：边缘 SC 天然最弱 → ZF 均衡 1/H 放大噪声 → 硬判决把
放大后的噪声当全强度比特 → 47–115 错 > 硬判决预算（~40/1144）→ FCS 失败。

Phase 161 verdict 指出：**唯一有原理依据的修复 = 软判决 viterbi（|H|² 加权
LLR）**，但有 Phase 129 REFUTED 前科（δ-on 旧体制），需在 δ-OFF 新基线上
独立评估。Phase 162 即此评估。

## 2. 设计（与两次前科的关键区别）

$$\mathrm{LLR}_i = \mathrm{Re}(eq_i)\cdot|H_i|^2 \;\propto\; \mathrm{LLR}^{true}_i$$

- **max-log viterbi 对全局正缩放不变 → 根本不需要 σ² 估计**。这绕开了
  Phase 129 v2 的死因（σ² 从 null SC 估计，太噪）。|H|² 是相对权重，
  正是最大比合并（MRC）的充分统计量。
- **Phase 44/129 都是 stale REFUTED**：P44 USRP 0/0 FCS_OK（检测链断，
  软路径一帧没跑过）；P129 仅 HT-SIG（48 bit header）且 δ-on 时代
  file-replay 基线 0 FCS_OK。P159b 已证那堵"墙"大部分是 δ 修正 artifact。

**实现（全部 opt-in，默认 OFF，`IEEE80211_DATA_SOFT_VITERBI=1`）：**

1. `frame_equalizer_impl.cc`：帧起始 tag 位点（与 frame_bytes/mcs 同偏移）
   额外发射 `soft_h2` f32vector[52] = |d_H52_tx_order[k]|²。已验证
   `extract_ht_data52_direct_tx_order` 中 out52[k] 与 H52_tx_order[k]
   逐元素对齐（kTxOrder52[0..1] 恰为 SC −28/−27）。
2. `decode_mac.cc`：解析 soft_h2 tag → `LLR[k] = Re(eq[k])·w[k%52]` →
   `ht_deinterleave_f32`（镜像 ht_deinterleave 的索引数学）→ 软 viterbi。
   仅 BPSK（n_bpsc==1）；其它 MCS 回退硬路径；无 tag 回退硬路径（一次性告警）。
3. `viterbi_decoder_x86.cc::decode_soft()`：与 decode() 同一 133/171 trellis、
   零态终止、chainback；分支度量换为 max-log 相关
   `bm = (ex0?+l0:−l0) + (ex1?+l1:−l1)`；打孔位 LLR=0（擦除）。
4. LDPC fallback 路径不受影响（d_rx_eq 不改，软路径用独立 buffer）。

## 3. T1 合成 TDD（p162_soft_viterbi_unit.py，5/5 PASS）

Python 脚手架镜像 utils.cc（scramble/conv-encode/interleave），T1 自检
交织/解交织互逆，T2 干净 5/5 FCS_OK 证明脚手架可信。P161 衰落签名：
SC −28..−23 分级衰落 + CN(0,σ²) + ZF（弱 SC 噪声放大）。

| 测试 | 结果 |
|---|---|
| T1 排列互逆 | PASS |
| T2 干净硬路径 | 5/5 PASS |
| T3 校准区（σ=0.68） | 硬 FCS_OK = 264/300 = **0.880** |
| T4 软救回（预注册：rescue≥90% 且 soft≥0.95） | 软 = **300/300 = 1.000**，救回硬失败的 **100%** |
| T5 hero 帧（min|H|/med=0.06） | 硬失败 → 软救回 PASS |

**交叉曲线（p162_crossover.py，σ=0.68，150 帧/点）**：软判决在 min|H|
低至 **0.04**（中值的 4%）仍 100% 救回，硬判决从 0.97 退化到 0.63。
机制直觉：SC 衰落越深，其错比特权重越小 → trellis 实际将其当擦除。

契约修正记录：T4 原预注册"≥ hard+0.25pp"在 hard>0.75 时算术不可达
（率上限 1.0），改为救回比 ≥0.90 且软 ≥0.95（在目标区间更严格）。

## 4. 回归与真实捕获回放

- **loopback**：10MHz 默认 OK=1；20MHz 默认 OK=1；20MHz 软开 OK=1
  （双 banner + 无回退告警 → 软路径真实激活，非静默回退）。
- **真实捕获回放**（`/home/hy/captures/p160_detect_60s.fc32`，δ-OFF + M24
  + margin 2.5，65s）：硬 588/590（2 终败）→ 软 590/591（1 失败）。
  **FCS 指纹定帧**：硬的两个失败帧 rx_fcs=0x117ed3de/0x72cb8c02；软的唯一
  失败帧 rx_fcs=0xb8c1504 ∉ {两者}，且软 ConvOK=590=硬OK588+硬失败2 →
  集合算术证明：**共享 590 帧上软 = 590/590（100%），两个硬失败帧全救回、
  零新发失败**；软唯一失败帧是回放非决定论多出的到达帧（P148 chunk 效应）。

## 5. T4 USRP ABAB（N=8 交错，预注册）

标准 harness `p158_abab_batch.py`，`--exp-env IEEE80211_DATA_SOFT_VITERBI=1`，
控制臂 unset。governor=performance 已确认。预注册决策规则（机制只作用于
"已到达解码器但硬判决 FCS 失败"的帧，不改变到达/检测）：

- **主终点：DECODE_FAIL（LDPC 终端失败数）的配对减少** —— CONFIRMED  iff
  mean(A−B) > 0 且 paired t（或 Wilcoxon）p < 0.05。
- **护栏：DECODE_SUCCESS 不下降**（mean(B−A) ≥ 0；预期 +2~+4/45s 但因
  到达率噪声 ±15~30，不做显著性要求）；arrival 不变（软 viterbi 在解码级，
  不应影响到达）。

**结果：** ✅ **CONFIRMED（预注册主终点）** —— 8/8 对全有效、0 INFRA_FAIL、0 UF/OF：

| 指标 | A=硬 | B=软 | 配对差 | 检验 |
|---|---|---|---|---|
| **DECODE_FAIL（主终点）** | [5,5,7,3,8,4,3,7] mean **5.25** | [1,2,2,3,1,2,2,3] mean **2.00** | [−4,−3,−5,0,−7,−2,−1,−4] mean **−3.25** | **t(7)=−4.08, paired t p=0.0047**, Wilcoxon p=0.0178 |
| DECODE_SUCCESS（护栏） | mean 641.1 | mean 643.9 | **+2.75** | t p=0.132（如预判：到达率噪声 ±15–30 淹没 DS，不做显著性要求） |
| ARRIVAL（护栏） | mean 646.6 | mean 646.9 | +0.25 | p=0.83 —— 到达不变，证实机制纯解码级、无上游副作用 |

- 主终点 CONFIRMED：终端失败 **5.25 → 2.0/45s（−62%）**，7/8 对减少、
  1 对持平，paired t **p=0.0047 < 0.05**。
- DS 均值差 +2.75 ≈ 终败减少 −3.25（自洽：救回的失败帧转为成功）。
- harness 自带的 DS 判据行输出 "NOT CONFIRMED" 属预期 —— 那是 P158 遗留的
  粗粒度 DS 主判据；本实验的预注册主终点（§5 上段，批次运行前定稿）是
  DECODE_FAIL 减少，因为软 viterbi 只作用于"已到达但硬判决 FCS 失败"的帧，
  其信号在 DS 上被到达率噪声稀释、在终败数上集中。
- 残存 ~2.0/45s 失败：真实信道有相位噪声与更复杂的深衰落实现（合成模型
  只含幅度衰落 + AWGN），部分失败的错误分布在中等 |H| SC 上或呈密集突发，
  超出软加权能救的范围。
- **端到端推算**：基线 98.9%（残余 32/3000，其中 LDPC 21）→ LDPC 终端
  −62% ≈ 21→8 → **端到端 ≈ 99.3%**。距 99.9%（≤3/3000）的剩余：~8 LDPC
  残存 + ~11 检测/其他 + ~4 is_ht 误判。

## 6. 产物

- 提交 `6e11f87`（实现 + TDD harness）
- `p162_soft_viterbi_unit.py`（TDD，5/5）、`p162_crossover.py`（交叉曲线）
- 回放日志 `/tmp/p162_replay_{hard,soft}.{log,err}`
- ABAB：`batch_results/p162_soft_viterbi/<ts>/summary.txt`
- harness 增强：`p158_abab_batch.py` 新增 DECODE_FAIL 配对报告（分析用，
  不改既有 DS 主判据输出）。

**相关：** [[Phase 161 残余失败根因]]、[[Phase 160 trailing-window]]、
[[Phase 159b δ REFUTED]]（δ-OFF 基线）、[[Phase 158-ABAB 方法论]]。
