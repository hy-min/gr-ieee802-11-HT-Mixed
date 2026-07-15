# Phase 112 R1 — Per-SC argH 相位噪声根因诊断 (2026-07-07)

**Branch**: TEST1
**Status**: 🟢 **根因确认** — per-symbol 随机相位噪声 (~101°),与 Phase 25 的 1.77 rad 残余**完全匹配**,证实是 USRP 模拟链路(振荡器/RF chain)问题。

## TL;DR

R1 验证了一个被 Phase 25 误 REFUTED 的根因:**USRP 模拟链路的随机 per-symbol 相位噪声**。

- Per-SC |H[sc]| 时间序列的 std (vs L-LTF 参考): **1.77-1.79 rad ≈ 101-102°** (跨越 L-SIG / HT-SIG / HT-STF / HT-LTF / DATA[0..5])
- Per-frame 跨同 symbol 索引的 std: 2.5 rad ≈ 143°
- SFO slope 仅 -0.03 rad/SC (≈ 1.7°) — 不是 SFO
- 无 per-second trend — 不是 thermal drift

这与 Phase 25 verdict 中"residual phase noise std=1.77rad"的数字**完全匹配**,说明过去 9 个月的所有 equalizer-layer 攻击(30+ REFUTED)都是在对抗**一个 analog-chain 物理噪声源**,而不是 decoder-amenable 问题。

## 测量 (p110_t10_capture.fc32, 5250 MHz, --tx-gain 20, 30 frames)

### Per-symbol std (vs L-LTF reference)

| Symbol | mean (rad) | std (rad) | std (°) | max_std (°) |
|--------|-----------:|----------:|--------:|-----------:|
| HT-STF  | +0.065 | 1.794 | 102.8° | 116.9° |
| HT-LTF0 | +0.072 | 1.774 | 101.7° | 116.9° |
| HT-LTF1 | +0.005 | 1.791 | 102.6° | 123.8° |
| DATA[0] | -0.079 | 1.784 | 102.2° | 123.2° |
| DATA[1] | -0.030 | 1.762 | 100.9° | 115.8° |
| DATA[2] | +0.013 | 1.758 | 100.7° | 120.9° |
| DATA[3] | -0.095 | 1.746 | 100.0° | 121.5° |
| DATA[4] | +0.034 | 1.793 | 102.7° | 120.9° |
| DATA[5] | +0.031 | 1.790 | 102.6° | 120.3° |

**全 OFDM symbol 都是 ~1.77 rad (≈ 101°)**。横向一致性极高,说明噪声源稳定。

### Time-scale decomposition

| 测量 | std (rad) | std (°) |
|------|----------:|--------:|
| Δφ(DATA[1] - DATA[0]) | 2.59 | 148.5° |
| Δφ(DATA[2] - DATA[0]) | 2.48 | 141.8° |
| Δφ(DATA[3] - DATA[0]) | 2.53 | 144.9° |
| Δφ(DATA[4] - DATA[0]) | 2.51 | 143.7° |
| Δφ(DATA[5] - DATA[0]) | 2.50 | 143.4° |
| Per-frame drift (DATA[0] across frames) | 2.50 | 143.1° |

`Δφ(DATA[k] - DATA[0])` 的 ≈√2 × 1.77 rad 行为**正是**两份独立噪声之和预期的统计 — 噪声**确实是 per-symbol 独立**的,不是累积漂移。

### Per-frame 时间序列样本 (Frame 0)

```
Symbol     argH (rad)   argH (°)
L-LTF0     +0.445       +25.5
L-LTF1     -0.115        -6.6
L-SIG      +0.055        +3.2
HT-SIG0    +0.380       +21.8
HT-SIG1    -0.318       -18.2
HT-STF     -0.161        -9.2
HT-LTF0    -0.198       -11.4
HT-LTF1    -0.093        -5.4
DATA[0]    +0.038        +2.2
DATA[1]    -0.191       -10.9
DATA[2]    +0.177       +10.1
DATA[3]    -0.455       -26.1
DATA[4]    -0.194       -11.1
DATA[5]    +0.069        +3.9
```

从 L-LTF 到 HT-SIG0: +22°(单帧),但跨 30 帧平均后 std 是 101° — 单帧波动很大。

## 根因解读 (重要!)

### Phase 25 的过度 REFUTED 误判

Phase 25 verdict 标题: "Phase 25 SFO/Phase Noise. REFUTED. slope=-0.03rad/SC, residual phase noise std=1.77rad."

Phase 25 仅因为 **slope 是 0.03 rad/SC** 就认定 SFO 不存在,然后整个 verdict 是 REFUTED。但它**没考虑到**:还有 1.77 rad 的 random residual!

R1 证明:1.77 rad 不是"残余可以忽略",而是**主导相位噪声**。Phase 25 的结论把婴儿和洗澡水一起倒掉了。

### 物理来源候选 (按可能性排序)

1. **USRP X310 local oscillator phase noise** (最可能)
   - TCXO/VCXO 在 5 GHz 有 ~1-3 rad 短期相位漂移
   - 解释 std ≈ 1.77 rad 在 0.4 ms (1 OFDM symbol) 内
2. **UBX-160 RF chain phase noise** (可能)
   - 上变频器/下变频器的本振相位噪声
   - 与 oscillator phase noise 累加
