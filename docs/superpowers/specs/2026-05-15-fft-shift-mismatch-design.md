# FFT/IFFT Shift 配置不一致问题设计文档

**日期:** 2026-05-15
**状态:** 调查总结 - 待验证
**问题:** MCS 0-7 端到端测试失败，接收消息数为 0

---

## 问题背景

MCS 0-7 端到端测试失败，HT-SIG CRC 验证不通过。调试过程中发现 TX 和 RX 的 FFT/IFFT 配置存在不对称问题。

---

## 调试发现

### 1. SPLITTER FFT 窗口边界 - 正常工作

通过 `STATE_PROBE` 验证，SPLITTER 的 FFT 窗口边界触发正常：

```
rel_idx=63: LTF0 FFT 输出 ✅
rel_idx=127: LTF1 FFT 输出 ✅
rel_idx=207: L-SIG FFT 输出 ✅
rel_idx=303: HT-SIG0 FFT 输出 ✅
rel_idx=383: HT-SIG1 FFT 输出 ✅
```

### 2. LTF0 vs LTF1 时域数据完全不同 - 异常！

**TX 端 (MM-CA PREAMBLE):**
- sym2 (LTF0) pilots: {1, -1, 1, 1}
- sym3 (LTF1) pilots: {1, -1, 1, 1}
- 频域 pilots 相同 ✅

**RX 端 (SYNC_LONG OUTPUT):**
```
LTF0_START (rel=0): sample=-0.0343+0.0261i
LTF1_START (rel=64): sample=-0.6887-0.1321i
```

LTF0 和 LTF1 的时域数据完全不同，差值不为零：
```
TD[0]: LTF1 + LTF0 = -0.722972-0.106005i (should be ~0 if negated)
```

### 3. TX IFFT 和 RX FFT 的 shift 参数不对称

| 参数 | TX IFFT | RX FFT |
|------|---------|--------|
| shift | **False** | **True** |
| 说明 | 自然顺序 | 频率移位 |

**位置:** `wifi_phy_hier.py`
```python
# TX IFFT (line 80)
self.fft_vxx_0_0 = fft.fft_vcc(64, False, tuple([1/52**.5] * 64), False, 1)

# RX FFT (line 78)
self.fft_vxx_0_1 = fft.fft_vcc(64, True, window.rectangular(64), False, 1)
```

---

## 根本原因分析

### 假设：FFT/IFFT shift 不匹配导致 LTF 数据异常

当 TX IFFT 使用 `shift=False`，RX FFT 使用 `shift=True` 时：

1. **TX 发送：** 将频域数据按自然顺序（DC 低频 → 高频 → 负频率）输入 IFFT
2. **RX 接收：** 使用频率移位的 FFT 输入顺序期望数据

这会导致：
- subcarrier 顺序被错误解释
- LTF 序列的相位关系被破坏
- 信道估计 H0 vs H1 出现大幅差异

### 数据验证

从 `NAKED_TEST` 的 H 估计结果：
```
SC[-26] bin[38]: H0=0.1357 mag=0.2225 | H1=-0.0235 mag=0.0307 | ratio=0.14
SC[-24] bin[40]: H0=1.3214 mag=2.0591 | H1=1.4892 mag=2.0787 | ratio=1.01
```

某些 subcarrier 的 H0/H1 比率只有 0.14（86% 差异），而其他接近 1.0。这种不一致性暗示 FFT/IFFT 顺序问题。

---

## 可能的解决方案

### 方案 A：统一 shift 参数（推荐）

**修改 TX IFFT 的 shift 参数：**
```python
# 方案 A1: TX IFFT 改为 shift=True
self.fft_vxx_0_0 = fft.fft_vcc(64, True, tuple([1/52**.5] * 64), False, 1)

# 方案 A2: RX FFT 改为 shift=False
self.fft_vxx_0_1 = fft.fft_vcc(64, False, window.rectangular(64), False, 1)
```

**优点：** 彻底解决顺序不匹配问题
**风险：** 可能影响其他模块，需要完整测试

### 方案 B：保持现状，修复信道估计算法

如果 TX IFFT 和 RX FFT 的 shift 配置是有意为之（例如兼容 legacy），则需要调整信道估计算法来适应这种不对称。

**需要验证：**
- TX 发送的 LTF 频域顺序
- RX 接收期望的 LTF 频域顺序
- kHeader48Bin 映射是否与实际顺序匹配

### 方案 C：深入调查 LTF 生成逻辑

**需要确认：**
1. TX 端 preamble 生成的两个 LTF 是否真的相同？
2. 如果不同，是哪里生成的？
3. mixed_mode_carrier_allocator 中 LEGACY_LTF 的使用是否正确？

---

## 验证步骤

### 步骤 1：验证 TX IFFT 输出的 LTF 时域

在 TX IFFT 后添加探针，验证两个 LTF 符号是否相同：
```python
# 在 wifi_phy_hier.py 中添加
# TX IFFT 输出探针
```

### 步骤 2：验证 FFT/IFFT shift 影响

对比两种配置下的 FFT 输出：
- TX IFFT shift=False → TX 发送的自然顺序
- TX IFFT shift=True → TX 发送的移位顺序

### 步骤 3：验证 kHeader48Bin 映射

检查 `frame_equalizer_impl.cc` 中的 `kHeader48Bin` 数组是否与 TX 的 subcarrier 顺序匹配。

---

## 待验证的关键假设

| 假设 | 验证方法 | 预期结果 |
|------|---------|---------|
| LTF0 和 LTF1 时域应该相同 | TX IFFT 输出探针 | 两个 LTF 样本相同 |
| shift=False/True 导致顺序不匹配 | 对比两种配置 | FFT 输出 subcarrier 顺序不同 |
| kHeader48Bin 与 TX 顺序匹配 | 检查 TX RX subcarrier 对应关系 | 映射正确 |

---

## 下一步行动

1. **优先验证 TX IFFT 输出**：确认两个 LTF 符号是否真的相同
2. **检查 mixed_mode_carrier_allocator**：验证 preamble 生成逻辑
3. **决定是否修改 FFT/IFFT shift 配置**
4. **修复后进行完整的 MCS 0-7 测试**

---

## 参考文件

- `wifi_phy_hier.py:78-80` - FFT/IFFT 配置
- `lib/frame_equalizer_impl.cc` - kHeader48Bin 映射
- `lib/ht_symbol_splitter_impl.cc` - FFT 窗口边界
- `examples/mixed_mode_carrier_allocator.py` - TX preamble 生成
