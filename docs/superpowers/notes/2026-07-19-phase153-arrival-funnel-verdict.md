# Phase 153: 实时到达率漏斗 — 首次端到端定量 + 损失机制定位

**日期：** 2026-07-19
**状态：** ✅ 漏斗完整测量；两个损失机制带直接证据定位；主瓶颈排除 sync_short
**方法：** systematic-debugging（先定位跌幅最大的一级，再提修复）

---

## 测量方法（零新计数器 + 1 行插桩）

复用现有 ground-truth 日志（stderr/stdout 流），仅给
`sync_short_fused` SPIKE dump 加 `pos=nitems_read(0)`（1 行，env-gated，
`IEEE80211_SYNC_SHORT_FUSED_DUMP=1`），使强相关事件（max_cor≥100 =
真帧候选）可与 sync_short 检测位置（`frame start at in: X out: Y`，
Y=USRP 绝对样本域）在同一时间域 join。

## 端到端漏斗（45s，发送 ~450 帧，run: DECODE_SUCCESS=67）

| 层级 | 计数 | 条件通过率 | 损失 | 机制（带证据） |
|---|---|---|---|---|
| TX 发送（估计） | 450 | — | — | 实测周期 101.5ms（≠100ms，msg_strobe 调度） |
| fused 强相关事件 | 650 | — | — | max_cor 367-518（真帧）vs 噪声 <0.15，分离极好 |
| **sync_short 检出** | **71.5%（465/650）** | 71.5% | **-28%** | **COPY 捕获**：假检测先于真帧 8-19 样本 → COPY 态吞并真帧（直接证据：`miss at 18068114: nearest det before=8`） |
| splitter frame-start | 97%（633/650） | 97% | -3% | d_frame_start=174 稳定 |
| 到达 L-SIG viterbi | 69%（450/650） | — | — | |
| **正确 L-SIG（len=52/66/72）** | **17%（109/650）** | **24%** | **-52%** | L-SIG viterbi 解出垃圾 len（1611 OK 中 77 个 len=72；垃圾 len 与假帧噪声假阳性混合） |
| HT-SIG CRC OK | 74 | 96% of good L-SIG | -1% | |
| decode 尝试 | 74 | 100% | 0 | |
| **FCS_OK** | **60-67** | **81%** | -19% | 1.77 rad per-frame 墙（已知，软件不可解） |

## 排除的假设（REFUTED with evidence）

1. **sync_short 自适应门限膨胀** — 门限 99.98% 时间在 0.2 地板（4,096,247/4,096,887 样本）。真帧 boxcar 300-500，门限 0.2 —— 门限不是漏检原因。
2. **sync_short 漏检为主瓶颈** — 检出率 71.5%，不是主损失级（但 COPY 捕获是第二大损失）。
3. **SPLITTER_BUFFER_FULL 丢帧** — 是每 OFDM 符号的正常探针（64 样本 FFT 窗满），非丢帧信号。
4. **实时特有流式伪影** — p150 离线 file-replay（无流式/无 chunk 抖动）100 帧测试床到达率仅 ~5.5%，低于实时的 13-15%。L-SIG 52% 垃圾率与 1.77 rad 噪声墙同源，非实时可修 bug。
5. **L-SIG viterbi 硬失败** — 0 个 `LSIG_DECODE FAIL`（viterbi 总能返回 24 bit；垃圾是收敛到错误码字，非解码器崩溃）。

## 关键结论

1. **主瓶颈 = L-SIG viterbi 对真帧解出垃圾（52%）**，与 1.77 rad per-SC 相位噪声墙同源（离线同样失败，非流式伪影）→ 软件杠杆已穷尽（p150 结论覆盖），唯一真杠杆 = 外部参考钟。
2. **第二大损失 = sync_short COPY 捕获（28%）— 唯一明确软件可修。** 机制：MIN_PLATEAU=2（仅需 3 连续样本过门限）对噪声过于宽松 → 假检测 46/s（真帧仅 10/s）→ COPY 占用 22.5% → 真帧到达时 COPY 不扫描被吞。
3. **HT-SIG 级已近乎无损**（good L-SIG → HT-SIG 96% → decode 100% → FCS 81%）。

## Phase 154 攻击方向（从本次测量直接导出）

`IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16`（Phase 89 预留旋钮，
匹配 L-STF 平台 1600 样本结构；噪声 boxcar 16 样本平滑难以持续 17 个
连续样本过门限）：预期同时降低假检测率（46/s → ~真帧率）、COPY 占用
（22.5% → ~5%）、COPY 捕获损失（28% → 个位数）。单变量、免重编译、
可回退。

## 产物

- 插桩：`sync_short_fused.cc` SPIKE dump 加 `pos=`（env-gated）
- 分析脚本：本 verdict 内嵌方法（spike 聚类 + 位置 join + 事件流段扫描）
- 数据：`/tmp/rt_validate.err/.out`（诊断跑，DECODE_SUCCESS=67）

**相关：** [[Phase 152 zero-run RFNoC]], [[Phase 151e COPY continuation]],
[[Phase 150 realtime path]], [[Phase 151d gap-stuck fix]]
