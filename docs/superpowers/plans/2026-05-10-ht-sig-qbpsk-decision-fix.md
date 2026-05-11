# HT-SIG QPSK 判决修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 修复 HT-SIG CRC 失败问题。当前 HT-SIG 使用错误的判决轴（imag()）提取 bit，导致 Viterbi 输入错误，CRC 不匹配。

**Architecture:** IEEE 802.11n HT-SIG 使用标准 QPSK 调制（45° 偏移），每符号编码 2 个 bits。QPSK 判决应该用 `real() >= 0` 判决第一个 bit（MSB），`imag() >= 0` 判决第二个 bit（LSB）。当前代码只用 `imag() >= 0`，丢失了一半的编码信息。

**Tech Stack:** GNU Radio, IEEE 802.11n, C++, Python

---

## 当前状态 (2026-05-10 下午)

### 问题症状
- `computed_crc=0x41, rx_crc=0x8E`
- Viterbi 解码后有 12 个 bit 错误（在 48 个 info bits 中）
- L-SIG 解码成功 (rate=0x0D, enc=0)

### 根因分析

**已确认的修复：**
- 将 `decode_htsig_from_rotated` 中的判决从 `imag() >= 0` 改为 `real() >= 0`

**但 CRC 仍然失败！** 问题可能不在判决轴，而在于：

1. **TX/RX 交织/解交织不匹配**
   - TX interleave_bpsk 使用公式 `i = 3*(k%16) + k/16`
   - RX deinterleave_bpsk 使用公式 `j = 16*(k%3) + k/3`
   - 这两个公式可能不是正确的逆函数

2. **Viterbi 输入数据不匹配**
   - TX: 48 info bits → BCC encode → 96 coded bits → interleave → 96 bits
   - RX: 48 subcarriers × 1 bit/subcarrier = 48 bits (如果使用 BPSK)
   - 但 Viterbi 需要 96 bits 输入！

### 关键发现

**TX HT-SIG 调制分析：**
- gr-htsig 使用 `interleave_bpsk(encoded.data(), out_u, 48, 2)`
- 这意味着：48 info bits → BCC → 96 coded bits → interleave → 96 bits
- 96 bits 分成 2 个 HT-SIG 符号，每个 48 bits

**问题：每 HT-SIG 符号只有 48 bits，但 Viterbi 需要 96 bits！**

可能的解释：
- HT-SIG 使用 BPSK，每符号 1 bit → 48 bits
- 然后 BCC encode → 96 coded bits
- 但 RX 只有 48 个硬判决，无法给 Viterbi 提供 96 bits

这暗示：
- 要么 HT-SIG 使用 QPSK（每符号 2 bits）
- 要么 Viterbi 以不同方式处理

### 相关文件
- `lib/frame_equalizer_impl.cc` - `decode_htsig_from_rotated()` 函数
- `lib/sync_long.cc` - 帧检测
- `lib/ht_symbol_splitter_impl.cc` - 符号分割

---

## 任务 1: 验证交织器公式

**文件:**
- TX: `/home/hy/src/gr-htsig/lib/ht_sig_field_impl.cc`
- RX: `lib/frame_equalizer_impl.cc` - `deinterleave_bpsk_48()`

**Step 1: 检查 TX interleave_bpsk 公式**

```cpp
// TX interleave_bpsk:
for (int k = 0; k < N; k++) {
    const int i = a * (k % 16) + (k / 16);  // a = N/16 = 3
    out[k] = in[i] & 0x1;
}
```

**Step 2: 检查 RX deinterleave_bpsk_48 公式**

```cpp
// RX deinterleave_bpsk_48:
for (int k = 0; k < 48; k++) {
    const int j = 16 * (k % 3) + k / 3;
    out48[k] = in48[j] & 0x1;
}
```

**Step 3: 验证是否是逆函数**

对于 N=48，这两个公式不是正确的逆函数！

---

## 任务 2: 修复交织器/解交织器

如果任务 1 确认了问题，更新交织器/解交织器公式。

---

## 任务 3: 测试 HT-SIG CRC

**文件:**
- Test: `examples/test_loopback_noqt.py`

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio
cd /home/hy/gr-ieee802-11
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "RX_CRC|PARSE_HT_SIG"
```

预期：`computed_crc=rx_crc=0x41`

---

## 任务 4: 验证完整帧解析

**Step 1: 检查 L-SIG 和 HT-SIG 解码**

```bash
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG|HTSIG|HT-DATA|FCS"
```

预期:
- `LSIG_DECODE SUCCESS`
- HT-SIG CRC PASS
- HT-DATA 正确发出
- FCS PASS

---

## 任务 5: 清理调试输出

移除临时调试语句。

---

## 任务 6: 提交更改

---

## 调试命令总结

```bash
# 编译
cd /home/hy/gr-ieee802-11/build && make -j$(nproc)

# 激活 conda
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio

# 测试 HT-SIG
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "RX_CRC|PARSE_HT_SIG"

# 完整测试
LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib \
    timeout 60 python examples/test_loopback_noqt.py 2>&1 | grep -E "LSIG|HTSIG|HT-DATA|FCS"
```

## 成功标准

1. HT-SIG CRC `computed_crc == rx_crc == 0x41`
2. `mcs=0, len=96, bw40=0, agg=0, sgi=0, stbc=0, nltf=0`
3. L-SIG 解码继续正常工作
4. HT-DATA 正确发出
5. FCS PASS
