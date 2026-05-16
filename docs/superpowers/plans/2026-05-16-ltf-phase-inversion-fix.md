# LTF Phase Inversion Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 HT-SIG CRC 失败问题。当前状态：LTF 相位已修复，但 HT-SIG 解码仍有 1-6 位的系统误差。

**Architecture:** LTF0 和 LTF1 相位已修复（commit e33a8fc）。HT-SIG CRC 失败是因为解码的位数有 1-6 位的系统误差，可能是 QBPSK 比特映射反转或相位旋转检测问题。

**Tech Stack:** GNU Radio 3.10, C++ (sync_long.cc, frame_equalizer_impl.cc, ht_symbol_splitter_impl.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**当前状态（已更新）：**
- LTF0 vs LTF1 相位差：~0-4°（已修复）✓
- HT-SIG FFT 输出位置：正确（rel_idx=303, 383）✓
- HT-SIG CRC 失败：1-6 位误差 ✗

**根本原因分析：**
1. ✅ LTF 相位反转：已修复（commit e33a8fc 替换纯实数 LONG 模板为 IEEE 复数模板）
2. ✅ SPLITTER HT-SIG FFT 边界：已修复（移位 32 样本）
3. ❓ HT-SIG CRC：仍然失败，疑是 QBPSK 比特映射反转
   - TX 生成：bit 0 → -j，bit 1 → +j
   - RX 解码：imag >= 0 → bit 0，imag < 0 → bit 1
   - 当信道相位 ≈ 0° 时，TX bit 0 (-j) 被解码为 bit 1

**调查结论：**
- LTF 相位反转问题已解决
- FFT 窗口定时正确
- HT-SIG 解码问题是 QBPSK 映射或相位旋转检测的问题，需要进一步调查

---

## 文件映射

**主要文件：**
- `lib/sync_long.cc` - LONG 模板相关
- `lib/ht_symbol_splitter_impl.cc` - FFT 边界和符号分割
- `lib/frame_equalizer_impl.cc` - 信道估计和 HT-SIG 解码
- `lib/equalizer/ls.cc` - LS 信道估计
- `test_mcs_end_to_end.py` - 测试脚本

**诊断文件：**
- `docs/superpowers/specs/2026-05-14-ltf-phase-inversion-diagnostic.md` - LTF 相位反转诊断规格

---

## Task 1: 验证 LTF0 vs LTF1 时域数据 ✅ COMPLETED

**Findings (2026-05-16):**

**SPLITTER_TD_PROBE 输出：**
```
LTF0 TD[0] = 1.085774+0.029127i
LTF1 TD[0] = 1.041578+0.026737i
```
- 这两个值几乎相同（幅度差异约4%），不是简单的否定关系

**NAKED_TEST LTF0 vs LTF1 FFT 相位比较：**
```
bin[ 6]: LTF0=8.615∠33.4° LTF1=8.715∠34.3° diff=+0.9°
bin[ 7]: LTF0=9.095∠128.5° LTF1=8.829∠131.9° diff=+3.4°
bin[ 8]: LTF0=8.609∠-132.3° LTF1=9.082∠-136.0° diff=-3.7°
bin[ 9]: LTF0=8.928∠-39.6° LTF1=8.730∠-39.2° diff=+0.4°
bin[10]: LTF0=8.902∠56.0° LTF1=8.915∠54.9° diff=-1.1°
```
- 相位差异仅为 0-4 度，不是 180°！

**信道估计 H 比较：**
```
SC[-26] bin[38]: H0=0.8252+0.5225i mag=0.9768 | H1=0.8228+0.5650i mag=0.9981 | ratio=1.02
[SUMMARY] Avg H magnitude: LTF0=0.9980 LTF1=0.9986 ratio=1.0005
```
- LTF0 和 LTF1 的信道估计几乎相同

**结论：LTF0 vs LTF1 180° 相位反转问题已被修复！**

根本原因：之前 sync_long.cc 中的 LONG 模板是纯实数近似值（×0.1），已由 commit e33a8fc 替换为正确的 IEEE 复杂 LTF 模板（×1.0）。

**当前状态：**
- LTF0 和 LTF1 的 TD 和 FFT 数据现在几乎相同 ✓
- HT-SIG CRC 仍然失败，但原因不是 180° 相位反转 ✗

---

## Task 2: 检查 FFT 窗口是否对齐 ✅ (分析完成)

### HT-SIG Bit Error Analysis (2026-05-16)

**CRC 错误模式：**
```
RX_CRC decoded_bits[0:34] = 1110010111011011000110001000010001
RX_CRC computed_crc=0xB7 rx_crc=0xB6  <- 只有1位错误!
```

**发现：**
1. HT-SIG CRC 错误始终很小（1-6位），不是完全随机
2. 某些情况下只有1位错误（rx_crc=0xB6 vs computed=0xB7）
3. Viterbi 解码器正在部分纠正错误，但不完全

**旋转检测冲突：**
```
[HT_SIG] pilot-based rotation=0
[HT_SIG] energy-based rotation=1
[HT_SIG] Energy vote overrides pilot: 0 -> 1
```
- 能量投票（ratio=1.094 > 1.0）检测到 QBPSK 旋转
- 导频投票返回无旋转（可能是导频子载波的相位问题）

**HT-SIG0 子载波分析：**
```
[ 0] sc=-26 bin=38 val=1.1972-0.0383i |I|=1.1972 |Q|=0.0383 bit=1
[ 1] sc=-25 bin=39 val=2.4632-0.3615i |I|=2.4632 |Q|=0.3615 bit=1
[ 2] sc=-24 bin=40 val=-3.2292+4.0950i |I|=3.2292 |Q|=4.0950 bit=0
```
- 值[0]和[1]有 |I| >> |Q|，这不是正确的 QBPSK 旋转
- QBPSK 应该有 |Q| >> |I|（虚轴编码）

**FFT 窗口时间假设：**
- d_frame_start_abs=176 可能与 HT-SIG 符号边界不完全对齐
- SPLITTER 在 rel_idx=3 输出 HT-SIG0（sample 368）
- 标准 HT-Mixed 将 HT-SIG0 放在 sample 240
- 48-sample 差异可能导致 HT-SIG FFT 窗口的 CP 污染

**可能的根本原因：**
1. SPLITTER 的 FFT 窗口边界对于 HT-SIG 符号不正确
2. 信道估计 H 用于 HT-SIG 解码时有轻微的相位偏移
3. QBPSK 旋转补偿应用不正确

**待验证：**
- SPLITTER 是否在正确的样本位置输出 HT-SIG FFT？
- HT-SIG 解码时使用的信道估计是否正确？

---

## Task 3: 修复 LTF 相位问题

**Files:**
- Analyze: `lib/ht_symbol_splitter_impl.cc` - FFT 边界条件
- Analyze: `test_mcs_end_to_end.py` - 测试探针

**Step 1: 检查 SPLITTER 是否在正确的位置输出 FFT**

SPLITTER 应在以下位置输出 FFT：
- LTF0: rel_idx=63 (64个样本后)
- LTF1: rel_idx=143 (再64个样本后)

**Step 2: 验证 FFT 输入数据**

检查 FFT 输入的前几个样本是否与预期的 LTF 数据匹配。

**Step 3: 分析 LTF0 和 LTF1 是否使用相同的模板**

如果 LTF0 和 LTF1 使用不同的模板或有不同的相位旋转，则会导致相位差。

---

## Task 3: 调查 HT-SIG QBPSK 映射问题

**Files:**
- Analyze: `lib/frame_equalizer_impl.cc` - HT-SIG 解码和 QBPSK 旋转检测
- Analyze: `examples/mixed_mode_carrier_allocator.py` - TX QBPSK 生成

**Step 1: 检查 QBPSK 比特映射**

TX 和 RX 的 QBPSK 映射需要一致：
- TX: bit 0 → -j, bit 1 → +j (或反过来)
- RX: 需要正确检测相位旋转并解码

**Step 2: 检查相位旋转检测逻辑**

在 frame_equalizer_impl.cc 中检查 HT-SIG 的相位旋转检测逻辑。

**Step 3: 运行测试验证**

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "CRC|pass|fail" | tail -20
```

---

## Task 4: 最终验证

**Files:**
- Test: `test_mcs_end_to_end.py` - 端到端测试

**Step 1: 运行完整测试**

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1
```

**Step 2: 验证 HT-SIG CRC 通过**

检查输出中是否有 "CRC 通过" 或类似的成功消息。

**Step 3: 清理调试日志**

移除所有临时调试日志。

**Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: fix LTF phase inversion issue"
```
