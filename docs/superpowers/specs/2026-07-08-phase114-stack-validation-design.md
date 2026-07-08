# Phase 114 — T5.A + T3.B + T4.D Stack Validation Design (2026-07-08)

**Branch**: TEST1
**Author**: Claude (Phase 114)
**Status**: 🟡 设计阶段 — 用户已批准 Plan A(分步验证)

## 背景

### Phase 113 关键发现

T5.A UHD API micro-tuning 实验(2026-07-08 verdict)实际是 **PARTIAL**,不是 REFUTED:

| Metric | Baseline | T5.A |
|--------|----------|------|
| LSIG_DECODE OK (60s) | 1 | **11 (+1000%)** |
| L-SIG EQ ratio | 2.681 | **0.863 (clean)** |
| avg_snr \|eq\|² | 9.70 | **3.65 (closer to 1.0)** |

UBX-160 auto DC offset + IQ balance calibration chasing the 1.77 rad
noise floor causes over-amplified equalizer output. Disabling both
calibrations produces near-ideal L-SIG constellation.

但 HT-SIG viterbi 仍失败 — 1.77 rad per-SC phase noise(R1 ceiling)未变。

## 设计目标

通过在 T5.A 基础上叠加 T3.B(L-LTF averaging)和 T4.D(HT-LTF 2x averaging),
尝试突破 HT-SIG viterbi 阈值。

## 设计范围(Scope)

### 包含

**Step 1**: 测试 T5.A + 现有 `IEEE80211_H52_SNR_WEIGHTED`(零 C++ 改动)
- Phase 77c 之前 REFUTED,但 analog chain 状态已改变
- 复验 SNR-weighted L-LTF averaging 在新信号状态下是否有效
- 测试方法:`--uhd-tune` + `IEEE80211_H52_SNR_WEIGHTED=1`

**Step 2**: 实现 T4.D — HT-LTF 2x averaging(~30 行 C++)
- HT-Mixed preamble 有 2 个 HT-LTF symbols
- 当前代码使用单 HT-LTF;新增 2x averaging
- 新增 env var `IEEE80211_HTLTF_AVG=1`
- 新增文件:本文件
- 修改文件:`lib/frame_equalizer_impl.cc`

**Step 3**: T5.A + T3.B + T4.D 全组合
- 仅在前两步 PARTIAL 时启用

### 不包含(YAGNI)

- ❌ 重新实现 IEEE80211_H52_SNR_WEIGHTED(已存在)
- ❌ 改 L-LTF0 / L-LTF1 FFT 时序
- ❌ 改 sync_short / sync_long / UHD streaming
- ❌ 外部时钟(LFCS / GPSDO)— 用户排除
- ❌ 算法替换(LDPC / 其他)— 用户排除

## 架构

### 调用链

```
Step 1:
  argparse --uhd-tune
    ↓
  os.environ IEEE80211_H52_SNR_WEIGHTED=1
    ↓
  UHD source: set_auto_dc_offset(False), set_auto_iq_balance(False)
  frame_equalizer: estimate_header_channel_from_lltf52 (SNR-weighted)
    ↓
  USRP → cleaner L-SIG + averaged H52 → viterbi

Step 2 (additive to Step 1):
  os.environ IEEE80211_HTLTF_AVG=1
    ↓
  frame_equalizer: 2x HT-LTF averaging for H52 estimation
    ↓
  cleaner HT-SIG channel estimation

Step 3 (additive):
  All three enabled simultaneously
```

### T4.D 实现位置

`lib/frame_equalizer_impl.cc` 中处理 HT-LTF symbols 的位置。
HT-LTF 在 HT-Mixed preamble 中是 counter=6 和 counter=7(在 HT-SIG 之后)。

## 数据流

```
--uhd-tune flag → UHD API block (T5.A)
env var H52_SNR_WEIGHTED → estimate_header_channel_from_lltf52 L-LTF0+L-LTF1 avg (T3.B)
env var HTLTF_AVG → HT-LTF 2x averaging (T4.D)
   ↓
frame_equalizer processes frame
   ↓
L-SIG decode (BPSK, 180° margin) — already 1000% better with T5.A
   ↓
HT-SIG decode (QBPSK, 45° margin) — currently fails, T3.B+T4.D should help
   ↓
viterbi metric floor < 10 → FCS_OK
```

## 错误处理

每个 env var 独立:
- `IEEE80211_H52_SNR_WEIGHTED` — Phase 77c 已有,错误处理已有
- `IEEE80211_HTLTF_AVG` — 新增,使用 try/catch 包裹新代码
- `--uhd-tune` — 已有 try/except

如果某个 step 引入崩溃,该 step 默认 OFF 不影响 baseline。

## 测试计划

### Step 1 测试

**命令**:
```bash
sleep 15 && timeout 240 /home/hy/conda/envs/gnuradio/bin/python \
    test_usrp_minimal_loopback.py --uhd-tune \
    --freq 5250 --tx-gain 0 --rate 20 --warmup 60 \
    --rx-subdev A:0 --duration 60 \
    2>&1 | tee /tmp/p114_t1_usrp.log
```

