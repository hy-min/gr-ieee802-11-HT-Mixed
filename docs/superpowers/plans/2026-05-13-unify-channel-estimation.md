# 统一信道估计 TX 参考值 - 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 ls.cc 和 frame_equalizer 的信道估计方法，消除 H 幅度不一致问题

**Architecture:** 创建共享常量头文件 `ieee80211_constants.h`，定义 `kFftNormalize`、`kLltf48TX`、`kLltf64Binned`。两种估计器都使用 `RX / kLltf48TX / kFftNormalize` 方法。

**Tech Stack:** GNU Radio C++ (ls.cc, frame_equalizer_impl.cc), 共享头文件

---

## 问题根因

| 参数 | ls.cc (当前) | frame_equalizer (当前) | 问题 |
|------|-------------|----------------------|------|
| TX 参考 | `FFT_LONG[i]` (~8.875) | `kLltf48TX[i] / kFftNormalize` (±1/8.875) | 不一致 |
| H 幅度 | ~0.02 | ~0.8-1.0 | 差 40x |
| 原因 | FFT_LONG 与实际 RX FFT 不匹配 | 使用 BPSK ±1 归一化 | 数学模型错误 |

**正确方法：** `H = RX / (kLltf48TX[i] / kFftNormalize) = RX * kFftNormalize / kLltf48TX[i]`

---

## 文件结构

- 新建: `lib/ieee80211_constants.h` — 共享常量
- 修改: `lib/equalizer/ls.cc` — 使用共享常量
- 修改: `lib/frame_equalizer_impl.cc` — 使用共享常量

---

## Task 1: 创建共享常量头文件

**文件:**
- 新建: `lib/ieee80211_constants.h`

**步骤:**

- [ ] **Step 1: 创建 lib/ieee80211_constants.h**

```cpp
/*
 * IEEE 802.11 共享常量
 *
 * 包含 L-LTF 信道估计所需的 TX 参考值和归一化因子。
 * 所有使用这些常量的文件必须包含此头文件。
 */

#ifndef INCLUDED_IEEE80211_CONSTANTS_H
#define INCLUDED_IEEE80211_CONSTANTS_H

#include <complex>

namespace gr {
namespace ieee802_11 {

// FFT 归一化因子: 64 / sqrt(52)
// TX IFFT 使用 1/sqrt(52) 归一化，RX FFT 无归一化
// 有效增益 = 64/sqrt(52) ≈ 8.875
static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);

// L-LTF TX BPSK 值，48 个数据子载波，按 kHeader48Sc 顺序排列
// 这些是 TX 实际传输的 BPSK ±1 调制值（实轴）
// 用于信道估计: H = RX / TX / kFftNormalize
static constexpr gr_complex kLltf48TX[48] = {
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc -26 to -20
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -19 to -14
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -13 to -8
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc  -6 to  -1
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +1 to  +6
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +8 to +13
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +14 to +19
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +20 to +26
};

// L-LTF TX 值，按 FFT bin 顺序排列（0..63）
// 用于 ls.cc 的 64-bin 信道估计
// 非数据/导频位置的 bin 值为 0（保护带、DC）
// 注意: kHeader48Bin[48] 定义了哪些 bin 是数据子载波
// kLltf64Binned[bin] = kLltf48TX[kHeader48Bin 的对应索引位置]
// 对于非数据 bin，值为 gr_complex(0,0)
static constexpr gr_complex kLltf64Binned[64] = {
    // bin  0-5: 保护带 -> 0
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    // bin  6: SC -26 -> kLltf48TX[0] = +1
    gr_complex(+1.0f, 0.0f),
    // bin  7: SC -25 -> kLltf48TX[1] = +1
    gr_complex(+1.0f, 0.0f),
    // bin  8: SC -24 -> kLltf48TX[2] = -1
    gr_complex(-1.0f, 0.0f),
    // bin  9: SC -23 -> kLltf48TX[3] = -1
    gr_complex(-1.0f, 0.0f),
    // bin 10: SC -22 -> kLltf48TX[4] = +1
    gr_complex(+1.0f, 0.0f),
    // bin 11: SC -21 (导频) -> 0 (导频单独处理)
    gr_complex(0.0f, 0.0f),
    // bin 12-15: SC -20 to -17 -> kLltf48TX[6-9] = +1, -1, +1, +1
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 16-21: SC -16 to -11 -> kLltf48TX[10-15] = +1, +1, +1, +1, +1, +1
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 22-25: SC -10 to -8 (跳过导频 SC -7)
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 26-27: SC -6 to -5 -> kLltf48TX[18-19]
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 28-31: SC -4 to -1 -> kLltf48TX[20-23]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 32: DC -> 0
    gr_complex(0.0f, 0.0f),
    // bin 33: SC +1 -> kLltf48TX[24]
    gr_complex(+1.0f, 0.0f),
    // bin 34-38: SC +2 to +6 -> kLltf48TX[25-29]
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 39: SC +7 (导频) -> 0
    gr_complex(0.0f, 0.0f),
    // bin 40-47: SC +8 to +15 (跳过导频 SC +21)
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 48-51: SC +16 to +19
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 52: SC +20 -> kLltf48TX[42]
    gr_complex(+1.0f, 0.0f),
    // bin 53: SC +21 (导频) -> 0
    gr_complex(0.0f, 0.0f),
    // bin 54-58: SC +22 to +26 -> kLltf48TX[43-47]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 59-63: SC +27 to +31 (超出范围，不使用) -> 0
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
};

// 验证: kLltf64Binned 中非零元素数量应等于 48 (数据) + 4 (导频) = 52
// 实际数据子载波 48 个由 kHeader48Bin 指定

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE80211_CONSTANTS_H */
```

