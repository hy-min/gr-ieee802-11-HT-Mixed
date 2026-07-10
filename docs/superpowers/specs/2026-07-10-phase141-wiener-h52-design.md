# Phase 141: Wiener H52 频域收缩 — Design Spec

**Date**: 2026-07-10
**Branch**: TEST1
**Author**: Claude (after systematic-debugging on Phase 140)
**Status**: 📋 DRAFT (awaiting user review)

## 1. Background

Phase 140 (2-way + cross-frame L-SIG H52) USRP 验证结果 (`2026-07-10-phase140-t9-usrp-verdict.md`):

- **机制数学正确**: σ_post 严格匹配 `1.25/√n_avg` 理论
- **T9j 好状态**: avg_snr_ht peak +6.44 dB, LSIG_DECODE_OK +317%
- **0 FCS_OK**: 即使 σ_post=0.559 rad (N=4 全 FIFO), HT-SIG metric 仍 14, **σ→metric 模型不成立**

按 systematic-debugging Phase 4.5（3+ 修复失败）需要质疑架构。Phase 138/139/140 都是 σ 缩减架构（同 L-LTF0 估计的方差缩减），σ→metric 非单调说明**根本限制不在 σ**。

新架构：**Wiener H52 频域收缩**。从单次新 H_ls 估计出发，每 SC 独立用统计最优收缩，对 UBX-160 self-cal 漂移免疫（不依赖 FIFO 填充）。

## 2. Goal

USRP realtime `FCS_OK ≥ 1` 在 5250 MHz SMA direct + Phase 141 + Phase 140 堆叠下达成。

**Acceptance criteria**:
1. **最低**: file-replay 1/1 PASS 不退化（baseline 回归）
2. **目标**: USRP avg_snr_ht peak ≥ 7.86 dB（T9g 5.86 + 2 dB）
3. **突破**: HT_SIG_CAND best metric ≤ 10 → viterbi CRC pass → FCS_OK ≥ 1

## 3. Architecture

### 3.1 算法核心：频域 SC-by-SC Wiener 收缩

```
LS 估计:  H_ls[k] = H_true[k] + n_ls[k],  var(n_ls) = σ²_noise / |x_ltf|²

Wiener MMSE:  H_wiener[k] = G[k] · H_ls[k]
其中 G[k] = R_hh[k] / (R_hh[k] + σ²_noise / |y_ltf[k]|²)

R_hh[k] = E[|H[k]|²]    信道 PSD（频域相关结构）
σ²_noise = noise variance（从 null SCs 估计）
```

**为什么有效**：
- 当 |H_ls[k]| 小（频域深衰落），G → 0，**抑制 LS 噪声放大**
- 当 |H_ls[k]| 大（信号好），G → 1，**保留 LS**
- 5 stable null SCs {-21,-13,-7,+7,+21}（Phase 78b 验证 |H|≈0）会被 G→0 收缩

### 3.2 与 Phase 140 协同（用户选 B 选项）

```
L-LTF → Wiener (a) → Phase 140 FIFO → L-SIG viterbi
HT-LTF → Wiener (b) → Phase 140 FIFO → HT-SIG viterbi
HT-SIG decoded → Data: Phase 139 2-way → Wiener (c) → Data viterbi
```

- **Wiener 用单帧 R_hh 收缩 H_ls**（不依赖 FIFO 填满，**对 sync_short 饿死免疫**）
- **Phase 140 FIFO 提供长期 R_hh 估计**（跨帧统计）
- **两者堆叠**：Wiener 主滤波 + Phase 140 喂 R_hh

### 3.3 与 Phase 138/139 关系

| Phase | 思路 | 与 Wiener 关系 |
|-------|------|----------------|
| 138 freq-lowpass | 频域滤波 | 替代（不堆叠） |
| 139 2-way | 跨 LTS 平均 | Phase 139 之后接 Wiener (c) |
| 140 cross-frame | 跨帧 FIFO | Wiener 在前喂 R_hh |

## 4. Components

### 4.1 Wiener 收缩函数 `wiener_filter_h52`

**位置**: `lib/frame_equalizer_impl.cc`，新增 `static void wiener_filter_h52(...)`

