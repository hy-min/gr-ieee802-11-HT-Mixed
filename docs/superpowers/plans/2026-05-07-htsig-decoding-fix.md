# HT-SIG 解码修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 HT-SIG 解码，使 VITERBI_IN 匹配 TX encoded bits

**Architecture:** HT-SIG 解码失败的根本原因是 demapper/equalization 阶段的输出与 TX encoded bits 完全不匹配。问题可能在于：
1. L-LTF channel estimate (Hhdr52) 用于 HT-SIG equalization 不合适
2. QBPSK rotation compensation 方向错误
3. Demapper 使用虚部判断但符号可能反转

**Tech Stack:** GNU Radio, C++, IEEE 802.11-2016

---

## 当前状态

### 已确认的事实

| 指标 | TX 值 | RX VITERBI_IN 值 |
|------|-------|------------------|
| HT-SIG0 encoded[0:24] | `000000000000000000000000` | `100100000001001000111000` |
| HT-SIG1 encoded[48:96] | `000000000000000000001110...` | 不同 |
| Demapper output (eqbits48_a) | - | `100001011111011000000000` |

**结论：** Demapper 输出与 TX encoded 完全不匹配，问题在 demapper/equalization 阶段

### 关键代码位置

| 位置 | 功能 |
|------|------|
| `lib/frame_equalizer_impl.cc:1193` | `apply_htsig_rotation` - 旋转补偿 |
| `lib/frame_equalizer_impl.cc:1523-1539` | HT-SIG0 demapper - 提取 bits |
| `lib/frame_equalizer_impl.cc:1571-1582` | HT-SIG deinterleaver |
| `lib/frame_equalizer_impl.cc:2904` | Hhdr52 (L-LTF channel estimate) 传给 decode_htsig |

---

## Task 1: 验证 rotation compensation 方向

**Files:**
- Modify: `lib/frame_equalizer_impl.cc:1193`
- Debug: 添加临时调试输出

**问题分析：**
- TX: HT-SIG 乘以 j (+90°) 进行 QBPSK 旋转
- RX: `apply_htsig_rotation` 乘以 `rot` 进行补偿
- 如果 rot=1 (+90°), `in * rot = in * j` = 又旋转 +90° = 错误
- 正确应该是 `in * conj(rot) = in * (-j)` = 补偿 +90°

**验证步骤：**

- [ ] **Step 1: 理解当前代码的 rotation 逻辑**

```bash
grep -n "get_htsig_rotation_factor\|apply_htsig_rotation" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

当前代码：
```cpp
static gr_complex get_htsig_rotation_factor(int rotation) {
    case 0: return gr_complex(1.0f, 0.0f);   // 0°
    case 1: return gr_complex(0.0f, 1.0f);    // +90°
    case 2: return gr_complex(0.0f, -1.0f);   // -90°
    case 3: return gr_complex(-1.0f, 0.0f);   // 180°
}

static void apply_htsig_rotation(const gr_complex* in52, gr_complex* out52, int rotation) {
    gr_complex rot = get_htsig_rotation_factor(rotation);
    for (int i = 0; i < 52; i++) {
        out52[i] = in52[i] * rot;  // 当前代码
    }
}
```

- [ ] **Step 2: 分析 compensation 方向**

_TX 旋转：_ TX 的 QBPSK 旋转是乘以 j (+90°)
_RX 补偿：_ 应该除以 j，即乘以 -j (conj(j))

如果 `apply_htsig_rotation(1)` 返回 j：
- `out = in * j` = 又旋转 +90° = 错误

如果使用 `conj(rot)`：
- `out = in * conj(j) = in * (-j)` = 补偿 +90° = 正确

**结论：** `apply_htsig_rotation` 应该使用 `conj(rot)` 而不是 `rot`

- [ ] **Step 3: 临时修改并测试**

修改 `lib/frame_equalizer_impl.cc:1193`：
```cpp
out52[i] = in52[i] * std::conj(rot);
```

重新编译并运行：
```bash
cd /home/hy/gr-ieee802-11/build && rm -f lib/CMakeFiles/gnuradio-ieee802_11.dir/frame_equalizer_impl.cc.o && make -j4
```

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(VITERBI_IN|TX_HT_SIG)" | head -10
```

预期：VITERBI_IN 应该开始匹配 TX_HT_SIG encoded

---

## Task 2: 如果 Task 1 不能解决，检查 Hhdr52 的使用

**Files:**
- Debug: `lib/frame_equalizer_impl.cc`

**问题分析：**
HT-SIG 解码使用 L-LTF channel estimate (Hhdr52)。但 L-LTF 信道估计可能不适合 HT-SIG（因为 HT-SIG 有 QBPSK 旋转）。

- [ ] **Step 1: 检查 Hhdr52 的值**

在 `decode_htsig_from_rotated` 开头添加调试：
```cpp
fprintf(stderr, "[HT_SIG] H52[0:5]=");
for (int i = 0; i < 5; i++) {
    fprintf(stderr, "%.3f+%.3fi ", H52[i].real(), H52[i].imag());
}
fprintf(stderr, "\n");
```

- [ ] **Step 2: 考虑使用 unity channel estimate**

如果 Hhdr52 全是零或非常小，HT-SIG 解码可能不工作。可以尝试使用 unity H：
```cpp
// 在 decode_htsig_from_rotated 中
gr_complex unity_H = gr_complex(1.0f, 0.0f);
eq = safe_div(rx52_a[i], unity_H);  // 而不是 H52[i]
```

---

## Task 3: 验证 HT-SIG 解码成功

- [ ] **Step 1: 运行测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 30 python3 examples/test_constellation_real.py 2>&1 | grep -E "(d_have_ht_header=1|HT_SIG.*OK|mcs=|decode.*TRUE)" | head -10
```

预期：`d_have_ht_header=1` 和 `mcs=0`

---

## Task 4: 验证 HT-DATA 和 FCS

- [ ] **Step 1: 运行 MCS FCS 测试**

```bash
cd /home/hy/gr-ieee802-11 && source /home/hy/miniforge3/etc/profile.d/conda.sh && conda activate gnuradio && PYTHONPATH=/home/hy/gr-ieee802-11/build/python:/home/hy/gr-ieee802-11/grc:$PYTHONPATH LD_LIBRARY_PATH=/home/hy/gr-ieee802-11/build/lib:/home/hy/conda/envs/gnuradio/lib:$LD_LIBRARY_PATH timeout 60 python3 examples/test_mcs_fcs.py 2>&1 | grep -E "(MCS|FCS|PASS|FAIL)"
```

预期：所有 MCS 0-7 PASS

---

## Task 5: 提交修复

- [ ] **Step 1: 清理调试输出**

移除所有临时调试 `fprintf` 语句。

- [ ] **Step 2: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix: HT-SIG rotation compensation - use conj(rot) for proper undoing of TX QBPSK rotation"
```

---

## 关键文件路径

| 文件 | 说明 |
|------|------|
| `lib/frame_equalizer_impl.cc` | 包含 HT-SIG 解码所有逻辑 |
| `examples/test_constellation_real.py` | 实时星座图测试 |
| `examples/test_mcs_fcs.py` | MCS FCS 验证脚本 |

---

## 预期结果

- VITERBI_IN 匹配 TX encoded bits
- HT-SIG 解码成功 (d_have_ht_header=1, mcs=0)
- HT-DATA 误码 ~1-2/52
- 所有 MCS 0-7 FCS PASS
