# FFT 窗口对齐修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 修复 FFT 窗口对齐问题，使导频子载波（indices 48-51）能正确捕获，L-SIG 解码成功。

**Architecture:** FFT 窗口对齐问题导致 ht_symbol_splitter 输出的符号边界不正确，使得 FFT 采样点落在 CP 区域而非数据区域。

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python

---

## 根因分析结果（2026-05-10 子智能体调试）

### 问题：FFT 窗口对齐错误

**关键发现：**
- `rx52[48:51]`（导频）全为 `0.000+0.000i` — 导频完全丢失
- `H52[48:51]` ≠ 0 — 信道估计非零，但接收数据是零
- 说明 FFT 窗口没有对齐到正确的符号边界

### HT-mixed mode 帧结构

```
rel_idx:
  0-63:   L-LTF0 DATA (64 samples)
  64-127: L-LTF1 DATA (64 samples)
  128-191: L-SIG DATA (64 samples) + CP (前16个sample)
  192-255: HT-SIG0 CP + DATA
```

### 已应用的修复

1. **sync_long**: `d_frame_start = 176` 强制值
2. **frame_equalizer**: 4th power CPE 估计函数（回退方案）
3. **deinterleaver**: `j = 16*(k%3) + k/3` 公式

### 当前状态

- `lib/frame_equalizer_impl.cc` — 有 4th power CPE 回退
- `lib/ht_symbol_splitter_impl.cc` — 符号分割
- `lib/sync_long.cc` — `d_frame_start = 176`

### 部分修复效果

- rate=0x01, rate=0x07 可以解码成功
- **但 rate=0x0D (HT-mixed mode) 仍失败**

---

## 任务 1: 验证 FFT 窗口对齐

**文件:**
- 修改: `lib/frame_equalizer_impl.cc` - 添加 FFT 输出调试

- [ ] **Step 1: 在 extract_header52_from_sym64 添加调试**

在 `extract_header52_from_sym64` 函数中，打印 `sym64[48:52]` 的值：

```cpp
fprintf(stderr, "[FFT_RAW] sym64[48:52]=");
for (int i = 48; i < 52; i++) {
    fprintf(stderr, "[%d]=%.3f+%.3fi ", i, sym64[i].real(), sym64[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: 编译**

```bash
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)
```

- [ ] **Step 3: 运行测试**

```bash
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 30 python examples/test_loopback_noqt.py 2>&1 | grep -E "FFT_RAW|sym64\[48"
```

**预期:** `sym64[48:52]` 应该有非零值（导频 {1, 1, 1, -1}）

**如果全为零:** FFT 窗口对齐错误

---

## 任务 2: 检查 ht_symbol_splitter 边界计算

**文件:**
- 修改: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: 阅读当前符号边界计算逻辑**

```bash
sed -n '180,280p' /home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc
```

- [ ] **Step 2: 验证 L-SIG 符号位置**

L-SIG 应该输出在 `rel_idx 128-191`

---

## 任务 3: 验证 sync_long 帧起始检测

**文件:**
- 修改: `lib/sync_long.cc`

- [ ] **Step 1: 检查 d_frame_start 值**

当前强制值 `d_frame_start = 176` 是否正确？

---

## 任务 4: 定位 FFT 窗口偏移

- [ ] **Step 1: 对比 TX 和 RX 的符号计数**

- [ ] **Step 2: 识别偏移模式**

---

## 任务 5: 实现修复

基于任务 1-4 分析实现修复。

---

## 任务 6: 验证修复

- [ ] **Step 1: 运行 L-SIG 解码测试**

```bash
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG_DECODE.*SUCCESS|lsig_enc=0"
```

- [ ] **Step 2: 验证 HT-SIG CRC**

- [ ] **Step 3: 验证 HT-DATA 输出**

---

## 任务 7: 清理调试输出

- [ ] 移除任务 1-4 添加的调试 fprintf
- [ ] 保留 4th power CPE 函数
- [ ] 编译验证

---

## 调试命令

```bash
# 编译
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# 激活 conda
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# 运行测试
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1

# L-SIG 专用
LD_LIBRARY_PATH=... timeout 60 python ... 2>&1 | grep -E "LSIG|TX.*LSIG|FFT_RAW"
```

## 成功标准

1. `sym64[48:52]` 包含非零值（导频 {1, 1, 1, -1}）
2. `lsig_enc == 0` (HT-mixed mode)
3. `d_have_ht_header == 1`
4. HT-SIG CRC = 0x41
5. HT-DATA 正确发出