```cpp
static void wiener_filter_h52(
    const gr_complex* h_ls,     // [52] 当前 LS 估计
    const gr_complex* y_ltf,    // [52] L-LTF 接收符号
    const float* r_hh,          // [52] 信道 PSD
    float sigma2_noise,         // 噪声方差
    float g_min,                // 最小 G 保护 (默认 0.1)
    gr_complex* h_out)          // [52] 输出
{
    for (int k = 0; k < 52; k++) {
        float y_abs2 = std::norm(y_ltf[k]);
        float noise_term = sigma2_noise / std::max(y_abs2, 1e-12f);
        float G = r_hh[k] / (r_hh[k] + noise_term);
        if (G < g_min) G = g_min;
        h_out[k] = gr_complex(G * h_ls[k].real(), G * h_ls[k].imag());
    }
}
```

**性质**:
- 纯算术，30 行，无动态分配
- 单 SC 独立（可向量化，但不强求）
- 数值稳定：`std::max(y_abs2, 1e-12f)` 防 0 除

### 4.2 R_hh 估计器 `estimate_r_hh`

**位置**: `lib/frame_equalizer_impl.cc`，新增 `int frame_equalizer_impl::estimate_r_hh(...)`

```cpp
int frame_equalizer_impl::estimate_r_hh(
    const gr_complex* h_ls,     // [52] 当前 LS 估计
    double freq_key,            // 当前载波频率（Hz）
    float* r_hh_out)            // [52] 输出 R_hh
{
    if (!d_apply_wiener_h52 || d_wiener_rhh_history_depth < 1) {
        // Wiener OFF: 用单帧 |H|² 不平滑（退化到 LS）
        for (int k = 0; k < 52; k++) {
            r_hh_out[k] = std::norm(h_ls[k]);
        }
        return 1;
    }

    // Step 1: 频域 3-tap 平滑（相邻 SC 相关）
    float h_abs2[52];
    for (int k = 0; k < 52; k++) {
        h_abs2[k] = std::norm(h_ls[k]);
    }
    float h_smooth[52];
    for (int k = 0; k < 52; k++) {
        int k_prev = (k - 1 + 52) % 52;
        int k_next = (k + 1) % 52;
        h_smooth[k] = (h_abs2[k_prev] + h_abs2[k] + h_abs2[k_next]) / 3.0f;
    }

    // Step 2: 频率键控 reset（与 Phase 140 一致, 1 Hz 阈值）
    const double freq_delta = std::abs(freq_key - d_wiener_rhh_history_freq_key);
    const bool first_call = (d_wiener_rhh_history_count == 0);
    const bool freq_changed = !first_call && (freq_delta > 1.0);
    if (freq_changed) {
        d_wiener_rhh_history_count = 0;
    }
    if (first_call || freq_changed) {
        d_wiener_rhh_history_freq_key = freq_key;
    }

    // Step 3: 跨帧 FIFO 平均
    const int n_avg = 1 + d_wiener_rhh_history_count;
    const float inv_n = 1.0f / (float)n_avg;
    for (int k = 0; k < 52; k++) {
        float sum = h_smooth[k];
        for (int j = 0; j < d_wiener_rhh_history_count; j++) {
            sum += d_wiener_rhh_history[j][k];
        }
        r_hh_out[k] = sum * inv_n;
    }

    // Step 4: FIFO push（带频域 wrap-around shift）
    if (d_wiener_rhh_history_count >= d_wiener_rhh_history_depth) {
        for (int j = 0; j < d_wiener_rhh_history_depth - 1; j++) {
            for (int k = 0; k < 52; k++) {
                d_wiener_rhh_history[j][k] = d_wiener_rhh_history[j + 1][k];
            }
        }
        d_wiener_rhh_history_count = d_wiener_rhh_history_depth - 1;
    }
    for (int k = 0; k < 52; k++) {
        d_wiener_rhh_history[d_wiener_rhh_history_count][k] = h_smooth[k];
    }
    d_wiener_rhh_history_count++;

    return n_avg;
}
```

**性质**:
- 与 Phase 140 FIFO 模式一致（freq-keyed reset, shift-down eviction）
- 频域 3-tap 平滑给 R_hh 一个先验相关结构
- `n_avg` 返回用于诊断 log

### 4.3 σ²_noise 估计器 `estimate_sigma2_noise`

**位置**: `lib/frame_equalizer_impl.cc`，新增 `static float estimate_sigma2_noise(...)`

