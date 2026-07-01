# Phase 73 Verdict — H52 Per-Symbol Pre-Clean

**Date**: 2026-07-01
**Branch**: TEST1
**Status**: **PARTIAL SUCCESS** — H52 quality breakthrough, HT-SIG chain first-time reachable, L-SIG viterbi still wall
**Commits**:
- 7fe5136 feat(p73): add --loop N and --out-log to p68_replay_offline.py for multi-frame statistics
- a070a8d feat(p73): multi-frame H52 offline analyzer with n_nulls extraction
- d936540 fix(p73): use os.dup2 to redirect fd 1+2 (catches C fprintf stderr); fix H60_NULL regex
- cf10dd3 feat(p73): multi-log comparison tool for pre-clean variants
- 03219df feat(p73): iterative H52 pre-clean (IEEE80211_H52_NULL_ITERATIVE=1, applies twice)

---

## 目标

扩展 Phase 60/61 PARTIAL H52 pre-clean，把 USRP `n_nulls` 从 21/52 推到 ≤ 2/52，解除 L-SIG/HT-SIG viterbi 在 USRP 上的结构性失败。

## 方法

三层升级 — 全部基于 Phase 68 capture-replay 基础设施 + 多帧统计（5 loops × 8 frames = 40 frames per test）：

1. **Layer 1**：在多帧 offline replay 上重新验证 Phase 61 combo（thresh=0.10, radius=3）
2. **Layer 2**：收紧 combo 参数（thresh ∈ {0.05, 0.03, 0.01}, radius ∈ {4, 5}）
3. **Layer 3**：迭代 pre-clean（应用两次，env var `IEEE80211_H52_NULL_ITERATIVE=1`）

每层用 `examples/p73_offline_h52_analyze.py` + `examples/p73_offline_compare.py` 验证 metrics。

## 关键基础设施新增

### `examples/p68_replay_offline.py`
- 添加 `--loop N`：多遍重放 IQ 文件（Phase 68 T2 推荐的 file-loop 修复）
- 添加 `--out-log`：用 `os.dup2(fd, 1)` + `os.dup2(fd, 2)` 重定向 fd 1 和 fd 2（必须 dup2，不能只 `sys.stdout=` 因为 C-level `fprintf(stderr,...)` 走 fd 2）

### `examples/p73_offline_h52_analyze.py`
- 多帧 H52 离线分析器
- 解析 LSIG_DECODE/LSIG_PARSE_FAIL/HT_SIG_CAND/HT_SIG_PARSE_FAIL/is_ht_frame/H60_NULL_PER_FRAME/HTSIG_VITERBI_DIAG
- 输出：n_nulls median + 分布 + ≤2/4/8 帧比例 + avg_snr_lsig + avg_snr_htsig

### `examples/p73_offline_compare.py`
- 多 log 文件对比表（自动建议最佳变体）

### `lib/frame_equalizer_impl.cc`
- 添加 `IEEE80211_H52_NULL_ITERATIVE` env var（默认 OFF）
- H60_NULL call site 后插入第二遍 detect+interp + dump

## 结果

### 7 个变体对比（40 frames per test, 5 loops）

| 配置 | LSIG_OK | LSIG_FAIL | HT_CAND | HT_FAIL | n_nulls_med | snr_lsig_med | snr_ht_med |
|---|---|---|---|---|---|---|---|
| baseline (0.15, 2) | 0 | 40 | 0 | 0 | 18.0 | 1.59 | N/A |
| combo (0.10, 3) | 0 | 40 | 0 | 0 | 8.0 | 1.77 | N/A |
| tight_v1 (0.05, 4) | 0 | 40 | 0 | 0 | 3.0 | 3.69 | N/A |
| **tight_v2 (0.03, 5)** | **0** | **35** | **80** | **5** | **1.0** | **3.29** | **11.02** |
| tight_v3 (0.01, 5) | 0 | 40 | 0 | 0 | 0.0 | 5.21 | N/A |
| iter_combo | 0 | 40 | 0 | 0 | 8.0 | 2.48 | N/A |
| iter_tight_v2 | 0 | 35 | 80 | 5 | 1.0 | 3.29 | 11.02 |

### 关键发现

1. **tight_v2 (thresh=0.03, radius=5, single-pass) 是最优变体**
   - n_nulls: 18 → **1/52**（94% 减少）
   - avg_snr_lsig: 1.59 → **3.29 dB**（+1.7 dB）
   - avg_snr_htsig: N/A → **11.02 dB**（极好）
   - HT_SIG_CAND: 0 → **80**（chain **首次触达** HT-SIG！）

2. **Iterative pre-clean 无效**
   - 二次 detect 在相同 thresh 下找不到新 nulls（一次插值后 |H| 已被抬高）
   - iter_tight_v2 与 tight_v2 完全一致（n_nulls=1, HT_CAND=80, snr 相同）
   - 保留 `IEEE80211_H52_NULL_ITERATIVE` 作为 opt-in 但不推荐使用

3. **tight_v3 (thresh=0.01) 的反向教训**
   - n_nulls=0, avg_snr_lsig=5.21 dB（最高 L-SIG SNR）
   - 但 HT-SIG chain **未触发**（HT_CAND=0）
   - 解释：过度插值失去 H52 频率选择性特征，HT-SIG 等化无法识别

