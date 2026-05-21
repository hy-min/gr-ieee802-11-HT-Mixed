# SPLITTER HT-SIG FFT 输出修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SPLITTER 在 HT-SIG FFT 边界处无法正确输出的问题，使 HT-SIG0 (rel_idx=271) 和 HT-SIG1 (rel_idx=351) FFT 正确输出。

**Architecture:** SPLITTER 的 d_frame_start_abs 初始化为 176，但当 wifi_start tag 被处理时可能被更新为其他值。当前的 NON_BOUNDARY 消息显示 buffer 在 rel_idx=319 满了，而不是预期的 rel_idx=271。这表明 rel_idx 计算与实际边界不匹配。

**Tech Stack:** GNU Radio 3.10, C++ (ht_symbol_splitter_impl.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**当前状态：**
- SPLITTER 输出：LTF0 (rel_idx=63), LTF1 (rel_idx=143), L-SIG (rel_idx=223) ✓
- SPLITTER 未输出：HT-SIG0 (rel_idx=271), HT-SIG1 (rel_idx=351) ✗
- SPLITTER 在 rel_idx=319, 399, 495 处触发 NON_BOUNDARY（应该只在 rel_idx=271, 351 处输出）
- 差距：实际 rel_idx=319，预期 rel_idx=271，差值为 48

**根本原因分析：**
- NON_BOUNDARY 消息：`current_idx=495 d_frame_start_abs=176 out_rel_idx=319`
- 计算：`out_rel_idx = current_idx - d_frame_start_abs = 495 - 176 = 319`
- 预期 HT-SIG0 边界应该在 `current_idx = 176 + 271 = 447`
- 但实际上 buffer 在 `current_idx=495` 时满了

**关键发现：**
- d_frame_start_abs 在构造函数中初始化为 176
- 但实际处理时，wifi_start tag 可能将其更新为不同的值
- 或者，sync_long 输出的实际起始位置与预期不同

---

## 文件映射

**主要文件：**
- `lib/ht_symbol_splitter_impl.cc` - SPLITTER 实现
- `lib/ht_symbol_splitter_impl.h` - SPLITTER 头文件
- `test_mcs_end_to_end.py` - 测试脚本

**诊断文件：**
- `docs/superpowers/specs/2026-05-14-ltf-phase-inversion-diagnostic.md` - LTF 相位反转诊断规格

---

## Task 1: 验证 d_frame_start_abs 的实际值

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - 添加 d_frame_start_abs 追踪日志
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: 在 SPLITTER work 函数开始时打印 d_frame_start_abs 值**

在 `general_work` 函数开始处，wifi_start tag 处理之前添加：

```cpp
fprintf(stderr, "[SPLITTER_DBG] call=%d ninput_items[0]=%d start_abs_idx=%llu d_frame_start_abs=%lld d_frame_start_known=%d\n",
        work_call, ninput_items[0], (unsigned long long)start_abs_idx, (long long)d_frame_start_abs, d_frame_start_known);
```

- [ ] **Step 2: 运行测试验证 d_frame_start_abs 值**

Run: `LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_DBG"`

Expected: 显示每次调用时 d_frame_start_abs 的实际值

- [ ] **Step 3: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "debug: add d_frame_start_abs tracking in SPLITTER"
```

---

## Task 2: 分析 rel_idx 计算与边界不匹配问题

**Files:**
- Analyze: `lib/ht_symbol_splitter_impl.cc` - 分析 rel_idx 计算逻辑
- Analyze: `lib/sync_long.cc` - 分析 sync_long 输出的起始位置

- [ ] **Step 1: 理解 sync_long 输出结构**

sync_long 在 COPY 状态下输出从 d_frame_start 开始的样本。检查 sync_long 的 d_frame_start 值：
- sync_long.cc 中的 d_frame_start 是检测到的 L-LTF0 DATA 起始位置
- sync_long 输出 rel_idx=0 对应输入 d_frame_start

- [ ] **Step 2: 理解 SPLITTER 如何计算 rel_idx**

在 SPLITTER 中：
```cpp
uint64_t rel_idx = 0;
bool frame_started = (d_frame_start_known && current_idx >= d_frame_start_abs);
if (frame_started) {
    rel_idx = current_idx - d_frame_start_abs;
}
```

- d_frame_start_abs 初始化为 176
- 当 wifi_start tag 被处理时，d_frame_start_abs 被设置为 tag 中的 d_frame_start + 16

- [ ] **Step 3: 验证预期边界位置**

HT-Mixed preamble 结构（sync_long 输出）：
```
rel_idx 0-63:   L-LTF0 DATA (input 176-239)
rel_idx 64-79:  L-LTF1 CP (input 240-255) - SKIP
rel_idx 80-143: L-LTF1 DATA (input 256-319)
rel_idx 144-159: L-SIG CP (input 320-335) - SKIP
rel_idx 160-223: L-SIG DATA (input 336-399)
rel_idx 224-239: HT-SIG0 CP (input 400-415) - SKIP
rel_idx 240-303: HT-SIG0 DATA (input 416-479)
rel_idx 304-319: HT-SIG1 CP (input 480-495) - SKIP
rel_idx 320-383: HT-SIG1 DATA (input 496-559)
```

**预期 FFT 边界：**
- LTF0 FFT 输出：rel_idx=63（buffer 已满）
- LTF1 FFT 输出：rel_idx=143（buffer 已满）
- L-SIG FFT 输出：rel_idx=223（buffer 已满）
- HT-SIG0 FFT 输出：rel_idx=271（buffer 已满）— **当前未达到**
- HT-SIG1 FFT 输出：rel_idx=351（buffer 已满）— **当前未达到**

- [ ] **Step 4: 提交**

```bash
git add docs/superpowers/plans/YYYY-MM-DD-splitter-htsig-output-fix.md
git commit -m "docs: add HT-SIG FFT output fix plan"
```

---

## Task 3: 修复 SPLITTER rel_idx 计算问题

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` - 修复 rel_idx 计算
- Modify: `lib/ht_symbol_splitter_impl.h` - 可能需要添加调试状态

- [ ] **Step 1: 理解问题的根本原因**

根据 NON_BOUNDARY 消息：
- `current_idx=495 d_frame_start_abs=176 out_rel_idx=319`
- buffer 在 current_idx=495 时满了，说明 HT-SIG1 DATA 的最后一个样本是 495
- 如果 d_frame_start_abs=176，那么 rel_idx=495-176=319

**问题分析：**
- HT-SIG1 DATA 应该从 rel_idx=320 开始，到 rel_idx=383 结束
- 但 buffer 在 rel_idx=319 满了，说明我们从 rel_idx=256 开始缓冲
- 这意味着我们应该从 rel_idx=256 开始缓冲，而不是从 rel_idx=320

**可能的错误：**
- should_buffer 在 HT-SIG1 CP (rel_idx=304-319) 期间应该为 false
- 但实际上可能在 rel_idx=304-319 期间仍然在缓冲

- [ ] **Step 2: 检查 should_buffer 在 rel_idx=304-319 期间的值**

在代码中添加日志：

```cpp
// Debug: trace should_buffer at critical boundaries
static int should_buffer_probe = 0;
if (should_buffer_probe < 20 && (rel_idx == 270 || rel_idx == 271 || rel_idx == 272 || 
    rel_idx == 303 || rel_idx == 304 || rel_idx == 305 || rel_idx == 319 || rel_idx == 320)) {
    fprintf(stderr, "[SPLITTER_SHOULD_BUF] rel_idx=%llu should_buffer=%d d_buffer_count=%d\n",
            (unsigned long long)rel_idx, should_buffer, d_buffer_count);
    should_buffer_probe++;
}
```

- [ ] **Step 3: 运行测试观察 should_buffer 行为**

Run: `LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_SHOULD_BUF"`

Expected: 观察 rel_idx 在 304-320 范围内 should_buffer 的值

- [ ] **Step 4: 根据观察结果修复 should_buffer 逻辑**

如果 should_buffer 在 rel_idx=304-319 期间为 true，需要修改边界条件。

当前代码：
```cpp
} else if (rel_idx < 288) {
    // Stage 4: HT-SIG1 CP (rel_idx 272-287) - 跳过
    should_buffer = false;
} else if (rel_idx < 352) {
    // Stage 4b: HT-SIG1 DATA (rel_idx 288-351) - 64 samples
    should_buffer = true;
```

可能的问题：边界检查 `rel_idx < 288` 和 `rel_idx < 352` 可能不准确。

- [ ] **Step 5: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix: add should_buffer tracing at HT-SIG boundaries"
```

---

## Task 4: 验证修复后的 FFT 输出

**Files:**
- Test: `test_mcs_end_to_end.py` - 验证 HT-SIG FFT 输出

- [ ] **Step 1: 运行测试检查 FFT 输出**

Run: `LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE"`

Expected: 看到 type=3 (HT-SIG0) 和 type=4 (HT-SIG1) 的 FFT 输出

- [ ] **Step 2: 检查 HT-SIG CRC 结果**

Run: `LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "CRC|pass|fail" | tail -20`

Expected: HT-SIG CRC 通过

- [ ] **Step 3: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix: correct HT-SIG FFT boundary detection in SPLITTER"
```

---

## Task 5: 最终验证

**Files:**
- Test: `test_mcs_end_to_end.py` - 端到端测试

- [ ] **Step 1: 运行完整测试**

Run: `LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1`

Expected: MCS 0 测试通过

- [ ] **Step 2: 清理调试日志**

移除所有临时调试日志。

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "feat: fix SPLITTER HT-SIG FFT output"
```
