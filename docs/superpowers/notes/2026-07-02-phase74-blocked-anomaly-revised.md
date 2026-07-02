# Phase 74 Verdict — BLOCKED (Phase 73 Anomaly Discovered)

**Date**: 2026-07-02
**Branch**: TEST1
**Status**: **BLOCKED** — Phase 73 "breakthrough" was a 0.4s short-burst capture artifact, not a reproducible steady-state. Steady-state USRP channel returns to n_nulls=4-8, snr_lsig=1.8-5.9 dB, HT_CAND=0 (consistent with prior phases). Phase 75 must attack upstream per HARD CONSTRAINT.

---

## 关键发现（MAJOR）

**Phase 73 PARTIAL 结论被推翻**:

| 指标 | Phase 73 verdict (Jun 30) | Phase 74 v1 (60s full) | Phase 74 v2 (60s full) |
|---|---|---|---|
| Capture 文件 | 116 MB | 9.0 GB | 9.0 GB |
| 实际采集数据 | **0.4s** (99% UHD overflow) | 60s (100% delivery) | 60s (100% delivery) |
| **HT_CAND** | **80** | **0** | **0** |
| n_nulls_med | 1.0 | 4.0 | 8.0 |
| snr_lsig_med | 3.29 dB | 1.84 dB | 5.94 dB |
| snr_htsig_med | 11.02 dB | N/A | N/A |

**根因分析**:
- Phase 68 capture (116 MB) 是 60s wall-clock 但 99% UHD overflow → 只捕到 0.4s 实际 IQ
- 那 0.4s 撞到 channel 的好瞬间，触发了 HT-SIG 链
- Phase 74 v1 + v2 都是 60s 100% delivery 的完整 capture
- 稳态 USRP channel 是 n_nulls=4-8, snr_lsig=1.8-5.9 dB，**与 Phase 38/41/64/65 的稳态完全一致**
- Phase 73 的"首次让 HT-SIG 链触达"是**短 burst anomaly**，不是 software 突破

**对 Phase 73 结论的修正**:
- ❌ "tight_v2 配置 (THRESH=0.03, RADIUS=5) 是 73 阶段最大真实进展"
- ✓ "tight_v2 配置在 0.4s 短 burst 上展示潜力，但稳态 60s 仍失败"

**对项目整体的意义**:
- 72 阶段 software equalizer-layer 调查的 REFUTED 结论保持有效
- HT-SIG viterbi 仍是 channel-physics 限制（per Phase 41 closure）
- **唯一未充分探索的方向是 RF 上游**

---

## 目标

重新 capture Phase 73 tight_v2 baseline，验证 80 HT_CAND 是否在 60s 完整 capture 上可重现。

## 方法

两次 60s 完整 capture（用 Phase 58 T3 验证的 16MB recv_buff_size / num_recv_frames=256 配置），每次后跑 tight_v2 reference replay。

## 结果

### 1st attempt (`/tmp/p74_raw_iq.bin`, 9.0 GB)
- USRP 100% delivery, 60s
- tight_v2 replay (5 loops × 8 frames = 40 frames)
- HT_CAND=0, n_nulls_med=4.0, snr_lsig_med=1.84 dB

### 2nd attempt (`/tmp/p74_raw_iq_v2.bin`, 9.0 GB)
- 同样 60s 100% delivery
- tight_v2 replay
- HT_CAND=0, n_nulls_med=8.0, snr_lsig_med=5.94 dB

### 关键观察
- 两次 capture HT_CAND 都是 0（与 Phase 73 reference 的 80 完全相反）
- n_nulls 在 4-8 之间波动（vs Phase 73 reference 的 1）
- snr_lsig 在 1.8-5.9 dB 之间波动（vs Phase 73 reference 的 3.29）
- 第二次 capture 的 snr 反而比第一次高 4.1 dB（5.94 vs 1.84），n_nulls 反而更差（8 vs 4）
- **没有看到 Phase 73 reference 那种"all metrics simultaneously good"的特征**

### 解释
Phase 73 reference 短 burst 的 0.4s 是 channel 处于"低 nulls + 适中 SNR"的有利瞬间：
- 4-8 SCs of |H| < 0.03 (nulls)
- avg_snr_lsig=3.29 dB
- 这种组合让 viterbi 偶尔能解码

稳态 60s 包含各种 channel 状态：
- 一些瞬间像 Phase 73 短 burst
- 一些瞬间更差（n_nulls>20, snr<2 dB）
- 整体来看没有 Phase 73 那种 lucky moment 集中

---

## 决策

**Phase 74 BLOCKED — Phase 73 anomaly**:
- 原计划（重测 Phase 35/36/44 on top of tight_v2）**前提不存在**（HT_CAND=0，HT-SIG 链触达不到）
- Phase 73 tight_v2 配置在稳态 60s 上**不重现** 80 HT_CAND
- 实际上"tight_v2 把 n_nulls 从 18 压到 1"的描述只在 0.4s 短 burst 上成立

