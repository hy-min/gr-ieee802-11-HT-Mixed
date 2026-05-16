# HT-SIG 解析成功 - 持续调试计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持续调试直到 HT-SIG CRC 在纯 loopback 仿真中通过（SNR=30dB，无信道损伤）。

**Architecture:** RX 链：sync_long → ht_symbol_splitter → fft_vcc → frame_equalizer → decode_mac。SPLITTER 必须正确输出 HT-SIG OFDM 符号（64 采样点），以便 frame_equalizer 能够从 LTF 进行信道估计并通过 QBPSK  demapping 解码 HT-SIG。

**Tech Stack:** GNU Radio 3.10, C++ (SPLITTER, frame_equalizer), Python (test_mcs_end_to_end.py)

---

## 文件映射

| 文件 | 职责 |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | SPLITTER 块 — CP 跳过，符号边界检测，FFT 输出 |
| `lib/frame_equalizer_impl.cc` | 信道估计，HT-SIG 解码，QBPSK demapping |
| `test_mcs_end_to_end.py` | 端到端 MCS 测试（TX→RX loopback） |

---

## 诊断总结

**当前状态（修复前）**：
- SPLITTER 输出：LTF0 (rel_idx=63), LTF1 (rel_idx=143), L-SIG (rel_idx=223) ✓
- SPLITTER 未输出：HT-SIG0, HT-SIG1, HT-STF ✗
- SPLITTER 在第二次 work call 饿死，位置 rel_idx=605，remaining=39 < 80
- HT-SIG 从未到达 frame_equalizer → HT-SIG CRC 从未尝试

**根本原因**：SPLITTER 的 `general_work` 使用 `consume(0, consumed)` 而非 `consume_each()`，导致 GNU Radio 调度器不断 re-play 相同的 79 个样本。

---

## Task 1: 修复 SPLITTER consume 逻辑

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`（general_work 函数）

### Step 1: 阅读当前 general_work 结构

```bash
sed -n '82,695p' /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

### Step 2: 定位 consume 调用

```bash
grep -n "consume" /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

### Step 3: 修改 - 替换 consume(0, consumed) → consume_each()

**修改位置 1**: starvation return 处（约第 334 行）

**修改前**:
```cpp
d_items_processed += items_consumed_this_call;
consume(0, items_consumed_this_call);
return produced;
```

**修改后**:
```cpp
d_items_processed += i;
consume_each(i);  // 关键：所有已读输入都被消费
return produced;
```

**修改位置 2**: 函数末尾（约第 690 行）

**修改前**:
```cpp
d_items_processed += consumed;
consume(0, consumed);
return produced;
```

**修改后**:
```cpp
d_items_processed += consumed;
consume_each(consumed);
return produced;
```

### Step 4: 删除或注释掉 starvation early return

Starvation 不再触发 return，而是继续消耗所有输入。找到约第 325-336 行的 starvation 检查块：

```cpp
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    // Output partial buffer before returning
    fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, outputting partial buffer (d_buffer_count=%d)\n",
            remaining_items, items_needed_for_current_symbol, d_buffer_count);
    for (int j = 0; j < d_buffer_count; j++) {
        out[produced++] = d_buffer[j];
    }
    d_buffer_count = 0;
    d_buffer_filled = false;
    d_items_processed += items_consumed_this_call;
    consume(0, items_consumed_this_call);
    return produced;
}
```

**替换为**:
```cpp
// Starvation 不再返回 - 继续消耗所有输入
// 如果 buffer 未满，继续循环直到所有输入被消费
// 只在 buffer 满时输出
```

### Step 5: 构建

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

**预期**: 无编译错误

### Step 6: 测试

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_OUT|SPLITTER_STARV|SYNC_LONG_PRODUCE" | head -30
```

**预期**:
- SPLITTER 输出 HT-SIG0 (type=3 at rel_idx=303)
- SPLITTER 输出 HT-SIG1 (type=4 at rel_idx=383)
- 无 SPLITTER_STARVATION

---

## Task 2: 验证 HT-SIG 到达 Equalizer

