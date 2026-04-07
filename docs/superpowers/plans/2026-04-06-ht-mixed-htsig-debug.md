# HT-Mixed Mode HT-SIG Decoding Debug Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Debug why HT-SIG decoding fails in gr-ieee802-11 HT-Mixed mode loopback test. FFT output phase doesn't match FFT_LONG reference, causing HT-SIG bits to decode incorrectly.

**Architecture:** The gr-ieee802-11 library implements IEEE 802.11n HT-Mixed mode TX/RX. The issue is in the RX path: sync_long detects frame start, frame_equalizer performs channel estimation using L-LTF and tries to decode HT-SIG. FFT output (after OFDM receiver FFT) doesn't match expected FFT_LONG values.

**Tech Stack:** GNU Radio, gr-ieee802-11 (C++), Python, FFTW, VOLK

---

## 背景与问题分析

### 当前状态

| 指标 | 状态 | 说明 |
|------|------|------|
| d_frame_start 检测 | ✅ 约 70% 为 176 | L-LTF 数据开始位置 |
| FFT 幅度 | ✅ 约 8-14 | 接近预期 8.875 |
| FFT 相位 | ❌ 完全不匹配 | 核心问题 |
| HT-SIG0 检测 | ✅ 部分成功 | 但比特可能错误 |
| HT-SIG1 检测 | ❌ 比特全 1 | 应该是混合的 0/1 |
| HT-SIG 解码 | ❌ 始终失败 | parse failed |

### 关键调试观察

```
# 当 d_frame_start=176 时，FFT 输出：
raw FFT[6-10] = -6.385+3.999i -6.480+-1.500i -7.494+-6.401i 2.885+6.985i
FFT_LONG[6-10] = -7.380+-4.931i 6.861+5.630i -6.276+-6.276i 5.630+6.860i

# CFO 估算值异常（对于环回应该接近 0）：
CFO = ±0.04 (过大)
```

### 可能原因

1. **CFO 估算错误**：arg() 函数相位卷绕导致 CFO 估算不准确
2. **CFO 校正公式错误**：COPY 状态中的 CFO 校正方向或公式有误
3. **FFT 窗口对齐问题**：虽然 d_frame_start=176，但 OFDM 接收器 FFT 可能未在正确窗口执行
4. **FFT_LONG 计算错误**：预计算的 FFT_LONG 数组与实际 L-LTF FFT 不匹配

---

## 文件清单

| 文件 | 责任 |
|------|------|
| `lib/sync_long.cc` | 帧同步、CFO 估算、d_frame_start 检测 |
| `lib/equalizer/ls.cc` | LS 信道估计，使用 FFT_LONG |
| `lib/frame_equalizer_impl.cc` | HT-SIG 解码、Viterbi、帧均衡 |
| `examples/test_loopback_noqt.py` | 环回测试脚本 |

---

## 调试任务

### Task 1: 隔离 TX/RX 问题

**目标**: 验证 TX 端是否正确生成 HT-Mixed 模式信号

**Files:**
- Modify: `examples/test_loopback_noqt.py` - 添加 TX 信号采样保存

- [ ] **Step 1: 添加 TX 信号采样代码**

在 test_loopback_noqt.py 的 TX 路径中，添加代码将 HT-Mixed 模式的前导码采样保存到文件：

```python
# 在 TX 路径中添加 (在 ofdm_cyclic_prefixer 之后)
self.connect(..., self.file_sink_tx, ...)
# 或使用 qt_time_plot 或其他探针
```

- [ ] **Step 2: 运行测试并保存 TX 信号**

```bash
cd /home/hy/gr-ieee802-11/build
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | head -50
# TX 信号应保存到 /tmp/tx_signal.cfile
```

- [ ] **Step 3: 验证 TX 信号格式**

使用 Python 加载 TX 信号并验证：
- L-STF (短训练符号) 是否存在
- L-LTF (长训练符号) 是否存在
- L-SIG, HT-SIG, HT-STF, HT-LTF 是否按顺序排列

```python
import numpy as np
tx_signal = np.fromfile('/tmp/tx_signal.cfile', dtype=np.complex64)
print(f"TX signal length: {len(tx_signal)}")
# 验证前导码结构
```

- [ ] **Step 4: 提交**

