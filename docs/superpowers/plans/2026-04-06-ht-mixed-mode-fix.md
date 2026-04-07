# HT Mixed Mode 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 gr-ieee802-11 的 HT Mixed 模式，使 TX/RX 环回测试能够正确解码，数据比特不再全零

**Architecture:** IEEE 802.11n HT Mixed 模式修复，核心问题在于：
1. sync_long.cc 的 `search_frame_start()` 被硬编码为测试值，丢失了原始的相关峰值检测逻辑
2. FFT 窗口位置与 OFDM 符号边界不对齐，导致信道估计错误
3. CFO 校正方向可能反转

**Tech Stack:** C++ (GNU Radio blocks), Python (GRC), IEEE 802.11n

---

## 问题诊断总结

根据记忆文件和代码分析：

| 问题 | 症状 | 根因 |
|------|------|------|
| FFT 窗口对齐 | d_H 有大虚部(-1.914+1.130i)，幅度 2-5 | d_frame_start=32 可能对应 L-LTF 数据部分中间位置，而非 OFDM 符号起始 |
| sync_long 硬编码 | FIR 相关峰值位置检测失效 | `search_frame_start()` 被替换为 `d_frame_start = 32; d_freq_offset = 0.0f` |
| Viterbi 多项式 | 已在 commit d81be6c 修复 | 0155/0117 → 0133/0171 |

**IEEE 802.11n L-LTF 结构：**
```
位置 0-159:    L-STF (短训练符号)
位置 160-175:  L-LTF0 循环前缀 (CP, 16样本)
位置 176-239:  L-LTF0 数据部分 (64样本 LONG 序列)
位置 240-255:  L-LTF1 CP
位置 256-319:  L-LTF1 数据部分
```

FIR 滤波器使用 64 样本 LONG 序列，相关峰值应该在 `L-LTF 数据部分起始位置 (176)` 最强。但 FFT 窗口必须从 **CP 结束位置 (176)** 开始，而不是从数据部分中间开始。

---

## 任务分解

### Task 1: 恢复 sync_long.cc 的原始检测逻辑

**文件:** `lib/sync_long.cc:190-205`

**当前问题:** `search_frame_start()` 被硬编码为固定值 32，丢失了原始的相关峰值检测 + CFO 估算逻辑。

**修复步骤:**

- [ ] **Step 1: 查看当前 search_frame_start() 代码**

```cpp
// lib/sync_long.cc:190-205
void search_frame_start()
{
    // sort list (highest correlation first)
    assert(d_cor.size() == SYNC_LENGTH);
    d_cor.sort(compare_abs);

    // copy list in vector for nicer access
    vector<pair<gr_complex, int>> vec(d_cor.begin(), d_cor.end());
    d_cor.clear();

    // Use fixed d_frame_start for testing - assume L-LTF starts at position 32
    d_frame_start = 32;
    // No CFO correction
    d_freq_offset = 0.0f;
}
```

- [ ] **Step 2: 恢复原始检测逻辑**

用以下代码替换硬编码部分：

```cpp
void search_frame_start()
{
    // sort list (highest correlation first)
    assert(d_cor.size() == SYNC_LENGTH);
    d_cor.sort(compare_abs);

    // copy list in vector for nicer access
    vector<pair<gr_complex, int>> vec(d_cor.begin(), d_cor.end());
    d_cor.clear();

    // in case we don't find anything use SYNC_LENGTH
    d_frame_start = SYNC_LENGTH;

    for (int i = 0; i < 3; i++) {
        for (int k = i + 1; k < 4; k++) {
            gr_complex first;
            gr_complex second;
            if (get<1>(vec[i]) > get<1>(vec[k])) {
                first = get<0>(vec[k]);
                second = get<0>(vec[i]);
            } else {
                first = get<0>(vec[i]);
                second = get<0>(vec[k]);
            }
            int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
            if (diff == 64) {
                d_frame_start = min(get<1>(vec[i]), get<1>(vec[k]));
                d_freq_offset = arg(first * conj(second)) / 64;
                // nice match found, return immediately
                return;
            } else if (diff == 63) {
                d_frame_start = min(get<1>(vec[i]), get<1>(vec[k]));
                d_freq_offset = arg(first * conj(second)) / 63;
            } else if (diff == 65) {
                d_frame_start = min(get<1>(vec[i]), get<1>(vec[k]));
                d_freq_offset = arg(first * conj(second)) / 65;
            }
        }
    }
}
```

- [ ] **Step 3: 验证修改**

检查 `d_frame_start` 和 `d_freq_offset` 是否被正确设置。

- [ ] **Step 4: 编译测试**

```bash
cd /home/hy/gr-ieee802-11/build
make -j4
```

- [ ] **Step 5: 提交**

```bash
git add lib/sync_long.cc
git commit -m "fix: restore original correlation-based frame start detection in sync_long"
```

---

### Task 2: 分析 FFT 窗口对齐问题

**问题背景:**

即使恢复了 sync_long 的原始检测逻辑，FFT 窗口位置仍然可能不正确。

**802.11n HT Mixed 帧结构：**
```
L-STF × 2 (160 samples) → L-LTF × 2 (160 samples) → L-SIG (80 samples) → HT-SIG × 2 (160 samples) → HT-STF (80 samples) → HT-LTF × 2 (160 samples) → HT-DATA
```

**OFDM 符号结构：**
- 每符号 = CP(16样本) + 数据(64样本) = 80 样本
- L-LTF0: CP(16) 在位置 160-175，数据(64) 在位置 176-239
- L-LTF1: CP(16) 在位置 240-255，数据(64) 在位置 256-319