### Step 1: 检查 GATE 消息

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "\[EQ\]\[GATE\]" | head -10
```

**预期**:
```
[EQ][GATE] sym=X valid={lltf0=1 lltf1=1 lsig=1 htsig0=1 htsig1=1} have_ht=1
```

### Step 2: 检查 HT-SIG CRC

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "HT-SIG.*parse|parse failed|RX_CRC" | head -20
```

**预期**: HT-SIG CRC 通过或至少被尝试解码

---

## Task 3: 如果 Task 1/2 失败 - 迭代调试

**如果 HT-SIG 仍未到达 equalizer，执行以下检查**：

### 3A: 验证 SPLITTER 被多次调用

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_WORK" | head -20
```

**检查**: `ninput_items[0]` 在每次调用中是否不同？

### 3B: 验证 consume_each 生效

添加 debug probe 在 general_work 末尾：
```cpp
fprintf(stderr, "[SPLITTER_DBG] consume_each(%d) called\n", consumed);
```

### 3C: 检查 SPLITTER 输出位置

```bash
grep -E "SPLITTER_OUT.*type=3|type=4" 输出
```

**预期**: HT-SIG0 (type=3) 和 HT-SIG1 (type=4) FFT 输出

---

## Task 4: 如果 HT-SIG 到达但 CRC 失败 - 调试解码链

**如果 HT-SIG 符号到达 equalizer 但 CRC 失败**：

### 4A: 验证信道估计

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "CHAN_EST" | head -20
```

检查 H magnitude ≈ 1.0，phase 线性

### 4B: 验证 HT-SIG QBPSK demapping

检查 `lib/frame_equalizer_impl.cc` 中 `decode_htsig_from_rotated()` 函数

### 4C: 验证 CRC-8 实现

IEEE 802.11n HT-SIG 使用 CRC-8 (polynomial 0x1F)

---

## Task 5: 最终验证 - 端到端测试

### Step 1: 运行完整 MCS 测试

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | tail -30
```

### Step 2: 验证成功标准

| 标准 | 预期 |
|------|------|
| SPLITTER 输出 HT-SIG0 | 是 (type=3 at rel_idx=303) |
| SPLITTER 输出 HT-SIG1 | 是 (type=4 at rel_idx=383) |
| frame_equalizer 接收 HT-SIG | 是 (htsig0=1, htsig1=1 in GATE) |
| HT-SIG CRC | 通过 |
| MCS 0 测试 | 通过 (1/1 received) |

---

## Task 6: 提交

```bash
cd /home/hy/gr-ieee802-11
git add lib/ht_symbol_splitter_impl.cc lib/frame_equalizer_impl.cc
git commit -m "$(cat <<'EOF'
fix(splitter): use consume_each for proper input consumption

The root cause of HT-SIG never reaching the equalizer was
incorrect input consumption in general_work. Changed consume(0, consumed)
to consume_each() to ensure GNU Radio scheduler properly advances
the input read pointer.

HT-SIG symbols now correctly output at rel_idx=303 and rel_idx=383.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## 故障排除指南

### 如果 SPLITTER 仍然饿死

**症状**: SPLITTER_STARVATION 仍然出现

**检查**:
1. `consume_each()` 是否在所有 return 路径中被调用？
2. `while (i < ninput_items[0])` 循环是否始终执行 `i++`？

**修复**: 确保没有 early return 路径绕过 `consume_each()`

### 如果 HT-SIG 到达但 CRC 失败

**症状**: HT-SIG 符号到达 equalizer 但 `parse failed` 或 CRC 错误

**按顺序检查**:
1. 信道估计 H — 运行 `equalize_header_signal()` 验证 H magnitude ≈ 1.0
2. HT-SIG bit extraction — 验证 48 HT-SIG bits 正确映射
3. QBPSK demapping — 验证 rotation 和 demapping 参考星座图
4. CRC-8 实现 — 验证 polynomial 和 bit ordering

### 如果 LTF0/LTF1 相位问题重现

**症状**: HT-SIG bits 反转（QBPSK 点在错误象限）

**检查**: LTF0 vs LTF1 的相位关系。如果仍有 ~180° 差异，应用信道估计 workaround（只用 LTF0 或对 LTF1 应用 π 相位校正）
