# L-SIG/HT-SIG 解码修复计划 v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 修复 L-SIG/HT-SIG 解码，使 RX 能够正确解析 HT-Mixed 模式的帧头

**Architecture:** 需要深入调试 L-LTF 信道估计和 CPE 校正

---

## 当前状态

### 已修复

1. **TX FFT→IFFT 错误**: wifi_phy_hier.py 中 TX 链使用 `ifft=False` 而非 `ifft=True`
   - 修复后 L-SIG 解析成功 ✅

### 待修复

1. **HT-LTF 信道估计错误**: H magnitude 约为 20.6 而不是 1.0
2. **HT-SIG CRC 失败**: TX encoded 全 0，但 RX deinterleaved 非 0

### 诊断数据

**TX L-LTF (修复后):**
- L-LTF magnitude: ~0.94 (正确)

**HT-LTF 信道估计:**
```
[HT-LTF] H[0] = -19.890-5.530i (magnitude ~20.6)
[HT-LTF] H[1] = 2.123+8.456i (magnitude ~8.7)
```

**HT-SIG 解码:**
- TX encoded[0:24] = `000000000000000000000000` (全0)
- RX VITERBI_IN enc96[0:24] = `000011000101001011001111` (非0)

### 根因分析

1. HT-LTF 信道估计产生过大增益 (H ~20.6)
2. 这导致 HT-SIG 均衡失败
3. TX encoded 是全 0，但 RX 收到的不是全 0

### 可能原因

1. **HT-LTF 参考值错误**: kHtLtfDataRef 或 kHtLtfPilotSign 使用错误
2. **FFT shift 配置问题**: HT-LTF 的 FFT shift 设置可能不正确
3. **HT-LTF 生成问题**: TX 端 HT-LTF 生成可能有误

---

## Task 1: 验证 TX L-LTF 生成

**Files:**
- Debug: `examples/wifi_constellation.py` - TX 输出

**Steps:**

- [ ] **Step 1: 打印 TX L-LTF 的星座图值**

在 `examples/wifi_constellation.py` 中添加调试，打印 L-LTF 符号的实部虚部。

预期：L-LTF 数据子载波应该是 ±1 (实数)

---

## Task 2: 验证 RX FFT 输出

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - 打印 RX FFT 原始输出

**Steps:**

- [ ] **Step 1: 在 general_work 中打印 sym64 的前几个值**

```cpp
fprintf(stderr, "[FFT_RAW] sym64[0:4]=%.3f+%.3fi %.3f+%.3fi %.3f+%.3fi %.3f+%.3fi\n",
        sym64[0].real(), sym64[0].imag(),
        sym64[1].real(), sym64[1].imag(),
        sym64[2].real(), sym64[2].imag(),
        sym64[3].real(), sym64[3].imag());
```

预期：在 loopback 情况下，sym64 应该与 TX L-LTF 相同

---

## Task 3: 对比 TX 和 RX 的 L-LTF

**Files:**
- Compare: TX `examples/wifi_constellation.py` 输出 vs RX `lib/frame_equalizer_impl.cc` 调试

**Steps:**

- [ ] **Step 1: 记录 TX L-LTF 的参考值**

```python
# 在 wifi_constellation.py 中
print("[TX_LLTF] First 10 L-LTF symbols (real,imag):")
for i in range(10):
    print(f"  i={i}: {ltf_symbols[i].real():.3f}+{ltf_symbols[i].imag():.3f}i")
```

- [ ] **Step 2: 对比 RX 收到的 L-LTF 值**

---

## Task 4: 修复发现的问题

根据 Task 1-3 的结果确定问题并修复。

**可能的修复:**
1. **FFT 增益问题** → 调整 FFT 归一化
2. **L-LTF 生成错误** → 修复 TX 端 L-LTF 生成
3. **CPE 估计错误** → 使用正确的 pilot 参考值

---

## Task 5: 验证修复

**Files:**
- Test: `examples/wifi_constellation.py`
- Test: `examples/verify_ht_data_fix.py`

**Steps:**

- [ ] **Step 1: 运行 wifi_constellation.py**

```bash
python3 ./examples/wifi_constellation.py 2>&1 | grep -E "(LSIG_DECODE|LSIG_PARSE|d_have_ht)"
```

预期：
- `LSIG_PARSE` 返回 TRUE
- `d_have_ht_header=1`

- [ ] **Step 2: 运行 verify_ht_data_fix.py**

```bash
python3 ./examples/verify_ht_data_fix.py 2>&1 | grep -E "(Rx PDU|PARSE_HT_SIG|FCS)"
```

预期：
- `PARSE_HT_SIG` CRC 匹配
- `Rx PDU` 出现
- `FCS` PASS

---

## Task 6: 验证 TX HT-LTF 生成

**Files:**
- Debug: `examples/wifi_constellation.py` - TX HT-LTF 输出

**Steps:**

- [ ] **Step 1: 打印 TX HT-LTF 的星座图值**

