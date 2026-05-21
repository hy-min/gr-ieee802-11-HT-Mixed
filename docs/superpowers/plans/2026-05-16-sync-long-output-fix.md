# sync_long 输出完整性修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SPLITTER 在 L-SIG 边界处饿死的问题，使 L-SIG 和 HT-SIG0 符号能够正确输出。

**Architecture:** SPLITTER 在 i=399（L-SIG DATA 结束，rel_idx=223）时触发 starvation check，导致 L-SIG FFT 从未被输出。修复方法：在 starvation check 之前检查是否需要先输出已满的 buffer。

**Tech Stack:** GNU Radio 3.10, C++ (ht_symbol_splitter_impl.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**当前状态（修复后）：**
- SPLITTER 输出：LTF0 (rel_idx=63), LTF1 (rel_idx=143) ✓
- SPLITTER 未输出：L-SIG (rel_idx=223), HT-SIG0 (rel_idx=271) ✗
- SPLITTER 在 i=399 饿死，remaining=48 < 80
- L-SIG FFT 从未被输出，因为 starvation check 先于 boundary check 执行

**根本原因**：在 i=399 时：
1. `d_buffer_count == 64` (L-SIG DATA 已满)
2. `at_boundary = (rel_idx == 223) = true`
3. 但 `remaining = 48 < 80` 先触发 starvation check
4. Starvation check return，绕过了 boundary check 和 L-SIG 输出

**代码流程（当前）**：
```cpp
while (i < ninput_items[0]) {
    // ... should_buffer 计算 ...

    // STARVATION CHECK（在 boundary check 之前）
    if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
        // 输出 partial buffer
        return produced;  // ← L-SIG 从未输出！
    }

    // BUFFERING
    if (should_buffer) {
        d_buffer[d_buffer_count++] = in[i];
    }

    // BOUNDARY CHECK（在 starvation check 之后）
    if (d_buffer_count == d_fft_size) {
        // 应该在这里输出 L-SIG，但从未到达
    }
}
```

---

## 文件映射

| 文件 | 职责 |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | SPLITTER 块 — 需要修改 starvation 和 boundary 检查顺序 |

---

## Task 1: 修复 SPLITTER饿死问题 - 输出已满的Buffer

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (general_work 函数中的 starvation 和 boundary 检查逻辑)

### Step 1: 阅读当前 starvation 和 boundary 检查代码

```bash
sed -n '495,560p' /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

### Step 2: 分析当前代码流程

当前流程：
1. 计算 `should_buffer`
2. 检查 starvation（输出 partial buffer，如果 remaining < 80 且 d_buffer_count > 0）
3. buffering
4. 检查 boundary（如果 d_buffer_count == 64）

问题：`remaining < 80` 时，在 boundary check 之前就 return 了。

### Step 3: 修改 - 在 starvation check 之前先检查是否需要输出已满的 buffer

**修改前**:
```cpp
// STARVATION CHECK
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    // Output partial buffer and return
    for (int j = 0; j < d_buffer_count; j++) {
        out[produced++] = d_buffer[j];
    }
    d_buffer_count = 0;
    d_buffer_filled = false;
    at_end_of_input = true;
    // Fall through to end - don't buffer more
}

// Always buffer when should_buffer is true (unless at end of input)
if (should_buffer && !at_end_of_input) {
    d_buffer[d_buffer_count++] = in[i];
}

// Check boundary conditions when buffer is full
if (d_buffer_count == d_fft_size) {
    // ... boundary check ...
}
```

**修改后**:
```cpp
// BOUNDARY CHECK FIRST: If buffer is full, output regardless of starvation
if (d_buffer_count == d_fft_size) {
    uint64_t out_rel_idx = current_idx - d_frame_start_abs;
    bool at_boundary = false;
    // ... calculate at_boundary based on rel_idx ...
    if (at_boundary) {
        // Output the full buffer
        for (int j = 0; j < d_fft_size; j++) {
            out[produced++] = d_buffer[j];
        }
        d_buffer_count = 0;
        d_buffer_filled = false;
    }
    // Continue processing even if not at exact boundary
}

// Then starvation check: only if buffer is NOT full
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0 && !at_end_of_input) {
    // Output partial buffer but don't return early - continue consuming
    fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, outputting partial buffer (d_buffer_count=%d)\n",
            remaining_items, items_needed_for_current_symbol, d_buffer_count);
    for (int j = 0; j < d_buffer_count; j++) {
        out[produced++] = d_buffer[j];
    }
    d_buffer_count = 0;
    d_buffer_filled = false;
    at_end_of_input = true;  // Skip further buffering for this call
}

// Buffer input if not at end
if (should_buffer && !at_end_of_input) {
    d_buffer[d_buffer_count++] = in[i];
}
```

### Step 4: 构建

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

预期：无编译错误

### Step 5: 测试

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "SPLITTER_FFTPROBE|SPLITTER_STARV|SYNC_LONG_PRODUCE" | head -30
```

