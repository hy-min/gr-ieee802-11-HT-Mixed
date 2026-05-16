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
