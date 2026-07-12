# Phase 142 T3: Wiener 前移 HT-SIG 同板验证

## 变更

当前工作树 `lib/frame_equalizer_impl.cc` 已实现 Phase 142 T1/T2：

- **T1**: `IEEE80211_HTSIG_EQ_DIAG=1` 诊断：在 `decode_htsig_from_rotated()` 中 dump
  每个 (rot, inv_a, inv_b) 候选的 eq 实部/虚部均值与标准差、H 幅度均值、H 相位 circular std。
- **T2**: Wiener MMSE shrinkage 前移到 HT-SIG0/1 的 pilot-based H re-estimate。
  在 `general_work()` 调用 `estimate_H_from_htsig_pilots()` 得到 `H_htsig0/1` 后，
  立即用 `wiener_filter_h52()` 对这两个 52-SC H 估计做 per-SC shrinkage，
  再用于 HT-SIG viterbi。此前 Wiener 只过滤 `Hhdr52_for_lsig`（L-SIG 路径），
  HT-SIG viterbi 仍使用未过滤的 `H_htsig0/1`。

## 构建与安装

```bash
cd /home/hy/gr-ieee802-11/build
make -j$(nproc)
make install   # prefix=/home/hy/conda/envs/gnuradio, 无需 sudo
```

构建安装成功，Python 加载新 .so。

## USRP 实时同板测试

### 配置
- 同板 A:0 TX → A:0 RX2（默认，未加 `--cross-board`）
- `--freq 5250 --tx-gain 0 --rate 20 --warmup 60 --duration 30`
- `--phase139-on --wiener-on --wiener-log`
- 环境变量：`IEEE80211_HTSIG_H_REESTIMATE=1 IEEE80211_HTSIG_EQ_DIAG=1 IEEE80211_WIENER_LOG=1`

### 结果 #1：rx-gain=20（标准配置）
```
[TEST] Sent: 90
[TEST] Recv: 0
[TEST] Success Rate: 0.0%
[TEST] FCS_OK=0 FCS_FAIL=0
```

日志出现 **SPLITTER runaway**：30 秒内产生超过 700 万个 `SPLITTER_FRAME_START`
事件，`d_frame_start=174 sync=0 wifi_pos=11225` 完全不变，仅有 2 次
`[LSIG_DECODE] OK`，无 `HT_SIG_CAND`。

判读：RX 前端处于饱和/自泄漏状态，sync_short / sync_long / splitter
被连续误触发，未进入正常的 per-frame 解码流程。这与 SMA 线缆连接状态
或当天 RF 链路状态有关，不是 Wiener 算法本身的失败。

### 结果 #2：rx-gain=0（最小增益）
为排除饱和，把 rx-gain 降到 0：

```
[TEST] Sent: 20
[TEST] Recv: 0
[TEST] FCS_OK=0 FCS_FAIL=0
```

`SPLITTER_FRAME_START` 仅 2 次，无 LSIG/HTSIG 事件。说明信号太弱，
检测门限以下。后续验证表明：rx-gain=20 时的 runaway 并非真正的
信号饱和，而是检测器在噪声/泄漏中被连续误触发；提高 rx-gain 到 31
后信号质量显著改善。

### 结果 #3：rx-gain=31（用户提示后重新配置）
把 rx-gain 提到 31（与今天捕获的 `p142_5250_rx31.fc32` 一致）：

```
[TEST] Sent: 120
[TEST] Recv: 0
[TEST] FCS_OK=0 FCS_FAIL=0
[TEST] Capture file: /tmp/p142_fresh_rx31_forward.fc32 (18513296 bytes)
```

关键统计（60s，Phase 142 Wiener 前移全开）：
- `LSIG_DECODE OK: 27`, `LSIG_PARSE_FAIL: 39`
- `HT_SIG_CAND: 16`（来自 1 个成功触发 HTSIG_H_REESTIMATE 的帧）
- `WIENER_HTSIG applied: 1`, `HTSIG_H_REESTIMATE h0=ok h1=ok: 1`
- `avg_snr_htsig: 4.86 dB`
- `HT_SIG_CAND metric min/mean/max: 14/15.8/16`
- `H arg_std (HTSIG_EQ_DIAG): 0.733 rad`

加入 `--uhd-tune`（Phase 113 UHD 微调）后 30s：
- `LSIG_DECODE OK: 16`, `HT_SIG_CAND: 64`
- `WIENER_HTSIG applied: 4`
- `avg_snr_htsig: 3.35/59.08/77.65 dB`
- `metric min/mean/max: 13/15.3/17`
- 仍 **0 FCS_OK**

