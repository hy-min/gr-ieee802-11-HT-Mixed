# L-SIG/HT-SIG 解码修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 L-SIG/HT-SIG 解码，使 RX 能够正确解析 HT-Mixed 模式的帧头

**Architecture:** 问题可能在于 FFT bin 映射、CPE 校正或 Viterbi 解码器。需要对比 TX 端生成的和 RX 端接收的完整数据流。

**Tech Stack:** GNU Radio, C++, IEEE 802.11-2016, Python

---

## 当前状态

### TX 端输出（已确认）

```
[TX_LSIG] signal_header[0:24]=110100110011000001000000
[TX_LSIG] encoded[0:48]=110110001010100111001111000000101000100011110111
[TX_LSIG] interleaved[0:48]=111110000100111010010010101001101001100001011101
[TX_HT_SIG] ht_bits[0:48] = 000000000000011000000000000000000010000010000000
[TX_HT_SIG] encoded[0:96] = 000000000000000000000000001101101100101011000000000000000000000000001110001111010010001111011100
```

### RX 端输出（当前状态）

```
[LSIG_DECODE] decoded24[0:24]=010010000000001110000000  (完全错误)
[LSIG_DECODE] Parity check failed! parity_sum=1, parity_bit=0
[PARSE_HT_SIG] CRC mismatch
```

### 关键文件

| 文件 | 功能 |
|------|------|
| `lib/frame_equalizer_impl.cc` | 帧均衡器，包含 L-SIG/HT-SIG 解码 |
| `lib/viterbi_decoder/` | Viterbi 译码器 |
| `examples/wifi_phy_hier.py` | RX FFT 配置 (shift=False) |
| `examples/wifi_constellation.py` | TX 生成输出参考 |

---

## Task 1: 验证 TX 生成与预期对比

**Files:**
- Test: `examples/wifi_constellation.py` - 已有完整 TX 输出

**Steps:**

- [ ] **Step 1: 运行 wifi_constellation.py 获取 TX 参考**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 10 python3 ./examples/wifi_constellation.py 2>&1 | grep -E "TX_LSIG|TX_HT_SIG" | head -10
```

预期输出：
```
[TX_LSIG] signal_header[0:24]=110100110011000001000000
[TX_LSIG] encoded[0:48]=110110001010100111001111000000101000100011110111
[TX_LSIG] interleaved[0:48]=111110000100111010010010101001101001100001011101
[TX_HT_SIG] ht_bits[0:48] = 000000000000011000000000000000000010000010000000
```

---

## Task 2: 在 RX 端添加完整数据流诊断

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - 添加 TX 参考打印和 RX 输入对比

**Steps:**

- [ ] **Step 1: 在 decode_lsig_direct_from_header52 添加 TX 参考打印**

在 `lib/frame_equalizer_impl.cc` 的 `decode_lsig_direct_from_header52` 函数中，在 Viterbi 解码前添加：

```cpp
// DEBUG: 打印 TX 预期值（已知正确的 L-SIG 编码比特）
// TX L-SIG interleaved bits (从 wifi_constellation.py 输出):
// interleaved[0:48]=111110000100111010010010101001101001100001011101
static const uint8_t kTxLsigInterleaved[48] = {
    1,1,1,1,1,0,0,0,1,0,0,1,1,1,0,1,0,0,1,0,0,1,0,1,0,1,0,0,1,1,0,1,0,0,1,0,0,0,0,1,0,1,1,1,0,1
};
fprintf(stderr, "[LSIG_COMPARE] TX expected: ");
for(int i=0;i<48;i++) fprintf(stderr,"%d", kTxLsigInterleaved[i]);
fprintf(stderr, "\n");
fprintf(stderr, "[LSIG_COMPARE] RX deintl48: ");
for(int i=0;i<48;i++) fprintf(stderr,"%d", deintl48[i]);
fprintf(stderr, "\n");
fflush(stderr);
```

- [ ] **Step 2: 重新编译**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -3
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 15 python3 ./examples/wifi_constellation.py 2>&1 | grep -E "LSIG_COMPARE" | head -5
```

预期：TX 和 RX 的 48 bits 应该完全相同（或只有少数位不同）

---

## Task 3: 验证 Deinterleaver 公式

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`

**问题:** 如果 TX interleaved bits 和 RX deintl48 不同，需要确定是哪个环节出错。

**Steps:**

- [ ] **Step 1: 添加 interleave/deinterleave 验证**

在 `decode_lsig_direct_from_header52` 中添加：

```cpp
// 验证 deinterleave 是否正确
// TX interleave 公式: k = 3*(j mod 16) + floor(j/16)
// RX deinterleave 公式: j = 16*(k mod 3) + floor(k/3)
fprintf(stderr, "[DEINT_VERIFY] Testing deinterleave round-trip:\n");
for (int j = 0; j < 48; j++) {
    int k = 3 * (j % 16) + j / 16;  // TX interleave
    int j_back = 16 * (k % 3) + k / 3;  // RX deinterleave
    if (j != j_back) {
        fprintf(stderr, "  MISMATCH j=%d -> k=%d -> j_back=%d\n", j, k, j_back);
    }
}
fprintf(stderr, "[DEINT_VERIFY] Round-trip test done\n");
fflush(stderr);
```

- [ ] **Step 2: 重新编译并测试**

---

## Task 4: 验证 Viterbi 解码器

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` 或 `lib/viterbi_decoder/`

**问题:** 如果 deinterleave 正确但 Viterbi 输出错误，需要检查卷积码解码器。

