# sync_long LONG 模板修复设计文档

**日期：** 2026-05-14
**状态：** 设计批准
**分支：** fcs-backup-apply

## 问题陈述

### 根本原因

TX (`mixed_mode_carrier_allocator.py`) 和 RX (`sync_long.cc`) 使用了**不同的 LTF 序列**：

| 组件 | LTF 类型 | 时域输出 |
|------|----------|---------|
| TX | `LEGACY_LTF` (IEEE 802.11 标准频域序列) | 复数（有虚部） |
| RX | `LONG` (纯实数模板) | 纯实数 |

**关键发现：**
- TX 的 `LEGACY_LTF` 是 IEEE 802.11 标准嫡系序列 ✅
- RX 的 `LONG` 是为了省算力被"阉割"的纯实数近似版本 ❌
- 这导致 `sync_long` 的互相关峰值检测不准确
- FFT 窗口起始点偏移，导致频域输出包含非预期的虚部分量
- 信道估计 H 有巨大相位偏差
- BPSK 均衡后虚部与实部相当（|Q| ≈ |I|）

### 影响链

1. TX IFFT 后的 LTF 是复数信号（虚部最大 0.16）
2. RX sync_long 用纯实数模板做相关 → 无法正确检测 LTF
3. 帧检测/同步有偏差
4. FFT 窗口位置不准确
5. 频域输出包含虚部分量
6. 信道估计 H 有误
7. L-SIG/HT-SIG 解码失败

## 解决方案

### 修复策略

**以 TX 为准**：TX 的 `LEGACY_LTF` 是 IEEE 802.11 标准，不能改动。

**替换 RX 模板**：将 `sync_long.cc` 中的纯实数 `LONG` 替换为 `LEGACY_LTF` 的 IFFT 结果（匹配滤波器抽头形式）。

### 技术细节

#### FIR 滤波器架构确认

`sync_long.cc` 中使用：
```cpp
d_fir(gr::filter::kernel::fir_filter_ccc(LONG))
```

- `fir_filter_ccc` = Complex Input × Complex Output × Complex Taps
- 底层管道本来就是为复数匹配滤波器设计的
- **不需要改动 FIR 滤波器代码**，只需替换 `LONG` 数组

#### 匹配滤波器抽头生成

FIR 滤波器执行**卷积**，而互相关需要**时间反转+共轭**：

$$ R[n] = x[n] * s^*[-n] $$

因此，生成的 C++ 数组必须是：
```python
taps = np.conj(time_seq[::-1])  # 时间反转 + 取共轭
```

#### FFT 映射规则

根据 GNU Radio IFFT (shift=False) 的要求：
- Index 0: DC (必须为 0)
- Index 1-26: 正频率
- Index 27-37: Guard Bands (补 0)
- Index 38-63: 负频率

## 修改文件

- `lib/sync_long.cc` — 替换 `LONG` 数组（第 505-538 行）

## Python 生成脚本

```python
import numpy as np

# IEEE 802.11 标准 L-LTF 频域序列 (52 个非零子载波, -26 到 +26)
LEGACY_LTF = (
    0,  # 占位，实际索引 0 是 DC
    1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,  # 正频率 SC +1 到 +26
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # Guard bands
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1   # 负频率 SC -26 到 -1
)

# 1. 构造 64 点频域数组
freq_seq = np.zeros(64, dtype=complex)
freq_seq[1:27] = LEGACY_LTF[1:27]    # 正频率
freq_seq[38:64] = LEGACY_LTF[27:53]  # 负频率

# 2. 执行 IFFT
time_seq = np.fft.ifft(freq_seq)

# 3. 生成匹配滤波器抽头 (时间反转 + 取共轭)
taps = np.conj(time_seq[::-1])

# 4. 打印 C++ 格式
print("const std::vector<gr_complex> LONG = {")
for t in taps:
    print(f"    gr_complex({t.real:.6f}, {t.imag:.6f}),")
print("};")
```

## 预期结果

修复后：
- `sync_long` 的互相关峰值将精准定位
- FFT 窗口起始点准确
- 频域输出为纯实数（或只有很小的残余虚部）
- 信道估计 H 相位正确
- BPSK 均衡后符号落在实数轴上
- L-SIG parity check 通过
- HT-SIG CRC 通过

## 验证方法

1. 编译后运行 `test_mcs_end_to_end.py`
2. 检查 `SYNC_LONG_OUT` 输出的 LTF 时域信号是否为纯实数
3. 检查 `RAW_RX_PHASE` 中 LTF1 的相位应该接近 0° 或 180°
4. 检查 `H_PHASE_CHECK` 中 H 相位应该是线性斜率（而非混乱）
5. 检查 `DIAG_LLR` 中 BPSK 均衡后虚部应该接近 0

## 注意事项

- `fir_filter_ccc` 无需改动
- 生成的 `LONG` 数组必须经过时间反转和取共轭
- 编译后需要 `make install` 更新动态库
