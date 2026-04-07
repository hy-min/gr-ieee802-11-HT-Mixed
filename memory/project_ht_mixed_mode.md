# HT Mixed Mode Project - 调试进展

> 更新：2026-04-06

## 项目背景

实现 gr-ieee802-11 的 HT Mixed 工作模式，解决 TX/RX 环回测试中 HT-SIG 解码失败的问题。

## 已尝试的修复

1. ✅ **移除 Legacy 模式检测** - 防止干扰
2. ✅ **wifi_start 64 对齐** - 确保 FFT 块边界对齐
3. ✅ **Method 2 位置验证** - 拒绝不合理的峰值位置
4. ✅ **HT Mixed CP 移除逻辑** - 尝试正确处理 CP
5. ✅ **输出所有样本（无 CP 移除）** - 简化处理

## 根本问题

**HT-Mixed 前导码结构与 FFT 块大小不匹配**

### HT-Mixed 前导码时间域结构

```
L-STF:   d_offset 0-159    (160 samples)
L-LTF:   d_offset 160-239  (CP 16 + 数据 64)
L-SIG:   d_offset 240-319  (CP 16 + 数据 64)
HT-SIG:  d_offset 320-415  (CP 16 + 数据 80)  ← 80 样本，不是 64！
HT-STF: d_offset 416-479  (64 samples)
HT-LTF:  d_offset 480-607  (CP 16 + 数据 112)
```

### FFT 块问题

FFT 块大小为 64 样本，但 HT-SIG 是 80 样本。这意味着：
- HT-SIG 不能对齐到单个 FFT 块
- HT-SIG 跨越 FFT[2] 和 FFT[3]

### 当 wifi_start=64 时

```
FFT[0]: 位置 64-127  → d_offset 192-255  → L-LTF 数据 ✓
FFT[1]: 位置 128-191 → d_offset 256-319  → L-SIG 数据 ✓
FFT[2]: 位置 192-255 → d_offset 320-383 → HT-SIG CP(16) + 数据(48) 部分 HT-SIG
FFT[3]: 位置 256-319 → d_offset 384-447 → HT-SIG 数据(32) + HT-STF(32)
```

HT-SIG 数据从 d_offset 336 开始，但 FFT[2] 捕获的是 d_offset 320-383，只包含 HT-SIG 的最后 48 样本和 CP。

## 当前状态

| 测试项 | 状态 | 说明 |
|--------|------|------|
| 模式检测 | ✅ | 只检测 HT Mixed |
| wifi_start 64 对齐 | ✅ | mod64=0 |
| L-LTF FFT | ✅ | 非零值 |
| L-SIG FFT | ✅ | 非零值 |
| HT-SIG FFT | ❌ | rel=3,4 为零 |
| HT-SIG 解码 | ❌ | parse failed |

## 关键文件

| 文件 | 问题 |
|------|------|
| `lib/sync_long.cc` | CP 移除逻辑与 HT Mixed 结构不匹配 |
| `lib/frame_equalizer_impl.cc` | 假设 HT-SIG 在单个 FFT 块中 |

## 结论

HT-Mixed 模式的 HT-SIG 字段是 80 样本，而 FFT 块是 64 样本。这是一个**架构层面的不匹配**，不是简单的 bug。

可能的解决方案：
1. 修改 sync_long 输出以对齐 HT-SIG 到 FFT 块
2. 修改 frame_equalizer 处理跨越多个 FFT 块的 HT-SIG
3. 使用 GNU Radio 官方的 HT-Mixed 实现作为参考
