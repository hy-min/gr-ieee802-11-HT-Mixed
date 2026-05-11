# FCS 真正通过实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 L-SIG/HT-SIG 解析和 HT-DATA 信道估计，使 FCS 真正通过（非虚假通过）

**Architecture:** 问题根因是 HT-LTF channel estimate 被 `d_have_ht_header` gate 住，但 `d_have_ht_header` 需要 HT-SIG 解码成功才设置。HT-SIG 解码使用 L-LTF channel estimate，所以存在潜在的循环依赖问题。需要确保：
1. L-LTF channel estimate 在 L-SIG 解析时可用
2. HT-SIG 使用 L-LTF channel estimate 正确解码
3. HT-LTF channel estimate 在 HT-LTF1 符号时计算（无条件）
4. HT-DATA 使用正确的 HT-LTF channel estimate

**Tech Stack:** GNU Radio, C++, IEEE 802.11-2016

---

## 当前状态分析

### 已确认的事实
1. L-LTF channel estimate (`Hhdr52`) 有有效值
2. L-SIG symbols 有有效值但 parity check 失败
3. HT-SIG 解析进入但 L-SIG decode 失败
4. 当前未提交修改：移除 `d_have_ht_header` 条件从 HT-LTF channel estimate

### 关键代码位置
| 位置 | 内容 |
|------|------|
| `lib/frame_equalizer_impl.cc:2323` | HT-LTF channel estimate gate（当前修改位置）|
| `lib/frame_equalizer_impl.cc:2318` | `extract_header52_from_sym64` - 符号提取 |
| `lib/frame_equalizer_impl.cc:2713` | L-LTF channel estimate 计算 |
| `lib/frame_equalizer_impl.cc:2032` | `d_have_ht_header = true` 设置位置 |

---

## Task 1: 验证 L-SIG 解析问题

**Files:**
- Test: `examples/test_constellation_real.py`
- Debug: `lib/frame_equalizer_impl.cc`

- [ ] **Step 1: 重新编译并运行详细调试**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

运行：
```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(LSIG_DECODE|Hhdr52|L-SIG equalized)" | head -20
```

预期：查看 L-SIG decode 的 24 bits 和 parity 计算

- [ ] **Step 2: 检查 TX L-SIG bits**

TX 端输出了 L-SIG bits:
```
[TX_LSIG] signal_header[0:24]=110100110011000001000000
```

分析：`1101 0011 0011 0000 0100 0000`
- Rate: `1101` = 0xD?
- Reserved: `0`
- Length: `00110011000001000000` = 0x3304 = 13060?

需要确认 TX 和 RX 的 L-SIG bits 是否匹配。

- [ ] **Step 3: 提交验证结果**

---

## Task 2: 分析 L-SIG Parity Check 失败根因

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` - 添加更详细的 L-SIG decode 调试

- [ ] **Step 1: 添加 L-SIG decode 中间过程调试**

在 `decode_lsig_from_bits52` 函数中添加：
```cpp
fprintf(stderr, "[LSIG_DECODE] rate_bits=%x%xx%x reserved=%d length=%d parity=%d\n",
        bits52[0], bits52[1], bits52[2], bits52[3],
        ((bits52[4] << 8) | (bits52[5] << 4) | bits52[6]),
        bits52[17]);
fprintf(stderr, "[LSIG_DECODE] parity_calc=%d parity_expected=%d\n",
        parity_sum & 1, bits52[17]);
```

- [ ] **Step 2: 重新编译并运行**

```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep LSIG_DECODE
```

- [ ] **Step 3: 对比 TX 和 RX 的 L-SIG bits**

找到 TX 端输出的 L-SIG header bits 和 RX 端解码的 bits，对比差异。

- [ ] **Step 4: 分析问题根因**

根据对比结果：
- 如果 bits 完全一致 → L-SIG decode 函数有 bug
- 如果 bits 不同 → 信道估计或均衡有问题

---

## Task 3: 修复发现的问题

根据 Task 2 的分析结果，确定修复方案。

---

## Task 4: 验证 HT-SIG 解析

- [ ] **Step 1: 检查 HT-SIG decode 是否成功**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(HT_SIG_PARSE|d_have_ht_header|mcs=)" | head -20
```

预期：看到 `d_have_ht_header=1` 和 `mcs=0`

---

## Task 5: 验证 HT-DATA 信道估计和 FCS

- [ ] **Step 1: 检查 HT-DATA 误码**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(HT-DATA|FCS|errors)" | head -20
```

- [ ] **Step 2: 运行 MCS FCS 测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 60 python3 examples/test_mcs_fcs.py 2>&1 | grep -E "(MCS|FCS|PASS|FAIL)"
```

预期：所有 MCS 0-7 显示 PASS

---

## Task 6: 提交最终修改

- [ ] **Step 1: 清理调试输出**

移除所有临时调试 `fprintf` 语句。

- [ ] **Step 2: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: 修复 L-SIG/HT-SIG 解析和 HT-DATA 信道估计，使 FCS 真正通过"
```

---

## 关键文件路径

| 文件 | 说明 |
|------|------|
| `lib/frame_equalizer_impl.cc` | Frame equalizer 实现，包含 L-SIG/HT-SIG decode |
| `examples/test_constellation_real.py` | 实时星座图测试 |
| `examples/test_mcs_fcs.py` | MCS FCS 验证脚本 |

---

## 预期结果

- L-SIG parity check 通过
- HT-SIG 成功解码 (`d_have_ht_header=1`)
- HT-DATA 误码 ~1-2/52
- 所有 MCS 0-7 FCS PASS