**FIR 滤波器相关峰值位置分析:**

FIR 滤波器使用 64 样本 LONG 序列与输入相关。当 L-LTF 数据部分（位置 176-239）与 LONG 序列对齐时，相关输出最强。

但 FFT 窗口应该从 **CP 结束位置 (176)** 开始，而不是从位置 176 之后某个偏移开始。

**实际测试方案:**

- [ ] **Step 1: 在 sync_long 中添加调试输出**

在 `search_frame_start()` 中，打印找到的 `d_frame_start` 值和 CFO：

```cpp
mylog("LONG: frame start at {}, CFO={}", d_frame_start, d_freq_offset);
```

- [ ] **Step 2: 在 ls.cc 的 equalize() 中验证信道估计**

打印 d_H 值，验证其是否接近实数值（无信道畸变时）或具有合理的幅度。

- [ ] **Step 3: 运行环回测试**

```bash
cd /home/hy/gr-ieee802-11/build
make -j4
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python examples/test_loopback_noqt.py 2>&1 | tee /tmp/test_output.log
```

- [ ] **Step 4: 分析输出**

检查：
1. d_frame_start 的实际值是多少？
2. d_H 的幅度和相位是否合理？
3. HT-SIG 解码是否成功？

---

### Task 3: FFT 窗口偏移补偿（如果 Task 2 分析表明需要）

**可能需要的修复:**

如果分析表明 FFT 窗口确实偏移了，需要在 frame_equalizer 或 sync_long 中添加补偿。

**方案 A: 在 sync_long 的 COPY 状态中调整 d_offset**

```cpp
case COPY:
    while (i < ninput && o < noutput) {
        int rel = d_offset - d_frame_start;

        // 如果 d_frame_start 对应 L-LTF 数据部分起始，
        // 需要偏移 -16 才能让 FFT 窗口从 CP 结束位置开始
        if (!rel) {
            add_item_tag(...);
        }

        // CP 长度 = 16 样本
        if (rel >= -16 && (rel < 128 || ((rel - 128) % 80) > 15)) {
            out[o] = in_delayed[i] * exp(gr_complex(0, d_offset * d_freq_offset));
            o++;
        }

        i++;
        d_offset++;
    }
    break;
```

**方案 B: 在 frame_equalizer 中调整 FFT 窗口起始位置**

如果 `d_early_eqsym` 缓存的数据是从错误位置提取的，需要修正。

---

### Task 4: 验证 Viterbi 解码器配置

**文件:** `lib/viterbi_decoder/viterbi_decoder_generic.cc`

**检查:** 确认 generator polynomials 是 IEEE 标准值：
- G1 = 0x6D (octal 0155) → 133 octal = 0x6D decimal = 109
- G2 = 0x4F (octal 0117) → 171 octal = 0x79 decimal = 121

等等，commit d81be6c 已经修复了这个问题：
> "Fix Viterbi decoder generator polynomials for L-SIG/HT-SIG decoding"

所以多项式应该是正确的 0155/0117 (octal)。

**验证:** 检查 Viterbi decoder 是否使用正确的 K=7, rate=1/2 配置。

---

### Task 5: 验证 HT-SIG QBPSK 旋转检测

**文件:** `lib/frame_equalizer_impl.cc:1015-1052`

**检查:** `detect_htsig_rotation()` 函数是否正确检测 QBPSK 旋转。

HT-SIG 使用 QBPSK，90° 旋转由乘以 `j` 实现：
- rotation=0: 无旋转
- rotation=1: +90° (乘以 j)
- rotation=2: -90° (乘以 -j)
- rotation=3: 180° (乘以 -1)

当前实现通过 pilot 相位来检测旋转。

---

### Task 6: 运行完整环回测试

- [ ] **Step 1: 清理并重新编译**

```bash
cd /home/hy/gr-ieee802-11/build
make clean
make -j4
```

- [ ] **Step 2: 运行环回测试**

```bash
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python examples/test_loopback_noqt.py 2>&1 | tee /tmp/test_final.log
```

- [ ] **Step 3: 检查输出**

期望看到：
1. `wifi_start` tag 被正确设置
2. L-LTF 信道估计产生合理的 d_H 值
3. HT-SIG CRC 校验通过
4. DATA 符号被正确解码

---

## 关键文件清单

| 文件 | 任务 | 状态 |
|------|------|------|
| lib/sync_long.cc | Task 1: 恢复原始检测逻辑 | 待修复 |
| lib/equalizer/ls.cc | Task 2: 信道估计验证 | 已修改（有调试输出） |
| lib/frame_equalizer_impl.cc | Task 3: FFT 窗口补偿 | 已修改（有调试输出） |
| lib/viterbi_decoder/viterbi_decoder_generic.cc | Task 4: 验证多项式 | 已在 d81be6c 修复 |
| examples/test_loopback_noqt.py | Task 6: 环回测试 | 存在 |

---

## 预期结果

修复完成后，期望：
1. `sync_long` 能够正确检测 L-LTF 的位置（不是硬编码的 32）
2. `d_H` 信道估计值接近实数幅度 1.0
3. HT-SIG 正确解码（CRC 通过）
4. TX/RX 环回数据匹配

---

## 备选方案: 使用 Python 脚本验证 TX 波形

如果 C++ 调试困难，可以创建一个 Python 脚本来：
1. 生成已知的 TX 波形
2. 手动运行部分 RX 处理
3. 验证每个步骤的输出

但这应该在 C++ 调试之后作为辅助手段。
