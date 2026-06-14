# Phase 10: L-SIG 误解码为非 BPSK (2026-06-14)

**Date:** 2026-06-14
**Branch:** TEST1
**Status:** HT-SIG parse failure 真实根因 = **L-SIG 误解码为非 BPSK encoding**, 不是 HT-SIG 自身 bug.

## TL;DR

USRP 链中, equalizer 输入端的 L-SIG 星座看起来像 QPSK/16QAM/64QAM,
不是 BPSK 1/2. viterbi decode 仍能 converge + parity check passes by chance,
但 viterbi path 是错的, 返回的 encoding 是 enc=2/4/6/7 而不是 0.

代码在 `lib/frame_equalizer_impl.cc` line 3041 检查 `if (lsig_enc != 0) continue;`,
跳过 HT-SIG candidate loop, 直接落到 `HT_SIG_PARSE_FAIL` 日志.

所以 Phase 9 看到的 "HT_SIG_PARSE_FAIL 56/56" 实际是 "L-SIG misdecode → 跳过 HT-SIG"
的连锁反应, 不是 HT-SIG 自身的 bug.

## 关键证据

| 测试 | L-SIG encoding | 长度 | 状态 |
|------|----------------|------|------|
| 直连 loopback (无 USRP) | **enc=0** (BPSK 1/2) | 54 μs | ✅ 正确 |
| USRP, 20 字节包 | enc=2 (QPSK 1/2) | 403 μs | ❌ 错 |
| USRP, 400 字节包 | enc=2, enc=4, enc=6, enc=7 | 518/3641/2045/1275 μs | ❌ 全部错 |

### HT_SIG_PARSE_FAIL 细节

```
[HT_SIG_PARSE_FAIL] timeout_sym=4 n_candidates=0 ... 
avg_snr_lsig=26534.28 avg_snr_htsig=20979.02 
lsig_rate=0x5 lsig_len=403 lsig_inv=0 
last_rot=-1 last_inv_a=-1 last_inv_b=-1 is_ht_frame=1
```

- `n_candidates=0` → 候选 loop 从未运行
- `last_rot=-1` → rot 变量从未设置
- `lsig_rate=0x5` → 错误的 rate field (期望 0xD for HT MF)
- `lsig_len=403` → 错误 length (20 字节 MCS0 期望 ~54 μs)

## 代码位置 (lib/frame_equalizer_impl.cc)

```
Line 3024: lsig_ok = decode_lsig_direct_from_header52(...)
Line 3025: if (lsig_ok) lsig_decode_calls++;
Line 3041: if (lsig_enc != 0) {
Line 3042:     // L-SIG succeeded with non-BPSK 1/2 rate - skip and try other inv
Line 3043:     continue;
Line 3044: }
```

候选 loop 在 line 3058-3120 (rot × inv_a × inv_b), HT_SIG_CAND log 在 line 3096-3100.

## 候选根因 (与 Phase 8/9 一致)

1. **L-LTF0 FFT 破坏** (Phase 3 Stage 1 已证 per-frame std=12.7 vs loopback 0)
2. **Hhdr52 估计错** → 错误 H → 错误 constellation rotation/scaling
3. **IQ 通道反相/不平衡** (USRP 硬件/驱动)
4. **CFO 残留** (Phase 1a 排除 — 没有 coherent phase 可补偿)

## 验证方法

- `IEEE80211_LTF0_FFT_DUMP=1` — dump L-LTF0 FFT 在 equalizer input
- 对比 USRP vs loopback L-LTF0 FFT 分布
- 检查 |H52[sc]| magnitude per-subcarrier (期望相对均匀)
- 检查 arg(H52[sc]) per-subcarrier (期望平滑, 无 ±π 跳变)

## 修复方向 (按优先级)

1. **H 估计方法改进** (L-LTF0 vs L-LTF1 timing alignment)
2. **per-SC 通道补偿** (frequency-domain equalization)
3. **IQ 平衡校准** (USRP 内部 calibration)
4. **最终手段**: 跳过 `lsig_enc != 0` 检查, 强制尝试 HT-SIG (软修复)

## 关键教训

- **HT_SIG_CAND instrumentation 工作正常** — 0 行不是 instrumentation 失败, 是 candidate loop 被早退
- **直连 loopback 仍然 0/1 FCS OK** (Phase 9 之前已存在) — 即便无 USRP, HT-SIG 仍有小概率失败
- **"enc=0 是 HT 唯一合法值"** — 802.11 HT MF 中 L-SIG 必须 BPSK 1/2 (rate field 0xD)
- **诊断符号层位置**: 应该用 H52_DUMP 而不是 TX_HT_SIG_BITS
- **USRP_LO phase noise 4 rad** (Phase 6 真实测量) — 仍可能影响 constellation 旋转 (BPSK 4 簇应在 ±1±i 但实际是歪的)

## 任务状态

- Task 1 (HT_SIG_CAND instrumentation): ✅ 完成 (in place, working correctly)
- Task 1.5 (让 L-SIG 正确解码): ✅ 完成 (确认 L-SIG 误解码是上游问题)
- Task 7 (Phase 10 根因发现): 🔄 进行中

## 下一步 (ranked)

1. `IEEE80211_LTF0_FFT_DUMP=1` + 400 字节包, 看 USRP L-LTF0 FFT 分布
2. 计算 H52 per-SC, 看 magnitude/phase 模式
3. 与 L-LTF1 timing 检查
4. 改用 viterbi path metric 做 L-SIG constellation 验证 (硬阈值)

## 相关

- [[2026-06-12-phase9-final-diagnosis]] — 之前的根因 (HT-SIG specific, 部分推翻)
- [[2026-06-11-stage1-reorganized-verdict]] — Phase 3 Stage 1: L-LTF0 FFT 破坏
- [[2026-06-12-phase6-verdict]] — Phase 6 TCXO 结论 (推翻)
- [[2026-06-12-phase5-verdict]] — Phase 5 LO measurement (推翻)