```cpp
static float estimate_sigma2_noise(
    const gr_complex* y_ltf,    // [52] L-LTF 接收符号
    const int* null_scs,        // null SC 索引列表（绝对 -26..+26）
    int n_nulls)                // null SC 数量
{
    if (n_nulls <= 0) return 0.0f;
    float powers[8];
    int n = std::min(n_nulls, 8);
    for (int i = 0; i < n; i++) {
        int sc = null_scs[i] + 26;  // 绝对 SC -26..+26 → index 0..52
        if (sc < 0 || sc >= 52) continue;
        powers[i] = std::norm(y_ltf[sc]);
    }
    // 中位数比均值鲁棒（拒绝 outlier）
    std::sort(powers, powers + n);
    return powers[n / 2];
}
```

**默认 null SCs**: `{-21, -13, -7, +7, +21}`（Phase 78b 验证的 5 stable nulls）

**Env var override**: `IEEE80211_WIENER_NULL_SCS='-21,-13,-7,7,21'` 解析为 int 数组

### 4.4 3 个调用点

| ID | 位置 (approx) | 当前 H 来源 | Wiener 输入 | Wiener 输出 |
|----|---------------|-------------|-------------|-------------|
| (a) | `frame_equalizer_impl.cc:6307` (L-LTF H52 estimate) | `estimate_header_channel_from_lltf52` 输出 | `y_ltf[52]` + R_hh FIFO (a) | 给 L-SIG viterbi 前 Phase 140 |
| (b) | `frame_equalizer_impl.cc:6390` (HT-LTF H52) | `estimate_header_channel_from_htltf52` 输出 | `y_htltf[52]` + R_hh FIFO (b) | 给 HT-SIG viterbi 前 Phase 140 |
| (c) | Phase 139 2-way 之后 | 2-way 平均 H52 | `y_data[52]` + R_hh FIFO (c) | 给 Data viterbi |

**调用顺序**（与 §3.2 一致）:
```
L-LTF → Wiener (a) → Phase 140 FIFO → L-SIG viterbi
HT-LTF → Wiener (b) → Phase 140 FIFO → HT-SIG viterbi
HT-SIG decoded → Data: Phase 139 2-way → Wiener (c) → Data viterbi
```

**调用点集成原则**:
- 每个调用点独立 R_hh FIFO（不共享，因为 L-LTF/HT-LTF/Data 信道变化）
- 调用点 (a) (b) (c) 都用跨帧 R_hh（保持一致，简化代码）
- Wiener OFF 时退化到 LS（`wiener_filter_h52` 不调用，直接用 H_ls）

### 4.5 头文件成员（`lib/frame_equalizer_impl.h`）

```cpp
// Phase 141 Wiener H52 estimation
bool d_apply_wiener_h52;                              // 总开关
int  d_wiener_rhh_history_depth;                      // FIFO 深度（默认 4）
int  d_wiener_rhh_history_count;                      // FIFO 当前计数
gr_complex d_wiener_rhh_history[8][52];               // FIFO buffer
double d_wiener_rhh_history_freq_key;                 // 频率键控 reset
float d_wiener_g_min;                                 // 最小 G 保护（默认 0.1）
int   d_wiener_null_scs[8];                           // null SCs 列表
int   d_wiener_n_nulls;                               // null SC 数量
bool  d_wiener_log;                                   // 诊断 log 开关
```

### 4.6 Env Var 解析（`lib/frame_equalizer_impl.cc`，新增于 Phase 140 env var parser 之后 ~line 4890）

