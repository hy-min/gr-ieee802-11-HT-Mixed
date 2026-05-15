# SPLITTER Starvation Fix Design Spec

**日期：** 2026-05-16
**状态：** 设计完成，待实现验证
**分支：** fcs-backup-apply

---

## 一、问题描述 (Problem Statement)

### 核心故障

SPLITTER 在第二次 work call 时饿死（starvation），导致 HT-SIG 符号从未被输出到 RX 链下游。

**实测数据（debug probes）：**

| Call | ninput_items | start_abs_idx | d_frame_start_abs | 饿死位置 |
|------|-------------|---------------|-------------------|----------|
| 0 | 448 | 0 | 0 | rel_idx=369, remaining=79, HT-SIG0 CP 区 |
| 1 | 356 | 448 | 160 | rel_idx=605, remaining=39, HT-DATA 区 |

**HT-SIG 输出状态：**
- Call 0: 输出 LTF0 (63), LTF1 (143), L-SIG (223) — 从未到达 HT-SIG0 DATA (rel_idx 240-303)
- Call 1: 饿死于 rel_idx=605 (HT-DATA) — 从未到达 HT-SIG0 DATA (rel_idx 288-303)

### 根本原因

当 `d_buffer_count == 0`（尚未开始缓冲任何符号）时，当前 starvation 逻辑会直接返回，即使剩余项 (`remaining`) 虽然不足以完成当前符号，但仍然应该被消耗以便后续 call 继续处理。

**问题代码逻辑：**
```cpp
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
    if (in_data_region) {
        // 返回，丢弃 remaining 项
        return early;
    }
}
```

**场景分析：**
1. Call 0: 在 HT-SIG0 CP 区（rel_idx 240-303，非 DATA 区）饿死，但此时 d_buffer_count=0，应该继续消耗 CP 项而不是返回
2. Call 1: 在 HT-DATA 区（rel_idx 464+）饿死，d_buffer_count=0，应该继续消耗项以便后续 call 继续处理 HT-DATA

### 后果

- HT-SIG 从未被 SPLITTER 输出
- frame_equalizer 收不到 HT-SIG FFT，无法进行信道估计
- HT-SIG CRC 永远无法通过

---

## 二、解决方案 (Solution)

### 核心原则

**当 `d_buffer_count == 0` 时，永远不要因为 `remaining < 80` 而返回。**

理由：
1. `d_buffer_count == 0` 表示尚未开始缓冲任何符号
2. 如果此时返回，后续 call 会以相同的 d_buffer_count=0 状态开始，仍然无法完成符号
3. GNU Radio 调度器会持续调用 SPLITTER 直到它消费了所有项
4. SPLITTER 应该持续消费所有可用项，而不是在 `d_buffer_count == 0` 时提前返回

### 修复策略

移除 `d_buffer_count == 0` 情况下的 starvation 检查。

**修改前：**
```cpp
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
    if (in_data_region) {
        return early;
    }
}
```

**修改后：**
```cpp
// 只有在 d_buffer_count > 0（正在缓冲中）时才检查 starvation
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    if (in_data_region) {
        return early;
    }
}
```

### 解释

| d_buffer_count | 状态 | 是否检查 starvation |
|---------------|------|-------------------|
| 0 | 尚未开始缓冲任何符号 | **不检查** — 继续消费 |
| > 0 | 正在缓冲某个符号 | 检查 — 如果无法完成则返回 |

**为什么这样安全：**
1. `d_buffer_count == 0` 时，我们不在任何符号的中间位置
2. 即使 `remaining < 80`，我们可以继续消费这些项到 buffer 中
3. GNU Radio 会再次调用 SPLITTER 提供更多项
4. Buffer 会在多次 call 中逐渐填满，最终在边界处输出

---

## 三、实现 (Implementation)

### 修改文件

`lib/ht_symbol_splitter_impl.cc`

### 修改位置

第 286 行附近的 starvation 检查逻辑

### 修改内容

```cpp
// 修改前
if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
    if (in_data_region) {
        fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, returning early\n",
                remaining_items, items_needed_for_current_symbol);
        d_items_processed += items_consumed_this_call;
        d_buffer_filled = false;
        d_buffer_count = 0;
        consume(0, items_consumed_this_call);
        return produced;
    }
}

// 修改后
// 只有在 d_buffer_count > 0（正在缓冲某个符号）时才触发 starvation 保护
if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0) {
    if (in_data_region) {
        fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, returning early\n",
                remaining_items, items_needed_for_current_symbol);
        d_items_processed += items_consumed_this_call;
        d_buffer_filled = false;
        d_buffer_count = 0;
        consume(0, items_consumed_this_call);
        return produced;
    }
}
```

### 同时移除 debug probes

修改完成后，移除调试用的 `SPLITTER_STARVATION_CHECK` 探针和 `SPLITTER_STARVATION` 消息。

---

## 四、验证计划 (Verification Plan)

### 验证步骤

1. **构建并运行测试**
   ```bash
   cd build && make -j4 && cd .. && python test_mcs_end_to_end.py
   ```

2. **检查 SPLITTER 输出**
   - 应该输出 HT-SIG0 FFT（rel_idx=303 对应 i=15 on call 1）
   - 应该输出 HT-SIG1 FFT（rel_idx=335 对应 i=47 on call 1）
   - 不应该再有 SPLITTER_STARVATION 消息

3. **检查 HT-SIG 解码**
   - HT-SIG CRC 应该通过
   - GATE 输出应该显示 `htsig0=1, htsig1=1`

### 预期结果

| 指标 | 预期 |
|------|------|
| SPLITTER HT-SIG 输出 | HT-SIG0, HT-SIG1 均输出 |
| HT-SIG CRC | 通过 |
| 帧解析状态 | L-SIG OK, HT-SIG OK |

---

## 五、相关文件索引

| 文件 | 关键代码 | 备注 |
|------|---------|------|
| `lib/ht_symbol_splitter_impl.cc` | starvation 检查逻辑 (行 286) | 需要修改 |
| `test_mcs_end_to_end.py` | 端到端测试 | 验证 HT-SIG 通过 |

---

## 六、风险评估

| 风险 | 级别 | 缓解 |
|------|------|------|
| 移除 starvation 检查后 SPLITTER 可能消费过多项 | 低 | GNU Radio 调度器会控制节奏 |
| 下游 FFT block 可能没有完整符号 | 低 | SPLITTER 输出 64 点 FFT 块是完整 OFDM 符号 |
| Call 1 仍然无法到达 HT-SIG0 | 低 | d_buffer_count=0 时继续消费，最终会到达边界 |

---

## 七、后续工作

修复完成后，如果 HT-SIG 仍然无法通过，需要检查：
1. HT-SIG FFT 数据是否正确
2. 信道估计是否正确
3. HT-SIG QBPSK 解码是否正确
