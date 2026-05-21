# HT-SIG 真正解码计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 HT-SIG 解码，使 VITERBI_IN 匹配 TX encoded bits，让帧真正到达 decode_mac 进行 FCS 验证

**Architecture:** 问题在于 HT-SIG 的 demapper/deinterleaver 输出与 TX encoded bits 完全不匹配。需要逐级验证 TX 和 RX 的数据流，找出第一个分歧点。

**Tech Stack:** GNU Radio, C++, IEEE 802.11-2016

---

## 当前状态

### 已确认的事实

| 指标 | TX 值 | RX 值 |
|------|-------|-------|
| ht_bits[0:24] | `000000000000011000000000` | (N/A - TX only) |
| encoded[0:48] | `000000000000000000000000001101101100101011000000` | (TX output) |
| VITERBI_IN[0:24] | (TX reference) | `100100000001001000111000` |

**TX ht_bits 分析:**
- Bits 0-6 (MCS): `0000000` = MCS 0
- Bit 7 (CBW): `0` = 20 MHz
- Bits 8-23 (Length): `0000000000000110` = 长度字段
- Bits 24-33: `0000000000` = reserved

**问题:** TX encoded 和 RX VITERBI_IN 完全不匹配

### 关键代码位置

| 位置 | 功能 |
|------|------|
| `lib/signal_field_impl.cc:162-239` | TX HT-SIG 生成 |
| `lib/frame_equalizer_impl.cc:1188-1194` | apply_htsig_rotation |
| `lib/frame_equalizer_impl.cc:1523-1539` | HT-SIG0 demapper |
| `lib/frame_equalizer_impl.cc:1571-1582` | HT-SIG deinterleaver |

---

## Task 1: 验证 TX 端 ht_bits 生成

**Files:**
- Debug: `lib/signal_field_impl.cc`
- Test: 运行 test_constellation_real.py

**问题分析:**
TX ht_bits[0:24] = `000000000000011000000000` 看起来可疑：
- Length 字段应该是 38 bytes = 0x26
- LSB first 应该是 `0110 0100`
- 但实际是 `0000000000000110` = 0x6000

这表明 TX 端的 ht_len 值可能不是预期的。

**Steps:**

- [ ] **Step 1: 添加 TX ht_len 调试**

在 `lib/signal_field_impl.cc` 的 `generate_ht_sig_header` 函数中（约第 178 行后）添加：

```cpp
fprintf(stderr, "[TX_HT_SIG] ht_len=%d (0x%x), psdu_size=%d\n", ht_len, ht_len, data_frame.psdu_size);
```

- [ ] **Step 2: 重新编译**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/signal_field_impl.cc.o && make -j4
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -E "(TX_HT_SIG.*ht_len|ht_bits\[0:24\])" | head -5
```

预期：看到 ht_len 的实际值

---

## Task 2: 验证 TX convolutional encoding

**Files:**
- Debug: `lib/signal_field_impl.cc`
- Test: 运行测试

**问题分析:**
即使 ht_bits 正确，convolutional encoding 可能有问题。

**Steps:**

- [ ] **Step 1: 验证 convolutional encoding**

在 `generate_ht_sig_header` 中，convolutional_encoding 后添加调试：
```cpp
fprintf(stderr, "[TX_HT_SIG] After encoding, encoded[0:24]=%s\n", bits_to_bin_string(encoded, 24).c_str());
```

或者直接查看 TX 输出的 encoded 值是否与预期一致。

**预期 TX encoded (MCS=0, length=38):**
```
HT-SIG content:
- MCS=0: bits 0-6 = 0000000
- CBW=20MHz: bit 7 = 0
- Length=38: bits 8-23 = 0010 0110 LSB first = 0110 0100 0000 0000
- Reserved: bits 24-33 = 0000000000
- CRC: computed
- Tail: bits 42-47 = 000000

After encoding, 96 coded bits.
```

---

## Task 3: 验证 RX demapper 输出

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`
- Test: 运行测试

**问题分析:**
VITERBI_IN 完全不对，说明问题在 demapper 或更早的阶段。

**Steps:**

- [ ] **Step 1: 检查 rx52_a 在 demapper 之前的值**

在 `decode_htsig_from_rotated` 函数开头（约第 1490 行），在 equalization 之前添加：

```cpp
fprintf(stderr, "[RX_RAW] rx52_a[0:5]=");
for (int i = 0; i < 5; i++) {
    fprintf(stderr, "%.3f+%.3fi ", rx52_a[i].real(), rx52_a[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: 检查 apply_htsig_rotation 后的值**

在 `decode_htsig_from_rotated` 中，equalization 之后添加：

```cpp
fprintf(stderr, "[AFTER_ROT] rot_htsig0[0:5]=");
for (int i = 0; i < 5; i++) {
    fprintf(stderr, "%.3f+%.3fi ", rot_htsig0[i].real(), rot_htsig0[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 3: 检查 equalization 后的值**

在 demapper 之前（约第 1523 行）添加：

```cpp
fprintf(stderr, "[AFTER_EQ] eq[0:5]=");
for (int i = 0; i < 5; i++) {
    fprintf(stderr, "%.3f+%.3fi ", eq.real(), eq.imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 4: 重新编译并运行**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -E "(RX_RAW|AFTER_ROT|AFTER_EQ)" | head -10
```

---

## Task 4: 对比 TX 和 RX 的数据流

**目标:** 找出第一个分歧点

**验证路径:**
1. TX: ht_bits → encoded → interleaved → TX output
2. RX: FFT output → extract 52 SC → apply_htsig_rotation → equalize → demap → deinterleave → VITERBI_IN

**需要验证:**
- TX interleaved[0:24] vs RX deinterl48[0:24]
- TX encoded[0:24] vs RX enc96[0:24]

---

## Task 5: 修复发现的问题

根据 Task 1-4 的分析结果，确定并实施修复。

**可能的修复点:**
1. TX: ht_len 计算错误 → 修复 signal_field_impl.cc
2. RX: rotation 方向错误 → 修复 apply_htsig_rotation
3. RX: deinterleaver 公式错误 → 修复 decode_htsig_from_rotated
4. RX: H hdr 使用不当 → 使用 unity H

---

## Task 6: 验证 decode_mac FCS

**Files:**
- Test: 修复后运行测试

- [ ] **Step 1: 运行测试，检查 decode_mac FCS 输出**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -iE "(decode_mac.*FCS|FCS.*OK|FCS.*error)"
```

预期：看到 `[decode_mac] FCS OK`

---

## Task 7: 提交最终修复

- [ ] **Step 1: 清理调试输出**

移除所有临时调试 fprintf 语句。

- [ ] **Step 2: 提交**

```bash
git add lib/frame_equalizer_impl.cc lib/signal_field_impl.cc
git commit -m "fix: HT-SIG decoding - 修复 TX/RX 数据流不匹配问题"
```

---

## 关键文件路径

| 文件 | 说明 |
|------|------|
| `lib/signal_field_impl.cc` | TX HT-SIG 生成 |
| `lib/frame_equalizer_impl.cc` | RX HT-SIG 解码 |
| `examples/test_constellation_real.py` | 测试脚本 |

---

## 预期结果

- TX ht_bits 生成正确
- TX encoded 与 RX VITERBI_IN 匹配
- HT-SIG 解码成功 (d_have_ht_header=1, mcs=0)
- decode_mac FCS 输出 `FCS OK`
