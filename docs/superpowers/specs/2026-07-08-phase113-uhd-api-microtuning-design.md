# Phase 113 T5.A — UHD API 微调设计规范

**Date**: 2026-07-08
**Branch**: TEST1
**Author**: Claude (Phase 113)
**Status**: 🟡 设计阶段 — 待用户批准

## 背景与动机

### Phase 112 关键发现

Phase 112 T7e D4-fix USRP 验证(2026-07-08 verdict)确认:
- **1.77 rad (101°) per-SC 相位噪声 = USRP 模拟前端 floor**
- 30+ REFUTED equalizer 层攻击 + T7e K=5 averaging 全部命中此上限
- HT-SIG viterbi metric=15,R1 预测 12-18,无法通过(自由距离 ≈ 10)
- 0 FCS_OK = R1 ceiling 完全 match

### Phase 113 方向选择

用户明确指示:**排除外部时钟和换算法,尽可能给出更多可行方案**。

最高 ROI 方向 = **T5.A UHD API 微调**(5 行代码,直击 1.77 rad 同源噪声)。

## 设计目标

通过低层 UHD API 调用调整 USRP X310 + UBX-160 v2 的模拟前端状态,
尝试降低 1.77 rad per-SC 相位噪声 floor。

## 设计范围(Scope)

### 包含

1. **3 个 UHD API 调用**(默认 OFF,env var / flag 控制):
   - `set_rx_dc_offset(False, 0)` — 关闭 ADC DC offset 自动 calibration
   - `set_rx_iq_balance(False, 0)` — 关闭 I/Q imbalance 自动 calibration
   - `set_rx_lo_source('internal', 0)` — 显式指定 LO 为 internal(防止 UHD 自动协商)

2. **1 个 argparse flag**:`--uhd-tune`(默认 False)

3. **错误处理**:try/except RuntimeError,实验不中断

4. **3 步测试**:
   - Loopback 5 min 验证 baseline 保持
   - USRP 30s 无 flag(复现 Phase 112)
   - USRP 30s `--uhd-tune`(测量 SNR 改善)

### 不包含(YAGNI)

- ❌ 改 `--rate` 默认值(20 MHz 不变,Phase 58 已 REFUTED --rate 5)
- ❌ 改 `--rx-gain` 默认值(20 dB 不变,Phase 95 验证干净)
- ❌ 改 `--tx-gain` 默认值(0 dB cable 模式不变)
- ❌ 改 `set_rx_bandwidth`(默认 20e6 = `--rate`,保持)
- ❌ 改 `set_center_freq` 或 `set_gain`
- ❌ 修改 `frame_equalizer_impl.cc`(保持 C++ 完全不变)
- ❌ 外部时钟(LFCS / GPSDO)— 用户明确排除
- ❌ 算法替换(用户明确排除)

## 架构

### 调用点

`test_usrp_minimal_loopback.py` line ~184,在 `set_bandwidth` 调用后插入新 block。

```python
# 现有 line 183:
self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, rx_ch)

# 新增(line ~184):
if args.uhd_tune:
    print("[TEST] UHD micro-tunings ENABLED: DC=off, IQ=off, LO=internal")
    try:
        self.uhd_usrp_source.set_rx_dc_offset(False, 0)
        self.uhd_usrp_source.set_rx_iq_balance(False, 0)
        self.uhd_usrp_source.set_rx_lo_source('internal', 0)
    except RuntimeError as e:
        print(f"[TEST] UHD API micro-tuning failed (non-fatal): {e}")
```

### Argparse 新增

```python
# 在 line 309(--t7e-k)后:
parser.add_argument('--uhd-tune', action='store_true',
                    help='Apply Phase 113 UHD API micro-tunings: '
                         'DC offset off, IQ balance off, LO source internal')
```

## 数据流

```
argparse --uhd-tune flag
   ↓
args.uhd_tune (bool)
   ↓
if True → 3 UHD API calls on uhd_usrp_source[ch=0]
   ↓
USRP X310 模拟前端状态变更(每个设置独立生效)
   ↓
后续 GNU Radio flow graph 完全不变
   ↓
sync_short → sync_long → equalizer → 解码
```

## 错误处理

### 失败场景

1. **UHD 库不识别 API**:抛 `RuntimeError:AttributeError` → try/except 捕获,打印警告
2. **UBX-160 不支持某 API**:抛 `RuntimeError:ValueError` → 同上
3. **API 设置但实际无效果**:无异常,但 avg_snr 不变 → 实验失败,记录 REFUTED

### 失败回退

如果 `--uhd-tune` 导致 baseline regress(如 avg_snr_lsig 下降):
1. 撤回 `set_rx_dc_offset`(先撤回单一调用,逐个隔离)
2. 撤回 `set_rx_iq_balance`
3. 保留 `set_rx_lo_source`(影响最小,作为最终保留)
4. 标记 T5.A REFUTED,转向 T3.B

