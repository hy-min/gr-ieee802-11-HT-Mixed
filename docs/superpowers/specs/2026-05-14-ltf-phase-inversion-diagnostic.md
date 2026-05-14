# L-LTF 相位反转（180°）诊断规格文档

**日期：** 2026-05-14
**状态：** 诊断完成，待修复验证
**分支：** fcs-backup-apply

---

## 一、症状与现象 (Symptoms)

### 核心故障

- L-SIG Parity Check 失败：`parity_sum=1`
- HT-SIG parse failed
- BPSK 均衡后虚部巨大：`|Q|/|I|` 达到 0.2-0.5（理想应接近 0）
- H 相位混乱：相邻 SC 之间相位差跳变 ±180°，而非线性斜率

### 深层现象

在纯仿真（SNR=30dB，无信道损伤）环境下：

| 帧序号 | LTF0 vs LTF1 相位差 |
|--------|---------------------|
| 帧1 | bin[6](SC+6): -179.6°, bin[7](SC+7): +177.4°, bin[8](SC+8): -178.0° |
| 帧2 | bin[6](SC+6): -179.6°, bin[7](SC+7): +177.4°, bin[8](SC+8): -178.0° |

**关键特征：LTF0 和 LTF1 的 FFT 输出在全频段恒定相差 ~180°，无斜率。**

### RAW_FFT_64 探针数据（第一帧）

**LTF0 相位分布：** 散乱分布在 0°-180° 之间
**LTF1 相位分布：** 几乎全部集中在 -155° 至 -180° 范围

| bin | SC | LTF0 Phase | LTF1 Phase | diff |
|-----|-----|-----------|-----------|------|
| 5 | -7 (pilot) | +90.0° | -89.7° | ~180° |
| 6 | +6 | +160.9° | -18.7° | ~180° |
| 37 | +4 | +178.0° | +1.6° | ~176° |
| 38 | -26 | +176.8° | -5.5° | ~182° |

---

## 二、排查与排除 (Exclusions)

### ✅ 排除 FFT Shift 配置错误

- **文件：** `wifi_phy_hier.py:78`
- **代码：** `fft.fft_vcc(64, True, window.rectangular(64), False, 1)` — shift=False
- **验证：** `NAKED_FFT` 探针确认 bin 6 (SC+6) 有能量，bin 38 (SC-26) 有能量，能量位置与 unshifted FFT 一致
- **结论：** FFT 确认为自然顺序（DC=bin0, 1-26=正频率, 38-63=负频率）

### ✅ 排除 kHeader48Bin 数组映射错误

- **验证方法：** Python 脚本遍历所有 48 个 SC → bin 映射，数学证明 100% 正确
- **关键映射：**
  - SC -26 → bin 38 ✓
  - SC +6 → bin 6 ✓
  - SC -7 (pilot) → bin 57 ✓
  - SC +7 (pilot) → bin 7 ✓

### ✅ 排除 TX 发射极性错误

- **文件：** `mixed_mode_carrier_allocator.py`
- **验证：** `DEFAULT_SYNC_WORDS = (LEGACY_STF, LEGACY_STF, LEGACY_LTF, LEGACY_LTF)` — T1 和 T2 完全相同
- **POLARITY_127：** 仅用于 HT-DATA pilots，不用于 L-LTF
- **insert_ht_training：** 仅插入 HT-STF/HT-LTF，不动 Legacy Preamble
- **结论：** TX 发出的 L-LTF T1 和 T2 完全相同，无 P-Matrix 误用

### ✅ 排除 sync_long CFO 补偿干扰

- **文件：** `sync_long.cc:184-189`
- **代码：**
  ```cpp
  if (std::abs(d_freq_offset) > 100.0) {
      out[o] = in_delayed[i] * exp(gr_complex(0, -d_offset * d_freq_offset));
  } else {
      out[o] = in_delayed[i];  // CFO 补偿被禁用
  }
  ```
- **d_freq_offset 来源：** 仅来自 sync_short 的粗估计值 `d_freq_offset_short`，**没有**从 LTF0/LTF1 计算精细 CFO
- **结论：** sync_long 完全不进行 CFO 补偿，不是罪魁祸首

### ✅ 排除信道模型引入相位旋转

- **文件：** `test_mcs_end_to_end.py:126-133`
- **参数：** `frequency_offset=0.0, epsilon=1.0, taps=[1.0]`
- **结论：** 信道模型完全透明，不引入任何相位旋转

---

## 三、核心假设 (Hypothesis)

### 观察到的现象

在仿真实环境下，LTF0 和 LTF1 的 FFT 输出在全频段恒定相差 ~180°，但：
1. TX 发出的 LTF0 和 LTF1 完全相同
2. 信道模型无相位偏移
3. 无 CFO 补偿

### 假设一：GNU Radio 流图内禀特性