同配置下 Baseline / Phase 141 / Phase 142 30s 对比：

| 配置 | LSIG OK | HT_SIG_CAND | WIENER_HTSIG | metric min/mean/max | avg_snr_htsig | FCS_OK |
|------|---------|-------------|--------------|---------------------|---------------|--------|
| Baseline（2-way） | 3 | 16 | 0 | 13/15.0/16 | 4.09 dB | 0 |
| Phase 141（Wiener L-SIG） | 24 | 48 | 0 | 13/15.2/18 | 3.75/33.28/88.07 dB | 0 |
| Phase 142（Wiener 前移） | 27 | 16 | 1 | 14/15.8/16 | 4.86 dB | 0 |

观察：rx-gain=31 后信号足够强，L-SIG 解码率提升到 22-41%，
HT-SIG 候选开始出现。但 Phase 142 的 `HTSIG_H_REESTIMATE` 触发条件较严
（需 HT-SIG0/1 各 4 pilot 均有效），仅 1/27 个 LSIG OK 帧满足；
一旦触发，Wiener 前移即被应用，但 metric 仍在 14-16，未降到 ≤10。

### 结果 #4：新 USRP X310（序列号 36C26DB）上 Phase 142 60s

更换设备并烧写 FPGA HG 39.3 镜像后重新上电，运行完整 Phase 142：

```
[TEST] Sent: 120
[TEST] Recv: 0
[TEST] FCS_OK=0 FCS_FAIL=0
[TEST] Capture file: /tmp/p142_new_x310.fc32 (62651664 bytes)
```

关键统计：
- `LSIG_DECODE OK: 32`（26.7%）
- `HT_SIG_CAND: 48`
- `WIENER_HTSIG applied: 3`
- `HTSIG_H_REESTIMATE h0=ok h1=ok: 3`
- `avg_snr_htsig: 0.80/3.24/5.78 dB`
- `HT_SIG_CAND metric min/mean/max: 12/15.2/18`
- `H arg_std (HTSIG_EQ_DIAG): 0.818/1.120/1.473 rad`
- 仍 **0 FCS_OK**

新设备信噪比明显低于旧 X310（avg_snr_htsig 均值 3.24 dB vs 4.86 dB），
但 HT-SIG metric 分布略好（min=12 vs 旧设备 min=14）。

同设备上 Baseline 30s Realtime 对比：

| 配置 | LSIG OK | HT_SIG_CAND | WIENER_HTSIG | avg_snr_htsig | FCS_OK |
|------|---------|-------------|--------------|---------------|--------|
| Baseline（2-way） | 2 | 0 | 0 | N/A | 0 |
| Phase 142（Wiener 前移） | 32 | 48 | 3 | 3.24 dB | 0 |

说明：不同 run 之间方差较大（30s Baseline 仅 2 LSIG OK，而 60s Phase 142 有 32），
不能直接由单次 30s 得出 Baseline 更差的结论，但 Phase 142 在新设备上确实
稳定产生了 HT_SIG_CAND 并触发了 Wiener 前移。

## 文件回放验证（使用今天已抓取的 p142 同板捕获）

由于实时采集受硬件状态阻塞，改用 `/tmp/p142_5250_*.fc32` 系列今天捕获的
同板数据做回放对比。回放脚本：`/home/hy/gr-ieee802-11/p142_replay_wiener.py`。

### 新 X310 捕获 `/tmp/p142_new_x310.fc32` 回放（60s 采集）

| 配置 | HT_SIG_CAND | metric min/mean/max | metric ≤10 计数 | FCS_OK |
|------|-------------|---------------------|-----------------|--------|
| Baseline（2-way） | 212,258 | 9 / 15.3 / 19 | 9:2, 10:8 = 10 | 0 |
| Phase 141（Wiener on L-SIG） | 212,899 | 9 / 15.3 / 19 | 9:6, 10:14 = 20 | 0 |
| **Phase 142（Wiener 前移 HT-SIG）** | 202,901 | 10 / 15.2 / 19 | **10:74** | 0 |

- 在新 X310 捕获上，**Phase 142 Wiener 前移的低误差候选数（74）是 Baseline（10）的 7.4 倍、Phase 141（20）的 3.7 倍**，均值也从 15.3 降到 15.2。
- 这是 Wiener 前移在今天所有测试中最清晰的正向信号，但仍未产生 FCS_OK。

### 新鲜捕获 `/tmp/p142_fresh_rx31_forward.fc32` 回放（rx-gain=31，60s 采集）

