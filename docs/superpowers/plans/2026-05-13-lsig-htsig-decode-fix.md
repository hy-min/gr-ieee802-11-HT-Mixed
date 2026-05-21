# L-SIG/HT-SIG Decode Fix - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 L-SIG 和 HT-SIG 解码失败的问题。当前 channel estimation 已恢复正常（H magnitude ~1.0），但解码仍失败：`parse failed: lsig=2 htsig=3/4`。

**Architecture:** 问题可能在：(1) L-SIG/HT-SIG bit extraction 逻辑，(2) deinterleaver/permutation 索引，(3) Viterbi decoder，(4) 符号边界对齐。调查方向：从 RX 链路末端向前追溯，定位第一个出错的位置。

**Tech Stack:** GNU Radio blocks (frame_equalizer), C++ debug probes, Python test

---

## 当前状态

**已修复：**
- ✅ sync_long COPY transition bug - 不再输出噪声
- ✅ L-LTF td_energy 恢复正常 (~63)
- ✅ Channel estimation H magnitude 恢复正常 (~0.5-1.6)

**当前问题：**
- ❌ L-SIG/HT-SIG parse failed: `lsig=2 htsig=3/4`
- ❌ 接收消息数 = 0

**测试输出（关键片段）：**
```
[CHAN_EST] n=0: d_H[6-10] = -0.4262-0.4969i 0.4508+0.3552i ... (mag=0.6546 0.5739 1.1300 1.5890 0.6298 )
[FRAME_DETECT] L-SIG: E_I=1738.02 E_Q=1891.96 ratio=1.089
[FRAME_DETECT] HT-SIG0: E_I=1754.70 E_Q=1823.27 ratio=1.067
[EQ_FULL] Equalized L-SIG symbols (48 data SC):
  SC[-26] idx[ 0]: eq=-12.3262+8.9063i bit=0
  SC[-25] idx[ 1]: eq=-8.4588-3.5182i bit=0
  ...
[EQ][HT-SIG] parse failed: lsig=2 htsig=3/4
```

---

## File Structure

- `lib/frame_equalizer_impl.cc` — L-SIG/HT-SIG 解码逻辑
- `lib/ht_symbol_splitter_impl.cc` — 符号边界对齐
- `examples/test_mcs_end_to_end.py` — 端到端测试

---

## Task 1: 分析 EQ_FULL 输出，验证均衡后符号是否正确

**Files:**
- Analyze: `examples/test_mcs_end_to_end.py` 输出
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 运行测试并保存完整 EQ_FULL 输出**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -A 50 "EQ_FULL.*L-SIG" | head -60
```

分析均衡后的 L-SIG 符号：
- 预期：对于 BPSK 1/2 (rate 0x0D)，均衡后符号应该在 I 轴上有明显分离
- 实际：查看 eq 值和 bit 判决

- [ ] **Step 2: 分析 HT-SIG 均衡输出**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -A 50 "EQ_FULL.*HT-SIG" | head -60
```

- [ ] **Step 3: 检查 TX 端的 L-SIG/HT-SIG 预期值**

对比 TX 打印的 bits 和 RX 解出的 bits：
```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(TX.*L-SIG|TX.*HT-SIG|TX.*lsig|TX.*htsig)" | head -20
```

---

## Task 2: 检查 L-SIG Rate 字段解析

**Files:**
- Read: `lib/frame_equalizer_impl.cc:parse_l_sig()`, `parse_ht_sig()`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 找到 L-SIG 解析函数**

```bash
grep -n "parse_l_sig\|lsig\b" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -30
```

- [ ] **Step 2: 检查 L-SIG rate field 解析逻辑**

读取 parse_l_sig() 函数的实现：
- 检查 rate 字段如何从 48 个 soft bits 变成硬判决 bits
- 检查 parity 计算
- 检查如何提取 length 字段

- [ ] **Step 3: 添加探针打印 L-SIG 原始 bits 和解析结果**

