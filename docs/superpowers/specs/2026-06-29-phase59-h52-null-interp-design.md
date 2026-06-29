# Phase 59 Design — Per-SC H52 Null Detect + 邻域插值（Unblock USRP FCS_OK 1/3 帧）

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: Design approved (5 sections reviewed 2026-06-29)
**Goal**: 从 `FCS_OK=0/30=0%` 提升到 `FCS_OK ≥ Sent/3 ≈ 33%` 单 run（35s）

## Background

Phase 41 已关闭 USRP HT-SIG viterbi 调查，鉴定根因：
- **Hhdr52 通道零陷**（|H| ∈ [0.02, 0.14] at null SCs）→ equalize 时放大 50× 噪声
- HT-SIG 星座被压到 REAL 轴 → QBPSK 旋转检测失败
- 12 个 equalizer 层假设已 REFUTED；这是**通道物理限制**

Phase 41 关闭时给出的 2 条路径：
- (a) 接受限制，用软件 loopback 验证 decoder
- (b) **新架构方案**：per-SC Hhdr52 null detection + H52 插值

Phase 59 采用路径 (b)。

## Section 1: 目标与成功标准

### 目标
让 USRP realtime 单 35s run 中 `FCS_OK / Sent ≥ 1/3`。

### 成功标准
1. **主目标**：单 35s run 中 `FCS_OK ≥ Sent/3`
2. **回归门槛（不可破坏）**：
   - 软件 loopback 3/3 PASS 仍维持
   - 现有 USRP env vars 保留：`IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14 IEEE80211_TIMING_OFFSET_APPLY=1`
   - `LSIG_OK` 数量不下降（当前 4-7/run）
3. **次要观察**：HT_SIG_CAND 应从 0 提升到 ≥ Sent/3；30-min soak 至少 1/3 runs 达到 1/3 帧

### 非目标
- 不重新调查已被 REFUTED 的 12 个 equalizer 层假设
- 不改 USRP UHD streaming 配置（Phase 58 已达 MARGINAL）
- 不改 RX chain 上游（sync_long / sync_short / frame_equalizer 入口）
- 不引入新的 RF 损伤假设

## Section 2: 架构总览

### 数据流（修改点用 🔧 标出）

```
USRP air → sync_short → sync_long (Phase 33 FRAME_START_BASE=174)
  → ht_symbol_splitter (Phase 40 已对齐)
  → frame_equalizer:
      ├─ extract H52 from L-LTF0/L-LTF1  [Phase 33: 14-sample shift applied]
      ├─ 🔧 Phase 59a: H52 null detector (新, 标记 |H[i]|<threshold 的 SC)
      ├─ 🔧 Phase 59b: H52 邻域插值 (新, 填补被标记的 SC)
      ├─ equalize HT-SIG0/1 with H52_corrected  [路径不再变]
      └─ viterbi_decode_133_171  [Phase 37 已验证, 不动]
  → decode_mac → FCS check
```

### 关键架构原则

1. **最小修改面**：仅在 `frame_equalizer_impl.cc` 的 H52 估计后、HT-SIG equalize 之前插入 2 个新函数
2. **不重写 H52 估计**：复用 Phase 33 修过的 L-LTF0/L-LTF1 extraction
3. **不重写 viterbi**：Phase 37 Layer 1 metric=0 已确认正确
4. **环境变量控制**：所有新逻辑默认关闭（`IEEE80211_H52_NULL_INTERP=0`）
5. **可观测性**：`IEEE80211_H52_NULL_DUMP=1`（默认 OFF）输出 null SC 数量、位置、插值后 |H| 分布

### 新增 / 修改文件

| 文件 | 改动 | 类型 |
|---|---|---|
| `lib/frame_equalizer_impl.h` | 增加 `d_h52_null_thresh`, `d_h52_interp_radius` 字段，访问函数 | 改 |
| `lib/frame_equalizer_impl.cc` | 增加 `detect_h52_nulls()`, `interp_h52_nulls()` 函数 + 调用 | 改 |
| `examples/test_h52_null_interp_synthetic.py` | 单元测试（detect / interp / e2e 三种 mode） | 新 |
| `examples/p59_h52_null_dump_analyze.py` | 离线分析器（与 `p35_htsig_analyze.py` 模式） | 新 |

