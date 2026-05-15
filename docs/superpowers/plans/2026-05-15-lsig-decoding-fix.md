# L-SIG Decoding Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 MCS 0-7 端到端完全通过，核心是修复 L-SIG 解码使 HT-SIG CRC 能正确验证。

**Architecture:** 逐段隔离 TX→RX 信号链路，在每个节点验证中间结果是否匹配。问题已定位在 RX EQ 输出就已经乱码（20/48 bits 错），需要找到 FFT 输出错误的根本原因。

**Tech Stack:** Python 验证模型 + C++ GRC 块 + GNU Radio 仿真

---

## 问题背景

**TX L-SIG 交织输出**（Python 验证）：
```
TX encoded (48 bits):     110110001001111111100101100100011111100011110111
TX interleaved (48 bits):  111111011101101010000010111001001111100101101111
```

**RX Viterbi 输入**（test_output.txt）：
```
RX VITERBI_IN:             010100110101111010001100110011001000111100111000
```

**完全不匹配！** Hamming 距离 20/48 = 42%。问题在 EQ 输出的 48 个硬判决 bits 就有 20 个错误。

---

## 文件映射

| 文件 | 作用 |
|------|------|
| `lib/frame_equalizer_impl.cc` | RX 信道估计、L-SIG/HT-SIG 解码 |
| `lib/ht_symbol_splitter_impl.cc` | CP 移除、符号分割、FFT 边界 |
| `lib/sync_long.cc` | 同步、LTF 模板 |
| `examples/wifi_phy_hier.py` | 主 PHY 层次块 |
| `lib/utils.cc` | TX 卷积编码、交织 |

---

## Task 1: Python 端到端 L-SIG 验证模型

**Files:**
- Create: `test_lsig_e2e_verification.py`

- [ ] **Step 1: 写 Python 验证脚本**

```python
#!/usr/bin/env python3
"""
End-to-end L-SIG verification: TX -> RX simulation -> decoded bits
"""
import numpy as np

# L-SIG original 24 bits (rate=0xD, len=45, parity=1)
TX_LSIG_24 = [1,1,0,1,0,1,0,1,1,0,1,0,0,0,0,0,0,1,0,0,0,0,0,0]

# Conv encoder (G0=0x5B=octal0133, G1=0x79=octal0171)
def ones_local(n):
    return bin(n).count('1')

def conv_encode_133_171(bits24):
    state = 0
    out = []
    for b in bits24:
        state = ((state << 1) & 0x7e) | b
        o0 = ones_local(state & 0x5B) % 2  # octal 0133 = 0x5B
        o1 = ones_local(state & 0x79) % 2  # octal 0171 = 0x79
        out.extend([o0, o1])
    return out

# Interleaver: k -> i = 3*(k%16) + k//16
def interleave_48(bits48):
    out = [0]*48
    for k in range(48):
        i = 3*(k%16) + k//16
        out[i] = bits48[k]
    return out

# Deinterleaver inverse mapping
DEINT_INV = [0,16,32,1,17,33,2,18,34,3,19,35,4,20,36,5,21,37,
             6,22,38,7,23,39,8,24,40,9,25,41,10,26,42,11,27,43,
             12,28,44,13,29,45,14,30,46,15,31,47]

def deinterleave_48(bits48):
    out = [0]*48
    for i in range(48):
        out[DEINT_INV[i]] = bits48[i]
    return out

# Run
tx_enc = conv_encode_133_171(TX_LSIG_24)
tx_int = interleave_48(tx_enc)
rx_deintl = deinterleave_48(tx_int)

print("TX L-SIG 24 bits:", ''.join(map(str, TX_LSIG_24)))
print("TX encoded 48 bits:", ''.join(map(str, tx_enc)))
print("TX interleaved:", ''.join(map(str, tx_int)))
print("RX deintl (should=tx_enc):", ''.join(map(str, rx_deintl)))
print("Match:", rx_deintl == tx_enc)
print()
print("Expected VITERBI_IN:", ''.join(map(str, tx_int)))
print("Actual test output VITERBI_IN: 010100110101111010001100110011001000111100111000")
print("Python TX int matches test VITERBI_IN? NO - problem is upstream of deinterleaver")
```

- [ ] **Step 2: 运行脚本验证 TX 模型**

Run: `python3 test_lsig_e2e_verification.py`

Expected output: TX interleaved = `111111011101101010000010111001001111100101101111`, deintl round-trip = TX encoded (验证交织器本身正确)

---

## Task 2: 在 C++ 添加 VITERBI 输入对比打印

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (around line 1465)

- [ ] **Step 1: 在 decode_lsig_direct_from_header52 中添加 TX 交织输出对比**

在 `[VITERBI_IN]` 打印之后添加（保持现有打印不变，追加对比）：

