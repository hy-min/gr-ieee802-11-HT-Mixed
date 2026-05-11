# HT-SIG 解码修复计划 v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 L-SIG/HT-SIG 解码，使 d_early_eqsym 包含正确的 L-SIG/HT-SIG 符号数据，从而通过 VITERBI_IN 对比验证

**Architecture:** 问题根因是 `d_early_eqsym[kLSigRel]` 全为 0，但 `Hhdr52` 有正确的 L-LTF 信道估计。需要追踪符号提取流程，找出第一个全零的环节。

**Tech Stack:** GNU Radio, C++, IEEE 802.11-2016

---

## 当前状态

### 已确认的事实

| 指标 | 值 | 说明 |
|------|-----|------|
| Hhdr52[6:14] | 有效值 (如 4.184+-7.827i) | L-LTF 信道估计正确 |
| d_early_eqsym[kLSigRel][*] | 全为 0.000+0.000i | **问题点** |
| LSIG decode result | 全部比特为 1 | 无效数据 |
| wifi_phy_hier.py RX FFT | shift=False (已修复) | FFT 输出顺序正确 |

### 关键代码位置

| 位置 | 功能 |
|------|------|
| `lib/frame_equalizer_impl.cc:2320` | `extract_header52_from_sym64(sym64, d_early_eqsym[...])` |
| `lib/frame_equalizer_impl.cc:536-560` | `extract_header52_from_sym64` 函数 |
| `lib/frame_equalizer_impl.cc:2843` | 调用 `decode_lsig_direct_from_header52(d_early_eqsym[kLSigRel], Hhdr52, ...)` |
| `lib/frame_equalizer_impl.cc:2188-2190` | `sym64 = in + consumed * 64` |

---

## Task 1: 验证 sym64 在 L-SIG 符号时刻的值

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - 添加 sym64 调试
- Test: 运行 `test_constellation_real.py`

**问题分析:**
`d_early_eqsym[kLSigRel]` 全为 0，需要确认 `sym64` 在该时刻是否也是 0。如果是，说明问题在 FFT 输入；否则问题在 `extract_header52_from_sym64`。

**Steps:**

- [ ] **Step 1: 在 general_work 中添加 sym64 调试**

在 `lib/frame_equalizer_impl.cc` 的 `sym64` 定义后（约第 2190 行），`d_early_eqsym` 提取前添加：

```cpp
// DEBUG: 检查 L-SIG 符号时刻的 sym64 值
if (d_internal_symbol_counter == kLSigRel) {
    fprintf(stderr, "[SYMS64_DEBUG] L-SIG sym64[0:8] (DC to SC+7): ");
    for (int i = 0; i < 8; i++) {
        fprintf(stderr, "%.3f+%.3fi ", sym64[i].real(), sym64[i].imag());
    }
    fprintf(stderr, "\n");
    fprintf(stderr, "[SYMS64_DEBUG] L-SIG sym64[32:40] (DC附近): ");
    for (int i = 32; i < 40; i++) {
        fprintf(stderr, "%.3f+%.3fi ", sym64[i].real(), sym64[i].imag());
    }
    fprintf(stderr, "\n");
    fflush(stderr);
}
```

- [ ] **Step 2: 重新编译**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -E "(SYMS64_DEBUG|LSIG_DEBUG.*symbols)" | head -10
```

**预期结果:**
- 如果 sym64 全为 0 → 问题在 FFT 输入或更早阶段
- 如果 sym64 有有效值 → 问题在 `extract_header52_from_sym64`

---

## Task 2: 验证 extract_header52_from_sym64 输出

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - 添加提取后调试
- Test: 运行测试

**Steps:**

- [ ] **Step 1: 在 extract_header52_from_sym64 出口添加调试**

在 `lib/frame_equalizer_impl.cc` 第 559 行后（`extract_header52_from_sym64` 函数末尾）添加：

```cpp
// DEBUG: 打印提取后的 out52 前 8 个值
static int extract_debug_counter = 0;
fprintf(stderr, "[EXTRACT_DEBUG] call#%d out52[0:8]=", extract_debug_counter);
for (int i = 0; i < 8; i++) {
    fprintf(stderr, "%.3f+%.3fi ", out52[i].real(), out52[i].imag());
}
fprintf(stderr, "\n");
fflush(stderr);
extract_debug_counter++;
```

- [ ] **Step 2: 重新编译并运行**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -E "(EXTRACT_DEBUG)" | head -10
```

