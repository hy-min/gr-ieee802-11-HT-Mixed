# LTF Phase Inversion Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 LTF0 和 LTF1 之间的 180° 相位反转问题，使 HT-SIG CRC 能够通过。

**Architecture:** LTF0 和 LTF1 FFT 输出之间存在恒定的 180° 相位差。这导致信道估计错误，因为 LTF0 和 LTF1 的信道估计值不一致。需要调查相位反转的根源并修复。

**Tech Stack:** GNU Radio 3.10, C++ (sync_long.cc, frame_equalizer_impl.cc, ht_symbol_splitter_impl.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**当前状态：**
- SPLITTER HT-SIG FFT 输出已修复 ✓
- HT-SIG 符号到达均衡器 ✓
- HT-SIG CRC 失败 ✗

**LTF 相位反转现象：**
- LTF0 FFT vs LTF1 FFT 输出在全频段恒定相差 ~180°
- 相位差无斜率（排除 FFT 窗口定时偏移）
- 仿真实环境下 SNR=30dB，无信道损伤

**RAW_FFT_64 探针数据：**
```
| bin | SC | LTF0 Phase | LTF1 Phase | diff |
|-----|-----|-----------|-----------|------|
| 5 | -7 (pilot) | +90.0° | -89.7° | ~180° |
| 6 | +6 | +160.9° | -18.7° | ~180° |
| 37 | +4 | +178.0° | +1.6° | ~176° |
| 38 | -26 | +176.8° | -5.5° | ~182° |
```

**已排除的嫌疑人：**
1. ✅ FFT Shift 配置 — shift=False 确认正确
2. ✅ kHeader48Bin 数组映射 — 数学 100% 正确
3. ✅ TX 发射极性 — LEGACY_LTF x2 完全相同，无 P-Matrix 误用
4. ✅ sync_long CFO 补偿 — 被阈值 >100.0 禁用，未生效
5. ✅ 信道模型 — frequency_offset=0, taps=[1.0]

**SPLITTER_TD_PROBE 探针：**
- LTF0 TD[0] = -0.072+0.284i
- LTF1 TD[0] = 0.206-0.526i
- 两者既不相等，也不是简单的否定关系
- 说明 SPLITTER 输出的可能不是完整的 LTF DATA，而是混合了 CP 和 DATA 的窗口

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

## Task 3: 修复 LTF 相位问题

**Files:**
- Modify: `lib/sync_long.cc` - 如果 LONG 模板有问题
- Modify: `lib/ht_symbol_splitter_impl.cc` - 如果 FFT 窗口对齐有问题
- Modify: `lib/frame_equalizer_impl.cc` - 如果信道估计逻辑有问题

**Step 1: 确定修复方案**

根据 Task 1 和 Task 2 的分析结果，确定具体的修复方案。

**可能的修复方案：**
1. 如果是 LONG 模板问题：修正 sync_long.cc 中的 LONG 模板
2. 如果是 FFT 窗口对齐问题：调整 SPLITTER 的边界条件
3. 如果是信道估计问题：在 frame_equalizer_impl.cc 中添加相位补偿

**Step 2: 实现修复**

根据分析结果实施修复。

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