```cpp
// Hardcoded expected TX interleaved bits for MCS0 L-SIG
// TX L-SIG 24 bits: rate=0x0D len=45 parity=1
// TX encoded: 110110001001111111100101100100011111100011110111
// TX interleaved: 111111011101101010000010111001001111100101101111
static const uint8_t expected_tx_int_lsig[48] = {
    1,1,1,1,1,1,0,1,1,1,0,1,1,0,1,0,
    1,0,1,0,0,0,0,0,1,0,1,1,1,0,0,1,
    0,0,1,1,1,1,1,0,0,1,0,1,1,0,1,1,1,1,1,1,1
};

fprintf(stderr, "[VITERBI_IN] Expected TX interleaved L-SIG:\n");
for (int i = 0; i < 48; i++) {
    fprintf(stderr, "%d", expected_tx_int_lsig[i]);
    if ((i+1) % 16 == 0) fprintf(stderr, "\n");
}
fprintf(stderr, "[VITERBI_IN] Actual RX deintl48:\n");
for (int i = 0; i < 48; i++) {
    fprintf(stderr, "%d", deintl48[i]);
    if ((i+1) % 16 == 0) fprintf(stderr, "\n");
}
int diff = 0;
for (int i = 0; i < 48; i++) {
    if (deintl48[i] != expected_tx_int_lsig[i]) diff++;
}
fprintf(stderr, "[VITERBI_IN] Hamming diff: %d/48\n", diff);
fflush(stderr);
```

- [ ] **Step 2: 重新编译**

Run: `cd /home/hy/gr-ieee802-11/build && rm -f lib/libgnuradio-ieee802_11.so.* && make -j4 2>&1 | tail -20`

- [ ] **Step 3: 运行测试并检查 diff**

Run: `cd /home/hy/gr-ieee802-11 && python3 examples/mcs_test.py 2>&1 | grep "VITERBI_IN.*diff"`

Expected: `Hamming diff: 48/48`（全部不同，说明问题在解交织之前）

---

## Task 3: 在 C++ 添加 EQ 前原始 subcarrier 对比打印

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (around line 892)

- [ ] **Step 1: 在 equalize_header52_to_eq48_and_bits 开头添加 TX L-SIG 符号参考打印**

在 `equalize_header52_to_eq48_and_bits` 函数开头添加：

```cpp
// Hardcoded TX L-SIG interleaved bits -> BPSK symbols
// tx_int = 111111011101101010000010111001001111100101101111
// BPSK: bit 1 -> +1, bit 0 -> -1
static const gr_complex tx_lsig_bpsk[48] = {
    // bit 1->+1, bit 0->-1 for positions 0-15
    +1,+1,+1,+1,+1,+1,-1,+1,+1,+1,-1,+1,+1,-1,+1,-1,
    // positions 16-31
    +1,-1,+1,+1,+1,+1,+1,-1,+1,-1,+1,-1,+1,-1,-1,-1,
    // positions 32-47
    +1,-1,+1,-1,+1,-1,+1,+1,+1,+1,-1,+1,+1,-1,-1,+1,
    +1,+1,+1,+1,-1,+1,+1,+1,+1,+1,-1,-1
};

fprintf(stderr, "[EQ_REF] TX L-SIG BPSK symbols (first 8): ");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.1f%+.1fi ", tx_lsig_bpsk[i].real(), tx_lsig_bpsk[i].imag());
}
fprintf(stderr, "\n");
fprintf(stderr, "[EQ_REF] RX L-SIG raw FFT (first 8): ");
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.4f%+.4fi ", rx52[i].real(), rx52[i].imag());
}
fprintf(stderr, "\n");
fflush(stderr);
```

- [ ] **Step 2: 重新编译并运行**

Expected: RX raw FFT 与 TX BPSK 完全不同（magnitudes 3-15 vs 1.0），确认问题在 FFT 之前

---

## Task 4: 检查 SPLITTER 输出的符号顺序

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (around line 300)

- [ ] **Step 1: 添加 SPLITTER 输出符号类型和内容的打印**

在 SPLITTER 输出 HT-SIG 符号后添加（注意只打印一次避免刷屏）：

```cpp
static int splitter_debug_count = 0;
if (splitter_debug_count < 2) {
    fprintf(stderr, "[SPLITTER_OUT] output_item=%lld noutput_items=%d symbol_type=%s\n",
            (long long)nitems_written(0), noutput_items,
            symbol_type == SYMBOL_L_STF ? "L-STF" :
            symbol_type == SYMBOL_L_LTF ? "L-LTF" :
            symbol_type == SYMBOL_L_SIG ? "L-SIG" :
            symbol_type == SYMBOL_HT_SIG ? "HT-SIG" :
            symbol_type == SYMBOL_HT_HTF ? "HT-STF" :
            symbol_type == SYMBOL_DATA ? "DATA" : "UNKNOWN");
    fprintf(stderr, "[SPLITTER_OUT] rel_idx=%d out_rel_idx=%d first_sample=%.4f%+.4fi\n",
            rel_idx, out_rel_idx,
            out[0].real(), out[0].imag());
    splitter_debug_count++;
    fflush(stderr);
}
```

- [ ] **Step 2: 重新编译并运行**

Run: `cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10 && cd .. && python3 examples/mcs_test.py 2>&1 | grep "SPLITTER_OUT" | head -10`