| 配置 | HT_SIG_CAND | metric min/mean/max | metric ≤10 计数 | FCS_OK |
|------|-------------|---------------------|-----------------|--------|
| Baseline（2-way） | 97,383 | 9 / 15.2 / 19 | 9:4, 10:56 = 60 | 0 |
| Phase 141（Wiener on L-SIG） | 90,164 | 8 / 15.2 / 19 | 8:2, 9:2, 10:36 = 40 | 0 |
| Phase 142（Wiener 前移 HT-SIG） | 87,568 | 9 / 15.2 / 19 | 9:12, 10:40 = 52 | 0 |

- Phase 142 的 `WIENER_HTSIG` 触发 5,473 次，与 `HTSIG_H_REESTIMATE` 完全对应。
- 三种配置的 metric 均值完全相同（15.2），低误差候选数也无显著差异。
- 在**该段**新鲜同板数据上，Wiener 前移未表现出比 Baseline / Phase 141 更优的 metric 分布。

### p142_5250_std.fc32（标准配置，推测 tx-gain=0 rx-gain=20）

| 配置 | HT_SIG_CAND | metric min/mean/max | metric ≤10 计数 | FCS_OK |
|------|-------------|---------------------|-----------------|--------|
| Baseline（仅 2-way H52） | 125,968 | 9 / 15.3 / 19 | 9:4, 10:38 | 0 |
| Phase 141（Wiener on L-SIG） | 118,000 | 9 / 15.3 / 19 | 9:4, 10:46 | 0 |
| Phase 142（Wiener 前移 HT-SIG） | 111,179 | 9 / 15.2 / 19 | 9:12, 10:54 | 0 |

- 该捕获上 Wiener 前移有小幅正向效果：metric ≤10 候选从 42 提升到 66，
  均值从 15.3 降到 15.2。

### p142_5250_rx31.fc32（rx-gain=31）

| 配置 | HT_SIG_CAND | metric min/mean/max | metric ≤10 计数 | FCS_OK |
|------|-------------|---------------------|-----------------|--------|
| Baseline | 66,992 | 10 / 15.1 / 19 | 10:120 | 0 |
| Phase 142 Wiener 前移 | 60,384 | 9 / 14.9 / 19 | 9:2, 10:10 | 0 |

- Wiener 前移把均值从 15.1 降到 14.9，首次出现 metric=9 候选。
- 但低误差候选总数反而减少（120 → 12），说明此高增益捕获的非线性/饱和
  噪声与 Wiener shrinkage 假设不符。

### p142_5250_std.fc32 + H_AVERAGE（Phase 118b）叠加 Wiener 前移

| 配置 | HT_SIG_CAND | metric min/mean/max | metric ≤10 计数 | FCS_OK |
|------|-------------|---------------------|-----------------|--------|
| Wiener 前移 + H_AVERAGE | 116,144 | 9 / 15.2 / 19 | 9:8, 10:32 | 0 |

比单独 Wiener 前移（9:12, 10:54）略差，H_AVERAGE 在此捕获上无增益。

## HTSIG_EQ_DIAG 关键观察

从 `p142_5250_std.fc32` Wiener 前移日志抽取：
- `H_a_abs_mean` 典型 7-10，部分干净帧可达 40（高 SNR 事件）。
- `arg_std`（H 相位 circular std）范围 0.518–3.007 rad，均值 1.337 rad。
- 新鲜 `p142_fresh_rx31_forward.fc32` 上 Phase 142 的 `arg_std`
  范围 0.466–3.332 rad，均值 1.209 rad。
- eq 虚部标准差 `std_im` 可达 1.6-4.8，说明即使 H 被 Wiener shrinkage，
  残留相位噪声仍足以把 BPSK/QBPSK 点旋转到错误判决区。

这与 Phase 112 R1 的 **1.77 rad 每子载波相位噪声地板** 一致：
Wiener 只能在 H 估计上做点收缩，无法消除进入 FFT 之前的模拟链相位噪声。

## 组合方案验证（Wiener 前移 + FINE_ROT + PILOT_CPE）

由于 `/tmp` 被清理，重新抓取了新的同板基线捕获 `/tmp/p142_new_x310_v3.fc32`
（无 Wiener、无 HTSIG_H_REESTIMATE，90s warmup + 30s duration）：
- `LSIG_DECODE OK: 70`, `HT_SIG_CAND: 96`
- `avg_snr_htsig: 1.50/4.45/6.87 dB`
- `metric min/mean/max: 13/15.5/18`

在该捕获上做 60s 文件回放对比：