### 关键参数（env vars）
- `IEEE80211_H52_NULL_INTERP=1`（默认 0，opt-in）— 启用插值
- `IEEE80211_H52_NULL_THRESH=0.15`（默认 0.15）— 低于此值视为 null
- `IEEE80211_H52_INTERP_RADIUS=2`（默认 2）— 邻域半径（左右各 2 SC）
- `IEEE80211_H52_NULL_DUMP=1`（默认 0）— 诊断输出

## Section 3: 核心算法

### 3.1 H52 Null Detection

```cpp
std::vector<int> detect_h52_nulls(const std::vector<std::complex<float>>& h52,
                                   float thresh = 0.15f) {
    std::vector<int> nulls;
    for (size_t i = 0; i < h52.size(); i++) {
        if (std::abs(h52[i]) < thresh) {
            nulls.push_back(i);
        }
    }
    return nulls;
}
```

- 阈值 0.15 基于 Phase 38 数据：null SC |H| ∈ [0.02, 0.14]，强 SC |H| ∈ [0.5, 1.0]
- 跳过 DC (i=0 总是 0 magnitude)
- v1 不区分导频 SC；后续若与导频冲突再扩展

### 3.2 H52 邻域插值（均值法）

```cpp
std::vector<std::complex<float>> interp_h52_nulls(
    const std::vector<std::complex<float>>& h52,
    const std::vector<int>& nulls,
    int radius = 2) {
    auto result = h52;
    for (int null_idx : nulls) {
        std::complex<float> sum = 0;
        int count = 0;
        for (int d = 1; d <= radius; d++) {
            int left  = null_idx - d;
            int right = null_idx + d;
            if (left >= 0 && std::find(nulls.begin(), nulls.end(), left) == nulls.end()) {
                sum += h52[left];
                count++;
            }
            if (right < (int)h52.size() &&
                std::find(nulls.begin(), nulls.end(), right) == nulls.end()) {
                sum += h52[right];
                count++;
            }
        }
        if (count > 0) {
            result[null_idx] = sum / (float)count;
        }
        // else: 邻居全 null（cluster null）→ 保持原值（不更糟）
    }
    return result;
}
```

- 使用**均值**而非线性插值（避免 phase 不连续）
- 半径 2 = 最多 4 个邻居
- cluster null 退化：保持原值（行为不差于 baseline）

### 3.3 调用点集成

```cpp
if (d_h52_null_interp_enabled) {
    auto nulls = detect_h52_nulls(h52, d_h52_null_thresh);
    if (d_h52_null_dump_enabled) {
        USRP_LOG("[H52_NULL] n_nulls=%zu/%zu at indices:", nulls.size(), h52.size());
        for (int i : nulls) {
            USRP_LOG("  [%d] |H|=%.3f", i, std::abs(h52[i]));
        }
    }
    h52 = interp_h52_nulls(h52, nulls, d_h52_interp_radius);
}
```

## Section 4: 验证方案

### 4.1 单元验证（软件 loopback，必过）

| 测试 | 命令 | Pass 条件 |
|---|---|---|
| 单元 1：detect 精度 | `python examples/test_h52_null_interp_synthetic.py --mode detect` | 6/6 注入 null 识别，0 误判 |
| 单元 2：interp 精度 | 同上 `--mode interp` | 插值后 |H[i]| ∈ [0.4, 0.8]（vs 0.05），argH 误差 < 0.3 rad |
| 单元 3：e2e | 同上 `--mode e2e` | HT-SIG viterbi metric=0（env var ON） |
| 回归：loopback | `python examples/test_direct_loopback.py` | 3/3 PASS |
| 回归：HT-SIG viterbi | `python examples/test_htsig_viterbi_synthetic.py` | 3/3 PASS |
| 回归：L-SIG viterbi | `python examples/test_lsig_viterbi_synthetic.py` | 3/3 PASS |
| 回归：H 估计 | `python examples/test_h_estimation_synthetic.py` | 5/5 PASS |

