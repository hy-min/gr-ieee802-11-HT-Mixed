# sync_long 伪峰值检测修复 - 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 sync_long.cc 的 search_frame_start() 函数中的伪峰值锁定问题。当前的 delay-and-correlate 算法只要找到间隔在 78-82 之间的峰值对，就立即认定是 L-LTF，没有校验峰值幅度是否足够大。这导致低能量噪声峰被误判为帧起始点。

**Architecture:** 在 sync_long 的 search_frame_start() 中添加峰值幅度门限验证。必须满足两个条件才能确认是有效的 L-LTF 峰值对：(1) 峰间距符合预期（78-82 samples），(2) 峰值幅度超过全局最高幅度的某个比例（如 50%）。

**Tech Stack:** C++ GNU Radio blocks, 802.11 L-LTF 相关检测

---

## 问题根因分析

**当前代码逻辑 (lib/sync_long.cc:241-259)**：

```cpp
// Method 1: Try to find pairs with expected L-LTF spacing
// HT Mixed mode detection
for (int i = 0; i < (int)vec.size() && i < 10; i++) {
    for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
        int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
        double mag = abs(get<0>(vec[i]));

        // HT Mixed mode: L-LTF period is 80 samples (diff 78-82)
        if (diff >= 78 && diff <= 82) {
            int p1 = get<1>(vec[i]);
            int p2 = get<1>(vec[k]);
            int lower_peak = min(p1, p2);
            d_frame_start = 176;  // Hardcoded!
            mode = "HT-mode";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] d_frame_start=%d (%s, lower_peak=%d)\n",
                    d_frame_start, mode, lower_peak);
            return;  // 直接返回，无幅度校验！
        }
    }
}
```

**问题**：
1. 只要 diff ∈ [78, 82]，就认为找到了 L-LTF
2. 完全不检查 `mag`（峰值幅度）是否足够大
3. d_frame_start 被硬编码为 176，忽略 actual lower_peak 值
4. vec 是按幅度降序排列的，所以 vec[0] 是全局最强峰

**观察到的现象**：
- lower_peak=79 → d_frame_start=81（错误锁定）
- 期望 lower_peak≈171 → d_frame_start≈173（正确锁定）
- 92 samples 的偏差 ≈ 1 个多 OFDM 符号（80 samples）
- 导致 FFT 窗口切到 L-STF 尾巴/死区/噪声 → 能量极低

---

## File Structure

- `lib/sync_long.cc` — search_frame_start() 函数，L-LTF 峰值对检测逻辑
- `examples/test_mcs_end_to_end.py` — 端到端测试

---

## Task 1: 添加峰值幅度门限验证

**Files:**
- Modify: `lib/sync_long.cc:241-290`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 读取当前 search_frame_start() 代码**

确认以下关键变量：
- `vec` — 按幅度降序排列的相关峰列表
- `vec[0].first` — 最高幅度值
- `vec[i].first` — 第 i 个峰的幅度
- `get<1>(vec[i])` — 第 i 个峰的位置

- [ ] **Step 2: 添加峰值幅度门限校验逻辑**

在 HT Mixed mode 检测的 if 分支内（diff >= 78 && diff <= 82），添加幅度校验：

```cpp
// 在找到 diff ∈ [78, 82] 的峰值对后，添加：
double peak_mag = abs(get<0>(vec[i]));
double top_mag = abs(get<0>(vec[0]));

// 峰值幅度必须超过全局最强峰的 30% 才有效
// 这防止低能量噪声峰被误判为 L-LTF
const double MIN_PEAK_RATIO = 0.30;
if (peak_mag < top_mag * MIN_PEAK_RATIO) {
    fprintf(stderr, "[SYNC_LONG] Reject peak at %d: mag=%.4f (below %.0f%% of top mag %.4f)\n",
            get<1>(vec[i]), peak_mag, MIN_PEAK_RATIO * 100, top_mag);
    continue;  // 跳过这个峰，继续寻找
}
```

修改后的完整 HT Mixed mode 检测逻辑：

```cpp
// Method 1: Try to find pairs with expected L-LTF spacing
// HT Mixed mode detection
double top_mag = abs(get<0>(vec[0]));
fprintf(stderr, "[SYNC_LONG] Top correlation magnitude: %.4f\n", top_mag);

for (int i = 0; i < (int)vec.size() && i < 10; i++) {
    for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
        int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
        double mag = abs(get<0>(vec[i]));

        // Peak magnitude must exceed 30% of top magnitude to be valid
        const double MIN_PEAK_RATIO = 0.30;
        if (mag < top_mag * MIN_PEAK_RATIO) {
            continue;  // Skip low-energy peaks
        }

        // HT Mixed mode: L-LTF period is 80 samples (diff 78-82)
        if (diff >= 78 && diff <= 82) {
            int p1 = get<1>(vec[i]);
            int p2 = get<1>(vec[k]);
            int lower_peak = min(p1, p2);
            d_frame_start = lower_peak + 2;  // Use actual lower_peak, not hardcoded 176
            mode = "HT-mode";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] HT-mode: lower_peak=%d, d_frame_start=%d, peak_mag=%.4f (%.0f%% of top)\n",
                    lower_peak, d_frame_start, mag, (mag/top_mag)*100);
            return;
        }
    }
}
```

