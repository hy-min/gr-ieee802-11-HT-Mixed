# QBPSK 星座图能量投票检测实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 在 frame_equalizer 中实现基于星座图能量投票的 QBPSK 检测，以区分 Legacy 和 HT-Mixed 帧类型，替代当前不可靠的时域相关性检测。

**架构:** 帧类型判决权从时域 sync_long 移交给频域 frame_equalizer。在 L-SIG 解码后，计算 HT-SIG 符号的 E_I 和 E_Q 能量比值：若 E_Q > E_I 则判定为 HT-Mixed 模式，若 E_I ≫ E_Q 则为 Legacy 帧。

**技术栈:** GNU Radio C++ block (frame_equalizer), IEEE 802.11n 星座图 (QBPSK), Viterbi 解码

---

## 背景：为什么需要 QBPSK 能量投票？

### 时域相关性检测的固有问题

802.11n HT-Mixed  preamble 中 L-LTF 包含 64 采样周期重复，Legacy L-LTF 也是 64 周期。在多径衰落环境下，`diff=64`（Legacy）和 `diff=80`（HT Mixed）的峰值会糊成一个**相关性高原**，导致 `search_frame_start()` 频繁误判。

### 频域正交调制提供的物理级判决

IEEE 802.11n 标准在频域埋入了明确区分机制：

| 字段 | 调制方式 | 能量分布 |
|------|----------|----------|
| L-SIG | 标准 BPSK | 能量集中在 **实轴 (I)** |
| HT-SIG | QBPSK (逆时针90°) | 能量集中在 **虚轴 (Q)** |

这意味着解调后：
- `E_I = Σ|Re(X_k)|²` 对 L-SIG 很大，对 HT-SIG 很小
- `E_Q = Σ|Im(X_k)|²` 对 HT-SIG 很大，对 L-SIG 很小

**判决规则：**
- 若 `E_Q > E_I`（比值超过阈值，如 2:1）→ **HT-Mixed 帧**
- 若 `E_I ≫ E_Q` 或 `E_I ≈ E_Q` → **Legacy 帧**

---

## 文件结构

```
lib/frame_equalizer_impl.cc    # 主要修改：添加 QBPSK 能量投票函数
                                # 修改 decode_htsig() 添加能量验证步骤
                                # 修改 HT/LEGACY 状态机翻转逻辑
lib/frame_equalizer_impl.h     # 添加能量投票函数声明
lib/decode_mac.cc              # 可能需要修改 HT-SIG 后的状态处理
python/bindings/frame_equalizer_python.cc  # 如果添加新参数
examples/wifi_loopback.grc     # 测试验证
```

---

## 任务分解

### Task 1: 添加 QBPSK 能量投票函数

**文件:**
- 修改: `lib/frame_equalizer_impl.cc` (约 line 1000 附近，在 `detect_htsig_rotation()` 之后)
- 修改: `lib/frame_equalizer_impl.h`

**关键数据可用位置:**
- `d_early_eqsym[kHtSig0Rel]` 和 `d_early_eqsym[kHtSig1Rel]` — 52 个复数值（均衡后）
- `d_early_eqsym[kLSigRel]` — L-SIG 均衡后符号（用于基准比较）

- [ ] **Step 1: 阅读当前 `detect_htsig_rotation()` 实现**

```bash
sed -n '1013,1050p' /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

- [ ] **Step 2: 在 `frame_equalizer_impl.h` 中添加能量投票函数声明**

在私有方法区域找到合适位置添加：

```cpp
/**
 * 计算 52 子载波的能量分布
 * @param eq52 均衡后的 52 个复数值（48 数据 + 4 pilot）
 * @param Esum_I 输出：实轴能量 Σ|Re|²
 * @param Esum_Q 输出：虚轴能量 Σ|Im|²
 */
void compute_subcarrier_energy(const gr_complex* eq52, double& Esum_I, double& Esum_Q);
```

- [ ] **Step 3: 在 `frame_equalizer_impl.cc` 中实现能量计算函数**

在 `detect_htsig_rotation()` 函数之后添加（约 line 1050）：

```cpp
void frame_equalizer_impl::compute_subcarrier_energy(const gr_complex* eq52, double& Esum_I, double& Esum_Q)
{
    Esum_I = 0.0;
    Esum_Q = 0.0;
    for (int i = 0; i < 48; i++) {  // 48 数据子载波（不含 pilot）
        Esum_I += (double)eq52[i].real() * eq52[i].real();
        Esum_Q += (double)eq52[i].imag() * eq52[i].imag();
    }
}
```

- [ ] **Step 4: 添加 QBPSK 能量投票函数**

在 `compute_subcarrier_energy()` 之后添加：

```cpp
/**
 * 基于星座图能量投票判断 QBPSK 旋转方向
 * @param eq52 均衡后的 52 个复数值
 * @return 旋转方向: 0=0°(标准BPSK), 1=+90°(QBPSK逆时针), 2=-90°(QBPSK顺时针), 3=180°
 *
 * 原理: QBPSK 将标准 BPSK 逆时针旋转 90°，能量从实轴转移到虚轴
 * - E_Q > E_I → +90° 旋转 (HT-SIG 使用)
 * - E_I > E_Q → 0° 或 180° (Legacy 使用)
 */