### 4.2 USRP 端到端验证（主目标）

| 测试 | 命令 | Pass 条件 |
|---|---|---|
| 短 run 35s | 见 4.3 标准命令 | FCS_OK ≥ 1（3+ 帧解码） |
| 30-min soak | Phase 58 soak harness + env var | 至少 1/3 runs 达到 1/3 帧 |
| 三次独立 run | 重启 UHD × 3 | 至少 1/3 独立 run 达到 1/3 帧 |
| 离线 dump 分析 | `IEEE80211_H52_NULL_DUMP=1` + `p59_h52_null_dump_analyze.py` | null SC 数量 < 12 |

### 4.3 标准 USRP 测试命令

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
PYTHONPATH=./build/python/bindings:./python:./examples \
IEEE80211_LSIG_RATE_FORCE=0xD \
IEEE80211_LLTF_OFFSET_CORRECT=14 \
IEEE80211_TIMING_OFFSET_APPLY=1 \
IEEE80211_H52_NULL_INTERP=1 \
taskset --cpu-list 0-1 \
timeout 110 /home/hy/conda/envs/gnuradio/bin/python \
test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 35 --warmup 60 \
> /tmp/p59_e2e.log 2>&1
```

### 4.4 PASS / FAIL 判定

| 结果 | 含义 | 动作 |
|---|---|---|
| **PASS** | 单 run FCS_OK/Sent ≥ 1/3，软件 loopback 不退化 | 写绿色 verdict doc；MEMORY.md 更新 |
| **MARGINAL** | 单 run 1/3 < FCS_OK/Sent < 1/10，HT_SIG_CAND 提升 > 0 | 调阈值（0.15→0.20）或半径（2→1）重试一次 |
| **FAIL** | FCS_OK=0，HT_SIG_CAND=0，软件 loopback 退化 | 立即 revert（env var OFF 时与 baseline 一致），回 Phase 41 closure |

### 4.5 文档与可观测性

- **Verdict 文档**：`docs/superpowers/notes/2026-06-29-phase59-h52-null-interp-verdict.md`
- **离线分析器**：`examples/p59_h52_null_dump_analyze.py`（仿照 `p35_htsig_analyze.py` 模式）
- **MEMORY.md 更新**：成功路径后追加 Phase 59 行
- **Commits**：每个 Task 一个 commit，Co-Authored-By trailer 保留

### 4.6 明确禁止

- ❌ 修改 `viterbi_decode_133_171`（Phase 37 已验证正确）
- ❌ 修改 L-LTF0 提取逻辑（Phase 33 修复，勿触碰）
- ❌ 修改 ht_symbol_splitter（Phase 40 已验证对齐）
- ❌ 同时改 USRP UHD 配置（Phase 58 MARGINAL，不扰动）
- ❌ 重新尝试 12 个 REFUTED 假设中的任何一个

## Section 5: 实施计划（5 Task，2-3 小时）

### Task 1: Spec 文档 + 单元测试脚手架
- **Files**:
  - Create: `docs/superpowers/specs/2026-06-29-phase59-h52-null-interp-design.md`（本文）
  - Create: `examples/test_h52_null_interp_synthetic.py`（脚手架 + Python 模拟实现）
- **Sub-steps**:
  1. Spec doc 提交（已通过 5 个 section 对话定稿）
  2. 写 Python 模拟 `detect_h52_nulls` 和 `interp_h52_nulls`（用于脚手架测试）
  3. 跑 `--mode detect`：6/6 注入 null 识别
  4. 跑 `--mode interp`：|H| ∈ [0.4, 0.8]
  5. Commit: `test(p59): H52 null interp synthetic test scaffolding`

### Task 2: C++ H52 null detector + interp helpers
- **Files**:
  - Modify: `lib/frame_equalizer_impl.h`（+ 2 函数声明 + 4 字段）
  - Modify: `lib/frame_equalizer_impl.cc`（+ 函数实现）
- **Sub-steps**:
  1. 读 `lib/frame_equalizer_impl.h` 找 H52 估计签名
  2. 加 `detect_h52_nulls()` + `interp_h52_nulls()` 声明
  3. 加 C++ 实现（不调 libm 之外）
  4. **make + make install**（项目强制约定）
  5. 跑单元测试 detect / interp mode
  6. Commit: `feat(p59): H52 null detector + interp helpers`

### Task 3: env vars + 调用点集成
- **Files**:
  - Modify: `lib/frame_equalizer_impl.cc`（constructor + 调用点）
- **Sub-steps**:
  1. constructor 加 4 个 `getenv`
  2. 查 `frame_equalizer::equalize` 入口（codegraph）
  3. 插入 `if (d_h52_null_interp_enabled) { h52 = detect+interp; }`
  4. **make + make install**
  5. 跑软件 loopback（env var OFF）：3/3 PASS
  6. 跑软件 loopback（env var ON）：3/3 PASS
  7. Commit: `feat(p59): wire up H52 null interp with opt-in env vars`

### Task 4: 单元测试完整 + 软件 loopback 回归
- **Files**:
  - Modify: `examples/test_h52_null_interp_synthetic.py`（3 mode 完整）
  - Read-only: 跑其他 regression
- **Sub-steps**:
  1. `--mode e2e`：端到端 HT-SIG viterbi metric=0（env var ON）
  2. `test_direct_loopback.py`：3/3
  3. `test_htsig_viterbi_synthetic.py`：3/3
  4. `test_lsig_viterbi_synthetic.py`：3/3
  5. `test_h_estimation_synthetic.py`：5/5
  6. Commit: `test(p59): H52 null interp unit tests + regression suite`

### Task 5: USRP E2E + 30-min soak + verdict
- **Files**:
  - Create: `examples/p59_h52_null_dump_analyze.py`
  - Create: `docs/superpowers/notes/2026-06-29-phase59-h52-null-interp-verdict.md`
  - Modify: MEMORY.md
- **Sub-steps**:
  1. 写 `p59_h52_null_dump_analyze.py`
  2. 跑单 run E2E（35s + env var ON）
  3. PASS → 跑 30-min soak
  4. MARGINAL → 调阈值/半径重试
  5. FAIL → revert，写 verdict（指明根因没解决）
  6. 写 verdict doc
  7. 更新 MEMORY.md
  8. Commit: `verdict(phase59): H52 null interp on USRP (PASS/MARGINAL/FAIL)`

### 任务依赖

```
Task 1 ─→ Task 2 ─→ Task 3 ─→ Task 4 ─→ Task 5
   (spec)  (helpers) (wire)  (test)  (E2E)