## 测试计划

### 测试 1: Loopback baseline 验证

**目的**:确认 `--uhd-tune` 不破坏 software loopback。

**命令**:
```bash
cd /home/hy/gr-ieee802-11
python test_usrp_minimal_loopback.py --t7e-on --uhd-tune --freq 5250 --warmup 5 --duration 30
```

**期望**:`FCS_OK=1/1` 或 ≥90%,与 Phase 112 一致。

### 测试 2: USRP baseline 复现

**目的**:确认当前 Phase 112 USRP baseline 复现。

**命令**:
```bash
python test_usrp_minimal_loopback.py --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --duration 30
```

**期望**:`Sent≈60, Recv=0, FCS_OK=0`,与 Phase 112 verdict 一致。

### 测试 3: USRP `--uhd-tune` 实验

**目的**:测量 3 个 UHD API 调用的 SNR 影响。

**命令**:
```bash
python test_usrp_minimal_loopback.py --uhd-tune --freq 5250 --tx-gain 0 --rate 20 \
    --warmup 60 --rx-subdev A:0 --duration 30
```

**观察**:
- `avg_snr_lsig`(Phase 100 修正公式:`10*log10(1/(avg_snr-1))`)
- `avg_snr_htsig`
- `HT_SIG_PARSE_FAIL` 数量
- `FCS_OK` 数量

### 测试 4(可选): 分项隔离

如果测试 3 有部分改善,进一步隔离:
- 单独 `set_rx_dc_offset(False)` + 单独测
- 单独 `set_rx_iq_balance(False)` + 单独测

## 成功标准

| 指标 | Phase 112 baseline | Phase 113 T5.A 目标 | 失败 |
|------|---------------------|----------------------|------|
| avg_snr_lsig | 1.8-3 dB | >3 dB | <baseline |
| avg_snr_htsig | 2-3 dB | >4 dB | <baseline |
| HT_SIG_PARSE_FAIL | 6-12 | <6 | >baseline |
| FCS_OK | 0 | ≥1 | regress |

## 失败回退路径

如果 T5.A 完全失败(无 SNR 改善):
1. **T3.B**: L-LTF0 + L-LTF1 averaging(50-100 行 C++ 改动)
2. **T3.C**: LMMSE equalizer(d_mmse_equalize 字段已存在但未生效)
3. **T4.D**: HT-LTF 2x averaging(改 MCS)
4. **外部方向**:LDPC + per-SC phase tracking(算法替换,用户排除)

## 文件改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `test_usrp_minimal_loopback.py` | 新增 argparse + UHD API block | +12 行 |

## 兼容性

- ✅ 完全向后兼容(`--uhd-tune` 默认 False,行为等同 Phase 112)
- ✅ 不改任何 env var
- ✅ 不改 frame_equalizer_impl.cc
- ✅ 不改 wifi_phy_hier
- ✅ 不改 UHD sink,只改 UHD source

## 时间线

| 阶段 | 估计时间 |
|------|----------|
| 代码改动 | 5 分钟 |
| Loopback 验证 | 5-10 分钟 |
| USRP baseline 复现 | 30s + 60s warmup |
| USRP `--uhd-tune` 实验 | 30s + 60s warmup |
| 数据分析 + verdict 写入 | 10 分钟 |
| **总计** | ~30-40 分钟 |

## 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| UHD API 在 4.7.0.HEAD 不支持 | 低 | 实验失败 | try/except |
| 改了导致 baseline regress | 低 | 需回退 | 默认 OFF |
| 无 SNR 改善 | 中 | 标记 REFUTED,转向 T3 | 已有回退计划 |
| UHD 模拟前端 calibration 状态被破坏 | 极低 | 需重启 USRP | 一次实验,不持续 |

## 决策日志

- **2026-07-08**:决定排除外部时钟/算法替换(用户指示)
- **2026-07-08**:决定改 UHD source,不改 sink(直击 RX 链)
- **2026-07-08**:决定默认 OFF,env var 控制(Phase 89 风格,baseline 保护)
- **2026-07-08**:决定不改 `--rate`、`--rx-gain` 默认值(无依据)
- **2026-07-08**:决定 set_rx_bandwidth 不动(Phase 95 已用 20 MHz 干净 constellation)

## 相关引用

- `docs/superpowers/notes/2026-07-08-phase112-t7e-usrp-verification-verdict.md`
- `docs/superpowers/notes/2026-07-04-phase89-verdict.md`
- `docs/superpowers/notes/2026-07-06-phase107-deep-root-cause-verdict.md`
- CLAUDE.md Phase 82+ cable config,Phase 96 tx-gain 20 验证
- MEMORY.md `project-p112-t7e-usrp-verification`,`project-p112-r1-argh-rootcause`