Expected: SPLITTER 输出的符号顺序为 L-STF → L-STF → L-LTF → L-LTF → L-SIG → HT-SIG → ...

---

## Task 5: 在 SPLITTER 添加 FFT 原始 64-bin 输出打印（验证 FFT 输入正确性）

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (around line 370 where SYMBOL_L_SIG case is)

- [ ] **Step 1: 在 L-SIG case 中添加 FFT 原始数据打印**

在 L-SIG 输出路径中：

```cpp
case SYMBOL_L_SIG: {
    // Add after getting CP-removed samples
    fprintf(stderr, "[SPLITTER_LSIG] CP-removed samples[0:4]: ");
    for (int i = 0; i < 4; i++) {
        fprintf(stderr, "%.4f%+.4fi ", in[i].real(), in[i].imag());
    }
    fprintf(stderr, "\n");
    fflush(stderr);
    // ... rest of existing code
}
```

- [ ] **Step 2: 重新编译并运行**

Run: `cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -5 && cd .. && python3 examples/mcs_test.py 2>&1 | grep "SPLITTER_LSIG" | head -5`

Expected: CP 移除后的样本应该是正弦波的 4 个周期（L-SIG 是 0.8μs 数据 + 0.8μs CP）

---

## Task 6: 对比 TX 星座图和 RX FFT 原始输出

**Files:**
- Modify: `wifi_phy_hier.py` (TX 部分) 和 `lib/frame_equalizer_impl.cc` (RX 部分)

- [ ] **Step 1: 在 TX 端添加 L-SIG BPSK 星座图打印（如果还没有）**

查找 TX 端是否有 L-SIG 星座图打印。如果有，应该有类似 `[TX][LSIG_BPSK]` 的输出。

Run: `grep -n "LSIG\|lsig" /home/hy/gr-ieee802-11/examples/wifi_phy_hier.py | head -20`

如果没有，在 `ofdm_mapper` 之后添加：

```python
# After mapper for L-SIG
if symbol_type == "L-SIG":
    print(f"[TX_LSIG_COSTELLA] first_4_subcarriers: {out_sym[0]}, {out_sym[1]}, {out_sym[2]}, {out_sym[3]}")
```

- [ ] **Step 2: 在 RX FFT 输出后添加原始数据打印**

在 `lib/ht_symbol_splitter_impl.cc` 的 FFT 调用之后添加（需要找到 FFT 调用的位置）。

---

## Task 7: 端到端验证并修复

根据 Task 2-6 收集的数据，确定问题根源并修复：

**可能的问题场景：**

### 场景 A：SPLITTER 输出的 L-SIG 符号内容正确，但 FFT 后数据错乱
→ 检查 FFT 缩放因子、shift 参数

### 场景 B：SPLITTER 输出的符号类型错误（把 HT-SIG 当成 L-SIG）
→ 检查 SPLITTER 的 symbol_type 判断逻辑

### 场景 C：SPLITTER 时序错误，FFT window 切到了错误位置
→ 检查 SPLITTER 的 at_boundary 和 should_buffer 逻辑

### 场景 D：FFT 输出正确，但 kHeader48Bin 映射错误
→ 检查 `kHeader48Bin` 数组与 TX 的载波顺序是否一致

---

## Task 8: 最终端到端测试

**Files:**
- Test: `examples/mcs_test.py`

- [ ] **Step 1: 运行完整 MCS 测试**

Run: `cd /home/hy/gr-ieee802-11 && python3 examples/mcs_test.py 2>&1 | tee /tmp/final_test.txt`

Expected: MCS 0-7 all show `received_messages >= 1`

- [ ] **Step 2: 如果所有 MCS 通过，清理调试打印**

移除所有为调试添加的打印语句，保留关键的正常运行输出。

- [ ] **Step 3: 提交更改**

```bash
git add lib/frame_equalizer_impl.cc lib/ht_symbol_splitter_impl.cc
git commit -m "fix: debug and fix L-SIG decoding chain

- Add TX/RX interleaver verification prints
- Verify SPLITTER symbol ordering and timing
- Fix root cause of EQ output corruption based on data collected in Tasks 2-6
"
```

---

## 调试数据收集 Checklist

运行 `python3 examples/mcs_test.py 2>&1 | tee /tmp/lsig_debug.txt` 后检查：

- [ ] `[VITERBI_IN]` Hamming diff 是多少？（预期 48/48 = 完全不匹配）
- [ ] `[LSIG_RAW]` 的 RX magnitude 是多少？（预期 ~1.0，实际 3-15 = 错误）
- [ ] `[SPLITTER_OUT]` 的符号顺序是否正确？
- [ ] TX `wifi_phy_hier.py` 的 L-SIG BPSK 星座图是什么？

**关键判断依据：**
- 如果 `[LSIG_RAW]` magnitude ~1.0 → FFT 输出正确，问题在 channel estimation 或 equalization
- 如果 `[LSIG_RAW]` magnitude >> 1.0 → FFT 输出本身就有问题，问题在 SPLITTER 或更上游
- 如果 SPLITTER 符号顺序错乱 → SPLITTER 的符号判断逻辑有 bug