---

## Task 2: 修改 ls.cc 使用共享常量

**文件:**
- 修改: `lib/equalizer/ls.cc`

**步骤:**

- [ ] **Step 1: 添加 include 头文件**

在 `ls.cc` 顶部添加:
```cpp
#include "ieee80211_constants.h"
```

- [ ] **Step 2: 删除 FFT_LONG 数组定义**

删除 `ls.cc` 中的 `static const gr_complex FFT_LONG[64] = {...}` 定义（约第 15-80 行）。

- [ ] **Step 3: 修改信道估计公式**

在 `ls.cc` 的 `equalize()` 函数中，修改 n=1 情况的信道估计：

旧代码（约第 121-124 行）:
```cpp
d_H[i] += in[i];
d_H[i] /= FFT_LONG[i];  // 旧: 使用理论 FFT_LONG
```

新代码:
```cpp
d_H[i] += in[i];
// 防御性检查: 防止除以零 (guard bands 和 DC 位置 kLltf64Binned[i] == 0)
if (std::abs(kLltf64Binned[i]) > 1e-9f) {
    d_H[i] /= (kLltf64Binned[i] * kFftNormalize);
}
// 非数据 bin 的 H 值保持累加结果（不做归一化）
```

- [ ] **Step 4: 更新调试打印**

将调试输出中的 `FFT_LONG` 替换为 `kLltf64Binned`:
```cpp
fprintf(stderr, "[LS_EQ] n=1: kLltf64Binned[6-10] = ");
```

---

## Task 3: 修改 frame_equalizer_impl.cc 使用共享常量

**文件:**
- 修改: `lib/frame_equalizer_impl.cc`

**步骤:**

- [ ] **Step 1: 添加 include 头文件**

在 `frame_equalizer_impl.cc` 顶部添加:
```cpp
#include "ieee80211_constants.h"
```

- [ ] **Step 2: 删除本地定义的 kLltf48TX 和 kFftNormalize**

删除以下本地定义（约第 397-430 行）:
- `kLltf48TX[48]` 数组
- `kLltfPilotSign[4]` 数组
- `kLltfPilotTX[4]` 数组
- `kHeaderPilotBase[4]` 数组

**注意:** 如果 `kLltfPilotTX` 和 `kHeaderPilotBase` 被其他地方引用，需要检查是否也需要迁移或保留。

- [ ] **Step 3: 验证 kFftNormalize 使用**

检查代码中所有使用 `kFftNormalize` 的地方，确保它们引用的是共享头文件中的定义。

---

## Task 4: 构建验证

**步骤:**

- [ ] **Step 1: 运行 cmake 配置**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -20
```

- [ ] **Step 2: 编译**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -30
```

预期: 编译成功，无 "Multiple Definition" 错误

- [ ] **Step 3: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && /home/hy/conda/envs/gnuradio/bin/python3 test_mcs_end_to_end.py 2>&1 | grep -E "(LS_EQ|H magnitude|CHAN_EST)" | head -20
```

预期:
- `LS_EQ` 输出显示 H magnitude ≈ 0.8-1.0（之前是 ~0.02）
- `CHAN_EST` 显示一致的 H 幅度

---

## Task 5: 提交

**步骤:**

- [ ] **Step 1: 检查 git diff**

```bash
cd /home/hy/gr-ieee802-11 && git diff --stat
```

预期:
- `lib/ieee80211_constants.h` (新增)
- `lib/equalizer/ls.cc` (修改)
- `lib/frame_equalizer_impl.cc` (修改)

- [ ] **Step 2: 提交**

```bash
git add lib/ieee80211_constants.h lib/equalizer/ls.cc lib/frame_equalizer_impl.cc
git commit -m "refactor: unify channel estimation TX reference constants

Move kFftNormalize, kLltf48TX, and kLltf64Binned to shared
ieee80211_constants.h header. Both ls.cc and frame_equalizer
now use RX / kLltf48TX / kFftNormalize for channel estimation.

Root cause: FFT_LONG in ls.cc did not match actual RX FFT,
causing H magnitude ~0.02 instead of ~0.8-1.0.

Changes:
- Add lib/ieee80211_constants.h with static constexpr arrays
- ls.cc: replace FFT_LONG with kLltf64Binned, add zero-check
- frame_equalizer: remove local definitions, use shared constants

Note: kLltf64Binned uses static constexpr to comply with C++ ODR."
```

---

## 验证清单

- [ ] 编译无 "Multiple Definition" 错误
- [ ] 编译无除零警告
- [ ] ls.cc H magnitude ≈ 0.8-1.0（之前 ~0.02）
- [ ] frame_equalizer H magnitude 与 ls.cc 一致
- [ ] L-SIG/HT-SIG 解码正常
- [ ] 端到端测试通过

---

## 附录: C++ ODR 注意事项

**问题:** 如果在头文件中使用 `const` 而非 `static constexpr`，多个 .cc 文件包含后会在链接时产生 "Multiple Definition" 错误。

**解决:** 使用 `static constexpr`:
```cpp
static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);
static constexpr gr_complex kLltf48TX[48] = {...};
```

`static constexpr` 确保每个翻译单元只有一份定义，符合 ODR 规范。