**关键改动说明**：
1. 添加 `MIN_PEAK_RATIO = 0.30` — 峰值必须超过全局最强峰 30% 才被认为是有效 L-LTF
2. 跳过幅度不够的峰值（`continue`）
3. **重要**：`d_frame_start = lower_peak + 2` — 恢复到使用 actual lower_peak，而不是硬编码 176。之前硬编码 176 是为了掩盖检测错误，但正确做法是用实际检测到的 lower_peak

- [ ] **Step 3: 对 Legacy mode 检测应用相同的峰值门限**

Legacy mode（diff ∈ [62, 66]）也需要相同的幅度门限：

```cpp
// Legacy mode check - apply same peak ratio threshold
for (int i = 0; i < (int)vec.size() && i < 10; i++) {
    for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
        int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
        double mag = abs(get<0>(vec[i]));

        // Same magnitude threshold
        const double MIN_PEAK_RATIO = 0.30;
        if (mag < top_mag * MIN_PEAK_RATIO) {
            continue;
        }

        // Legacy mode: L-LTF period is 64 samples (diff 62-66)
        if (diff >= 62 && diff <= 66) {
            int p1 = get<1>(vec[i]);
            int p2 = get<1>(vec[k]);
            int lower_peak = min(p1, p2);
            d_frame_start = lower_peak + 2;
            mode = "Legacy-mode";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] Legacy-mode: lower_peak=%d, d_frame_start=%d, peak_mag=%.4f\n",
                    lower_peak, d_frame_start, mag);
            return;
        }
    }
}
```

- [ ] **Step 4: 重新构建**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
```

- [ ] **Step 5: 运行测试验证**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(SYNC_LONG|SPLITTER_FFTPROBE|total_energy|L-SIG|LSIG|FRAME_DETECT)"
```

**预期结果**：
- lower_peak 应该从 79 变成 ~171（正确值）
- d_frame_start 应该从 81 变成 ~173
- SPLITTER_FFTPROBE total_energy 对于 L-LTF/L-SIG/HT-SIG 应该从 ~1.6-1.9 上升到 ~8-12
- L-SIG rate field 应该显示 0x0D（而非 0x0A/0x00）

- [ ] **Step 6: 提交**

```bash
git add lib/sync_long.cc
git commit -m "fix: add peak magnitude threshold to prevent false lock in sync_long"
```

---

## Task 2: 验证和微调峰值门限

**Files:**
- Modify: `lib/sync_long.cc`（如果需要微调 MIN_PEAK_RATIO）
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 检查日志输出**

运行测试后，检查：
```
[SYNC_LONG] Top correlation magnitude: X.XXXX
[SYNC_LONG] Reject peak at Y: mag=Z.ZZZZ (below 30% of top)
```

如果 valid peak 也被 reject 了，降低 MIN_PEAK_RATIO 到 0.20 或 0.25。

如果 noise peak 仍然通过，增加 MIN_PEAK_RATIO 到 0.40 或 0.50。

- [ ] **Step 2: 如果需要微调，更新 MIN_PEAK_RATIO**

```cpp
const double MIN_PEAK_RATIO = 0.25;  // 调整这个值
```

重新构建和测试。

- [ ] **Step 3: 提交微调**

```bash
git add lib/sync_long.cc
git commit -m "fix: adjust MIN_PEAK_RATIO to X in sync_long"
```

---

## Task 3: 端到端验证

**Files:**
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 运行完整测试**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | tail -40
```

**预期**：
- L-SIG rate field: 0x0D（BPSK 1/2）
- HT-SIG0 CRC: 通过
- 帧检测为 HT-Mixed（而非 Legacy）
- 接收到消息数 > 0

- [ ] **Step 2: 提交**

```bash
git add lib/sync_long.cc lib/frame_equalizer_impl.cc
git commit -m "feat: sync_long peak threshold fix enables correct L-SIG/HT-SIG decoding"
```

---

## Self-Review Checklist

1. **Spec coverage:** 所有任务都映射到修复伪峰值锁定的目标。无占位符任务。

2. **Placeholder scan:** 无 "TBD"、"TODO" 或模糊步骤。每步显示精确代码改动。

3. **Type consistency:** 函数名（search_frame_start, compare_abs）保持一致。d_frame_start 和 lower_peak 的语义明确。

4. **测试验证:** 每任务以具体测试命令和预期输出结束。

5. **关键修复点:**
   - 添加峰值幅度门限（MIN_PEAK_RATIO = 0.30）
   - 使用 actual lower_peak + 2，而不是硬编码 176
   - 对 HT Mixed 和 Legacy 两种模式都应用门限