**预期结果**：
- SPLITTER 输出 LTF0 (rel_idx=63), LTF1 (rel_idx=143), L-SIG (rel_idx=223) ✓
- SPLITTER 输出 HT-SIG0 (rel_idx=271), HT-SIG1 (rel_idx=351), HT-STF (rel_idx=431) ✓
- Starvation 次数减少或消失

---

## Task 2: 验证 HT-SIG 到达 Equalizer

### Step 1: 检查 GATE 消息

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "\[EQ\]\[GATE\]" | head -10
```

**预期**:
```
[EQ][GATE] sym=X valid={lltf0=1 lltf1=1 lsig=1 htsig0=1 htsig1=1} have_ht=1
```

### Step 2: 检查 HT-SIG CRC

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "HT-SIG.*parse|RX_CRC" | head -20
```

**预期**: HT-SIG CRC 通过或至少被尝试解码

---

## Task 3: 如果 Task 1/2 失败 - 迭代调试

### 3A: 验证 SPLITTER 输出所有 preamble 符号

```bash
grep -E "SPLITTER_FFTPROBE.*rel_idx=" 输出
```

**预期**: 6 个 FFTPROBE 输出（type=0,0,2,3,4,5 对应 rel_idx=63,143,223,271,351,431）

### 3B: 验证 sync_long 输出足够 items

```bash
grep "SYNC_LONG_PRODUCE" 输出
```

**预期**: 至少 560 items 总产量

### 3C: 检查 SPLITTER 边界条件

如果某些符号仍然缺失，检查 at_boundary 计算是否正确：
- L-SIG: rel_idx == 223
- HT-SIG0: rel_idx == 271
- HT-SIG1: rel_idx == 351
- HT-STF: rel_idx == 431

---

## Task 4: 如果 HT-SIG 到达但 CRC 失败 - 调试解码链

**如果 HT-SIG 符号到达 equalizer 但 CRC 仍然失败**：

### 4A: 验证信道估计

检查 `lib/frame_equalizer_impl.cc` 中 `estimate_header_channel_from_lltf52()` 函数：
- H magnitude 应接近 1.0（loopback）
- H phase 应在子载波间线性

### 4B: 验证 HT-SIG QBPSK demapping

检查 `decode_htsig_from_rotated()` 函数：
- QBPSK 参考星座正确
- 相位旋转正确

### 4C: 验证 CRC-8 实现

IEEE 802.11n HT-SIG 使用 CRC-8 (polynomial 0x1F)

---

## Task 5: 最终验证 - 端到端测试

### Step 1: 运行完整 MCS 测试

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | tail -30
```

### Step 2: 验证成功标准

| 标准 | 预期 |
|------|------|
| SPLITTER 输出 L-SIG | 是 (rel_idx=223) |
| SPLITTER 输出 HT-SIG0 | 是 (rel_idx=271) |
| SPLITTER 输出 HT-SIG1 | 是 (rel_idx=351) |
| frame_equalizer 接收 HT-SIG | 是 (htsig0=1, htsig1=1 in GATE) |
| HT-SIG CRC | 通过 |
| MCS 0 测试 | 通过 (1/1 received) |

---

## Task 6: 提交

```bash
cd /home/hy/gr-ieee802-11
git add lib/ht_symbol_splitter_impl.cc lib/frame_equalizer_impl.cc
git commit -m "$(cat <<'EOF'
fix(splitter): output full buffer before checking starvation

The L-SIG FFT was never output because at i=399 (rel_idx=223),
the starvation check (remaining < 80) fired before the boundary
check could output the full buffer.

Now boundary check runs first - if buffer is full and at a symbol
boundary, output immediately. Then starvation check only applies to
partial buffers.

Also changed starvation to not return early, but set at_end_of_input
flag to skip further buffering.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## 故障排除指南

### 如果 SPLITTER 仍然没有输出 L-SIG

**症状**: SPLITTER_FFTPROBE 没有 rel_idx=223

**检查**:
1. d_buffer_count 是否在 i=399 时等于 64？
2. at_boundary 计算是否正确？
3. 是否有其他 early return 路径？

### 如果 HT-SIG 到达但 CRC 失败

**症状**: htsig0=1, htsig1=1 但 `parse failed`

**按顺序检查**:
1. 信道估计 H — magnitude ≈ 1.0, phase 线性
2. HT-SIG bit extraction — 48 bits 正确映射
3. QBPSK demapping — rotation 和 demapping 参考正确
4. CRC-8 实现 — polynomial 和 bit ordering

### 如果 LTF0/LTF1 相位问题重现

**症状**: HT-SIG bits 反转（QBPSK 点在错误象限）

**检查**: LTF0 vs LTF1 的相位关系。如果仍有 ~180° 差异，应用信道估计 workaround（只用 LTF0 或对 LTF1 应用 π 相位校正）
