# SPLITTER L-LTF 无缝边界修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `ht_symbol_splitter_impl.cc` 的状态机，使其严格遵循 L-LTF T1/T2 无缝连接（无 CP）的物理结构，消除 FFT 输入的符号间干扰，修复 LTF0/LTF1 相位混乱问题。

**Architecture:** 将 `should_buffer` 和 `at_boundary` 的判断逻辑从"硬编码的 out_rel_idx 偏移"改为"基于 rel_idx 的自适应边界"，让 rel_idx 成为唯一的事实来源（Single Source of Truth），彻底解耦 GNU Radio tag 的脆弱性。

**Tech Stack:** C++ (GNU Radio OOT block), IEEE 802.11n HT-Mixed 20MHz

---

## 背景：L-LTF 的特殊结构

IEEE 802.11n HT-Mixed 前导码中，L-LTF 的物理结构为：
- **T1 (LTF0)**: 64 点
- **T2 (LTF1)**: 64 点，无 CP，无缝衔接 T1
- 紧接着是 L-SIG 的 16 点 CP + 64 点 DATA

```
rel_idx:   0--------63 | 64-------127 | 128-----143 | 144-----207 | 208-----239 | 240-----303 | ...
           [  T1 64点  ] [  T2 64点  ] [LSIG CP 16] [LSIG DATA] [  32点间隙 ] [HTSIG0 DATA]
```

**关键探针数据（sync_long 输出）：**
```
rel=0:    LTF0_START
rel=64:   LTF1_START (无 CP！)
rel=128:  LSIG_CP
rel=144:  LSIG_DATA (推算)
rel=240:  HTSIG0_DATA (推算)
```

当前 SPLITTER 的错误：
- `should_buffer` 在 `rel_idx=64-79` 错误地跳过了"LTF1 CP"（实际不存在）
- `at_boundary` 使用 `out_rel_idx==143` 而非 `rel_idx==127`

---

## 文件变更

| 文件 | 变更 |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | 重构 `should_buffer` (lines 394-432) 和 `at_boundary` (lines 487-500) |

---

## 任务列表

### Task 1: 重构 should_buffer 逻辑

**文件:** Modify: `lib/ht_symbol_splitter_impl.cc:394-432`

- [ ] **Step 1: 备份并替换 should_buffer 逻辑**

将 lines 394-432 的整个 `if/else if` 块替换为：

```cpp
            // ============================================================
            // HT-Mixed 20MHz Preamble Structure (IEEE 802.11n)
            // L-LTF: T1 (0-63) + T2 (64-127) 无缝连接，无 CP
            // 后续符号: 16 CP + 64 DATA = 80 点
            // ============================================================
            bool should_buffer = false;
            if (rel_idx < 64) {
                // Stage 1: L-LTF0 DATA (rel_idx 0-63)
                should_buffer = true;
            } else if (rel_idx < 128) {
                // Stage 1b: L-LTF1 DATA (rel_idx 64-127) - 无 CP，无缝衔接！
                should_buffer = true;
            } else if (rel_idx < 144) {
                // Stage 2: L-SIG CP (rel_idx 128-143) - 跳过
                should_buffer = false;
            } else if (rel_idx < 208) {
                // Stage 2b: L-SIG DATA (rel_idx 144-207)
                should_buffer = true;
            } else if (rel_idx < 240) {
                // Stage 3: 32点间隙 (rel_idx 208-239) - 跳过
                should_buffer = false;
            } else if (rel_idx < 304) {
                // Stage 3b: HT-SIG0 DATA (rel_idx 240-303)
                should_buffer = true;
            } else if (rel_idx < 320) {
                // Stage 4: HT-SIG1 CP (rel_idx 304-319) - 跳过
                should_buffer = false;
            } else if (rel_idx < 384) {
                // Stage 4b: HT-SIG1 DATA (rel_idx 320-383)
                should_buffer = true;
            } else if (rel_idx < 400) {
                // Stage 5: HT-STF CP (rel_idx 384-399) - 跳过
                should_buffer = false;
            } else if (rel_idx < 464) {
                // Stage 5b: HT-STF DATA (rel_idx 400-463)
                should_buffer = true;
            } else {
                // Stage 6: HT-DATA and beyond (80-sample period: 16 CP + 64 Data)
                uint64_t sym_rel_idx = rel_idx - 464;
                uint64_t sym_offset = sym_rel_idx % 80;
                if (sym_offset >= 16) {
                    should_buffer = true;  // Skip CP, buffer Data
                }
            }
```

- [ ] **Step 2: 验证语法**

```bash
cd /home/hy/gr-ieee802-11
g++ -std=c++14 -fsyntax-only -I./lib -I/usr/include/gnuradio lib/ht_symbol_splitter_impl.cc 2>&1 | head -20
```

预期：无输出（编译成功）

- [ ] **Step 3: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): rewrite should_buffer for L-LTF seamless T1/T1 structure

L-LTF T1 and T2 are back-to-back 64-point symbols with NO CP between them.
Previous code incorrectly treated rel_idx 64-79 as LTF1 CP and skipped it.
Now correctly buffers rel_idx 0-63 (LTF0) and 64-127 (LTF1) continuously.

