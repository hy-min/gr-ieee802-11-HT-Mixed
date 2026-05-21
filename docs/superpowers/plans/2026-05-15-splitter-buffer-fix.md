# SPLITTER 缓冲区状态机修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 ht_symbol_splitter_impl.cc 中的缓冲区状态机 bug，使 LTF0、LTF1、L-SIG、HT-SIG 等符号能正确输出 FFT 数据。

**Architecture:** SPLITTER 的核心职责是：从 sync_long 接收连续样本流，按 preamble 结构提取 64-sample FFT 块，输出给后续 FFT/均衡器。问题根源是 `d_buffer_filled` 标志在非边界 FFT 触发时被设为 `true` 但从未清除，导致后续符号无法缓冲。

**Tech Stack:** GNU Radio blocks, C++, IEEE 802.11a/n OFDM

---

## 问题分析

### 当前行为（错误）

```
LTF0 缓冲过程:
  rel_idx=0-62:  d_buffer_count=0→63, should_buffer=true, d_buffer_filled=false
  rel_idx=63:    d_buffer_count=64 == fft_size → FFT 触发!
                 at_boundary=true → 输出 FFT ✓
                 d_buffer_count=0, d_buffer_filled=false ✓
  rel_idx=64:    d_buffer_count=1, should_buffer=true ✓

LTF1 缓冲过程:
  rel_idx=64-125: d_buffer_count=1→62, should_buffer=true, d_buffer_filled=false
  rel_idx=126:   d_buffer_count=63 (NOT 64!)
  rel_idx=127:   d_buffer_count=64 == fft_size → FFT 触发!
                 at_boundary=true → 输出 FFT ✓
                 d_buffer_count=0, d_buffer_filled=false ✓

L-SIG 缓冲过程:
  rel_idx=128-143: should_buffer=false (L-SIG CP), 跳过
  rel_idx=144-206: d_buffer_count=0→63, should_buffer=true
  rel_idx=207:    d_buffer_count=63 (NOT 64!)
  ...然后问题出现了...
```

**问题：** 在 rel_idx=207 时 d_buffer_count=63，下一个 sample (rel_idx=208) 会：
1. d_buffer_count++ → 64
2. d_buffer_count == fft_size 成立
3. at_boundary 检查：rel_idx=208 不在任何边界条件内，所以 at_boundary=false
4. 进入 else 分支：d_buffer_filled = true，但不输出任何东西！

**后果：**
- d_buffer_count 卡在 64
- 当进入下一个缓冲区域（should_buffer=true）时，由于 d_buffer_filled=true，无法缓冲新数据
- L-SIG FFT 永远无法输出

### 根本原因

```cpp
// 问题代码 (line 473-480):
if (d_buffer_count == d_fft_size) {
    if (at_boundary) {
        // 输出 FFT，重置状态
        d_buffer_count = 0;
        d_buffer_filled = false;
    } else {
        // 问题在这里！只设置 d_buffer_filled=true，但没有任何输出！
        d_buffer_filled = true;
    }
}
```

---

## 文件结构

**Modify:** `lib/ht_symbol_splitter_impl.cc`
- general_work() 函数中的缓冲区管理逻辑

**Header:** `lib/ht_symbol_splitter_impl.h`
- 添加 `d_prev_should_buffer` 成员变量

---

## Task 1: 添加 d_prev_should_buffer 成员变量

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.h:39` (在现有成员变量后添加)

- [ ] **Step 1: 在 ht_symbol_splitter_impl.h 中添加成员变量**

在 `d_wifi_start_accepted` 成员变量后添加：
```cpp
bool d_prev_should_buffer;  // Track previous should_buffer state to detect region transitions
```

---

## Task 2: 修复 general_work 中的缓冲区管理逻辑

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc:255-650`

### Subtask 2a: 移除 d_buffer_filled 检查，简化缓冲逻辑

**目标：** 移除 `if (should_buffer && !d_buffer_filled)` 条件中的 `!d_buffer_filled` 检查

- [ ] **Step 1: 修改缓冲条件**

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

### Subtask 2b: 在进入新缓冲区域时重置状态

**目标：** 当 should_buffer 从 false 变为 true 时（即进入新的缓冲区域），重置 d_buffer_filled 和 d_buffer_count

- [ ] **Step 1: 在 general_work 的 while 循环开始处，在 should_buffer 计算之前，添加状态重置逻辑**

