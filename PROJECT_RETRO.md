# HT Mixed Mode + LDPC 项目复盘

**日期**: 2026-05-29  
**分支**: merge  
**最终状态**: MCS0-7 Conv + LDPC 端到端测试 9/9 通过

---

## 1. 项目目标

在 gr-ieee802-11 (GNU Radio 802.11 实现) 上实现完整的 **HT Mixed Mode** (802.11n) 收发链路，并集成 **LDPC 编码** 支持。

**初始状态**:
- Legacy OFDM (48-carrier) 基本可用
- HT Mixed Mode 存在多帧 FCS 失败、等化异常、编码不匹配等问题
- LDPC 编解码器已集成但未经高阶调制验证

**目标状态**:
- HT 20MHz / 1SS / MCS0-7 完整收发
- Conv (BCC) 和 LDPC 双模式支持
- 星座图 GUI 显示 + 手动 MCS 切换

---

## 2. 时间线与关键里程碑

### Phase 1: 基础 HT 模式修复

| 日期 | Commit | 内容 |
|------|--------|------|
| 2026-05-15 | - | 发现多帧 FCS 问题：Frame 0 通过，Frame 1+ 失败 |
| 2026-05-18 | - | 根因定位：L-STF 干扰 + `ht_symbol_splitter` 偏移 bug |
| 2026-05-20 | - | **Multi-Frame FCS Fix**: 修复 SPLITTER offset，帧间 FCS 10/10 通过 |
| 2026-05-21 | - | **HT-LTF H Estimation Bug**: 修正 L-LTF 索引错位，解决 Frame 1 FCS 回归 |

### Phase 2: 64QAM 硬解调修复

| 日期 | Commit | 内容 |
|------|--------|------|
| 2026-05-25 | 6539c5d | `frame_equalizer`: 添加 64QAM 星座图支持 (n_bpsc=6) |
| 2026-05-26 | bcddfb2 | `constellation_64qam`: nearest-neighbor decision_maker 处理 TX/RX 幅度不匹配 |
| 2026-05-27 | eedeae3 | `frame_equalizer`: `kFftNormalize` 归一化修正等化后幅度 |
| 2026-05-28 | - | `decode_mac`: 动态 level 估计 + nearest-neighbor 64QAM 硬判决 |

**结果**: MCS5 Conv (64QAM 2/3) FCS OK

### Phase 3: LDPC 集成与修复

| 日期 | Commit | 内容 |
|------|--------|------|
| 2026-05-20 | - | LDPC 编解码器 (tavildar) C++ wrapper 集成 |
| 2026-05-25 | - | `mapper_impl`: `set_use_ldpc()` + TX LDPC 编码路径 |
| 2026-05-26 | - | `signal_field_impl`: HT-SIG bit 30 (adv_coding) 设置 |
| 2026-05-28 | - | 发现 batch 测试实际为 **MCS0 重复** (MAC 层 encoding 覆盖) |
| 2026-05-28 | - | `encoding_stripper` 修复 MAC 层覆盖问题 |
| 2026-05-29 | 80f095a | **LLR bit 映射修复** + QAM64_5_6 支持 + 动态 level |

**结果**: MCS0-7 LDPC 9/9 全部通过

---

## 3. 核心问题与解决方案

### 3.1 问题一：等化后符号幅度异常

**现象**: 等化后的 64QAM 星座点幅度 ~1.4，标准值应为 ~1.08，硬解调 55% 比特错误。

**根因**: TX IFFT 和 RX FFT 的缩放不一致。TX L-LTF 使用标准幅度，但数据符号经过 `mixed_mode_carrier_allocator` 和 IFFT 后有额外的 `1/sqrt(52)` 缩放，而 `frame_equalizer` 的 LS 等化器直接用 L-LTF 的信道估计去补偿数据符号，导致幅度偏差。

