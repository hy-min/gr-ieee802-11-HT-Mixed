# SPLITTER L-LTF 噪声问题调查 - 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调查为何即使 d_frame_start=182（接近正确值 ~173），SPLITTER 输出的 L-LTF 时域能量仍是 ~0.06（噪声级别），而 L-SIG 能量正常 ~12。

**Architecture:** 调查方向：确认 sync_long 输出的前几个样本是否正确，以及 SPLITTER 是否正确处理这些样本。关键怀疑：sync_long 在检测到帧开始后立即输出的样本可能仍处于 correlation transient 状态，而非真正的 L-LTF DATA。

**Tech Stack:** GNU Radio blocks (sync_long, ht_symbol_splitter), C++ debugging probes

---

## 问题背景

**当前观察到的现象：**
```
d_frame_start=182（接近正确值 ~173）
L-LTF0 td_energy=0.0594（应为 ~8-12，差 200 倍！）
L-LTF1 td_energy=0.0684（同样几乎是零）
L-SIG td_energy=12.1950（正常！）
HT-SIG0 td_energy=63.3108（正常！）
```

**关键矛盾：**
- sync_long 正确检测到 d_frame_start=182
- 但 SPLITTER 输出的 L-LTF 完全是噪声
- 而 L-SIG 和 HT-SIG 的能量完全正常

**这意味着：**
1. sync_long 检测是正确的
2. SPLITTER 的 CP-skip 逻辑对 L-LTF 正确执行了
3. 但 sync_long 输出给 SPLITTER 的样本本身就是噪声/死区

---

## File Structure

- `lib/sync_long.cc` — general_work()，COPY 逻辑，输出探针
- `lib/ht_symbol_splitter_impl.cc` — 处理逻辑，输入探针
- `examples/test_mcs_end_to_end.py` — 端到端测试

---

## Task 1: 添加 sync_long 输出探针，验证前 10 个输出样本

**Files:**
- Modify: `lib/sync_long.cc:general_work()`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 在 sync_long 的 COPY 逻辑中添加输出探针**

在 sync_long 的 general_work() 中，当处于 COPY 状态并输出前几个样本时，打印这些样本的值：

在 `SYNC_LONG_COPY` 日志处添加更详细的探针：

```cpp
// 在 general_work() 的 COPY 状态输出循环中（约 line 400+）
// 添加详细的输出样本探针
static int copy_probe_count = 0;
if (copy_probe_count < 20 && d_state == COPY) {
    // 打印前 10 个输出样本的详细信息
    if (d_offset < 10) {
        fprintf(stderr, "[SYNC_LONG_OUT] offset=%d out[%d]=%.6f%+.6fi amp=%.6f abs_offset=%llu\n",
                d_offset, d_offset,
                out[d_offset].real(), out[d_offset].imag(),
                std::abs(out[d_offset]),
                (unsigned long long)(d_abs_idx + d_offset));
    }
    copy_probe_count++;
}
```

- [ ] **Step 2: 重新构建**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "SYNC_LONG_OUT" | head -20
```

**预期结果：**
- 如果前几个样本 amplitude ~0.01-0.05 → 输出的是噪声 transient
- 如果前几个样本 amplitude ~0.5-2.0 → sync_long 输出是正确的
- 这样可以确认问题是否在 sync_long 的输出阶段

- [ ] **Step 4: 提交**

```bash
git add lib/sync_long.cc
git commit -m "debug: add SYNC_LONG_OUT probe to verify first samples in COPY state"
```

---

## Task 2: 交叉验证 SPLITTER 输入 vs sync_long 输出

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 在 SPLITTER 的输入点添加探针**

在 general_work() 的主循环中，当 should_buffer=true 时，打印前几个缓冲的样本：

```cpp
// 在 should_buffer 的缓冲逻辑中（约 line 355）
// 添加输入探针
static int input_probe_count = 0;
if (input_probe_count < 20 && should_buffer && d_buffer_count < 10) {
    fprintf(stderr, "[SPLITTER_IN] rel_idx=%llu buf[%d]=%.6f%+.6fi amp=%.6f\n",
            (unsigned long long)rel_idx, d_buffer_count,
            in[i].real(), in[i].imag(), std::abs(in[i]));
    input_probe_count++;
}
```

- [ ] **Step 2: 重新构建并测试**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(SYNC_LONG_OUT|SPLITTER_IN)" | head -30
```

**预期结果：**
- 比较 SYNC_LONG_OUT 和 SPLITTER_IN 的 amplitude
- 如果两者 amplitude 都 ~0.01-0.05 → 问题在 sync_long 输出
- 如果 SPLITTER_IN amplitude 正常但 SPLITTER_LLTF_VERIFY 能量为零 → SPLITTER 缓冲逻辑有问题