```cpp
// IEEE80211_WIENER_H52=1 enables Wiener H52 estimation
const char* env_wiener = std::getenv("IEEE80211_WIENER_H52");
if (env_wiener && env_wiener[0] != '0' && env_wiener[0] != '\0') {
    d_apply_wiener_h52 = true;
    // FIFO depth (default 4)
    const char* env_fifo_n = std::getenv("IEEE80211_WIENER_FIFO_N");
    if (env_fifo_n && env_fifo_n[0] != '\0') {
        int n = std::atoi(env_fifo_n);
        if (n >= 1 && n <= 8) d_wiener_rhh_history_depth = n;
    } else {
        d_wiener_rhh_history_depth = 4;
    }
    // G_MIN protection (default 0.1)
    const char* env_gmin = std::getenv("IEEE80211_WIENER_G_MIN");
    if (env_gmin && env_gmin[0] != '\0') {
        float g = std::atof(env_gmin);
        if (g >= 0.0f && g <= 1.0f) d_wiener_g_min = g;
    } else {
        d_wiener_g_min = 0.1f;
    }
    // Null SCs (default {-21,-13,-7,+7,+21})
    const char* env_null_scs = std::getenv("IEEE80211_WIENER_NULL_SCS");
    if (env_null_scs && env_null_scs[0] != '\0') {
        // Parse "-21,-13,-7,7,21" into int array
        d_wiener_n_nulls = 0;
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%s", env_null_scs);
        char* tok = std::strtok(buf, ",");
        while (tok && d_wiener_n_nulls < 8) {
            int sc = std::atoi(tok);
            if (sc >= -26 && sc <= 26) {
                d_wiener_null_scs[d_wiener_n_nulls++] = sc;
            }
            tok = std::strtok(nullptr, ",");
        }
    } else {
        d_wiener_null_scs[0] = -21; d_wiener_null_scs[1] = -13;
        d_wiener_null_scs[2] = -7;  d_wiener_null_scs[3] = 7;
        d_wiener_null_scs[4] = 21;
        d_wiener_n_nulls = 5;
    }
    // Diagnostic log
    d_wiener_log = (std::getenv("IEEE80211_WIENER_LOG") != nullptr);
    std::cout << "[FRAME_EQ] IEEE80211_WIENER_H52=1 "
              << "FIFO_N=" << d_wiener_rhh_history_depth
              << " G_MIN=" << d_wiener_g_min
              << " N_NULLS=" << d_wiener_n_nulls
              << " LOG=" << (d_wiener_log ? 1 : 0)
              << std::endl;
}
```

### 4.7 构造函数初始化（`frame_equalizer_impl.cc`，与 Phase 140 d_lsig_h52_history_* 同段 ~line 4810）

```cpp
d_apply_wiener_h52 = false;
d_wiener_rhh_history_depth = 0;
d_wiener_rhh_history_count = 0;
d_wiener_rhh_history_freq_key = 0.0;
d_wiener_g_min = 0.1f;
d_wiener_n_nulls = 0;
d_wiener_log = false;
for (int i = 0; i < 8; i++) {
    for (int k = 0; k < 52; k++) {
        d_wiener_rhh_history[i][k] = gr_complex(0.0f, 0.0f);
    }
}
```

## 5. Environment Variables

| Env var | Type | Default | Meaning |
|---------|------|---------|---------|
| `IEEE80211_WIENER_H52` | flag | OFF | 总开关（=1 启用） |
| `IEEE80211_WIENER_FIFO_N` | int | 4 | R_hh FIFO 深度（1..8） |
| `IEEE80211_WIENER_G_MIN` | float | 0.1 | 最小 G 保护（0.0..1.0） |
| `IEEE80211_WIENER_NULL_SCS` | string | '-21,-13,-7,7,21' | null SC 列表（绝对 SC） |
| `IEEE80211_WIENER_LOG` | flag | OFF | 诊断 log（=1 启用） |

**默认 OFF 保留 baseline**（CLAUDE.md 项目约定）。

## 6. Validation

### T1 单元测试 (`p141_t1_wiener_unit.py`)
- **场景**: 已知 H_true (频域稀疏, 5 stable nulls) + 加性高斯噪声 → LS 估计 → Wiener 估计
- **度量**: MSE(LS), MSE(Wiener); 5 stable nulls SC 的 H 估计误差
- **期望**: Wiener MSE < LS MSE, 特别在 5 stable nulls SC 上改善 5-10×
- **工具**: numpy 直接计算（无 UHD）

### T2 文件回放 (`test_file_replay_e2e.py --wiener-on`)
- 必须保持 1/1 PASS（baseline 不退化）
- 检查 avg_snr_ht 不下降
- 对比 baseline vs Phase 141 配置的 HT_SIG_CAND metric 分布

### T3 USRP 验证 (`test_usrp_minimal_loopback.py --wiener-on`)
- 5250 MHz SMA direct, --tx-gain 0
- 多运行（5-10 次）以应对 UBX-160 self-cal variance
- 关键指标: avg_snr_ht peak, HT_SIG_CAND best metric, FCS_OK

### T4 调试记录 (`docs/superpowers/notes/2026-07-10-phase141-verdict.md`)
- 与 Phase 140 verdict 同样格式
- 包含 Phase 141 机制数学正确性证明
- USRP 多运行数据 + 诚实评估

## 7. Risks & Mitigations

