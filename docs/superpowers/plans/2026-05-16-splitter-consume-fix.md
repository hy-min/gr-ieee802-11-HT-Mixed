# SPLITTER consume_each 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SPLITTER 的 `general_work` 函数，正确消费所有输入样本，使 GNU Radio 调度器能够推进到 HT-SIG 边界。

**Architecture:** 重构 `ht_symbol_splitter_impl.cc` 的 `general_work`，使用 `consume_each()` 替换 `consume()`，确保所有已读输入都被正确消费。

**Tech Stack:** GNU Radio 3.10, C++ (SPLITTER)

---

## 问题诊断总结

**根本原因**: `consume(0, consumed)` 没有正确推进 GNU Radio 的输入读指针。

当前代码：
```cpp
consume(0, consumed);  // 错误！端口 0 的 consumed 项没有被真正消费
```

**症状**: GNU Radio 调度器不断重复提供相同的 79 个样本（位置 369-447），而不是推进到新的样本。

**正确做法**: 使用 `consume_each()` 强制消费所有已读输入：
```cpp
consume_each(in_idx);  // 正确！所有 in_idx 项都被消费
```

---

## 文件映射

| 文件 | 职责 |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | SPLITTER 块实现 - 需要修改 `general_work` 函数 |

---

## Task 1: 理解当前代码结构

**Files:**
- Read: `lib/ht_symbol_splitter_impl.cc:268-695`

- [ ] **Step 1: 阅读 general_work 函数结构**

重点关注：
- `while (i < ninput_items[0])` 循环结构
- `consume(0, consumed)` 调用位置（第 690 行附近）
- `items_consumed_this_call` 累加逻辑
- starvation return 路径

---

## Task 2: 重构 general_work - 使用 consume_each

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: 读取当前 general_work 完整代码**

```bash
sed -n '82,695p' lib/ht_symbol_splitter_impl.cc
```

- [ ] **Step 2: 定位 consume 调用位置**

```bash
grep -n "consume" lib/ht_symbol_splitter_impl.cc
```

预期输出：
```
334:                consume(0, items_consumed_this_call);
690:        consume(0, consumed);
```

- [ ] **Step 3: 删除 items_consumed_this_call 累加逻辑**

将第 272 行的 `int items_consumed_this_call = 0;` 和所有 `items_consumed_this_call++` 删除或注释。

- [ ] **Step 4: 将所有 `consume(0, ...)` 替换为 `consume_each(...)`**

**修改前**（starvation return，第 334 行附近）：
```cpp
d_items_processed += items_consumed_this_call;
consume(0, items_consumed_this_call);
return produced;
```

**修改后**：
```cpp
d_items_processed += i;  // i 是当前循环位置
consume_each(i);
return produced;
```

**修改前**（函数末尾，第 690 行附近）：
```cpp
d_items_processed += consumed;
consume(0, consumed);
return produced;
```

**修改后**：
```cpp
d_items_processed += consumed;
consume_each(consumed);  // consumed == i 在循环结束后
return produced;
```

- [ ] **Step 5: 删除 starvation return 路径**

删除第 325-336 行的 starvation 检查和 return 语句，改为：
```cpp
// Starvation 不再返回 - 继续消耗所有输入
// 如果 buffer 未满，继续循环直到 i >= ninput_items[0]
```

或者更简单地：注释掉整个 starvation 检查块，让循环自然结束。

- [ ] **Step 6: 构建验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

预期：无编译错误

---

## Task 3: 测试验证

**Files:**
- Test: `test_mcs_end_to_end.py`

- [ ] **Step 1: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_OUT|SPLITTER_STARV|SYNC_LONG_PRODUCE" | head -30
```

**预期结果**：
- SPLITTER 输出 HT-SIG0 (type=3 at rel_idx=303)
- SPLITTER 输出 HT-SIG1 (type=4 at rel_idx=383)
- 无 SPLITTER_STARVATION 消息（或极少）
- sync_long 被多次调用，每次提供新的数据

- [ ] **Step 2: 检查 HT-SIG 到达 equalizer**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "\[EQ\]\[GATE\]" | head -10
```

**预期**：
```
[EQ][GATE] sym=X valid={lltf0=1 lltf1=1 lsig=1 htsig0=1 htsig1=1} have_ht=1
```

- [ ] **Step 3: 检查 HT-SIG CRC**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python test_mcs_end_to_end.py 2>&1 | grep -E "HT-SIG.*parse|parse failed|RX_CRC" | head -20
```

**预期**：HT-SIG CRC 通过或至少被尝试解码

---

## Task 4: 清理调试代码并提交

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: 移除 excessive debug probes**

保留：
- `SPLITTER_WORK` - 有用，用于验证调用次数
- `SPLITTER_OUT` - 有用，用于验证 FFT 输出

可以移除：
- 注释掉的 `[SPLITTER_INPUT_CHECK]` 等探针

- [ ] **Step 2: 提交变更**

```bash
cd /home/hy/gr-ieee802-11
git add lib/ht_symbol_splitter_impl.cc
git commit -m "$(cat <<'EOF'
fix(splitter): use consume_each instead of consume

The root cause of HT-SIG never reaching the equalizer was
incorrect input consumption in general_work. The old code used
consume(0, consumed) which didn't properly advance GNU Radio's
input read pointer, causing the scheduler to re-play the same
79 items repeatedly instead of advancing.

Now uses consume_each(i) at starvation return and
consume_each(consumed) at normal return to ensure all consumed
items are properly accounted for.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## 关键技术细节

### consume() vs consume_each()

**consume(port, nitems)**:
- 告诉调度器"我消耗了 port 上的 nitems 项"
- 但如果你的循环没有正确步进 i，调度器会认为你没处理完

**consume_each(nitems)**:
- 强制"无论发生了什么，这 nitems 项都被我消费了"
- 更适合 input-driven 循环（只要读了就消费）

### 为什么原来的代码有问题

```cpp
while (i < ninput_items[0]) {
    // 处理数据...
    if (starvation_condition) {
        consume(0, items_consumed_this_call);  // 只消费了 items_consumed_this_call 项
        return produced;  // 但 ninput_items[0] 可能有 448 项！
    }
    i++;
}
```

调度器说："你返回了但只消费了 200 项，剩下 248 项你没处理，我下次再给你。"

### 修复后的逻辑

```cpp
while (i < ninput_items[0]) {
    // 处理数据 - 没有 early return！
    // 如果 buffer 满了，输出到 out
    // 如果 buffer 没满，继续循环
    
    // Starvation 不再返回 - 只是跳过 buffering，继续消耗
    if (starvation_condition) {
        // 不返回！继续循环
        // 但此时不应该 buffer
    }
    
    i++;  // 关键：始终步进
}

// 函数结束时，所有 ninput_items[0] 项都被消费
consume_each(ninput_items[0]);
return produced;
```

---

## 验证检查清单

| 检查项 | 预期 | 实际 |
|--------|------|------|
| SPLITTER 被多次调用 | 是 (>3次) | ? |
| 每次调用 ninput 不同 | 是 | ? |
| HT-SIG0 输出 (rel_idx=303) | 是 | ? |
| HT-SIG1 输出 (rel_idx=383) | 是 | ? |
| GATE 显示 htsig0=1 htsig1=1 | 是 | ? |
| SYNC_LONG 被再次调用 | 是 | ? |
