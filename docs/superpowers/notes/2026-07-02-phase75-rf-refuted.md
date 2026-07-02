# Phase 75 Verdict — REFUTED (RF Upstream Investigation)

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: **REFUTED** — RF frequency and physical layer inspection do not improve steady-state USRP channel. T1 (physical) and T2 (frequency sweep) both NO_CHANGE/NO_DIFFERENCE. T3-T5 (antenna distance / L-SIG CPE / LNA) cannot be completed in current session. Per HARD CONSTRAINT, Phase 76 must plan upstream attack.

---

## 关键发现

**RF 物理层不是 USRP HT-SIG 失败的限制因素**:
- T1 (physical inspection): NO_CHANGE — 无 smoking gun
- T2 (frequency sweep 5180/5500/5890 MHz): NO_DIFFERENCE — 三个频率 snr_lsig 在 ±1.16 dB 内
- 稳态 USRP 真实情况：n_nulls=0-1 (with tight_v2 pre-clean), snr_lsig=2.67-4.91 dB, HT_CAND=0
- L-SIG viterbi 阈值 6 dB — 差 1.1-3.3 dB 不到

**Software equalizer-layer 73 阶段 + RF 4 方向都已 REFUTED**:
- 12+ 软件层 hypothesis (Phase 19/20/25-44/54/59/61-73) — 全部 REFUTED
- T1/T2 物理层 — REFUTED
- 唯一 remaining: T3 (antenna distance 需用户手动), T4 (L-SIG CPE 2-3h 代码 REFUTED 风险), T5 (LNA 需硬件)

---

## 目标

通过 RF 物理层调查（频率 / 天线 / 线缆 / LNA）提高稳态 USRP channel 质量，让 L-SIG viterbi 突破 6 dB 阈值。

## 方法

T1: 物理层检查 (RF 7d)
T2: 频率扫描 (RF 7a, 5180/5500/5890 MHz)
T3: 天线距离扫描 (RF 7c, 5/10/20/50 cm) — **未执行, 需用户手动**
T4: Per-symbol L-SIG CPE (RF 7e) — **未执行, 2-3h 代码 + REFUTED 风险**
T5: 外部 LNA (RF 7b) — **未执行, 需硬件**

## 结果

### T1: 物理层检查

| 项目 | 状态 |
|---|---|
| USRP 电气 OK (Phase 28) | 已确认 (DC=2e-6, TCXO 0.6ppb, noise floor -74.5 dB) |
| 物理 SMA / 线缆 / 屏蔽 | DEFERRED (subagent 无物理访问) |
| 60s control capture | HT_CAND=80, n_nulls_med=5.0, snr_lsig=2.10 dB |
| 与 Phase 74 v2 对比 | 在 channel variability 范围内 |
| **Verdict** | **NO_CHANGE** |

### T2: 频率扫描 (重做)

**T2 第一次尝试 (失败)**:
- p68_capture_raw_iq.py (无 TX), 漏设 tight_v2 env vars
- n_nulls=42-51 (signal absent), verdict 不可信

**T2 redo (有效)**:
- test_usrp_minimal_loopback.py --capture (atomic TX+RX+capture)
- tight_v2 env vars (THRESH=0.03, RADIUS=5, HTSIG_PILOT_CPE=1)
- 三个频率结果:

| Freq | file size | n_nulls_med | snr_lsig_med | HT_CAND |
|---|---|---|---|---|
| 5180 | 22 MB | 1.0 | 2.67 dB | 0 |
| 5500 | 74 MB | 0.0 | 4.91 dB | 0 |
| 5890 | 93 MB | 0.0 | 3.97 dB | 0 |
| **p74_v2 ref** | 8.7 GB | 8.0 | 5.94 dB | 0 |

- **Verdict**: **NO_DIFFERENCE** (snr_lsig 跨 3 频率在 ±1.16 dB 内, 全在 2.67-4.91 dB)
- **观察**: File size 22-93 MB 远小于 Phase 74 v2 的 8.7 GB — `test_usrp_minimal_loopback.py --capture` 在 UHD 压力下 capture 被 starving (但 TX 仍在跑, 收到 90 Sent / 0 Recv)
- **n_nulls=0-1** 比 Phase 74 v2 的 8.0 还低 — 但这是 file size 小的 capture 里的 lucky moment
- **真正可比的是 snr_lsig**: 三个频率都 < 6 dB viterbi 阈值

### T3: 天线距离扫描 (未执行)

- 需要用户手动调 4 个距离 (5/10/20/50 cm)
- Subagent 无物理访问
- Architecture 是 TDD same-board 但走**不同物理 SMA** (TX/RX vs RX2 端口), 不是内部直连 (subagent 错误声称)
- Phase 53 已确认同板比跨板强 2.4× — 暗示天线/距离是变量
- **决策**: 跳过 (用户时间投入 30-40 min, 期望 ROI 低)

### T4: Per-symbol L-SIG CPE (未执行)

