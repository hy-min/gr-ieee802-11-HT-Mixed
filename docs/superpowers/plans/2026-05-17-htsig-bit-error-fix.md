# HT-SIG High Bit Error Rate Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 HT-SIG 高错误率问题（39% 比特错误），找出比特错误的根本原因。

**Architecture:** HT-SIG CRC 仍然失败，尽管 QBPSK 映射已修复。19/48 比特错误表明问题不在比特映射，而在更深层的位置——可能是信道估计、FFT 数据、或同步问题。

**Tech Stack:** GNU Radio 3.10, C++ (frame_equalizer_impl.cc, ht_symbol_splitter_impl.cc, sync_long.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**当前状态：**
- QBPSK 比特映射已修复 ✓
- HT-SIG CRC 仍然失败 ✗
- 19/48 比特错误（39% 错误率）

**症状分析：**
- 错误率太高，不是随机噪声而是系统性错误
- LTF 相位已修复 ✓
- FFT 窗口定时已修复 ✓
- 但 HT-SIG 仍有近 40% 比特错误

**可能的原因：**
1. 信道估计 H 的幅度/相位不正确
2. FFT 数据被 CP 污染
3. SPLITTER 输出的 FFT 窗口包含错误的数据
4. Viterbi 解码器输入数据不正确
5. HT-SIG 旋转补偿不正确

---

## 文件映射

**主要文件：**
- `lib/frame_equalizer_impl.cc` - HT-SIG 解码
- `lib/ht_symbol_splitter_impl.cc` - FFT 边界
- `lib/sync_long.cc` - 同步
- `lib/equalizer/ls.cc` - LS 信道估计
- `test_mcs_end_to_end.py` - 测试脚本

---

## Task 1: 对比 TX 和 RX 的 HT-SIG 原始数据

**Files:**
- Analyze: `test_mcs_end_to_end.py` - TX HT-SIG 探针
- Analyze: `lib/frame_equalizer_impl.cc` - RX HT-SIG 探针

**Step 1: 获取 TX HT-SIG 原始比特**

TX ht_bits[0:24] = 000000000110010000000000

**Step 2: 获取 RX HT-SIG 解码比特**

decoded_bits[0:34] = 1110110000011001011001100101100010 (第一个RX包)

**Step 3: 计算 Hamming 距离**

TX vs RX 对比 (前24位):
```
TX: 000000000110010000000000
RX: 111011000001100101100110
     ✗✗✗   ✗✗  ✗✗✗✗  ✗  ✗✗  ✗✗
```

错误分布:
- Bit 0,1,2: TX=0 RX=1 (错误)
- Bit 3: TX=0 RX=0 (正确)
- Bit 4,5: TX=0 RX=1 (错误)
- Bit 6,7,8: TX=0 RX=0 (正确)
- Bit 9,10: TX=1 RX=0 (错误)
- Bit 11,12,13: TX=0 RX=1 (错误)
- Bit 14: TX=0 RX=0 (正确)
- Bit 15: TX=0 RX=1 (错误)
- Bit 16: TX=0 RX=0 (正确)
- Bit 17,18: TX=0 RX=1 (错误)
- Bit 19,20: TX=0 RX=0 (正确)
- Bit 21,22: TX=0 RX=1 (错误)
- Bit 23: TX=0 RX=0 (正确)

**结果: 15/24 bits wrong = 62.5% error rate**

**错误特征分析:**
- 错误分散在全部24个比特位置，不是集中在某几个位置
- 每6-bit组的错误数: 5, 3, 4, 3 (相对均匀分布)
- 错误不是简单翻转(如 QBPSK 映射反转会导致 100% 翻转)
- 高错误率(62.5%)远超随机噪声(30dB SNR应<0.1%)

**结论:**
- 错误是系统性的，不是随机噪声
- 错误分散在所有比特位置，排除简单的位翻转或交织问题
- 根本原因可能在 FFT 输入数据、信道估计、或同步问题

---

## Task 2: 检查 HT-SIG FFT 输入数据

**Files:**
- Analyze: `lib/frame_equalizer_impl.cc` - d_early_eqsym 探针

**Step 1: 检查 HT-SIG0 FFT 输出**

查看 SPLITTER 是否在正确的位置输出 HT-SIG FFT：
```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE"
```

**Step 2: 检查 d_early_eqsym 的值**

在 frame_equalizer_impl.cc 中添加探针，打印 HT-SIG0 和 HT-SIG1 FFT 的前几个样本值。

**Step 3: 验证 FFT 数据是否正确**

检查 FFT 输出是否包含有效的复数值（不是零或 NaN）。

**Task 2 分析结果:**

**SPLITTER FFT 输出位置:**
- HT-SIG0 (type=3) at rel_idx=303 - ✓ 位置正确
- HT-SIG1 (type=4) at rel_idx=383 - ✓ 位置正确

**HT-SIG0 FFT 输入数据异常:**
```
[SPLITTER_FFTPROBE] type=3 rel_idx=303 td_energy=35.0901 peak_mag=1.8896@41 first=0.0000+0.0000i last=0.6811-0.7192i buf_filled=0
```
- **问题: d_buffer[0] = 0.0000+0.0000i (第一个样本恰好为零!)**
- td_energy=35.09 (约为 L-SIG 64.31 的一半，符合 QBPSK 的预期)

**HT-SIG0 原始子载波相位 (HTSIG0_RAW):**
```
sc[-26]: -1.8°   sc[-25]: -8.3°   sc[-24]: +128.3°  sc[-23]: -105.3°
sc[-22]: +101.3° sc[-20]: -45.0°  sc[-19]: -36.5°   sc[-18]: -20.1°
```
- 相位散落在所有象限，不是预期的 QBPSK 聚集点 (45°, 135°, 225°, 315°)
- 表明 FFT 窗口对齐有问题或数据被破坏

**信道估计 (CHAN_EST_FULL):**
- H 幅度 ~8-9 (一致)
- LTF0/LTF1 比率 ~1.0 (良好一致性)
- 相位在频率上大致线性 (简单信道的合理特征)
- **信道估计本身看起来是正确的**

**结论:**
FFT 数据被破坏。SPLITTER 在 HT-SIG0 的 FFT 窗口捕获了错误的数据区域——可能是 CP/过渡区域而不是实际的 HT-SIG0 DATA 部分。

**根本原因:**
d_buffer[0] = 0 的问题表明 SPLITTER 的 rel_idx 计算可能有偏差，导致在 HT-SIG0 DATA 开始前一个样本处开始填充缓冲区。

---

## Task 3: 检查信道估计 H 的质量

**Files:**
- Analyze: `lib/frame_equalizer_impl.cc` - Hhdr52 探针
- Analyze: `lib/equalizer/ls.cc` - LS 信道估计

**Step 1: 检查 LTF 估计的 H 值**

查看 CHAN_EST 探针输出：
```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "CHAN_EST" | head -10
```

**Step 2: 检查 H 的幅度和相位**

H 的幅度应该接近信道增益（~1.0）。如果幅度太小或太大，说明信道估计有问题。

**Step 3: 检查 H 是否用于正确的 FFT 窗口**

确保用于 HT-SIG 解码的 H 与用于 LTF 估计的 H 一致。

**Task 3 分析结果:**

**CHAN_EST 探针输出:**
```
[CHAN_EST_DEBUG] i=0 lltf0=7.3241+4.6377i tx=1.0000+0.0000i H=7.3241+4.6377i kFftNormalize=8.8752
[EQ_TRACE] i=0 sc=-26 rx=7.1410+5.0921i(|rx|=8.7706) H=7.3241+4.6377i(|H|=8.6690) rx/H=1.0102+0.0556i(eq_bef_rot=1.0117) rot_ph=-0.2deg eq=1.0104+0.0519i
```

**H 幅度分析:**
- H 幅度 ~8.67-8.87 (约等于 kFftNormalize=8.875)
- kFftNormalize 未应用到 H 计算中（代码注释说"Remove kFftNormalize"但实际未执行）
- 但这不影响均衡输出，因为 rx/H 运算会消除这个缩放因子

**均衡器输出正确:**
- rx/H 幅度 ~1.0（正确）
- 相位 ~-0.9°（接近 0°，与预期的 HT-SIG bit 0 -> -1j + 旋转 一致）

**FFT 输入数据损坏 (根本原因):**
```
[SPLITTER_FFTPROBE] type=2 rel_idx=223 td_energy=64.3067 first=-0.6511+0.0543i - L-SIG 正确
[SPLITTER_FFTPROBE] type=3 rel_idx=303 td_energy=35.0901 first=0.0000+0.0000i - HT-SIG0 错误 (d_buffer[0]=0!)
[SPLITTER_FFTPROBE] type=4 rel_idx=383 td_energy=66.3679 first=0.4112+0.0922i - HT-SIG1 正确
[SPLITTER_FFTPROBE] type=5 rel_idx=463 td_energy=66.7442 first=-0.1119+0.9699i - HT-STF 正确
```

- HT-SIG0 的 td_energy=35.1 (约为 L-SIG 64.3 的一半)
- HT-SIG0 的 first=0.0000+0.0000i 表示 d_buffer[0]=0
- 这是 QBPSK 不可能出现的值 - 说明 FFT 窗口捕获了错误的数据

**结论:**
- 信道估计 H 质量可接受（rx/H 运算后幅度 ~1.0）
- HT-SIG CRC 失败不是由信道估计引起
- **根本原因是 SPLITTER FFT 窗口对齐问题 - HT-SIG0 的 d_buffer[0]=0**

---

## Task 4: 检查 Viterbi 解码器输入

**Files:**
- Analyze: `lib/frame_equalizer_impl.cc` - VITERBI_IN 探针

**Step 1: 检查 Viterbi 输入数据**

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "VITERBI_IN" | head -10
```

**Step 2: 对比 Expected TX 和 Actual RX**

Expected TX 是编码前的 48 个比特。
Actual RX 是解交织后的 48 个比特。
 Hamming 距离应该很小（0-5 个错误）。

**Step 3: 追踪错误的来源**

如果 Hamming 距离很大，问题在 FFT → EQ → 解映射/解交织链中。

---

## Task 5: 修复问题

**Files:**
- Modify: 根据分析结果修改相应文件

**Step 1: 根据 Task 1-4 的分析，确定修复方案**

**Step 2: 实现修复**

**Step 3: 运行测试验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "CRC|pass|fail" | tail -20
```

---

## Task 6: 最终验证

**Files:**
- Test: `test_mcs_end_to_end.py` - 端到端测试

**Step 1: 运行完整测试**

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1
```

**Step 2: 验证 HT-SIG CRC 通过**

**Step 3: 清理调试日志**

**Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: fix HT-SIG high bit error rate"
```

---

## Task 6 验证结果 (2026-05-17)

### 测试结果

```
[SPLITTER_FFTPROBE] type=3 rel_idx=303 td_energy=35.0901 peak_mag=1.8896@41 first=0.0000+0.0000i last=0.6811-0.7192i buf_filled=0
[EQ][HT-SIG] parse failed: lsig=2 htsig=3/4
```

### 关键发现

1. **HT-SIG0 d_buffer[0]=0 问题仍然存在**
   - type=3 rel_idx=303 的 FFT 输出显示 first=0.0000+0.0000i
   - 这表明 FFT 窗口捕获了错误的数据

2. **td_energy=35.1 是 L-SIG (64.3) 的一半**
   - 这符合 QBPSK 的预期能量
   - 但第一个样本为零是不合理的

3. **Carryover 边界修复 (b2db89f) 已应用但未解决问题**
   - 修复将边界从 271/351/431 更正为 303/383/463
   - 但 HT-SIG0 的 d_buffer[0]=0 问题仍然存在

4. **d_frame_start_abs 的影响**
   - d_frame_start_abs=176 对某些符号有效但破坏 HT-SIG0
   - d_frame_start_abs=0 修复 HT-SIG0 但破坏 HT-STF
   - 这表明边界条件与特定的 d_frame_start_abs 值耦合

### 根本原因分析

HT-SIG0 的 FFT 窗口正在捕获 CP/过渡区域而不是实际的 HT-SIG0 DATA 部分。

可能的原因：
1. SPLITTER 在错误的 rel_idx 位置开始填充缓冲区
2. CP 跳过逻辑在 HT-SIG0 DATA 开始前没有正确重置缓冲区
3. d_frame_start_abs 在不同的工作调用之间发生变化

### 下一步

1. 追踪 SPLITTER_WORK 日志以了解 d_frame_start_abs 的实际值
2. 检查第二个工作调用中的缓冲区状态
3. 验证在 HT-SIG0 DATA 开始时 (rel_idx=240) 缓冲区是否正确重置