```

### 风险缓解

| 风险 | 检测点 | 缓解 |
|---|---|---|
| 算法引入新 bias | Task 4 单元测试精度不达标 | 改均值→中位数，扩 radius |
| 集成破坏 baseline | Task 3 软件 loopback 退化 | 立即 revert（env var 默认 OFF） |
| USRP 单 run 失败 | Task 5 FCS_OK=0 | 调阈值/半径重试一次，仍失败则 verdict FAIL |
| UHD 不稳定混淆结果 | Task 5 沿用 Phase 58 5 pivots 标准命令 | 不动 UHD 配置 |

## 关键引用

- Phase 41 关闭：`docs/superpowers/notes/2026-06-28-usrp-final-verdict.md`
- Phase 38 H52 零陷数据：`docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md`
- Phase 37 viterbi 验证：`docs/superpowers/notes/2026-06-24-phase37-verdict.md`
- Phase 33 L-LTF0 修复：commit bd5c1d2
- Phase 58 UHD streaming MARGINAL：`docs/superpowers/notes/2026-06-29-phase58-verdict.md`

## 项目约定（必须遵守）

- **make install** 每次 `make` 后必须执行
- **GRC 禁止**生成 Python：直接编辑 `wifi_phy_hier.py`
- **多线程日志原子性**：`USRP_LOG` 非 atomic，dump 多个值用 `snprintf` + `USRP_LOG("%s", buf)`
- **env vars 默认 OFF**：所有新 env var 默认 0/关闭，需 opt-in
- **标准 USRP env vars 不可改**：`IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14 IEEE80211_TIMING_OFFSET_APPLY=1`