**修复**:
1. `frame_equalizer`: 添加 `kFftNormalize` 归一化因子（commit eedeae3）
2. `decode_mac`: 动态 level 估计（`max_abs/7`）替代固定 `sqrt(1/42)`
3. `constellation_64qam`: nearest-neighbor 硬判决替代阈值判决

**教训**: TX/RX 幅度一致性必须在等化器中处理，不能在解调端打补丁。

### 3.2 问题二：LLR bit 映射错误（LDPC 失败的真正原因）

**现象**: MCS5 Conv 通过但 MCS5 LDPC 完全失败。LLR 计算在 40dB SNR 下仍无法解码。

**根因**: `llr_64qam` 的 sign 比特（b0, b3）定义与 TX 端 `constellation_64qam_impl` 相反。RX 端的全局 LLR 反转（`-d_rx_llr[i]`）只修正了 sign 比特，但**破坏了幅度比特**（b1, b2, b4, b5），导致每个 64QAM 符号中 4/6 比特错误。

**修复**: 重写所有 LLR 函数（BPSK/QPSK/16QAM/64QAM）的 bit 映射，使其与 TX 端一致，移除全局反转。

**教训**: 软解调函数必须与硬解调/星座图映射共用同一份 bit 定义规范，不能各自为政。

### 3.3 问题三：MAC 层 Encoding 标签覆盖

**现象**: Batch 测试 "32/32 LDPC decode success"，但实际 TX 全部为 MCS0 (BPSK)。GUI 模式下下拉框切到 64QAM，RX 仍显示 BPSK。

**根因**: `lib/mac.cc` 在 PDU meta 中硬编码 `encoding=BPSK_1_2`，`mapper_impl::handle_msg()` 优先读取 PDU meta 标签而非 `set_encoding()` 的值。

**修复**: `test_mcs_end_to_end.py` 中添加 `encoding_stripper` 块，在 PDU 到达 mapper 前移除 `encoding` 和 `mcs` 标签。

**教训**: 系统边界处的标签/元数据优先级必须文档化，调试时要验证端到端的实际参数而非仅看接口设置。

### 3.4 问题四：QAM64_5_6 (MCS7) 支持缺失

**现象**: MCS7 报 `"wrong encoding"` 异常，修复后 RX 解析为 MCS0。

**根因**:
1. `chunks_to_symbols_impl.cc` 的 `switch(encoding)` 缺少 `QAM64_5_6` case
2. `signal_field_impl.cc` 的 `encoding_to_ht_mcs()` 缺少 MCS7 映射

**修复**: 两处均添加 `QAM64_5_6` 处理。

**教训**: 新增编码/MCS 时必须在全链路（mapper → signal_field → chunks_to_symbols → frame_equalizer → decode_mac）做一致性检查。

---

## 4. 技术债务与遗留问题

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| LDPC 零填充 | 中 | `ldpc_encode()` 输出 648/1296/1944 比特，但 `n_sym*n_cbps` 可能更大，差值用零填充。当前实现缩短信息位而非校验位，与 802.11n 标准不完全一致。 |
| 交织器硬编码 | 低 | `interleave()` 函数中 48/52-carrier 判断基于 `n_cbps` 的 magic number（48,96,192,288 和 52,104,208,312），不够健壮。 |
| GUI 无 headless 支持 | 低 | `test_mcs_end_to_end.py --gui` 需要 X11/Wayland，无法在无头环境运行。 |
| 硬解调 vs 软解调 | 低 | Conv 路径用硬解调，LDPC 路径用 LLR 软解调。理论上 LDPC 用软信息更好，但硬解调已通过验证，可作为 fallback。 |
| 动态 level 范围 | 低 | `compute_llr_block` 在 symbol block 级别计算 level，假设所有 symbol 有相同缩放。若信道频率选择性导致不同子载波缩放不同，可能仍有偏差。 |

---

## 5. 经验教训

### 5.1 调试方法论

1. **TX/RX 比特级对比是终极验证**: 从 `mapper` 输出 `/tmp/wifi_tx_punctured_all_bits.txt`，在 `decode_mac` 中对比 RX 硬判决比特，可以快速定位问题发生在哪一级（等化、解调、去交织、译码）。