在 `bool should_buffer = false;` 行之后，添加：
```cpp
// FIX: When entering a new buffering region (should_buffer just became true),
// reset d_buffer_filled so we can start buffering the new symbol.
// This fixes the bug where d_buffer_filled remained true after non-boundary
// FFT triggers, preventing subsequent symbols from being buffered.
if (should_buffer && !d_prev_should_buffer) {
    d_buffer_filled = false;
    d_buffer_count = 0;
}
d_prev_should_buffer = should_buffer;
```

注意：`d_prev_should_buffer` 需要在 while 循环外部初始化。在 general_work 函数开头，初始化列表区域添加：
```cpp
// 在 general_work 函数的变量声明区域
bool prev_should_buffer = false;  // 在循环外部维护状态
```

然后在循环内部使用：
```cpp
if (should_buffer && !prev_should_buffer) {
    d_buffer_filled = false;
    d_buffer_count = 0;
}
prev_should_buffer = should_buffer;
```

### Subtask 2c: 移除 else 分支中设置 d_buffer_filled=true 的代码

**目标：** 移除 else 分支中设置 d_buffer_filled=true 的代码，改为输出 FFT

- [ ] **Step 1: 修改 else 分支逻辑**

将 lib/ht_symbol_splitter_impl.cc 中的：
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
    // Buffer filled at non-boundary position (should_buffer just became false)
    // We cannot wait for next boundary because we've already collected 64 samples
    // for the previous symbol. Output it now.
    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
    produced += d_fft_size;
    d_buffer_count = 0;
    d_buffer_filled = false;
}
```

---

## Task 3: 验证修复

**Files:**
- Test: `test_mcs_end_to_end.py` (MCS 0-7 端到端测试)

- [ ] **Step 1: 构建项目**

Run: `cd /home/hy/gr-ieee802-11/build && cmake --build .`
Expected: 编译成功，无错误

- [ ] **Step 2: 运行 MCS 测试**

Run: `source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && cd /home/hy/gr-ieee802-11 && python3 test_mcs_end_to_end.py`
Expected: 接收消息数 > 0（之前是 0）

- [ ] **Step 3: 检查 SPLITTER FFT 输出**

Run: `python3 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE" | head -20`
Expected: 所有 preamble 符号（LTF0, LTF1, L-SIG, HT-SIG0, HT-SIG1）的 buf_filled 应该显示正确的 FFT 输出

- [ ] **Step 4: 检查 L-SIG 解码**

Run: `python3 test_mcs_end_to_end.py 2>&1 | grep "Hamming" | head -5`
Expected: Hamming diff 应该显著降低（之前是 20-30/48，现在应该是 0-5/48）

---

## Task 4: 清理调试探针

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: 移除或减少调试探针的输出频率**

当前代码中有多个静态计数器控制的调试探针。在验证修复后：
1. 保留 `[SPLITTER_FFTPROBE]` 探针（有用）
2. 移除 `[STATE_PROBE]` 探针（太多噪音）
3. 移除 `[TD_PROBE_CHECK]` 和 `[TD_BRANCH]` 探针（已验证正确）
4. 移除 `[SPLITTER_LATE_OUTPUT]` 探针（临时调试用）

---

## Task 5: 提交更改

- [ ] **Step 1: 提交代码更改**

```bash
git add lib/ht_symbol_splitter_impl.cc lib/ht_symbol_splitter_impl.h
git commit -m "fix(splitter): reset buffer state when entering new buffering region

The d_buffer_filled flag was set to true when FFT triggered at non-boundary
positions but was never cleared. This caused subsequent symbols (L-SIG,
HT-SIG) to be unbufferable.

Fix: When should_buffer transitions from false to true (entering new
buffering region), reset d_buffer_filled and d_buffer_count to allow
buffering to continue.
"
```

---

## 验证检查清单

- [ ] LTF0 FFT 输出：buf_filled=0, td_energy~48
- [ ] LTF1 FFT 输出：buf_filled=0, td_energy~63
- [ ] L-SIG FFT 输出：buf_filled=0, td_energy~63
- [ ] HT-SIG0 FFT 输出：buf_filled=0, td_energy~63
- [ ] HT-SIG1 FFT 输出：buf_filled=0, td_energy~63
- [ ] MCS 0 测试：received_messages >= 1
- [ ] L-SIG Hamming diff < 5/48
