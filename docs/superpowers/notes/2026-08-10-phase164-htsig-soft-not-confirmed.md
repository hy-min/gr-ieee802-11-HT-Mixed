# Phase 164: HT-SIG 软判决 viterbi —— NOT CONFIRMED on USRP（方向分裂）

**日期：** 2026-08-10
**状态：** ❌ **NOT CONFIRMED（方向分裂、方差大）**。Feature 保留 opt-in 默认
OFF（`8b957a5`），不进 harness。**HT-SIG 软判决第 3 次未过（P44/P129/P164）。**

---

## 1. 动机与实现

P163f 取证：清轮残余含"满强度帧提交正常但 HT-SIG 解析失败"（avg_snr_htsig
~1.9 dB、best_metric=N/A）。HT-SIG 默认硬判决 viterbi；P44/P129 软路径是
δ-on 时代 REFUTED（stale）。本 phase 把 P162 已验证的 \|H\|² 加权软判决推广
到 HT-SIG（`IEEE80211_HTSIG_SOFT_LLR_H2`，σ²-free，Im(eq)·\|H\|²/mean\|H\|²）。

**关键实现教训**：`viterbi_decode_133_171_soft` 用**平方误差**度量，要求
LLR 标定在 ~±1；未标定的 Im(eq)·\|H\|²（~±360000）使所有分支度量 ~r²，trellis
无法区分（回放到达 592→83 崩盘）。除以 mean\|H\|² 标定后恢复（592/592 中性）。
P162 数据路径用的是缩放不变的相关度量，不需标定——两路径度量不同。

## 2. 验证

- 算法模型 TDD（`p164_htsig_soft_model.py`）：QBPSK/48-bit，硬 0.810 → 软
  0.993（96% 救回）。
- 回归：10M/20M loopback OK=1；软开 loopback OK=1；p160 捕获回放中性
  （该捕获无 HT-SIG 失败可救）。
- subagent 代码审查 SAFE。

## 3. USRP 实时 4×300s 交错：NOT CONFIRMED

| 轮 | DS | HTSIG_FAIL | 终败 |
|---|---|---|---|
| off1 | 3170 | 16 | 9 |
| on1(软) | 3181 | 8 | 8 |
| off2 | 3168 | 8 | 9 |
| on2(软) | 3156 | 42 | 3 |

第 1 对软开助（DS +11，FAIL 16→8），第 2 对软开损（DS −12，FAIL 8→42）。
**方向分裂、方差大 → NOT CONFIRMED。**

**机制**：软权重 \|H\|² 只在 H 估计准确时有用。HT-SIG 是 H 误差集中处
（频带边缘）；H 噪时错误权重使软判决比硬判决更差。H 干净（on1）它助，
H 噪（on2）它害——机制上不可靠。数据路径软判决（P162）之所以成立，是因为
数据路径的 H 由 2 个 L-LTF 在整个帧上稳定估计；HT-SIG 头 48 bit、H 误差集中，
不满足该前提。

## 4. 结论

HT-SIG 软判决三次未过（P44/P129 δ-on；P164 δ-off + σ²-free 标定），机制上
不可靠（H 估计噪声主导）。**该轴关闭。** 当前清轮残余（~0.2-0.5%）= 衰落尾 +
HT-SIG/对齐尾 + 环境风暴尾，均为硬尾巴。Feature opt-in 默认 OFF，零基线影响。

**产物**：`p164_htsig_soft_model.py`、4×300s 日志 `/tmp/p164_{off,on}{1,2}.err`。
Verdict 本文。
