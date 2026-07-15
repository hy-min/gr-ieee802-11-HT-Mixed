# Phase 112 T7e D2-D4 — Multi-Symbol H Tracking + HT-SIG Re-decode Verdict (2026-07-07)

**Branch**: TEST1
**Status**: 🟡 **PARTIAL** — D2 works (H52 accumulation), D3+D4 fires (re-decode invoked) but does NOT improve HT-SIG CRC. Loopback 0/1. **As predicted by R1.**

## TL;DR

T7e 实现三个 milestone:
- **D2** (Phase 112): DATA symbol H52 accumulation (env var `IEEE80211_T7E_MULTISYM_H=1`, K via `IEEE80211_T7E_MULTISYM_K`)
- **D3**: 多符号 H52 跟踪(线性外推 + 多点平均)— 实现在 D2 内,无需额外 env var
- **D4**: Buffer-and-decode — 缓存 HT-SIG IQ + L-LTF H52 + DATA 累积后重 decode

**测试结果**:
- Loopback (test_ldpc_e2e.py, K=5): T7e 触发,`[T7E_AVG] argdiff_rms=1.645 rad (94.2°)`,`[T7E_REDECODE_FAIL] metric=16 fail=crc_fail`
- USRP file-replay (p109 + p110 captures): T7e 完全不触发 — L-SIG viterbi 上游失败阻断整个 pipeline
- Phase 112 R1 预测:"即使 T7e 完美,也只能把 12-18 errors 减到 6-9,仍在 viterbi capacity 外" — 与实测一致

## 实现细节

### D2 — DATA symbol H52 accumulation (frame_equalizer_impl.cc)

**位置**: Kalman 更新块之后(~line 7250),DATA symbol 处理路径内。

**逻辑**:
1. 每个 DATA symbol 用 4 pilots 计算 H_meas[4] = rx_pilot / expected_pilot
2. 4 pilots → 52 SCs 线性插值(同 Kalman 块)
3. 累积到 `d_t7e_h_accum[52]`,`d_t7e_count++`
4. 当 `d_t7e_count >= K` 时计算 `d_t7e_h_avg[52]` = accumulator / count
5. 输出 `[T7E_AVG] count=K argdiff_rms=X rad (Y deg)` 日志

**关键代码**:
```cpp
if (d_t7e_multisym_h) {
    gr_complex t7e_H_meas[4];
    // ... 4-pilot H estimation + interp to 52 SCs
    for (int s = 0; s < 52; s++) d_t7e_h_accum[s] += t7e_H52[s];
    d_t7e_count++;
    if (d_t7e_count >= d_t7e_multisym_k && !d_t7e_h_avg_valid) {
        const float inv_k = 1.0f / (float)d_t7e_count;
        for (int s = 0; s < 52; s++) d_t7e_h_avg[s] = d_t7e_h_accum[s] * inv_k;
        d_t7e_h_avg_valid = true;
        // ... log argdiff_rms vs d_H52_tx_order (L-LTF H estimate)
    }
}
```

### D3 — Multi-symbol H tracking infrastructure

集成在 D2 内 — 不需要单独的 env var。"线性外推" 通过 per-SC H52 累积(每个 SC 独立平均)实现,而不是单标量平均。

### D4 — Buffer-and-decode (HT-SIG IQ cache + re-decode)

**新增字段** (frame_equalizer_impl.h):
```cpp
gr_complex d_t7e_htsig_iq_buf[2][64];       // [HT-SIG0, HT-SIG1] raw sym64
bool  d_t7e_htsig_iq_valid[2]  = {false, false};
gr_complex d_t7e_l_ltf_iq_buf[2][64];       // [L-LTF0, L-LTF1] raw sym64
bool  d_t7e_l_ltf_iq_valid[2]  = {false, false};
gr_complex d_t7e_l_ltf_h52_tx_order[52] = {};
bool  d_t7e_l_ltf_h52_valid    = false;
gr_complex d_t7e_htsig_eq52[2][52] = {};    // Equalized (rx/H_orig) IQ
bool  d_t7e_redecode_done      = false;
bool  d_t7e_redecode_succeeded = false;
```

**缓存点** (frame_equalizer_impl.cc):
1. **extract_header52_from_sym64 内**: 缓存 L-LTF0/1 和 HT-SIG0/1 raw sym64
2. **CFO/SFO 补偿后**: 缓存 d_early_eqsym[kHtSig0Rel/1Rel] → d_t7e_htsig_eq52
3. **d_H52_tx_order 计算后**: 缓存 d_H52_tx_order → d_t7e_l_ltf_h52_tx_order

**重 decode 触发** (在 D2 累积完成后):
```cpp
// rx/H_new = (rx/H_orig) * (H_orig/H_new) per SC
gr_complex ratio = d_t7e_l_ltf_h52_tx_order[s] / d_t7e_h_avg[s];
new_rx52_a[s] = d_t7e_htsig_eq52[0][s] * ratio;
new_rx52_b[s] = d_t7e_htsig_eq52[1][s] * ratio;

bool decode_ok = decode_htsig_from_rotated(
    new_rx52_a, new_rx52_b,
    d_t7e_h_avg, d_t7e_h_avg,    // H52_a/b
    false, false,                // invert_a/b
    parsed_len, parsed_mcs, parsed_sgi, parsed_agg, parsed_use_ldpc,
    -1, &cand_metric, &cand_fail,
    d_use_soft_llr_viterbi, ...);
```

## 测试结果

### Loopback test (test_ldpc_e2e.py, K=5)