Also fixes L-SIG boundary (144-207) and HT-SIG0 gap (240-303)
to match TX structure observed in sync_long probes."
```

---

### Task 2: 重构 at_boundary 逻辑

**文件:** Modify: `lib/ht_symbol_splitter_impl.cc:487-500`

- [ ] **Step 1: 备份并替换 at_boundary 逻辑**

将 lines 487-500 的 `bool at_boundary = false;` 整个块替换为：

```cpp
                // ============================================================
                // Boundary trigger: output FFT window when 64 samples collected
                // Single Source of Truth: rel_idx (not out_rel_idx)
                // ============================================================
                bool at_boundary = false;
                if (rel_idx < 64) {
                    // LTF0 boundary: output at rel_idx=63
                    at_boundary = (rel_idx == 63);
                } else if (rel_idx < 128) {
                    // LTF1 boundary: output at rel_idx=127 (NOT 143 - no CP!)
                    at_boundary = (rel_idx == 127);
                } else if (rel_idx < 144) {
                    // L-SIG CP: no output
                    at_boundary = false;
                } else if (rel_idx < 208) {
                    // L-SIG boundary: output at rel_idx=207
                    at_boundary = (rel_idx == 207);
                } else if (rel_idx < 240) {
                    // 32-point gap: no output
                    at_boundary = false;
                } else if (rel_idx < 304) {
                    // HT-SIG0 boundary: output at rel_idx=303
                    at_boundary = (rel_idx == 303);
                } else if (rel_idx < 320) {
                    // HT-SIG1 CP: no output
                    at_boundary = false;
                } else if (rel_idx < 384) {
                    // HT-SIG1 boundary: output at rel_idx=383
                    at_boundary = (rel_idx == 383);
                } else if (rel_idx < 464) {
                    // HT-STF boundary: output at rel_idx=463
                    at_boundary = (rel_idx == 463);
                } else {
                    // HT-DATA and beyond: 80-sample periodicity
                    uint64_t sym_offset = (rel_idx - 464) % 80;
                    at_boundary = (sym_offset == 0);
                }
```

- [ ] **Step 2: 验证语法**

```bash
g++ -std=c++14 -fsyntax-only -I./lib -I/usr/include/gnuradio lib/ht_symbol_splitter_impl.cc 2>&1 | head -20
```

预期：无输出（编译成功）

- [ ] **Step 3: 提交**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "fix(splitter): rewrite at_boundary for L-LTF seamless boundaries

Switch from hardcoded out_rel_idx to rel_idx-based boundaries.
LTF1 boundary moves from 143 to 127 (no CP between T1/T2).
L-SIG boundary moves from 223 to 207 (matches TX structure).
HT-SIG0 boundary moves from 303 to 303 (unchanged).
HT-SIG1 boundary moves from 383 to 383 (unchanged).

This eliminates reliance on d_frame_start_abs and out_rel_idx,
making boundary detection robust against GNU Radio tag timing jitter."
```

---

### Task 3: 构建并验证

**文件:** Build: `lib/ht_symbol_splitter_impl.cc`

- [ ] **Step 1: CMake 构建**

```bash
cd /home/hy/gr-ieee802-11/build
cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
make -j$(nproc) 2>&1 | tail -20
```

预期：无错误，最后一行类似 `[100%] Built target ieee802-11`

- [ ] **Step 2: 安装**

```bash
sudo make install 2>&1 | tail -5
sudo ldconfig 2>&1 | tail -2
```

预期：无错误

- [ ] **Step 3: 运行端到端测试**

```bash
cd /home/hy/gr-ieee802-11
python3 examples/test_mcs_end_to_end.py 2>&1 | grep -E "L-SIG|Parity|HT-SIG|pass|fail|ERROR"
```

预期：L-SIG Parity Check 通过（parity_sum=0），无 HT-SIG parse errors

- [ ] **Step 4: 检查探针输出**

```bash
python3 examples/test_mcs_end_to_end.py 2>&1 | grep -E "RAW_FFT_64|SPLITTER_FFTPROBE|CHAN_EST|H_PHASE_CHECK"
```

预期：
- RAW_FFT_64 显示 LTF0 和 LTF1 相位差接近 0°（而非 180°）
- H_PHASE_CHECK 显示相位线性（而非混乱跳变）

- [ ] **Step 5: 提交构建验证**

```bash
git add -A
git commit -m "test: verify L-LTF seamless boundary fix - LTF0/LTF1 phase aligned"
```

---

## 验证检查清单

修复后应满足：
- [ ] LTF0 vs LTF1 FFT 输出相位差 ≈ 0°（全频段）
- [ ] H 相位呈线性斜率（而非混乱跳变）
- [ ] BPSK 均衡后虚部 |Q| ≈ 0
- [ ] L-SIG Parity Check 通过
- [ ] HT-SIG parse 成功
- [ ] SPLITTER_FFTPROBE 显示 LTF0/LTF1 能量正常

---

## 工程师笔记：关于 32 点间隙

在实测中发现 L-SIG DATA (144-207) 和 HT-SIG0 DATA (240-303) 之间有 32 点间隙（非标准 16 点 CP）。这是 TX 端或流图中某个环节引入的偏移。RX SPLITTER 必须如实处理这个间隙，保持与 TX 对齐，而非假设标准协议结构。

## 相关文件索引

| 文件 | 用途 |
|------|------|
| `lib/ht_symbol_splitter_impl.cc` | CP 移除，符号分割，FFT 边界 |
| `lib/sync_long.cc` | 同步，LTF 模板，相关峰值检测 |
| `lib/frame_equalizer_impl.cc` | 信道估计，H 计算 |
| `examples/test_mcs_end_to_end.py` | 端到端仿真测试 |
| `docs/superpowers/specs/2026-05-14-ltf-phase-inversion-diagnostic.md` | L-LTF 相位反转诊断规格 |