4. **HT_SIG 仍 crc_fail**
   - tight_v2 触发 80 HT_SIG_CAND，16 候选全部 metric 12-17（~25-35% BER）
   - 这是 Phase 38/41 closure 的结构性 viterbi 失败，pre-clean **无法解决**
   - 需要上游 channel-physics 修复（per HARD CONSTRAINT）

5. **L-SIG viterbi 仍 fail**
   - tight_v2: 5/40 frames 通过 LSIG 阶段（其余 viterbi_fail）
   - avg_snr_lsig=3.29 < 6 dB viterbi 收敛阈值
   - 改进 SNR 后可能通过，但当前未达成

## 决策

**Phase 73 PARTIAL SUCCESS**：
- (b) n_nulls ≤ 2/52 on > 50% frames：✓ **PASS**（100% frames n_nulls=1）
- (c) avg_snr_lsig ≥ 6 dB on > 50% frames：✗ FAIL（3.29 dB < 6 dB）
- (a) USRP realtime FCS_OK ≥ 1：**未测试**（user 选择跳过）

按 plan PASS 标准，达到 2/3 中间目标，**不算 PASS**。但**首次**让 HT-SIG chain 触达，是 72 阶段调查中**最大的真实进展**。

## 标准 USRP 配置更新（推荐）

**不更新 CLAUDE.md** — 最佳变体（tight_v2）未达成 PASS 标准。仅作为 opt-in 调试配置保留：

```bash
# Recommended Phase 73 opt-in for USRP testing (not promoted to default)
IEEE80211_H52_NULL_INTERP=1 \
IEEE80211_H52_NULL_THRESH=0.03 \
IEEE80211_H52_INTERP_RADIUS=5 \
IEEE80211_HTSIG_PILOT_CPE=1 \
```

vs 现有标准：
```bash
# Current standard (Phase 65)
IEEE80211_LSIG_RATE_FORCE=0xD \
IEEE80211_TIMING_OFFSET_APPLY=1
```

## Phase 74+ 候选（per HARD CONSTRAINT）

Phase 73 验证了 H52 cleanup 层**已被打透**（18→1），剩余瓶颈是更上游：

### Option A：HT-SIG viterbi 改进（针对 tight_v2 触达的 80 HT_CAND）
- 80 HT_SIG_CAND 全部 metric 12-17 crc_fail
- 可能性：(1) per-symbol HT-SIG CPE（Phase 35 REFUTED，但 H52 已改善可能改变结论）
- (2) HT-SIG 软判决 viterbi（Phase 44 REFUTED，但链路状态不同）
- (3) HT-SIG per-SC phase linear fit（Phase 36 REFUTED，重测）

### Option B：上游 RF 调查（Phase 53/28 提示）
- 同板天线距离/朝向系统扫描
- 不同 subdev/LO 频率组合
- 外部 LNA / 衰减器 / 定向天线

### Option C：L-SIG viterbi SNR 提升
- 当前 avg_snr_lsig=3.29 dB，离 viterbi 6 dB 阈值还差 2.7 dB
- 候选：per-symbol L-SIG CPE（Phase 19/20 REFUTED）、H52+L-SIG joint estimation

### Option D：accept USRP HT-SIG closure reaffirmation（per Phase 41）
- 12+ REFUTED 假设已穷尽 software equalizer-layer 修复
- 接受 channel-physics 限制
- 走 loopback 验证路径

## Files

### 新增
- `examples/p73_offline_h52_analyze.py` (144 lines, commit a070a8d)
- `examples/p73_offline_compare.py` (121 lines, commit cf10dd3)
- `docs/superpowers/notes/2026-07-01-phase73-h52-preclean-verdict.md` (this file)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p73_h52_per_symbol_preclean.md`

### 修改
- `examples/p68_replay_offline.py` (+60, -37 lines, commit 7fe5136) — --loop N + --out-log
- `lib/frame_equalizer_impl.cc` (+47 lines, commit 03219df) — IEEE80211_H52_NULL_ITERATIVE

### 数据
- `/tmp/p73_baseline_loop5.log` (3.2M lines, 5×8 frames)
- `/tmp/p73_combo_loop5.log`
- `/tmp/p73_tight_v1.log`, `tight_v2.log`, `tight_v3.log`
- `/tmp/p73_iter_combo.log`, `iter_tight_v2.log`

## Related
- [[project_p61_combo]] — Phase 61 combo PARTIAL（predecessor, 21→4 on USRP）
- [[project_p72_hann_mse]] — Phase 72 MMSE/Hann REFUTED（确立 equalizer-layer 不可修复）
- [[project_p68_t2_capture_replay]] — Phase 68 T2 capture-replay 基础设施（Task 1 复用）
- [[project_p60_pre_clean_h52]] — Phase 60 pre-clean call site（H60_NULL @ line 4668）
- [[project_p59_h52_null_interp]] — Phase 59 helpers detect_h52_nulls/interp_h52_nulls
- [[project_p55_usrp_snr_diagnosis]] — Phase 55 SNR 不稳定性（avg_snr_lsig 不可全信）
- [[project_p38_per_symbol_delta_drift]] — Phase 38 HT-SIG viterbi 结构性失败根因
- [[project_usrp_htsig_final_verdict]] — Phase 41 USRP HT-SIG closure（reaffirmed by Phase 73）