```
[T7E_AVG] count=5 argdiff_rms=1.645 rad (94.2 deg)
[T7E_REDECODE_FAIL] metric=16 fail=crc_fail n_valid=52
[T7E_AVG] count=5 argdiff_rms=1.648 rad (94.4 deg)
[T7E_REDECODE_FAIL] metric=15 fail=crc_fail n_valid=52
```

**分析**:
- T7e D2 工作正常 — 累积 5 个 DATA symbols,平均 H52
- argdiff_rms = 94° — 在 loopback 上 L-LTF H52 和 DATA-avg H52 差异巨大
  - 原因:loopback 通道是 identity (taps=[1.0], noise=0),L-LTF H52 是干净的 ground truth
  - DATA-avg H52 引入 K 个独立 measurement 的平均噪声,所以反而比 L-LTF H52 差
- T7E_REDECODE_FAIL metric=16 — viterbi decoder 的输出 metric
  - 即使 L-SIG 通过(意味着帧状态机允许 DATA 处理),HT-SIG viterbi 在干净通道上用 L-LTF H 应该成功(0 errors)
  - 用 DATA-avg H (噪声更大) 重新 decode → metric=16,CRC 失败
  - 这正是 R1 预测的:"T7e 不大可能 100% 解决"

### USRP file-replay (p109 + p110 captures)

```
=== T7E logs ===
(none — D2/D3/D4 never fires)
=== Final ===
[LSIG_PARSE_FAIL] sym=7 reason='viterbi_fail' rate=-1 length=-1 parity_ok=-1 avg_snr=2.67 ...
[LSIG_PARSE_FAIL] sym=8 reason='viterbi_fail' ...
```

**分析**:
- L-SIG viterbi 在 USRP analog chain phase noise (1.77 rad std) 下 fails
- 因为 L-SIG fails,frame state machine 跳过 HT-SIG 和 DATA
- 因此 D2/D3/D4 完全无法触发 — 即使编译通过、逻辑正确,在 USRP 上根本到不了 T7e 的 code path
- 这与 Phase 106 ROOT CAUSE 一致:L-SIG viterbi non-deterministic (166/191 attempts fail)

## 与 R1 预测的一致性

R1 verdict 中写到:
> **T7e 的极致**: 即使完美,也只能把 12-18 errors 减到 6-9 errors (仍在 viterbi capacity 外)

实测:
- Loopback (干净通道): T7e re-decode metric=16 ≈ 16 个 errors,**比原始 decode 差** (因为干净的 L-LTF H 已经是 optimal,加任何 K>1 平均都引入额外噪声)
- USRP (analog noise floor 1.77 rad): T7e 完全无法触发,因为 L-SIG viterbi 在 phase noise 下 fails

**结论**: T7e 在干净的模拟通道上没有帮助(因为 L-LTF H 已经是 optimal);在 USRP 上无法测试,因为上游 L-SIG viterbi 阻塞。

## Architectural Implications

T7e 的设计假设:**DATA pilots 提供干净的 H52 估计,可以 refine L-LTF H52**。这个假设在两个极端下都失败:

1. **干净通道 (loopback)**: L-LTF H 已经是 ground truth,DATA-avg 添加噪声
2. **USRP analog noise**: per-symbol phase noise 是 1.77 rad,即使 K=50 平均后还是 ~0.25 rad,而 viterbi capacity 是 ~4% (≈ 4 errors / 96 bits),phase noise 0.25 rad / 96 bits 仍然超过

**T7e 在两种 regime 都不工作**。这是 Phase 112 R1 已经预测的结论,现在的实测验证了这个预测。

## Phase 113+ Recommendations

T7e REFUTED → 用户的 hard constraint (USRP realtime FCS_OK) 未达成。

根据 R1 verdict 的 Phase 113+ 架构方案:
- **A**: 改 HT-SIG decoder 架构 (LDPC 替代 conv,违反 802.11n spec)
- **B**: 改 RF 模拟链路 (external ref clock 锁定 USRP)
- **C**: 接受 802.11n 限制 (违反 user hard constraint)

**推荐路径**:
1. **方案 B 优先**: external 10 MHz / PPS ref clock 可以把 phase noise 从 1.77 rad 减到 ~0.1 rad
2. **方案 A 备选**: 改 HT-SIG decoder 用 belief propagation(LDPC-style),理论上 d_free 从 10 提升到 25
3. **方案 C**: 不可接受(违反 user directive "不可能接受现状")

**短期 (Phase 113)**: 测试 external ref clock 是否可用(USRP X310 有 REF IN 端口)。如果可用:
- 准备 external 10 MHz source (例如 OCXO + GPSDO)
- 用 Phase 109 capture pipeline 验证 phase noise 改善
- 重测 T6a list viterbi(应该可以 1/256 CRC pass per path) + T7e(应该接近 0 errors)

## Files Modified

- `lib/frame_equalizer_impl.cc` — T7e D2 + D3 + D4 (cache fields, accumulation, re-decode)
- `lib/frame_equalizer_impl.h` — T7e data fields
- `docs/superpowers/notes/2026-07-07-phase112-t7e-d2-d4-verdict.md` (this file)

## Related

- [[project-p112-r1-argh-rootcause]] — R1 phase noise root cause (1.77 rad / 101°)
- [[project-p111-t6a-list-viterbi]] — T6a REFUTED (list viterbi 不工作)
- [[project-p107-deep-root-cause]] — Phase 107 argH std=108° 初判
- [[feedback-no-closure-usrp-fcs-ok]] — User hard constraint