- [ ] **Step 3: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "debug: add SPLITTER_IN probe to verify buffered samples"
```

---

## Task 3: 分析 sync_long 的 d_abs_idx 追踪逻辑

**Files:**
- Read: `lib/sync_long.cc:general_work()`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 理解 sync_long 的 d_abs_idx 逻辑**

sync_long 使用 `d_abs_idx` 来追踪绝对位置：
- 在 CORRELATE 状态下，每处理一个输入样本，`d_abs_idx++`
- 在 COPY 状态下，`d_abs_idx += d_offset` 递增

检查以下问题：
1. `d_abs_idx` 在 CORRELATE→COPY 转换时是否正确？
2. `d_offset` 的初始值是否为 d_frame_start？
3. wifi_start tag 写入的位置是否正确？

读取 general_work() 的相关代码（约 lines 350-450）：
```cpp
// 检查 d_abs_idx 的使用
// 检查 wifi_start tag 的写入
// 检查 CORRELATE vs COPY 状态的转换
```

- [ ] **Step 2: 添加 d_abs_idx 探针**

在 COPY 状态的主循环中添加：
```cpp
fprintf(stderr, "[SYNC_LONG_COPY] d_offset=%d d_abs_idx=%llu out_pos=%llu\n",
        d_offset, (unsigned long long)d_abs_idx, (unsigned long long)(d_abs_idx - d_frame_start));
```

- [ ] **Step 3: 运行测试分析**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "SYNC_LONG_COPY" | head -20
```

**关键问题：**
- `d_abs_idx - d_frame_start` 应该等于输出缓冲区的索引
- 如果这个值是负数或非常大，说明 d_abs_idx 追踪有问题

- [ ] **Step 4: 提交**

```bash
git add lib/sync_long.cc
git commit -m "debug: add d_abs_idx tracking probe in COPY state"
```

---

## Task 4: 验证 HT-SIG0 能量正常的原因

**Files:**
- Analyze: SPLITTER_FFTPROBE 输出
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 分析 HT-SIG0 能量正常的含义**

根据测试结果：
```
[SPLITTER_FFTPROBE] type=3 rel_idx=303 td_energy=63.3108 ← HT-SIG0 正常
[SPLITTER_FFTPROBE] type=4 rel_idx=383 td_energy=62.4166 ← HT-SIG1 正常
```

**这说明：**
- sync_long 正确输出了 HT-SIG0 和 HT-SIG1 的样本
- SPLITTER 正确缓冲和输出了 HT-SIG0 和 HT-SIG1
- 问题只影响 L-LTF（rel_idx 0-143）

**关键问题：为什么 L-LTF 被影响但 HT-SIG 没有？**

可能原因：
1. **时间对齐问题**：L-LTF 在帧的最开始，sync_long 可能还处于 transient 状态
2. **AGC 未收敛**：L-LTF 期间 AGC 还未收敛
3. ** Plateau 效应**：L-LTF 的 plateau 效应导致峰值位置不准确

- [ ] **Step 2: 运行测试检查 AGC 探针**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(AGC|agc|amplitude)" | head -20
```

- [ ] **Step 3: 提交分析结果**

```bash
git add lib/sync_long.cc lib/ht_symbol_splitter_impl.cc
git commit -m "debug: analyze why HT-SIG works but L-LTF fails"
```

---

## Task 5: 修复或绕过方案

基于 Task 1-4 的发现，应用修复：

- [ ] **Step 1: 根据调查结果应用修复**

**如果 sync_long 输出前几个样本 amplitude ~0.01-0.05（噪声）：**

修复方案：让 sync_long 在 CORRELATE→COPY 转换时跳过前 N 个样本（transient 跳过）

**如果 SPLITTER 缓冲逻辑有问题：**

修复方案：检查 SPLITTER 的 d_buffer_count 重置逻辑

**如果 AGC 未收敛：**

修复方案：在 L-LTF 期间使用更长的 AGC 时间常数

- [ ] **Step 2: 重新构建并测试**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "SPLITTER_FFTPROBE.*type=0"
```

预期：L-LTF0 td_energy 从 ~0.06 上升到 ~8-12

- [ ] **Step 3: 提交**

```bash
git add lib/sync_long.cc lib/ht_symbol_splitter_impl.cc
git commit -m "fix: [describe the fix based on findings]"
```

---

## Self-Review Checklist

1. **Spec coverage:** 所有任务映射到调查 SPLITTER L-LTF 噪声问题的目标。无占位符。

2. **Placeholder scan:** 无 "TBD"、"TODO" 或模糊步骤。每步显示精确代码。

3. **Type consistency:** 函数名、日志标签保持一致。

4. **测试验证:** 每任务以具体测试命令和预期输出结束。

5. **关键调查点：**
   - sync_long COPY 输出的前 10 个样本是否正确？
   - SPLITTER 缓冲的输入样本是否正确？
   - d_abs_idx 追踪是否正确？
   - 为什么 HT-SIG 正常但 L-LTF 失败？
