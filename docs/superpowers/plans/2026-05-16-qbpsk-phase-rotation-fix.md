# QBPSK Phase Rotation Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 QBPSK 相位旋转检测错误导致的比特反转问题，使 L-SIG 和 HT-SIG CRC 能够通过。

**Architecture:** L-SIG 和 HT-SIG 使用 QBPSK 调制。TX 生成 QBPSK：bit 0 → -j，bit 1 → +j。RX 需要正确检测信道相位旋转并解码。当前 RX 的相位旋转检测逻辑存在问题，导致 bit 0 被解码为 bit 1（反转）。

**Tech Stack:** GNU Radio 3.10, C++ (frame_equalizer_impl.cc), Python (test_mcs_end_to_end.py)

---

## 问题诊断总结

**症状：**
- L-SIG: `inv_lsig=0` (正确) 和 `inv_lsig=1` (反转) 交替出现
- HT-SIG: CRC 失败 1-8 位
- 错误是系统性的，不是随机的

**根本原因分析：**

TX QBPSK 编码：
```
bit 0 → -j (相位 -90°)
bit 1 → +j (相位 +90°)
```

RX 当前解码逻辑：
```
imag >= 0 → bit 0
imag < 0 → bit 1
```

问题：当信道相位 ≈ 0° 时：
- TX bit 0 = -j (imag = -1) 被解码为 bit 1 (错误！)
- TX bit 1 = +j (imag = +1) 被解码为 bit 0 (错误！)

**或者反之：**

TX QBPSK 编码（IEEE 802.11 规范）：
```
bit 0 → +1 (实轴正方向) 或 -1
bit 1 → -1 (实轴负方向) 或 +1
```

取决于具体的实现和相位旋转。

---

## 文件映射

**主要文件：**
- `lib/frame_equalizer_impl.cc` - HT-SIG 解码和相位旋转检测
- `test_mcs_end_to_end.py` - 测试脚本

**参考文件：**
- `examples/mixed_mode_carrier_allocator.py` - TX QBPSK 生成

---

## Task 1: 分析 TX QBPSK 编码方式

**Files:**
- Analyze: `examples/mixed_mode_carrier_allocator.py` - TX QBPSK 生成
- Analyze: `lib/frame_equalizer_impl.cc` - RX QBPSK 解码

**Step 1: 检查 TX QBPSK 编码**

查看 mixed_mode_carrier_allocator.py 中 QBPSK 的编码方式：

```bash
grep -n "bpsk\|QBPSK\|qbpsk\|bit_to_sym\|symbol" /home/hy/gr-ieee802-11/examples/mixed_mode_carrier_allocator.py | head -30
```

**Step 2: 检查 RX QBPSK 解码**

查看 frame_equalizer_impl.cc 中 HT-SIG 的解码方式：

```bash
grep -n "htsig\|rotation\|pilot\|bpsk" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -30
```

**Step 3: 对比 TX 和 RX 的映射关系**

分析 TX 和 RX 的映射是否一致。

---

## Task 2: 检查相位旋转检测逻辑

**Files:**
- Analyze: `lib/frame_equalizer_impl.cc` - 相位旋转检测

**Step 1: 查找相位旋转检测代码**

```bash
grep -n "rotation\|rot\|pilot" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -40
```

**Step 2: 分析 pilot-based rotation 检测**

Pilot 子载波用于检测相位旋转。检查 pilot 的预期相位和实际相位。

**Step 3: 检查能量投票机制**

能量投票机制可能导致错误的旋转检测。

---

## Task 3: 修复 QBPSK 映射或相位旋转检测

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - 修复 QBPSK 解码或相位旋转检测

**Step 1: 确定修复方案**

根据 Task 1 和 Task 2 的分析结果，确定具体的修复方案。

**可能的修复方案：**

方案 A: 修改 RX 解码逻辑
```cpp
// 当前（可能错误）：
imag >= 0 → bit 0
imag < 0 → bit 1

// 修正为：
imag < 0 → bit 0
imag >= 0 → bit 1
```

方案 B: 修改相位旋转检测
- 确保 pilot-based rotation 正确检测信道相位偏移
- 确保能量投票机制不会覆盖正确的 pilot 检测

**Step 2: 实现修复**

**Step 3: 运行测试验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1 | grep -E "inv_lsig|CRC|pass|fail" | tail -20
```

---

## Task 4: 最终验证

**Files:**
- Test: `test_mcs_end_to_end.py` - 端到端测试

**Step 1: 运行完整测试**

```bash
cd /home/hy/gr-ieee802-11 && LD_PRELOAD=./wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python3.8 test_mcs_end_to_end.py 2>&1
```

**Step 2: 验证 L-SIG 和 HT-SIG CRC 通过**

**Step 3: 清理调试日志**

**Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: fix QBPSK phase rotation detection"
```