int vote_qbpsk_rotation(const gr_complex* eq52)
{
    double E_I, E_Q;
    compute_subcarrier_energy(eq52, E_I, E_Q);

    // 计算比值
    double ratio = (E_I > 1e-10) ? E_Q / E_I : 0.0;

    fprintf(stderr, "[QBPSK_VOTE] E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I, E_Q, ratio);

    // HT-SIG QBPSK: E_Q 应该远大于 E_I (典型比值 > 2.0)
    if (ratio > 2.0) {
        return 1;  // +90° 旋转
    }
    // Legacy BPSK: E_I 主导或两者相当
    if (E_I > E_Q) {
        return 0;  // 0° 或 180°
    }
    // 两者能量相近，保守返回 0
    return 0;
}
```

- [ ] **Step 5: 提交**

```bash
git add lib/frame_equalizer_impl.cc lib/frame_equalizer_impl.h
git commit -m "feat: add QBPSK energy voting function for HT-SIG detection"
```

---

### Task 2: 在 HT-SIG 解码流程中集成能量投票

**文件:**
- 修改: `lib/frame_equalizer_impl.cc` (decode_htsig_direct_from_header52 函数，约 line 1965)

**当前问题:** `decode_htsig_direct_from_header52()` 使用 `detect_htsig_rotation()` 仅分析 pilot phase，未使用星座图能量验证。

- [ ] **Step 1: 阅读 decode_htsig 函数结构**

```bash
sed -n '1965,2050p' /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

- [ ] **Step 2: 找到 rotation 检测后的能量验证逻辑插入点**

在 `detect_htsig_rotation()` 调用之后（假设返回 `rot`），添加能量投票验证：

```cpp
// 原始 pilot-based rotation 检测
int rot = detect_htsig_rotation(rot_htsig0);
fprintf(stderr, "[HT_SIG] pilot-based rotation=%d\n", rot);

// NEW: 添加能量投票验证
int energy_rot = vote_qbpsk_rotation(rot_htsig0);
fprintf(stderr, "[HT_SIG] energy-based rotation=%d\n", energy_rot);

// 如果 pilot 和能量投票不一致，优先相信能量投票（更可靠）
if (energy_rot != rot && energy_rot == 1) {
    fprintf(stderr, "[HT_SIG] Energy vote overrides pilot rotation: %d -> %d\n", rot, energy_rot);
    rot = energy_rot;
}
```

- [ ] **Step 3: 编译验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

预期: 编译成功

- [ ] **Step 4: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "feat: integrate QBPSK energy voting into HT-SIG decoding"
```

---

### Task 3: 添加 Legacy/HT 帧类型自动检测与状态机翻转

**文件:**
- 修改: `lib/frame_equalizer_impl.cc` (general_work 中 HT-SIG 解析前)

**目的:** 在 L-SIG 解码后（rel_idx=2），自动检测下一个符号是 Legacy Data 还是 HT-SIG1。

- [ ] **Step 1: 理解当前状态机**

```bash
sed -n '2009,2100p' /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

关注: `d_have_ht_header` 标志何时设置，状态如何翻转。

- [ ] **Step 2: 在 HT-SIG 解析前添加帧类型检测逻辑**

在 `decode_htsig_direct_from_header52()` 调用前（约 line 2010）添加：

```cpp
// ===== Legacy vs HT-Mixed 帧类型检测 =====
// 在 L-SIG (rel_idx=2) 之后，检测 rel_idx=3 的符号类型
// 如果 E_Q > E_I (QBPSK)，则为 HT-SIG1 → 设置 HT 模式
// 如果 E_I ≫ E_Q (BPSK)，则为 Legacy Data → 保持 Legacy 模式
if (d_sym_idx == kHtSig0Rel && d_early_eqsym_valid[kLSigRel]) {
    double E_I_ls, E_Q_ls, E_I_ht, E_Q_ht;

    // 计算 L-SIG 能量分布（基准）
    compute_subcarrier_energy(d_early_eqsym[kLSigRel], E_I_ls, E_Q_ls);

    // 计算 HT-SIG0 能量分布
    compute_subcarrier_energy(d_early_eqsym[kHtSig0Rel], E_I_ht, E_Q_ht);

    double ratio_ls = (E_I_ls > 1e-10) ? E_Q_ls / E_I_ls : 0.0;
    double ratio_ht = (E_I_ht > 1e-10) ? E_Q_ht / E_I_ht : 0.0;

    fprintf(stderr, "[FRAME_DETECT] L-SIG: E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I_ls, E_Q_ls, ratio_ls);
    fprintf(stderr, "[FRAME_DETECT] HT-SIG0: E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I_ht, E_Q_ht, ratio_ht);

    // 如果 HT-SIG0 的 E_Q/E_I 比值显著高于 L-SIG，判定为 HT-Mixed 帧
    if (ratio_ht > 2.0 && ratio_ht > ratio_ls * 2.0) {
        fprintf(stderr, "[FRAME_DETECT] Detected HT-Mixed frame (QBPSK rotation)\n");
        d_is_ht_frame = true;
    } else {
        fprintf(stderr, "[FRAME_DETECT] Detected Legacy frame\n");
        d_is_ht_frame = false;
    }
}
```

- [ ] **Step 3: 在 frame_equalizer_impl.h 中添加成员变量**

找到私有成员区域添加：

```cpp
bool d_is_ht_frame;  // 帧类型检测结果：true=HT-Mixed, false=Legacy
```

在构造函数初始化列表中添加：

```cpp
d_is_ht_frame(false),
```

- [ ] **Step 4: 编译验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

预期: 编译成功

- [ ] **Step 5: 提交**

```bash
git add lib/frame_equalizer_impl.cc lib/frame_equalizer_impl.h
git commit -m "feat: add Legacy/HT-Mixed auto-detection via QBPSK energy voting"
```

---

### Task 4: 修改 HT-SIG 解析条件使用 d_is_ht_frame

**文件:**
- 修改: `lib/frame_equalizer_impl.cc`

**目的:** 只有 `d_is_ht_frame=true` 时才执行 HT-SIG 解码，避免对 Legacy 帧误解析。

- [ ] **Step 1: 找到 HT-SIG 解析条件**

```bash
grep -n "ht_parse_condition" /home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc
```

- [ ] **Step 2: 修改解析条件添加 d_is_ht_frame 检查**

将：
```cpp
const bool ht_parse_condition =
    !d_have_ht_header &&
    d_sym_idx >= kHtSig1Rel &&
    ...
```

修改为：
```cpp
const bool ht_parse_condition =
    !d_have_ht_header &&
    d_is_ht_frame &&     // NEW: 只对 HT-Mixed 帧解析 HT-SIG
    d_sym_idx >= kHtSig1Rel &&
    ...
```

- [ ] **Step 3: 编译验证**

```bash
cd /home/hy/gr-ieee802-11/build && make -j4 2>&1 | tail -10
```

- [ ] **Step 4: 提交**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "feat: gate HT-SIG parsing on d_is_ht_frame detection"
```

---

### Task 5: 复制库并运行 Loopback 测试

- [ ] **Step 1: 复制库到安装路径**

```bash
cp /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/
cp /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/
```

- [ ] **Step 2: 运行 loopback 测试**

```bash
cd /home/hy/gr-ieee802-11/build
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | grep -E "FRAME_DETECT|QBPSK_VOTE|HT-SIG\]|parsed OK|parse failed"
```

预期输出应包含：
```
[FRAME_DETECT] L-SIG: E_I=xxx E_Q=xxx ratio=xxx
[FRAME_DETECT] HT-SIG0: E_I=xxx E_Q=xxx ratio=xxx
[FRAME_DETECT] Detected HT-Mixed frame (QBPSK rotation)
```
或 Legacy 帧的检测结果。

---

## 验证清单

| 步骤 | 命令 | 预期结果 |
|------|------|----------|
| 编译 | `make -j4` | 无错误 |
| QBPSK 能量投票 | `grep QBPSK_VOTE` | 显示 E_I, E_Q, ratio |
| 帧类型检测 | `grep FRAME_DETECT` | 显示 HT-Mixed 或 Legacy |
| HT-SIG 解析 | `grep "HT-SIG\]"` | parsed OK 或 parse failed |
| FFT 输出 | `grep "FFT"` | 非零采样值 |

---

## 关键公式参考

**星座图能量计算：**
```
E_I = Σ_{k∈data} |Re(X_k)|²
E_Q = Σ_{k∈data} |Im(X_k)|²
```

**QBPSK 旋转判决：**
```
ratio = E_Q / E_I
ratio > 2.0 → +90° 旋转 → HT-SIG (QBPSK)
ratio < 0.5 → 0° 或 180° → Legacy (BPSK)
```

---

## 文件修改总结

| 文件 | 修改内容 |
|------|----------|
| `lib/frame_equalizer_impl.h` | 添加 `compute_subcarrier_energy()` 声明和 `d_is_ht_frame` 成员 |
| `lib/frame_equalizer_impl.cc` | 添加 `compute_subcarrier_energy()` 和 `vote_qbpsk_rotation()` 实现；集成到 HT-SIG 解码流程；添加帧类型自动检测 |