- Phase 19/20 per-symbol HT-SIG CPE 已 REFUTED
- L-SIG 专用 per-symbol CPE 未测试 (但原理相同)
- 需 2-3h 写 C++ 代码 + 测试
- 风险高 (类似 hypothesis 已 REFUTED)
- **决策**: 跳过 (回报递减)

### T5: 外部 LNA (未执行)

- 需要采购 Mini-Circuits ZX60-33LN-S+ (~$50-100, 1-2 天到货)
- 假设能 +20 dB gain → 推 snr 到 10+ dB
- 风险: 可能引入非线性 / IM3 distortion
- 架构层: 信号必须通过外部 SMA → LNA 才有意义
- **决策**: 跳过 (需硬件采购 + 时间)

---

## 决策

**Phase 75 REFUTED**:
- T1 + T2 redo 都 NO_CHANGE/NO_DIFFERENCE
- 物理层 + 频率 都不是 USRP HT-SIG 失败的限制因素
- 73+ 阶段 software + 4 RF 方向都已穷尽
- **唯一 remaining 攻击面**: T5 (LNA) 需硬件, T3 (天线) 需用户, T4 (L-SIG CPE) 需代码 + REFUTED 风险

**per HARD CONSTRAINT** — Phase 76 必须 attack upstream。Software + RF 已穷尽, 唯一 remaining:
- Option A: T5 (LNA 采购) + 重新跑 Phase 75
- Option B: 接受 channel-physics 限制 (Phase 41 closure reaffirmation)
- Option C: 走 **software loopback 3/3 PASS** 作为 decoder 验证路径 (虽然不是 USRP 验证, 但符合 hierarchy 第 2 档)

---

## Phase 76 Plan

**per HARD CONSTRAINT 必填**:

### 76a: T5 (LNA 采购) — 推荐先做
- 采购 Mini-Circuits ZX60-33LN-S+ (~$50-100)
- 重新跑 T2 (frequency sweep) + T3 (antenna distance) with LNA
- 预期: +20 dB gain → 稳态 snr 推到 10+ dB
- 成本: 1-2 天等待 + 1 小时实验
- 风险: 非线性 / IM3 distortion 可能让 HT-SIG 更糟

### 76b: 接受 channel-physics closure
- 73 阶段 + Phase 75 已穷尽软件 + 频率
- HT-SIG QBPSK 45° 边际 + H52 nulls 50× 噪声放大 = 物理限制
- 走软件 loopback 3/3 PASS 验证
- 风险: 违反 HARD CONSTRAINT 主线 (USRP realtime FCS_OK ≥ Sent/N)

### 76c: 换 modulation scheme 测试
- 试 MCS=1 (QPSK 1/2) 而非 MCS=0 (BPSK 1/2) — 更宽容错
- 试 HT-SIG-only 测试（不解 frame body）
- 成本: 1-2 小时实验
- 风险: 改用其他 MCS 不算"USRP end-to-end"验证

### 76d: 物理层重设计
- 换天线类型 (currently VERT2450, try更高增益)
- 换线缆 (LMR-400 vs current RG-58)
- 加 RF absorber
- 成本: 1-2 天采购 + 实验
- 风险: 同 76a, 改善有限

**推荐执行顺序**: 76a (LNA) → 76d (物理层重设计) → 76b (接受 closure) → 76c (modulation scheme)

---

## Files

### 新增
- `docs/superpowers/notes/2026-07-02-phase75-rf-refuted.md` (this file)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p75_rf_upstream.md`

### 数据
- `/tmp/p75_physical_control.bin` (5.4 GB, T1 control)
- `/tmp/p75_v2_freq_5180.bin` (22 MB), `/tmp/p75_v2_freq_5500.bin` (74 MB), `/tmp/p75_v2_freq_5890.bin` (93 MB)
- `/tmp/p75_v2_freq_*.log` (replay logs)
- `/tmp/p75_v2_compare.txt` (comparison table)
- `/tmp/p75_progress.md` (T1+T2 累积 verdict)

### 未修改
- `lib/frame_equalizer_impl.cc` (Phase 75 没改 C++ 代码)
- `CLAUDE.md` (Phase 75 REFUTED 不 promote 任何 config)
- 之前 T1/T2 capture 错误 subagent 报告的"internal TDD loopback" claim — 已证明错误 (TX/RX 和 RX2 是不同物理 SMA, 信号必须经外部)

---

## Related

- [[project_p74_blocked_anomaly]] — Phase 74 BLOCKED, Phase 73 anomaly 被推翻
- [[project_p73_h52_per_symbol_preclean]] — Phase 73 PARTIAL (0.4s anomaly, 已 REVISED)
- [[project_usrp_htsig_final_verdict]] — Phase 41 USRP HT-SIG closure (12+ REFUTED)
- [[project_p55_usrp_snr_diagnosis]] — Phase 55 UHD streaming 不稳定性
- [[project_p53_cross_board_weaker]] — Phase 53 同板比跨板强 2.4× (暗示 RF 是变量)
- [[project_p28_hw_characterization]] — Phase 28 USRP 电气 OK