**env var 注入**:在 `internal_run` 加:
```python
os.environ.setdefault('IEEE80211_H52_SNR_WEIGHTED', '1')
```
只在 `--uhd-tune` flag 启用时设置,默认 OFF。

**观察**:
- `LSIG_DECODE OK` 数量(基线 11)
- `avg_snr_lsig` / `avg_snr_htsig`
- `HT_SIG_CAND` 数量(基线 0)
- `FCS_OK`(基线 0)

### Step 2 测试

先实施 T4.D,然后:
```bash
sleep 15 && timeout 240 /home/hy/conda/envs/gnuradio/bin/python \
    test_usrp_minimal_loopback.py --uhd-tune \
    --freq 5250 --tx-gain 0 --rate 20 --warmup 60 \
    --rx-subdev A:0 --duration 60 \
    2>&1 | tee /tmp/p114_t2_usrp.log
```

启用 `IEEE80211_HTLTF_AVG=1`(only when --uhd-tune flag)。

**观察**:与 Step 1 对比,看 HT-SIG 路径是否改善。

### Step 3 测试

启用所有三个 env vars(only when --uhd-tune flag)。

### 软件 loopback 验证

每个 step 之前先跑 loopback,确保 baseline 保持(1/1 PASS)。

## 成功标准

| 指标 | Phase 113 baseline | Phase 114 目标 | 失败阈值 |
|------|---------------------|------------------|----------|
| LSIG_DECODE OK (60s) | 11 | ≥ 11 | < 11 regressed |
| avg_snr_htsig | 4.46 (linear) | > 4.46 | < baseline |
| HT_SIG_CAND | 0 | ≥ 1 | regressed |
| FCS_OK | 0 | ≥ 1 | regressed |
| L-SIG EQ ratio | 0.863 | < 1.0 | > 1.0 regressed |

## 失败回退路径

| Step | 失败 | 回退 |
|------|------|------|
| Step 1 | SNR-weighted 在 T5.A 下仍无效 | 直接到 Step 2 |
| Step 2 | HT-LTF 2x 无效 | 到 Step 3(可能组合有效) |
| Step 3 | 全部无效 | 标记 Phase 114 REFUTED,转向其他方向 |

## 文件改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `test_usrp_minimal_loopback.py` | env var 注入(Step 1) | +5 |
| `lib/frame_equalizer_impl.cc` | T4.D HT-LTF averaging(Step 2) | +30 |
| `test_usrp_minimal_loopback.py` | env var 注入(Step 2) | +3 |
| `docs/superpowers/notes/2026-07-08-phase114-stack-verdict.md` | 新建 verdict | +100 |

## 兼容性

- ✅ `--uhd-tune` 默认 OFF,Phase 112 baseline 完全保持
- ✅ 所有 env vars 默认 OFF/未设置
- ✅ C++ 改动用 env var gate,默认不影响 Phase 112/113
- ✅ 需要 `make install` 之后 C++ 改动生效

## 时间线

| 阶段 | 估计时间 |
|------|----------|
| Step 1 env 注入 + 测试 | 10 分钟 |
| Step 2 C++ 实现 + 测试 | 1-2 小时 |
| Step 3 组合测试 | 10 分钟 |
| 数据分析 + verdict 写入 | 15 分钟 |
| **总计** | ~2 小时 |

## 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Step 1 SNR-weighted 仍 REFUTED | 中 | 需实施 Step 2 | 直接到 Step 2 |
| Step 2 C++ 改动引入 bug | 中 | USRP 链路断 | loopback 1/1 验证 |
| Step 3 全无效 | 中 | Phase 114 REFUTED | 标记 + 转向 |
| UHD warmup 不稳定 | 低 | SNR 抖动 | 60s warmup per Phase 82 config |
| 1.77 rad floor 不可逾越 | 高 | Phase 114 也 REFUTED | 已知 R1 ceiling |

## 决策日志

- 2026-07-08:Phase 113 T5.A PARTIAL → 决定继续 Phase 114
- 2026-07-08:决定 Plan A 分步验证(用户批准)
- 2026-07-08:Step 1 零 C++ 改动优先(已有 IEEE80211_H52_SNR_WEIGHTED)
- 2026-07-08:Step 2 仅在 Step 1 无效时实施
- 2026-07-08:Step 3 是组合测试,不一定能突破 R1 ceiling

## 相关引用

- `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md` (PARTIAL 修正后)
- `docs/superpowers/notes/2026-07-07-phase112-r1-argh-rootcause-verdict.md` (R1 ceiling)
- `docs/superpowers/notes/2026-07-04-phase86-verdict.md` (L-LTF0 audit)
- `docs/superpowers/notes/2026-07-03-phase77-verdict.md` (SNR-weighted REFUTED)
- CLAUDE.md Phase 113 verdict
- MEMORY.md `project_p113_uhd_api_microtuning.md`