**Steps:**

- [ ] **Step 1: 手动计算 TX L-SIG 卷积编码验证**

已知：
- L-SIG bits (24 bits): `110100110011000001000000`
- Convolutional encoder: g0=0133, g1=0171
- Puncturing: none (BPSK 1/2)

```python
# Python 验证脚本
def ones8(n):
    return bin(n & 0xFF).count('1')

def convolutional_encode(bits):
    state = 0
    encoded = []
    for b in bits:
        state = ((state << 1) & 0x7E) | b
        o0 = ones8(state & 0o133) % 2
        o1 = ones8(state & 0o171) % 2
        encoded.extend([o0, o1])
    return encoded

lsig_bits = [int(x) for x in '110100110011000001000000']
encoded = convolutional_encode(lsig_bits)
print(f"Encoded: {''.join(map(str, encoded[:48]))}")
```

- [ ] **Step 2: 对比 Viterbi 输出的 48 bits 与 TX encoded bits**

---

## Task 5: 验证 Demapper 输出（eqbits48）

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`

**问题:** 如果 Viterbi 输出正确但最终 decoded24 错误，问题在 parity check 或 bit extraction。

**Steps:**

- [ ] **Step 1: 打印 demapper 输出的 48 bits 与 TX interleaved 对比**

```cpp
fprintf(stderr, "[DEMAP_COMPARE] TX interleaved:  ");
for(int i=0;i<48;i++) fprintf(stderr,"%d", kTxLsigInterleaved[i]);
fprintf(stderr, "\n");
fprintf(stderr, "[DEMAP_COMPARE] RX eqbits48:     ");
for(int i=0;i<48;i++) fprintf(stderr,"%d", eqbits48[i]);
fprintf(stderr, "\n");

// 统计不同位数
int diff_count = 0;
for(int i=0;i<48;i++) if(eqbits48[i] != kTxLsigInterleaved[i]) diff_count++;
fprintf(stderr, "[DEMAP_COMPARE] Differences: %d/48\n", diff_count);
```

---

## Task 6: 验证均衡器和 CPE 校正

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`

**Steps:**

- [ ] **Step 1: 打印均衡前后符号**

在 `equalize_header52_to_eq48_and_bits` 中，打印前 5 个子载波的：
- H52[i] (信道估计)
- rx52[i] (接收符号)
- eq (均衡后)
- 预期 TX 符号 (BPSK: ±1)

```cpp
// 预期 L-SIG TX 符号 (从 kHeader48Sc 顺序的 L-LTF 序列)
static const gr_complex kExpectedLsigTx[48] = {
    // 需要根据 L-SIG bits 计算
};
fprintf(stderr, "[EQ_DEBUG] i=%d sc=%d H=%.3f+%.3fi rx=%.3f+%.3fi eq=%.3f+%.3fi bit=%d\n",
        i, kHeader48Sc[i], H52[i].real(), H52[i].imag(),
        rx52[i].real(), rx52[i].imag(), eq.real(), eq.imag(),
        hard_bit_from_complex(eq));
```

---

## Task 7: 修复发现的问题

根据 Task 2-6 的结果，确定并修复问题。

**可能的修复:**

1. **FFT shift 不匹配** → 修改 `sc_to_fft_bin` 或 `wifi_phy_hier.py`
2. **CPE 估计错误** → 检查 `estimate_header_cpe_rad`
3. **Deinterleave 公式错误** → 修正 `deinterleave_bpsk_48`
4. **Viterbi 多项式不匹配** → 检查 `ones8_local` vs `ones`

---

## Task 8: 验证修复

**Files:**
- Test: `examples/wifi_constellation.py`
- Test: `examples/verify_ht_data_fix.py`

**Steps:**

- [ ] **Step 1: 运行 wifi_constellation.py 验证 L-SIG 解码**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 15 python3 ./examples/wifi_constellation.py 2>&1 | grep -E "(LSIG_DECODE|LSIG_PARSE|d_have_ht_header)" | head -10
```

预期：
- `LSIG_PARSE` 返回 TRUE
- `d_have_ht_header=1`

- [ ] **Step 2: 运行 verify_ht_data_fix.py 验证 HT-DATA 解码**

```bash
source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 25 python3 ./examples/verify_ht_data_fix.py 2>&1 | grep -E "(Rx PDU|PARSE_HT_SIG|FCS)" | head -10
```

预期：
- `PARSE_HT_SIG` CRC 匹配
- `Rx PDU` 出现（数据包被解码）

---

## Task 9: 清理调试并提交

**Steps:**

- [ ] **Step 1: 移除所有调试 fprintf**

- [ ] **Step 2: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: 修复 L-SIG/HT-SIG 解码"
```

---

## 关键常量参考

### TX L-SIG 生成（从 wifi_constellation.py）
```
signal_header[0:24] = 110100110011000001000000
  rate = 0x0D (1101) = 6 Mbps
  length = 96 bytes (0b000000000100)
  parity = 0 (even)
  tail = 000000

encoded[0:48] = 110110001010100111001111000000101000100011110111
interleaved[0:48] = 111110000100111010010010101001101001100001011101
```

### Viterbi 多项式（IEEE 802.11）
- g0 = 0133 (八进制) = 0x5B = 0b1011011
- g1 = 0171 (八进制) = 0x79 = 0b1111001

---

## 预期结果

- L-SIG parity check 通过
- HT-SIG CRC 匹配
- d_have_ht_header = 1
- HT-DATA 正确解码
- FCS 通过