**预期结果:**
- EXTRACT_DEBUG 输出与 SYMS64_DEBUG 输出一致（排除提取函数问题）
- 或者发现提取后全部为 0（确定问题在提取函数内）

---

## Task 3: 分析 kLSigRel 值和 d_internal_symbol_counter

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`

**问题分析:**
`kLSigRel` 可能是 -1 或其他值，导致条件 `d_internal_symbol_counter == kLSigRel` 永远不满足。

**Steps:**

- [ ] **Step 1: 检查 kLSigRel 定义和 kHtSig0Rel, kHtSig1Rel 值**

```bash
grep -n "kLSigRel\|kHtSig0Rel\|kHtSig1Rel\|kLltf0Rel\|kLltf1Rel\|kHtTrain1Rel" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc | head -20
```

- [ ] **Step 2: 添加 d_internal_symbol_counter 追踪**

在 `extract_header52_from_sym64` 调用处（第 2320 行）附近添加：

```cpp
fprintf(stderr, "[SYMCNT_DEBUG] d_internal_symbol_counter=%d, kLSigRel=%d, kHtSig0Rel=%d, kHtSig1Rel=%d, match_lsig=%d\n",
        d_internal_symbol_counter, kLSigRel, kHtSig0Rel, kHtSig1Rel,
        d_internal_symbol_counter == kLSigRel ? 1 : 0);
fflush(stderr);
```

---

## Task 4: 验证 FFT 输入到 sym64 的数据流

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`
- Test: `wifi_phy_hier.py`

**问题分析:**
`sym64` 是从 `in + consumed * 64` 获取的。问题可能在于：
1. `in` 缓冲区没有正确数据
2. `consumed` 计数器不正确
3. 帧边界检测（wifi_start）有问题

**Steps:**

- [ ] **Step 1: 添加 wifi_start 和 consumed 调试**

在 `sym64` 定义后（约第 2190 行）添加：

```cpp
fprintf(stderr, "[FFTIN_DEBUG] consumed=%d, wifi_start=%d, d_in_frame=%d, d_internal_symbol_counter=%d, abs_in_off=%llu\n",
        consumed, wifi_start ? 1 : 0, d_in_frame ? 1 : 0, d_internal_symbol_counter, (unsigned long long)abs_in_off);
fflush(stderr);
```

- [ ] **Step 2: 重新编译并运行**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 20 python3 examples/test_constellation_real.py 2>&1 | grep -E "(FFTIN_DEBUG|SYMCNT_DEBUG)" | head -20
```

---

## Task 5: 修复发现的问题

根据 Task 1-4 的分析结果，确定并实施修复。

**可能的修复点:**

1. **kLSigRel 值错误** → 检查常数定义
2. **consumed 计数器错误** → 修正符号边界
3. **帧检测时机错误** → 修正 wifi_start 判断
4. **extract 函数索引错误** → 修正 kHeader48Sc 到 FFT bin 的映射

---

## Task 6: 验证修复后的 L-SIG/HT-SIG 解码

**Files:**
- Test: `examples/test_constellation_real.py`

- [ ] **Step 1: 运行测试验证 L-SIG decode 成功**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(LSIG_DECODE|LSIG_PARSE.*TRUE|d_have_ht_header)" | head -10
```

预期：`LSIG_PARSE` 返回 TRUE，`d_have_ht_header=1`

- [ ] **Step 2: 验证 HT-SIG VITERBI_IN 对比**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(VITERBI_IN|TX_HT_SIG.*encoded)" | head -10
```

预期：TX encoded 和 RX VITERBI_IN 匹配（或高度相似）

---

## Task 7: 清理调试输出并提交

- [ ] **Step 1: 移除所有临时调试 fprintf 语句**

移除 Task 1-4 中添加的所有调试输出。

- [ ] **Step 2: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: 修复 L-SIG/HT-SIG 符号提取，使 d_early_eqsym 包含正确数据"
```

---

## 关键文件路径

| 文件 | 说明 |
|------|------|
| `lib/frame_equalizer_impl.cc` | 包含所有符号提取和解码逻辑 |
| `examples/wifi_phy_hier.py` | RX FFT 配置（shift=False） |
| `examples/test_constellation_real.py` | 实时星座图测试 |

---

## 预期结果

- d_early_eqsym[kLSigRel] 包含有效的 L-SIG 符号（非全零）
- L-SIG decode 成功，parity check 通过
- HT-SIG decode 成功，VITERBI_IN 匹配 TX encoded
- d_have_ht_header=1, mcs=0
- HT-DATA 误码 ~1-2/52