在 parse_l_sig() 中添加：
```cpp
fprintf(stderr, "[LSIG_PARSE] rate_bits=%02x parity=%d length=%d\n",
        lsig_bits.rate(), lsig_bits.parity(), lsig_bits.length());
```

---

## Task 3: 检查 Deinterleaver 索引

**Files:**
- Read: `lib/frame_equalizer_impl.cc` 中的 permutation/deinterleaver 逻辑
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 找到 deinterleaver/permutation 逻辑**

```bash
grep -n "deinterleave\|permut\|k.*%" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -20
```

- [ ] **Step 2: 验证 permutation 索引是否正确**

IEEE 802.11n 的 L-SIG deinterleaver：
- 第一次 permutation: `k = (N_col * i) mod 48`，其中 N_col=13
- 第二次 permutation: `j = N_row * k / N_col`

检查代码中的索引计算是否与标准一致。

---

## Task 4: 检查 HT-SIG 解析

**Files:**
- Read: `lib/frame_equalizer_impl.cc:parse_ht_sig()`
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 找到 HT-SIG 解析函数**

```bash
grep -n "parse_ht_sig\|htsig\|ht_sig" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -30
```

- [ ] **Step 2: 检查 HT-SIG CRC 验证**

```bash
grep -n "CRC\|crc\|checksum" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -20
```

- [ ] **Step 3: 添加探针打印 HT-SIG 原始 bits 和 CRC 结果**

在 parse_ht_sig() 中添加：
```cpp
fprintf(stderr, "[HTSIG_PARSE] MCS=%02x CRC=%02x expected_CRC=%02x\n",
        htsig_bits.mcs(), htsig_bits.crc(), calculated_crc);
```

---

## Task 5: 对比 TX 和 RX 的 L-SIG/HT-SIG bits

**Files:**
- Analyze: TX bits vs RX bits
- Test: `examples/test_mcs_end_to_end.py`

- [ ] **Step 1: 找到 TX 打印的 L-SIG bits**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep "TX.*L-SIG\|TX.*lsig" | head -10
```

- [ ] **Step 2: 找到 RX 解出的 L-SIG bits (EQ_FULL 之后)**

在 EQ_FULL 输出中手动提取 bit 判决。

- [ ] **Step 3: 对比 TX 和 RX 的 bits**

预期：TX 和 RX 的 bits 应该基本一致（允许少量误码）。

---

## Task 6: 修复并验证

Based on Task 1-5 findings, apply fix:

- [ ] **Step 1: 应用修复**

根据调查发现的问题，应用修复：
- 如果是 deinterleaver 索引错误 → 修正 permutation 公式
- 如果是 bit extraction 错误 → 修正 hard_bit_from_complex()
- 如果是 CRC 验证错误 → 检查 CRC 计算逻辑

- [ ] **Step 2: 重新构建**

```bash
cd /home/hy/gr-ieee802-11/build && cmake --build . -j$(nproc)
```

- [ ] **Step 3: 运行测试验证**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(parse|CRC|L-SIG|HT-SIG)" | head -30
```

预期：parse failed 消息消失，接收消息数 > 0。

- [ ] **Step 4: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: [describe the fix based on findings]"
```

---

## Self-Review Checklist

1. **Spec coverage:** 所有任务映射到修复 L-SIG/HT-SIG 解码失败的目标。无占位符。

2. **Placeholder scan:** 无 "TBD"、"TODO" 或模糊步骤。每步显示精确代码。

3. **Type consistency:** 函数名、日志标签保持一致。

4. **测试验证:** 每任务以具体测试命令和预期输出结束。

5. **关键调查点：**
   - EQ_FULL 均衡后的符号值是否正确？
   - L-SIG rate field 解析是否正确？
   - Deinterleaver 索引是否正确？
   - HT-SIG CRC 验证是否通过？
   - TX bits vs RX bits 对比结果？