**per HARD CONSTRAINT** — Phase 75 必须 attack upstream。Software equalizer-layer 已穷尽（73 阶段 + 12+ REFUTED hypothesis）。

---

## Phase 75 Plan — RF Upstream Investigation

软件层已穷尽。唯一未充分探索的是 RF 物理层。Phase 28 验证了 USRP 电气 OK（DC=2e-6, TCXO 0.6ppb, noise floor -74.5 dB），但**系统级 RF 优化未做**。

### 7a: 频率扫描（最便宜，先做）
- 测试 5 GHz UNII band 多个频道：5180 / 5500 / 5890 MHz
- 假设：某些频段可能有更好的 multipath / Rician profile
- 成本：5 min 切换频率 + 60s capture
- 预期：找到 snr 最高的 sweet spot

### 7b: 外部 LNA 接入
- USRP 噪声地板 -74.5 dB，加 LNA 可推到 -85+ dB → SNR 提升 10+ dB
- 需要硬件：Mini-Circuits ZX60-33LN-S+ (~$50, 5 GHz, 20 dB gain) 或类似
- 成本：硬件采购 ~$50-100
- 风险：可能引入非线性 / IM3 distortion

### 7c: 天线距离 / 朝向系统扫描
- Phase 53 验证了同板 (A:0/A:0) 比跨板 (A:0/B:0) 强 2.4×
- 但**没系统扫过同板内部的天线距离**（5 cm / 10 cm / 20 cm / 50 cm）
- 假设：近距离高 multipath，远距离低信号 → 找 sweet spot
- 成本：手动调天线 + capture（每次 5 min）
- 风险：低，纯物理测量

### 7d: 物理层检查
- SMA 连接器 torque（应该是 8 in-lbs）
- 同轴线缆损耗（5 GHz 下每米 1-2 dB）
- 屏蔽 / RF absorber 放置
- 接地
- 成本：30 min 检查 + 可能换线
- 风险：可能发现 "smoking gun"（松连接器 / 损坏线缆）

### 7e: 并行软件线 — per-symbol L-SIG CPE
- Phase 19/20 per-symbol CPE 是 HT-SIG 上的，REFUTED
- 没用过 L-SIG 专用 per-symbol CPE
- 新稳态 baseline (n_nulls=4-8, snr=1.8-5.9) 上测试
- 成本：~2-3 hours（需要写新代码 + 测试）
- 风险：中等（Phase 19/20 REFUTED 类似 hypothesis）

### 7f: 接受 channel-physics 限制（最差选项）
- per Phase 41 closure：HT-SIG QBPSK 45° 边际 + H52 50× 噪声放大 = 物理限制
- 接受 USRP HT-SIG 不可解
- 走 loopback 验证路径
- 风险：违反 HARD CONSTRAINT（"不是可接受最终结果"）

**推荐执行顺序**: 7d（30 min 物理检查）→ 7a（频率扫描）→ 7c（天线扫描）→ 7b（LNA）→ 7e（软件）

---

## Files

### 新增
- `docs/superpowers/notes/2026-07-02-phase74-blocked-anomaly-revised.md` (this file)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p74_blocked_anomaly.md`

### 数据（保留供 Phase 75 使用）
- `/tmp/p74_raw_iq.bin` (9.0 GB, 1st attempt 60s)
- `/tmp/p74_raw_iq_v2.bin` (9.0 GB, 2nd attempt 60s)
- `/tmp/p73_tight_v2.log` (222 MB, 1st attempt replay)
- `/tmp/p73_tight_v2_v2.log` (200 MB, 2nd attempt replay)

### 修改
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p73_h52_per_symbol_preclean.md` — 修正 "首次触达 HT-SIG" 描述为 "0.4s 短 burst anomaly"
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md` — Phase 73 index 修正

### 不修改
- `CLAUDE.md` 标准 USRP test config（Phase 73 没通过，未 promote）
- `lib/frame_equalizer_impl.cc`（Phase 74 没改代码）
- `examples/`（除了已存在的分析器）

---

## Related

- [[project_p73_h52_per_symbol_preclean]] — Phase 73 PARTIAL 结论（被 Phase 74 推翻）
- [[project_p55_usrp_snr_diagnosis]] — Phase 55 SNR 不稳定性（capture 文件 99% overflow 是常见现象）
- [[project_usrp_htsig_final_verdict]] — Phase 41 USRP HT-SIG closure（reaffirmed by Phase 74 finding）
- [[project_p38_per_symbol_delta_drift]] — Phase 38 HT-SIG viterbi 结构性失败根因
- [[project_p53_cross_board_weaker]] — Phase 53 同板 vs 跨板（RF 7c 基础）
- [[project_p28_hw_characterization]] — Phase 28 USRP 电气 OK（RF 7d 基础）