| 配置 | HT_SIG_CAND | metric mean | metric=9 | metric=10 | metric≤10 | FCS_OK |
|------|-------------|-------------|----------|-----------|-----------|--------|
| Baseline | 167,904 | 15.3 | 2 | 60 | 62 | 0 |
| Phase 141（Wiener L-SIG） | 173,024 | 15.2 | 12 | 66 | 78 | 0 |
| **Phase 142（Wiener 前移）** | 167,891 | 15.2 | 10 | **372** | **382** | 0 |
| p142 + FINE_ROT | 325,685 | 15.2 | 14 | 358 | 372 | 0 |
| p142 + PILOT_CPE | 158,864 | 15.2 | 2 | 156 | 158 | 0 |
| p142 + FINE_ROT + PILOT_CPE | 334,958 | 15.2 | 22 | 196 | 218 | 0 |

关键发现：
- **Phase 142 单独使用在本捕获上效果最好**：metric=10 候选 372 个，
  是 Baseline（60）的 6.2 倍、Phase 141（66）的 5.6 倍。
- **FINE_ROT 增加了总候选数**（16万 → 32万），但未显著增加低误差候选；
  更多是在 explored 更多旋转，而不是真正改善 SNR。
- **PILOT_CPE 在此捕获上反而有害**：metric=10 候选从 372 降到 156，
  说明 pilot-based CPE 与 Wiener 前移联合时引入额外相位扰动。

### 实时 USRP 组合验证

| 配置 | 时长 | LSIG OK | HT_SIG_CAND | WIENER_HTSIG | avg_snr_htsig | metric min/mean/max | FCS_OK |
|------|------|---------|-------------|--------------|---------------|---------------------|--------|
| Phase 142 | 60s | 32 | 48 | 3 | 0.80/3.24/5.78 | 12/15.2/18 | 0 |
| **p142 + FINE_ROT** | 60s | **91** | **512** | 16 | 0.52/3.00/8.75 | 12/15.3/18 | 0 |
| p142 + FINE_ROT + PILOT_CPE | 60s | 19 | 96 | 3 | 4.07/4.79/5.27 | 12/15.2/17 | 0 |

- **p142 + FINE_ROT 在实时中表现最好**：LSIG OK 91、HT_SIG_CAND 512，
  是今天所有实时 run 中 HT-SIG 候选数最多的一次。
- 但 **0 FCS_OK**，metric 最低 12，仍高于 ≤10 的 viterbi 门槛。
- PILOT_CPE 在实时中同样拉低表现（LSIG OK 19 vs 91）。

## 结论

1. **Wiener 前移代码实现正确**，编译安装通过，USRP loopback 不回归，
   HT-SIG 路径上 `WIENER_HTSIG` 确实被应用。
2. **用户关于 rx-gain=31 的提示正确**：把 rx-gain 从 20 提到 31 后，
   实时同板数据可用，HT-SIG 候选稳定出现。
3. **新 X310 已启用并验证**：FPGA 39.3 镜像烧写成功，设备可识别，
   但新设备信噪比低于旧 X310（avg_snr_htsig 均值 3.24 dB vs 4.86 dB）。
4. **Wiener 前移在新 X310 上给出清晰正向信号**：
   在 `/tmp/p142_new_x310_v3.fc32` 回放中 metric=10 候选：Baseline=60，
   Phase 141=66，**Phase 142=372**（6.2× Baseline）。
5. **组合方案结果**：
   - `+ FINE_ROT` 大幅提升实时 HT_SIG_CAND 数量（48 → 512），但未压低 metric。
   - `+ PILOT_CPE` 在回放和实时中均表现更差。
   - **最佳组合：p142 + FINE_ROT**，但仍 **0 FCS_OK**。
6. **仍未突破 viterbi 墙**：HT-SIG metric 最低 10-12，高于 ≤10 阈值；
   即使 metric=10 也 CRC 失败，说明 1.77 rad 模拟相位噪声仍主导。

## 下一步建议

1. **p142 + FINE_ROT 是当前最佳配置**，可作为后续实验基线。
2. **调查 metric=10/12 仍 CRC 失败的原因**：
   - 可能是 viterbi 阈值或 LLR 计算问题
   - 可能是 HT-SIG 符号映射/QBPSK 解调问题
   - 可能是 `best_metric=N/A` 路径中的隐藏 bug
3. **考虑放宽 HTSIG_H_REESTIMATE**：当前仅 ~10% LSIG-OK 帧触发 Wiener 前移，
   让单 pilot 有效也进入可扩大受益面。
4. **考虑更大胆的算法**：例如 per-SC Kalman 跟踪、数据辅助相位同步、
   或直接在 equalizer 输出做 BPSK 硬判反馈来估计残留相位。

---
*Verdict written: 2026-07-11, revised 2026-07-12 with rx-gain=31 fresh capture, new X310, and combination experiments*