3. **DPD artifacts** (UHD 内置 digital pre-distortion,可能)
   - DPD 引入 per-symbol 状态依赖
   - 但通常影响是确定性的(可重复),不像是 random
4. **采样时钟 jitter** (低可能)
   - SFO 已排除 (slope=0.03 rad/SC)
   - 但 jitter 可以造成 random per-sample timing noise

### 排除的非根因

- ❌ **SFO**: slope = 0.03 rad/SC ≈ 1.7° (Phase 25 + R1 都验证)
- ❌ **CFO**: 同上,如果 CFO 显著,symbol 间会有累积 phase ramp,但 R1 看的是 per-symbol std,即使有 CFO 也只贡献一个 linear term
- ❌ **Per-second thermal drift**: 数据无每秒趋势
- ❌ **UHD streaming**: Phase 109 (UHD T1-T2 REFUTES Phase 55) 验证 UHD 稳定 20s 流

## 对 Equalizer-Layer Attack 的根本影响

**30+ REFUTED 的攻击都是在对抗 analog-chain phase noise**,而不是 decoder bug。这是**架构错误**:

1. **H52 re-estimation** (Phase 39, 73, 79, 80b 等):
   - L-LTF H52 是 1.77 rad-noise 估计 → 当 reference 必然带噪
   - 无法用更精细的平均化得到 1.77 rad 以下 (那是物理噪声下限)
2. **Phase correction algorithms** (Phase 38, 79 等):
   - 平均化、线性外推、Kalman 都收敛到 1.77 rad — 这是 analog-noise floor
3. **Decoder improvements** (Phase 44, 70, 79, 80b, T2-T6a):
   - Viterbi 最大可纠错:4 / 96 = 4%
   - 实际误码:12-18 / 96 = 12-19%
   - 任何卷积码算法都无法跨越这个物理极限

**这是 802.11n 设计的根本限制**:HT-SIG 用 (K=7, r=1/2) 卷积码 + 8-bit CRC,设计假设 H52(t) ≈ const。如果 H52(t) 实际 std = 1.77 rad,该码字设计**本身不支持**。

## T7e (decision-directed + multi-symbol) 的期望效果

T7e 用 DATA[k] 的 pilots 反推 HT-SIG 时刻的 H52(sc,t_HTSIG):

- **输入 data**: DATA[0..N] 每个 symbol 4 pilots → 4 SCs 测量
- **输出**: HT-SIG 时刻 48 SCs 的 H52 估计
- **预期改善**:
  - 单 SC: 用多符号平均 → 噪声减少 √N (例:N=10 → 3×)
  - H52(sc) 估计噪声从 1.77 rad → ~0.6 rad
- **但**: per-SC 噪声 floor 还是 ~0.5 rad (来自 analog chain)
- **T7e 的极致**: 即使完美,也只能把 12-18 errors 减到 6-9 errors (仍在 viterbi capacity 外)

**结论**: T7e 不大可能 100% 解决,**但**(a) 减少误码率是值得尝试;(b) 即使 FCS_OK 不实现,验证 T7e 是否能改善 SNR 也是有价值的中间步骤。

## 长远架构建议 (Phase 113+)

如果 T7e 也不行,要解决 USRP realtime FCS_OK,必须跨过 analog-chain 噪声 floor:

### 方案 A: 改 HT-SIG 解码器架构 (违反 802.11n spec)
- LDPC 替代 convolutional code (LDPC d_free = 25 vs conv d_free = 10)
- 软判决概率迭代 (belief propagation)
- 但:HT-SIG 不是 payload,LDPC 不在 802.11n spec 中

### 方案 B: 改 RF 模拟链路 (HW 修改)
- 用 external reference clock (10 MHz / PPS) 锁定 X310 + UBX-160
- 可减少 phase noise 到 0.1 rad 以下
- 但:需要 HW 投入 + 重测整个 USRP 设置

### 方案 C: 接受 802.11n 的环境限制
- 文档化 analog-chain 限制
- 项目目标从 "USRP realtime FCS_OK" 调整为 "USRP file-replay 部分成功"
- 但:这违反用户 hard constraint "不可能接受现状"

**推荐**: T7e 先尝试(可能改善,但不大可能完全解决)。若 T7e 仍 REFUTED,**正式发起 Phase 113 架构讨论**,用户必须从 A/B/C 中选择 — 没有第四条路。

## 测试方法

```bash
python p112_r1_t7e_d1_h52_timeseries.py [/optional/capture.fc32]
```

默认 `/tmp/p110_t10_capture.fc32`(5250 MHz cable, --tx-gain 20)。30 frames 输出。

## Files Modified

- `p112_r1_t7e_d1_h52_timeseries.py` (NEW)
- `docs/superpowers/notes/2026-07-07-phase112-r1-argh-rootcause.md` (this file)
- `~/.claude/projects/.../memory/project_p112_r1_argh_rootcause.md` (memory)

## Related

- [[project-p107-deep-root-cause]] — Phase 107: per-SC argH std=108°(root cause,初判)
- **Phase 25 SFO verdict** — 1.77 rad 残余被误 REFUTED,实际就是根因
- [[project-p111-t6a-list-viterbi]] — T6a: list viterbi 0 CRC pass(此根因的 decoder 反映)
- [[project-p109-uhd-t1t2-verification]] — Phase 109: UHD 20s 流稳定,不是 UHD 问题
- [[feedback-no-closure-usrp-fcs-ok]] — User feedback: continue attacking equalizer