| 风险 | 概率 | 缓解 |
|------|------|------|
| R_hh 跨帧 FIFO 在 sync_short 饿死状态无积累 | 中 | 第一次用单帧 R_hh（仍优于 LS） |
| σ²_noise 高估 → G 收缩过度 | 中 | G_MIN=0.1 保护, 用 Phase 78b 验证的 5 stable nulls |
| Wiener 把有效 H 收缩（不在 null SC 上） | 低 | 仅 |H|² < σ²/|y|² 的 SC 收缩（少量 SC） |
| 4 调用点相位不一致 | 低 | 每调用点独立 R_hh FIFO（不共享） |
| Wiener 与 Phase 139 4-way 冲突 | 低 | Wiener 在 2-way 之后（输入已平均） |
| Wiener 在频域 wrap-around 处失效 | 低 | 3-tap 平滑用 wrap-around (k-1+52)%52 |

## 8. Files Modified

| 文件 | 改动 | 行数估计 |
|------|------|----------|
| `lib/frame_equalizer_impl.cc` | 新增 3 函数（wiener_filter_h52 / estimate_sigma2_noise / estimate_r_hh）+ env var 解析 + 3 调用点接入 | +200 行 |
| `lib/frame_equalizer_impl.h` | 新增 8 成员变量 | +15 行 |
| `examples/test_usrp_minimal_loopback.py` | argparse + env setters | +25 行 |
| `examples/test_file_replay_e2e.py` | argparse + env setters | +25 行 |
| `docs/superpowers/notes/2026-07-10-phase141-verdict.md` | 验证记录 | +150 行 |
| `CLAUDE.md` | Phase 141 段落 | +30 行 |
| `MEMORY.md` | Phase 141 索引条目 | +1 行 |

总计：~450 行新增 + 文档。

## 9. Success Criteria (Project Goal Compliance)

**CLAUDE.md Project Goal**: USRP realtime FCS_OK ≥ 1 (per-frame on USRP X310 + UBX-160).

**Phase 141 SUCCESS** = USRP run with `--wiener-on` produces ≥1 FCS_OK in single 60s test.

**Phase 141 PARTIAL** = USRP run shows avg_snr_ht improvement > baseline but 0 FCS_OK. C++ preserved as opt-in for future work.

**Phase 141 REFUTED** = USRP run shows no avg_snr_ht improvement (≤ baseline). C++ may be preserved or removed based on architectural value.

Per CLAUDE.md: "Equalizer attacks MUST continue" 即使 REFUTED。

## 10. Related Work

- **Phase 140** (2-way + cross-frame L-SIG H52): 文件 `2026-07-10-phase140-verdict.md`, USRP verdict `2026-07-10-phase140-t9-usrp-verdict.md`
- **Phase 139** (2-way L-LTF0+L-LTF1 baseline): 文件 `2026-07-09-phase139-architecture-rewrite-design.md`
- **Phase 138** (freq-domain low-pass filter, REFUTED on USRP): `2026-07-09-phase138-freq-lowpass-verdict.md`
- **Phase 78b** (5 stable null SCs identified): 见 verdict notes
- **Phase 112 R1** (1.77 rad per-SC noise floor): `2026-07-07-phase112-r1-argh-rootcause.md`

## 11. Open Questions

1. **σ² 估计用 4 pilot nulls 还是 5 stable nulls？** Spec 推荐 5 stable（更鲁棒）。
2. **G_MIN 默认 0.1 是否合适？** 0.1 = 10% 保底；0.05 更激进。
3. **R_hh FIFO 是否应该与 Phase 140 共享？** Spec 推荐独立（不同信道）。
4. **是否在调用点 (c) Data 上加 Wiener？** Spec 推荐加（一致性）。

## 12. Implementation Plan Reference

Implementation plan 在 `docs/superpowers/plans/2026-07-10-phase141-wiener-h52.md`（用 superpowers:writing-plans skill 生成）。

实现步骤 T1-T7：
- T1: Wiener 滤波器内核 (`wiener_filter_h52`)
- T2: σ² 估计器 (`estimate_sigma2_noise`)
- T3: R_hh 估计器 (`estimate_r_hh`)
- T4: env var 解析
- T5: 调用点 (a) L-LTF → L-SIG
- T6: 调用点 (b) HT-LTF → HT-SIG + (c) Data
- T7: 文件回放 + USRP 验证

每个任务都有 failing test → impl → verify → commit 周期。