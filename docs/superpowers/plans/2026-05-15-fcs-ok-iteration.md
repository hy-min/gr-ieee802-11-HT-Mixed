# FCS OK 持续修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持续修复直到 MCS 测试通过（received_messages >= 1），FCS OK。

**Architecture:** 迭代式调试修复：应用修复 → 验证 → 如果失败，分析新问题 → 继续修复，直到 FCS OK。

**Tech Stack:** GNU Radio blocks, C++, IEEE 802.11a/n OFDM, MCS end-to-end test

---

## 迭代循环：应用 SPLITTER 缓冲区修复并验证

### 迭代 1: 应用 SPLITTER 缓冲区修复

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.h`
- Modify: `lib/ht_symbol_splitter_impl.cc`

**Context:** SPLITTER 存在状态机 bug：`d_buffer_filled` 标志在非边界 FFT 触发时被设为 `true` 但从未清除，导致后续符号（L-SIG、HT-SIG）无法缓冲。

- [ ] **Task 1.1: 添加 d_prev_should_buffer 成员变量**

在 `lib/ht_symbol_splitter_impl.h` 中 `d_wifi_start_accepted` 成员变量后添加：
```cpp
bool d_prev_should_buffer;  // Track previous should_buffer state to detect region transitions
```

- [ ] **Task 1.2: 修改缓冲区填充条件**

将 lib/ht_symbol_splitter_impl.cc 中的：
```cpp
if (should_buffer && !d_buffer_filled) {
    d_buffer[d_buffer_count++] = in[i];
}
```
改为：
```cpp
// Always buffer when should_buffer is true
if (should_buffer) {
    d_buffer[d_buffer_count++] = in[i];
}
```

- [ ] **Task 1.3: 在 general_work 变量声明区域添加 prev_should_buffer**

在 `bool should_buffer = false;` 行之前添加：
```cpp
bool prev_should_buffer = false;  // Track previous should_buffer for region transition detection
```

- [ ] **Task 1.4: 在 should_buffer 计算后添加状态重置逻辑**

在 `bool should_buffer = false;` 行之后，`if (rel_idx < 64)` 判断之前添加：
```cpp
// FIX: When entering a new buffering region (should_buffer just became true),
// reset d_buffer_filled so we can start buffering the new symbol.
if (should_buffer && !prev_should_buffer) {
    d_buffer_filled = false;
    d_buffer_count = 0;
}
prev_should_buffer = should_buffer;
```

- [ ] **Task 1.5: 移除 else 分支中设置 d_buffer_filled=true 的代码**

将：
```cpp
} else {
    // Buffer filled at non-boundary - hold for next boundary
    d_buffer_filled = true;
    // Don't reset d_buffer_count - keep at 64
}
```
改为：
```cpp
} else {
    // Buffer filled at non-boundary position
    // Output the buffered data and reset state so next symbol can be buffered
    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
    produced += d_fft_size;
    d_buffer_count = 0;
    d_buffer_filled = false;
}
```

- [ ] **Task 1.6: 构建项目**

Run: `cd /home/hy/gr-ieee802-11/build && cmake --build . 2>&1 | tail -10`
Expected: 编译成功

- [ ] **Task 1.7: 运行 MCS 测试**

Run: `cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && python3 test_mcs_end_to_end.py 2>&1 | tail -30`
Expected: received_messages >= 1

---

## 验证标准

**FCS OK 定义：** MCS 0 测试中 received_messages >= 1

**验证检查清单：**
- [ ] MCS 0 测试：received_messages >= 1
- [ ] SPLITTER_FFTPROBE 显示 LTF0, LTF1, L-SIG, HT-SIG0, HT-SIG1 都有 FFT 输出
- [ ] L-SIG Hamming diff < 5/48

---

## 迭代决策点

After Task 1.7, check if FCS OK (received_messages >= 1):

**如果 FCS OK：** 完成任务，提交所有更改。

**如果 FCS 仍然失败：** 执行迭代 2：分析新的调试输出，继续修复。

### 迭代 2: 分析新的调试输出并继续修复

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`
- Modify: `lib/frame_equalizer_impl.cc`
- Modify: `lib/sync_long.cc`

**Context:** SPLITTER 缓冲区修复已应用但 FCS 仍然失败。需要分析新的调试输出来确定下一个问题。

- [ ] **Task 2.1: 运行 MCS 测试并分析 SPLITTER_FFTPROBE 输出**

Run: `source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && python3 test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_FFTPROBE|L-SIG|Hamming|received" | head -50`

分析结果：
- 如果所有 preamble 符号都有 FFT 输出 → SPLITTER 修复成功，继续分析 FFT/均衡器
- 如果仍有符号缺失 → 继续修复 SPLITTER

- [ ] **Task 2.2: 检查 L-SIG FFT 数据是否正确**

Run: `source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && python3 test_mcs_end_to_end.py 2>&1 | grep -E "EQ_REF|RX L-SIG" | head -20`

分析 TX L-SIG BPSK 和 RX L-SIG raw FFT 的对比：
- TX 应该是 ±1.0（纯实数 BPSK）
- RX 应该在 0.8-1.2 范围内（经过信道衰减后）
- 如果 RX 幅度远大于 2 → FFT 数据仍然错误

- [ ] **Task 2.3: 根据分析结果继续修复**

根据 Task 2.1 和 2.2 的分析结果，确定下一个问题并修复。

常见问题模式：
1. **SPLITTER 仍有符号缺失** → 继续修复 SPLITTER 状态机
2. **L-SIG FFT 数据错误但存在** → 检查 FFT 边界对齐
3. **L-SIG 解码错误但 FFT 正确** → 检查均衡器和 Viterbi 解码
4. **HT-SIG 解码失败** → 检查 HT-SIG 特定的旋转和均衡

---

## 循环终止条件

**终止条件：** MCS 0 测试中 received_messages >= 1

**最大迭代次数：** 10 次迭代后如果仍然失败，报告无法解决的问题并请求人工干预。

---

## 文件变更日志

每次迭代后记录：
- 修改的文件和行号
- 迭代中发现的新问题
- 修复的效果（received_messages 变化）

**Iteration 1:**
- 修改: `lib/ht_symbol_splitter_impl.h` - 添加 d_prev_should_buffer
- 修改: `lib/ht_symbol_splitter_impl.cc` - 修复缓冲区状态机
- 状态: 待验证

**Iteration 2+:**
- 待定
