# HT Mixed Mode Project - 调试进展

> 更新：2026-04-06

## 项目背景

实现 gr-ieee802-11 的 HT Mixed 工作模式，解决 TX/RX 环回测试中 HT-SIG 解码失败的问题。

## 当前状态

### 问题描述
- HT-SIG 解码始终失败 (CRC 校验失败)
- HT-SIG FFT 输出在某些帧非零，某些帧为零
- L-SIG FFT 输出同样不一致
- HT-SIG 比特看起来像随机噪声而非有效的 HT-SIG 结构
- **FFT 相位与 FFT_LONG 完全不匹配**

### 已验证的情况

1. **TX 端正确**：HT-Mixed 模式前导码结构正确生成
2. **FFT_LONG 正确**：预计算的 FFT_LONG 与实际 L-LTF FFT 匹配（最大差异 0.000053）
3. **CFO 估算异常**：环回中 CFO 范围 0.001-0.04（应该接近 0）
4. **wifi_start 位置不一致**：出现在输出流的不同偏移位置 (0, 64, 1, 5, 8, 12, etc.)

### 代码修改历史

1. **COPY 状态修改**：将 `should_copy` 条件改为显式 if/else，显式处理 CP 过滤
2. **wifi_start 定位修改**：尝试将 wifi_start 放在 64-sample FFT 块边界，但未解决问题
3. **已回滚**：wifi_start 恢复到 rel=0 (输出位置 0)

### 关键发现

1. **L-LTF FFT 非零**：L-LTF1 的 FFT 输出显示非零复数值
2. **FFT_LONG 不匹配**：L-LTF1 raw FFT 与 FFT_LONG 参考值不匹配
3. **HT-SIG bits 随机**：HT-SIG 解码失败，比特看起来像随机噪声

### 根本原因分析

```
问题链路：
TX 信号 → sync_long (COPY 状态) → fft_vxx → frame_equalizer
                    ↓
           wifi_start 标签添加位置
                    ↓
           FFT 窗口是否对准 HT-SIG 符号？
```

**FFT 窗口对齐问题**：
- wifi_start 标签出现在不同位置 (0, 64, 1, 5, etc.)
- 这导致 FFT 窗口捕获不同的数据部分
- HT-SIG 符号位置 (rel=3,4) 有时捕获到有效数据，有时为零

## 下一步调试方向

### Task A: 验证 FFT 窗口对准
在 frame_equalizer 中添加调试，确认 wifi_start 位置与 FFT 输入的对应关系

### Task B: 验证 HT-SIG 符号位置映射
检查 d_sym_idx 与实际 FFT 输入的对应关系

### Task C: 验证 CFO 校正
CFO 估算值异常 (0.001-0.04)，可能导致相位旋转

## 测试命令

```bash
# 1. 构建
cd /home/hy/gr-ieee802-11/build && make -j4

# 2. 复制库
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/python3.8/site-packages/ieee802_11/libgnuradio-ieee802_11.so
cp build/lib/libgnuradio-ieee802_11.so.1.1.0git /home/hy/conda/envs/gnuradio/lib/libgnuradio-ieee802_11.so

# 3. 运行测试
cd /home/hy/gr-ieee802-11/build
LD_PRELOAD=../wrap_rpc2.so /home/hy/conda/envs/gnuradio/bin/python ../examples/test_loopback_noqt.py 2>&1 | tee /tmp/test_debug.log

# 4. 检查结果
grep "COPY_OUT\|EXTRACT_HT_SIG\|FFT_LONG\|HT-SIG\] parse" /tmp/test_debug.log | head -50
```

## 相关文件

| 文件 | 责任 |
|------|------|
| `lib/sync_long.cc` | 帧同步、CFO 估算、d_frame_start 检测、COPY 状态 |
| `lib/equalizer/ls.cc` | LS 信道估计，使用 FFT_LONG |
| `lib/frame_equalizer_impl.cc` | HT-SIG 解码、Viterbi、帧均衡 |
| `examples/test_loopback_noqt.py` | 环回测试脚本 |

## 提交历史

| Commit | 说明 |
|--------|------|
| 6e58e86 | fix: improve sync_long detection robustness |
| 11112c7 | fix: add HT Mixed L-LTF period detection |
| 79ee381 | fix: adjust d_frame_start by -16 |
| 5a9a263 | fix: restore original correlation detection |
| d81be6c | fix: Viterbi decoder generator polynomials |

## 文档

- 调试计划：`docs/superpowers/plans/2026-04-06-ht-mixed-htsig-debug.md`