在 `examples/wifi_constellation.py` 中添加调试，打印 HT-LTF 符号的实部虚部。

预期：HT-LTF 数据子载波应该是 BPSK ±1 (实数)

---

## Task 7: 验证 RX HT-LTF FFT 输出

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - 打印 RX HT-LTF FFT 输出

**Steps:**

- [ ] **Step 1: 在 general_work 中打印 HT-LTF 的 sym64 值**

在 `estimate_header_channel_from_lltf52` 或相关位置打印 HT-LTF 的原始 FFT 输出。

预期：在 loopback 情况下，HT-LTF FFT 输出应该与 TX HT-LTF 相同

---

## Task 8: 检查 HT-LTF 参考值

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - 检查 kHtLtfDataRef 和 kHtLtfPilotSign

**Steps:**

- [ ] **Step 1: 检查 HT-LTF 参考值定义**

```cpp
// HT-LTF reference values - 应该是 ±1 (BPSK)
static constexpr gr_complex kHtLtfDataRef[52] = { ... };

// HT-LTF pilot signs - 应该是 {1, -1, 1, 1}
static constexpr int kHtLtfPilotSign[4] = { 1, -1, 1, 1 };
```

- [ ] **Step 2: 验证 HT-LTF 参考值是否与 TX 一致**

---

## Task 9: 修复 HT-LTF 信道估计

**Files:**
- Debug: `lib/frame_equalizer_impl.cc` - HT-LTF 信道估计

**Steps:**

- [ ] **Step 1: 找到并修复 HT-LTF 信道估计问题**

根据 Task 6-8 的结果修复 HT-LTF 信道估计。

可能的修复:
1. 修正 kHtLtfDataRef 值
2. 修正 HT-LTF pilot signs
3. 修正 FFT shift 配置
4. 修正 HT-LTF 生成

---

## Task 10: 验证 HT-SIG/HT-DATA 解码

**Files:**
- Test: `examples/wifi_constellation.py`
- Test: `examples/verify_ht_data_fix.py`

**Steps:**

- [ ] **Step 1: 运行 wifi_constellation.py**

```bash
python3 ./examples/wifi_constellation.py 2>&1 | grep -E "(HT_SIG|d_have_ht_header)"
```

预期：
- `HT_SIG` CRC 匹配
- `d_have_ht_header=1`

- [ ] **Step 2: 运行 verify_ht_data_fix.py**

```bash
python3 ./examples/verify_ht_data_fix.py 2>&1 | grep -E "(Rx PDU|HT_DATA|FCS|PASS)"
```

预期：
- `Rx PDU` 出现
- `FCS` PASS

---

## 关键常量参考

### kLltf48TX (L-LTF 数据子载波 TX 参考值)
```cpp
static constexpr gr_complex kLltf48TX[48] = {
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // ... 48 values total
};
```

### kLltfPilotTX (L-LTF pilot 子载波 TX 参考值)
```cpp
static const gr_complex kLltfPilotTX[4] = {
    gr_complex(-0.6173f, -0.1253f),  // sc -21
    gr_complex( 0.3401f,  0.9423f),  // sc  -7
    gr_complex( 0.3401f, -0.9423f),  // sc  +7
    gr_complex(-0.6173f,  0.1253f)   // sc +21
};
```

### kHeaderPilotBase (SIGNAL/HT-SIG pilot 符号)
```cpp
static constexpr int kHeaderPilotBase[4] = { 1, 1, 1, -1 };
```

### kHtLtfDataRef (HT-LTF 数据子载波参考值)
```cpp
static constexpr gr_complex kHtLtfDataRef[52] = {
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // ... 52 values total
};
```

### kHtLtfPilotSign (HT-LTF pilot 符号)
```cpp
static constexpr int kHtLtfPilotSign[4] = { 1, -1, 1, 1 };
```

### kPilot4Sc (Pilot 子载波位置)
```cpp
static constexpr int kPilot4Sc[4] = { -21, -7, 7, 21 };
```

### kTxOrder52 (HT 模式 52 子载波顺序)
```cpp
static constexpr int kTxOrder52[52] = {
    // HT-DATA 子载波顺序，不同于 L-LTF 的 kHeader48Sc
    -28,-27,-26,-25,-24,-23,-22,-20,-19,-18,-17,-16,
    -15,-14,-13,-12,-11,-10,-9,-8,-6,-5,-4,-3,-2,-1,
     1, 2, 3, 4, 5, 6, 8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26
};
```

---

## 预期结果

### L-LTF 修复后

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| L-LTF magnitude | ~8.9 | ~0.94 |
| L-SIG 解码 | 失败 | 成功 ✅ |
| L-SIG parity | FAIL | PASS |

### HT-LTF 修复后

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| HT-LTF H magnitude | ~20.6 | ~1.0 |
| HT-SIG CRC | MISMATCH | MATCH |
| HT-DATA FCS | FAIL | PASS |
