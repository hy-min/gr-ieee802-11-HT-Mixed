# Phase 159b: δ retroactive 修正 = L-SIG 抽签根因 — CONFIRMED，USRP realtime FCS_OK ≈ 100% 达成

**日期：** 2026-08-05
**状态：** ✅✅ **CONFIRMED + 项目目标实质达成** — N=8 交错 ABAB：
DECODE_SUCCESS 231.9 → **453.8/45s**（+221.9，t(7)=34.55，**p<10⁻⁴**，
Wilcoxon 0.0078，8/8 对全正，范围 +198~+251）；arrival 240.6 → **464.0**
（~100% 发送帧）；到达后解码率 **97.8%**。翻转默认后验证轮
**DECODE_SUCCESS=448/450 = 99.6%**，0 下溢/溢出。

---

## 1. 根因（systematic-debugging Phase 1 全证据链）

**问题**：margin=2.5 之后（P159a），~49% 已检测真帧死在链里（episode
→好 L-SIG 仅 51%）。

**排除的假设（全部有实测证据）**：
frame-start 失准（d_frame_start=174 全一致、found 全在 sym 4）；
幅度（found/lost 的 |eq|² 分布相同）；CFO/SFO（两群估计同样微小）；
时段聚集（结局 iid，autocorr −0.07）；TX 双群体（奇偶帧相同）；
episode 截断（体长全部 ~2948）。

**决定性证据**：
1. LSIG_EQ_FULL dump（sym 3，δ 修正**前**）的 52-SC 星座图：X 帧硬比特
   与好帧模板 hamming 0-3/48，rot=0 一致 —— **信号本身完美可读**。
2. 同一帧 viterbi 输入 deintl48（sym 4，δ 修正**后**）：hamming 25/48
   —— **随机垃圾**。
3. dump-eq vs candidate-eq 逐帧差异：G=0.24 vs X=1.50（6×）。
4. 代码定位：`frame_equalizer_impl.cc:7466-7473` —— counter=4 时
   `d_early_eqsym[kLSigRel/kHtSig0Rel/kHtSig1Rel] *= exp(j·2π·k·δ/64)`，
   δ = `estimate_timing_offset_from_h52(Hhdr52)`（H52 相位斜率拟合）。
   USRP H52 有 1.77 rad/SC 相位噪声 → **δ 估计是噪声主导的逐帧抽签**：
   估得小（≈0）→ 修正良性（G 帧）；估得大 → 修正把干净星座图转成垃圾
   （X 帧）。静态 δ 本来就会在 eq=rx/H 中自动抵消（L-LTF 与 L-SIG 同
   偏移），该修正对 L-SIG 纯属有害。

**为什么 145c 没抓住它**：winning config 在**文件回放**上验证（FCS_OK=5），
回放 chunk 动力学不同 + 样本量小；且当时 δ 修正被认为承重
（Phase 34 遗产）。P145b 曾 REFUTED "δ 修正方向"但只扫了 ε-scan，
没怀疑修正本身有害。

## 2. 实验（Phase 3-4）

单变量：`IEEE80211_TIMING_OFFSET_APPLY` 1→0（env 翻转，零代码改动）。

| 臂 | DS 值 | 均值 | arrival 均值 |
|---|---|---|---|
| A (δ ON) | 217,246,227,229,226,252,246,212 | 231.9 | 240.6 |
| B (δ OFF) | 441,461,438,466,461,450,450,463 | **453.8** | **464.0** |

风险监视（数据路径 δ 是否承重）：**否** —— B 臂 decode-of-arrived
97.8%，HT-SIG/数据路径同样不需要 δ（与静态分析一致：静态偏移在
eq=rx/H 中自动抵消）。

## 3. 影响重估

- **"1.77 rad LO 墙"大部分是 δ 修正的人为 artifact**。P150 的
  "软件不可破"结论需要在 δ-OFF 基线下重新审视：当时 ground-truth
  台架测的 2-way/Wiener/跨帧"边际/有害"结论，都是在 δ 毁坏 L-SIG 的
  前提下得出的。
- **30+ 个均衡器层 REFUTED phase 需要重新解读**：它们攻击的失败模式中
  相当部分是 δ 修正制造的（L-SIG/HT-SIG 星座图毁坏）。
- 到达率主攻任务（用户 2026-08-04 指定）**超额完成**：arrival ~100%，
  解码率 ~98%。

## 4. 配置变更

- **harness 默认翻转**：`test_usrp_rxonly_instrumented.py`
  `TIMING_OFFSET_APPLY` setdefault '1'→'0'（C++ 默认本来就是 OFF；
  loopback 回归门一直在 δ-OFF 下运行，OK=1 保持不变）。
- 新增诊断：HTSIG_VITERBI_DIAG 行尾加 `cfo_sym`/`sfo_sc` 字段
  （frame_equalizer_impl.cc，本次根因定位的关键仪表之一）。
- 基线变为 margin=2.5 + δ-OFF：DECODE_SUCCESS ≈ 448-454/45s（~100%）。

## 5. 残余损失与下一步

- ~0.4-2%：LDPC 终端失败 5/450（validation 轮）。可选择攻：残余
  LDPC 失败分类。
- margin=2.5 在 δ-OFF 基线下的独立贡献可重新测量（P159a 是 δ-ON 下
  测的；陷阱污染在 δ-OFF 下的危害结构可能不同）。
- 长期：把"δ 修正对 L-SIG 有害"写进 Phase 34 遗产的教训——任何
  基于噪声敏感估计的 retroactive 符号重写都必须有每帧有效性门。

**产物：** `batch_results/p159_no_delta/20260805_152642/`、
`/tmp/p159_vitdiag{,2,3}.rt.err`、本文件
**相关：** [[Phase 159a trigger margin]]、[[Phase 158-ABAB 方法论]]、
[[Phase 145b δ REFUTED]]（部分翻案）、[[Phase 150 LO wall]]（重估）