```bash
git add examples/test_loopback_noqt.py
git commit -m "debug: add TX signal capture for HT-Mixed mode"
```

---

### Task 2: 验证 CFO 估算

**目标**: 检查 CFO 估算是否正确，验证 CFO 估算公式

**Files:**
- Modify: `lib/sync_long.cc:260-270` - 添加 CFO 调试输出

- [ ] **Step 1: 在 search_frame_start() 中添加 CFO 详细调试**

修改 `lib/sync_long.cc`，在 CFO 估算处添加详细调试：

```cpp
// 在 d_freq_offset 估算后添加
fprintf(stderr, "[SYNC_CFO] diff=%d, arg(vec[i])=%.4f, arg(vec[k])=%.4f, arg(product)=%.4f, CFO=%.6f\n",
        diff,
        std::arg(get<0>(vec[i])),
        std::arg(get<0>(vec[k])),
        std::arg(get<0>(vec[i]) * std::conj(get<0>(vec[k]))),
        d_freq_offset);
```

- [ ] **Step 2: 重新构建**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so
```

- [ ] **Step 3: 运行测试并检查 CFO 值**

```bash
cd /home/hy/gr-ieee802-11/build
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | grep "SYNC_CFO" | head -20
```

- [ ] **Step 4: 分析 CFO 估算**

对于环回测试，CFO 应该接近 0。如果 CFO 值异常：
- 检查 arg() 函数是否正确处理相位卷绕
- 考虑使用 atan2 直接计算相位差

- [ ] **Step 5: 提交**

```bash
git add lib/sync_long.cc
git commit -m "debug: add CFO estimation details"
```

---

### Task 3: 验证 FFT_LONG 计算

**目标**: 确认 FFT_LONG 数组与实际 L-LTF FFT 匹配

**Files:**
- Create: `examples/verify_fft_long.py`
- Modify: `lib/equalizer/ls.cc` - 可选修改

- [ ] **Step 1: 创建 FFT_LONG 验证脚本**

```python
#!/usr/bin/env python
"""验证 FFT_LONG 计算是否正确"""
import numpy as np

# LONG 从 sync_long.cc
LONG = np.array([
    -0.0455-1.0679j, 0.3528-0.9865j, 0.8594+0.7348j, 0.1874+0.2475j,
    # ... (完整 64 点)
], dtype=np.complex64)

# 计算 FFT
fft_result = np.fft.fft(LONG)

print("FFT of LONG (first 12 bins):")
for i in range(12):
    print(f"  bin {i}: {fft_result[i].real:.4f}{fft_result[i].imag:+4.4f}j  magnitude={np.abs(fft_result[i]):.4f}")

# 与 ls.cc 中的 FFT_LONG 比较
FFT_LONG_expected = [
    -0.0002+0.0000j, 8.8326+0.8699j, -8.7047-1.7315j, ...
]
print("\n比较结果:")
for i in range(12):
    diff = abs(fft_result[i] - FFT_LONG_expected[i])
    print(f"  bin {i}: diff={diff:.6f}")
```

- [ ] **Step 2: 运行验证脚本**

```bash
/home/hy/conda/envs/gnuradio/bin/python examples/verify_fft_long.py
```

- [ ] **Step 3: 如有差异，更新 FFT_LONG**

如果计算出的 FFT 与 FFT_LONG 有显著差异，更新 `lib/equalizer/ls.cc` 中的 FFT_LONG 数组。

- [ ] **Step 4: 提交**

```bash
git add lib/equalizer/ls.cc examples/verify_fft_long.py
git commit -m "debug: verify FFT_LONG calculation"
```

---

### Task 4: 禁用 CFO 校正测试

**目标**: 确认 CFO 校正是否是问题根源

**Files:**
- Modify: `lib/sync_long.cc:161-168` - 确保 CFO 校正被禁用

- [ ] **Step 1: 确认 CFO 校正被禁用**

检查 `lib/sync_long.cc` 中 COPY 状态的 CFO 校正代码：

```cpp
if (rel >= 0 && (rel < 128 || ((rel - 128) % 80) > 15)) {
    // 确保 CFO 校正对小值被禁用
    if (std::abs(d_freq_offset) > 0.001) {
        out[o] = in_delayed[i] * exp(gr_complex(0, -d_offset * d_freq_offset));
    } else {
        out[o] = in_delayed[i];  // 无 CFO 校正
    }
    o++;
}
```

- [ ] **Step 2: 修改阈值为 0（强制禁用 CFO 校正）**

```cpp
if (std::abs(d_freq_offset) > 100.0) {  // 设为极大值，强制禁用
    out[o] = in_delayed[i] * exp(gr_complex(0, -d_offset * d_freq_offset));
} else {
    out[o] = in_delayed[i];
}
```

- [ ] **Step 3: 重新构建并测试**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -3
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | grep -E "FFT_LONG|HT-SIG\] parse" | head -20
```