**可能的根源：**
- `ht_symbol_splitter` 的硬编码边界状态机
- 或流图中的隐藏延迟/相位旋转
- 或 FFT 模块的某种未记录行为

**数学约束：**
- 如果是 FFT 窗口偏移 Δn，相位差应为 Δφ = -2π·k·Δn/64（随 k 变化，有斜率）
- 实测 diff 全部钉在 180° 附近，无斜率
- **结论：** 不是 FFT 窗口偏移，是某种恒定的 π 相位旋转

### 假设二：LTF0 和 LTF1 在时域就差了 180°

**待验证：** LTF1_time[n] = -LTF0_time[n]（时域整体反相）

如果成立，则：
- FFT 后每个 subcarrier 自然差 180°（因为 FFT 是线性变换）
- 问题根源在更上游（流图中的某个模块对信号做了整体乘以 -1）

---

## 四、下一步行动 (Next Steps)

### 行动 1：探底时域（关键探针）

**目的：** 绕过 FFT，直接对比 LTF0 和 LTF1 的时域样本

**操作：** 在 `ht_symbol_splitter` 的边界输出点添加探针，打印 LTF0 和 LTF1 的完整 64 点时域数据

**验证目标：**
- 如果 `LTF1_time[n] = -LTF0_time[n]`（时域反相）→ 问题在流图上游
- 如果 `LTF1_time[n] ≈ LTF0_time[n]`（时域相同）→ 问题在 FFT 模块或 SPLITTER 输出逻辑

**预期结果（如果假设一成立）：**
```
LTF0_time[0] = +0.1094+0.0000i
LTF0_time[1] = +0.0309-0.0671i
...
LTF1_time[0] = -0.1094-0.0000i  ← 整体反相
LTF1_time[1] = -0.0309+0.0671i
```

### 行动 2：信道估计硬修复

**背景：** 如果确认 ~180° 是无法消除的内禀特性，需要修改 `estimate_header_channel_from_lltf52`

**方案 A（推荐）：**
- 放弃对 LTF0 和 LTF1 取平均
- 只使用 LTF0 计算 H（LTF0 更接近帧起始，信道响应更稳定）
- 或者在平均前对 LTF1 施加 π 相位补偿

**方案 B：**
- 确认 kLltfPilotTX 的正确性
- 修改 CPE 估计算法，使用正确的 pilots 参考序列

### 行动 3：端到端验证

修复后验证：
1. LTF0 vs LTF1 相位差应接近 0°（而非 180°）
2. H 相位应呈现线性斜率（而非混乱跳变）
3. BPSK 均衡后虚部应接近 0
4. L-SIG Parity Check 应通过

---

## 五、相关文件索引

| 文件 | 关键代码 | 备注 |
|------|---------|------|
| `lib/sync_long.cc` | d_freq_offset 阈值判断 | CFO 补偿被禁用 |
| `lib/ht_symbol_splitter_impl.cc` | at_boundary 硬编码 (rel_idx==63,143,223...) | 需审查 FFT 窗口边界 |
| `lib/frame_equalizer_impl.cc` | estimate_header_channel_from_lltf52() | 信道估计算法 |
| `lib/frame_equalizer_impl.cc` | extract_header52_from_sym64() | FFT 数据提取 |
| `examples/mixed_mode_carrier_allocator.py` | LEGACY_LTF, DEFAULT_SYNC_WORDS | TX 组帧逻辑 |
| `wifi_phy_hier.py:78` | fft.fft_vcc(64, True, ..., False) | RX FFT shift=False |
| `test_mcs_end_to_end.py:126` | channels.channel_model(...) | 信道模型参数 |

---

## 六、诊断时间线

| 时间 | 发现 | 意义 |
|------|------|------|
| 2026-05-14 AM | kHeader48Bin 映射数学验证正确 | 排除频域索引错位 |
| 2026-05-14 PM | sync_long CFO 补偿被阈值禁用 | 排除 CFO 干扰 |
| 2026-05-14 PM | TX LEGACY_LTF x2 完全相同 | 排除 TX 极性错误 |
| 2026-05-14 PM | FFT shift=False 确认 | 排除 FFT 配置错误 |
| 2026-05-14 PM | RAW_FFT_64 探针：LTF0/LTF1 恒定相差 ~180° | 核心异常确认 |
| 2026-05-14 PM | 相位差无斜率，排除 FFT 窗口偏移 | 缩小嫌疑范围 |

---

## 七待验证关键问题

> ❓ 在纯仿真（无任何信道损伤）的情况下，为什么 LTF0 和 LTF1 的 FFT 输出会恒定相差 ~180°？

**可能的答案：**
1. 流图中某个模块对信号整体施加了 π 相位旋转
2. SPLITTER 的边界输出逻辑引入了半个符号周期的延迟
3. FFT 模块在处理特定边界时有某种内禀的相位偏置
4. sync_long 的相关检测峰值位置引入了某种系统性偏移