2. **不要被表面成功率误导**: "32/32 LDPC decode success" 看起来很美好，但如果实际 TX 全部是 MCS0，这个成功率毫无意义。必须验证 TX 参数是否真正生效。

3. **分而治之**: 先验证 Conv 路径（硬解调 + Viterbi），再验证 LDPC 路径（LLR + 迭代译码）。Conv 通过说明等化和解调基本正确，LDPC 失败则定位到软信息计算。

### 5.2 设计层面

1. **单一事实来源**: `constellation_64qam_impl` 的 `d_constellation` 数组定义了 TX 端的 bit 映射，这个映射应该被所有 RX 组件（硬解调、LLR 计算、Viterbi、LDPC）共享，而不是各自实现。

2. **标签系统需要审计**: PMT 标签（`encoding`, `mcs`, `use_ldpc`）在全链路中传递，优先级规则复杂。建议添加一个 "tag audit" 调试模式，在关键节点打印所有标签。

3. **单元测试不足**: 当前只有 `test_ldpc_codec` 单元测试。应该为 `llr_demod`、`ht_deinterleave`、`mcs_to_encoding` 等函数添加独立的单元测试，避免回归。

---

## 6. 验证矩阵

| MCS | 调制 | 码率 | Conv | LDPC | 备注 |
|-----|------|------|------|------|------|
| 0 | BPSK | 1/2 | ✅ | ✅ | Baseline |
| 1 | QPSK | 1/2 | ✅ | ✅ | |
| 2 | QPSK | 3/4 | ✅ | ✅ | |
| 3 | 16QAM | 1/2 | ✅ | ✅ | |
| 4 | 16QAM | 3/4 | ✅ | ✅ | |
| 5 | 64QAM | 2/3 | ✅ | ✅ | 硬解调+LDPC 均通过 |
| 6 | 64QAM | 3/4 | ✅ | ✅ | |
| 7 | 64QAM | 5/6 | ✅ | ✅ | QAM64_5_6 支持已添加 |

---

## 7. 关键文件清单

**修复文件**:
- `lib/llr_demod.h` / `lib/llr_demod.cc` — LLR bit 映射 + 动态 level
- `lib/decode_mac.cc` — 解码主逻辑 (硬解调 + LDPC 路径)
- `lib/chunks_to_symbols_impl.cc` — QAM64_5_6 case
- `lib/signal_field_impl.cc` — HT-MCS7 映射
- `lib/mapper_impl.cc` — TX 编码 + LDPC 路径
- `lib/frame_equalizer_impl.cc` — HT-SIG 解码 + 等化
- `test_mcs_end_to_end.py` — 端到端测试 + GUI

**诊断工具**:
- `/tmp/wifi_tx_punctured_all_bits.last.txt` — TX 参考比特
- `/tmp/wifi_tx_interleaved_all_bits.last.txt` — TX 交织后参考
- `/tmp/wifi_rx_hard_all_bits.last.txt` — RX 硬判决比特
- `/tmp/wifi_rx_deintl_all_bits.last.txt` — RX 去交织比特

---

## 8. 下一步建议

1. **SNR 扫频测试**: 在 batch 测试中加入 SNR 从 0 到 40dB 的扫描，绘制 BER/FER 曲线，验证与 802.11n 理论曲线的吻合度。

2. **多径信道测试**: 当前仅在 AWGN 信道下验证。需要添加多径信道（如 802.11n Channel Model B/D）测试 frame_equalizer 的鲁棒性。

3. **LDPC  shortening/puncturing 标准化**: 当前 `ldpc_encode()` 的实现与 802.11n 标准有偏差（直接截断而非 shortening），需要按标准实现完整的 shortening 和 puncturing 逻辑。

4. **MIMO 扩展**: 当前仅实现 1SS（单空间流）。MIMO 扩展需要修改 `frame_equalizer` 支持多流信道估计和 `mapper` 支持空间映射。