- [ ] **Step 4: 分析结果**

如果禁用 CFO 校正后 FFT 输出仍然不匹配 FFT_LONG，说明问题不在 CFO 校正。

- [ ] **Step 5: 提交**

```bash
git add lib/sync_long.cc
git commit -m "debug: disable CFO correction for testing"
```

---

### Task 5: 验证 Viterbi 解码器

**目标**: 确认 Viterbi 解码器使用正确的多项式和工作

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:659-660` - 添加 Viterbi 输入调试

- [ ] **Step 1: 在 Viterbi 解码前打印输入比特**

修改 `lib/frame_equalizer_impl.cc`，在 viterbi_decode_133_171 调用前添加：

```cpp
// 在 enc96 填充后添加
fprintf(stderr, "[VITERBI_IN] enc96[0:24] = ");
for (int i = 0; i < 24; i++) fprintf(stderr, "%d", enc96[i]);
fprintf(stderr, "\n");
```

- [ ] **Step 2: 重新构建并测试**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -3
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | grep "VITERBI_IN" | head -10
```

- [ ] **Step 3: 验证 Viterbi 输入**

对于 HT-SIG，期望的 Viterbi 输入是 96 个比特（48 比特 × 2 符号）。验证：
- 比特不是全 0 或全 1
- 比特模式与 TX 发送的 HT-SIG 编码匹配

- [ ] **Step 4: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "debug: add Viterbi input debugging"
```

---

### Task 6: 检查 HT-SIG 符号位置映射

**目标**: 确认 HT-SIG 符号被正确提取

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:1785-1810` - 检查 d_early_eqsym 提取

- [ ] **Step 1: 在 extract_header52_from_sym64 调用处添加调试**

```cpp
// 在 extract_header52_from_sym64 调用后添加
fprintf(stderr, "[EXTRACT_HT_SIG] rel=%d, sym64[6-10] = ", d_sym_idx);
for (int i = 6; i < 10; i++) {
    fprintf(stderr, "%.3f+%.3fi ", sym64[i].real(), sym64[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: 重新构建并测试**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -3
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | grep "EXTRACT_HT_SIG" | head -20
```

- [ ] **Step 3: 验证 HT-SIG 符号位置**

检查 rel=3 (HT-SIG0) 和 rel=4 (HT-SIG1) 的 FFT 输出：
- 幅度应该合理（约 1-10）
- 相位应该在 ±π 范围内

- [ ] **Step 4: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "debug: add HT-SIG symbol extraction debugging"
```

---

## 测试命令汇总

```bash
# 1. 构建
cd /home/hy/gr-ieee802-11/build && make -j4

# 2. 复制库
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so

# 3. 运行测试
cd /home/hy/gr-ieee802-11/build
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | tee /tmp/test_debug.log

# 4. 检查结果
grep "SYNC_CFO\|FFT_LONG\|VITERBI_IN\|EXTRACT_HT_SIG\|HT-SIG\] parse" /tmp/test_debug.log | head -50
```

---

## 预期结果

当所有调试任务完成后，应该能够：

1. ✅ 确认 TX 端正确生成 HT-Mixed 模式信号
2. ✅ CFO 估算值在环回中接近 0
3. ✅ FFT 输出与 FFT_LONG 匹配（幅度和相位）
4. ✅ HT-SIG 比特正确解码（非全 0 或全 1）
5. ✅ HT-SIG CRC 校验通过

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 问题难以复现 | 使用固定的 test_loopback_noqt.py 参数 |
| 调试输出过多 | 使用 grep 过滤关键信息 |
| 修改破坏现有功能 | 每个任务单独测试后提交 |
