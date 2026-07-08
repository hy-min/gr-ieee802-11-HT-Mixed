# Phase 115 T1+T2: 3-way averaging bug fix (2026-07-08)

**Branch**: TEST1
**Status**: 🟢 **PASS (loopback + USRP)** — 3-way code path now actually fires
on both loopback AND USRP 5250 cable. HT-SIG viterbi metric still >10
(Phase 112 R1 ceiling confirmed).

## TL;DR

The Phase 114 root cause (saved_htltf_52 populated at `extract_call_count==6`
in `extract_header52_from_sym64`, but `estimate_header_channel_from_lltf52`
called at `d_internal_symbol_counter==3` and `>=4` so `htltf_52_saved` was
always false at the 3-way check) is fixed by:

1. **T1**: Keep the original save site at `extract_call_count==6` (T1 originally
   tried to move it, but reverted — original is correct since the diagnostic
   shows `htltf_52_saved=1` after extract_call_pre=6)
2. **T2**: Add a NEW call to `estimate_header_channel_from_lltf52` at
   `d_internal_symbol_counter==6` in `general_work` (after CFO/SFO compensation
   on d_early_eqsym[6]). This new call runs AFTER the extract save, so
   `htltf_52_saved=true` is observed and 3-way fires.
3. **T3**: Override `d_H52_tx_order` with the 3-way H52 and set
   `d_H52_tx_order_valid=true`, so the existing DATA path's lazy
   `compute_H52_tx_order` is skipped and 3-way H is used. Also call
   `d_equalizer->set_H(h_eq_3way)` for the `d_equalizer->equalize()` path.

## USRP 5250 cable 60s verification (2026-07-08)

Command: `test_usrp_minimal_loopback.py --uhd-tune --htltf-avg --freq 5250
--tx-gain 0 --rate 20 --warmup 60 --rx-subdev A:0 --duration 60`

Log: `/tmp/p115_usrp_step2_60s.log`

| 指标 | Phase 114 Step 2 (3-way dead) | **Phase 115 (3-way 触发)** |
|------|-------------------------------|-----------------------------|
| Sent (60s) | 120 | 120 |
| LSIG_DECODE OK | 13 | 8 (variance, CV=0.329) |
| HT_SIG_CAND | 16 | 16 |
| HT_SIG metric | 14-17 | **14-16** (略改善) |
| **H52_3WAY_AVG** | **0 (dead code)** | **2 ✅ 触发** |
| Recv / FCS_OK | 0 | 0 |

`H52_3WAY_AVG` 真实日志 (USRP 5250 cable):
```
[H52_3WAY_AVG] wt0=34.3928 wt1=36.7311 wt_ht=34.2189 ratio_ltf01=0.936 ratio_ltf0ht=1.005
[H52_3WAY_AVG] wt0=30.7214 wt1=32.4725 wt_ht=36.7199 ratio_ltf01=0.946 ratio_ltf0ht=0.837
```
- 3 sources 都贡献(wt0/wt1/wt_ht 都 > 27,无单一源压制)
- 权重比 ~0.94-1.00(平衡,3-way 充分混合)

## 关键结论

### ✅ 3-way 真正触发了

Phase 114 root cause 完全修复:
- Loopback: H52_3WAY_AVG fires 100% when 3-way path enabled
- USRP 5250: H52_3WAY_AVG fires 2x in 60s(等于实际通过 H52 估算的 frame 数)

### ⚠️ HT-SIG metric 仍 >10(Phase 112 R1 ceiling 验证)

HT_SIG viterbi 仍 fail:
- Metric 14-16(略低于 Phase 114 14-17)
- 全部 16 candidates crc_fail

avg_snr_ht=74.02(线性)= 10*log10(1/73.02) ≈ -18.6 dB 实际 SNR
(per Phase 100 SNR 公式,远低于 6 dB viterbi 阈值)

**结论**:3-way averaging 无法把 metric 压到 ≤10,符合 Phase 112 R1 root cause 预测:
- 1.77 rad per-SC 相位噪声是物理上限
- 3-way averaging(算术平均)最多降到 1.77/√3 ≈ 1.02 rad,仍无法让 viterbi pass
- 这是 analog chain 物理限制,等化器层已无法突破

### 📋 Phase 115+ 必须切换策略

按用户指令"排除外部时钟和换算法,尽可能给出更多的解决方案",3-way 这条路已到尽头。
下一步必须尝试用户"新架构"指令:
- 决策导向(DD)等化器:用 decoded bits refine H
- 替代 H 估算:HT-LTF 重新估计 H(用真实 HT-LTF P-matrix)
- 联合 phase tracking:把 1.77 rad 噪声建模为 Wiener process
- 不同 channel model:用时间序列预测 H drift

## Files Modified

- `lib/frame_equalizer_impl.cc:5194-5258` — new counter=6 H52 estimate block
  (T2 + T3 combined)
- `lib/frame_equalizer_impl.cc:963-984` — T1 reverted (original save kept)
- `lib/frame_equalizer_impl.cc:4013-4014` — `IEEE80211_HTLTF_AVG_DEBUG=1`
  (already added in Phase 114)
- `lib/frame_equalizer_impl.cc:5251-5257` — T4D_DIAG log gated by debug flag

## Loopback Verification

```
[H52_3WAY_AVG] wt0=27.4856 wt1=29.4837 wt_ht=28.2022 ratio_ltf01=0.932 ratio_ltf0ht=0.975
[T4D_DIAG] extract_call_count=7 (post-increment) g_htltf_avg=1 htltf_52_saved=1 extract_call_count_pre=6
[T4D_DIAG] 3way check: g_htltf_avg=1 htltf_52_saved=1
[T4D_DIAG] 3way stored: counter=6 d_H52_tx_order_valid=1 |H3way[-26]|=0.268938 |H3way[0]|=0.397724 |H3way[+26]|=0.0285726
```

## Why the fix is structurally correct

The original save at `extract_call_count==6` fires when
`d_internal_symbol_counter==6` (HT-LTF arrival) because both increment in
lockstep (verified by diagnostic: pre=6 → counter=6). The new H52 estimate
call at `d_internal_symbol_counter==6` runs AFTER the save (since
`extract_call_count++` happens at line 986 inside the function, returning
control to `general_work` before the new call at line ~5210). So the
`htltf_52_saved` static is true at the new call.

The 3-way blending inside `estimate_header_channel_from_lltf52` (lines
1155-1213) reads the static `saved_htltf_52` and computes H_HTLTF, then blends
with H_LTS0 and H_LTS1 using |H|-weighted averaging (Phase 77c scheme).

## Default OFF

- `IEEE80211_HTLTF_AVG=1` opt-in (default unset)
- `IEEE80211_H52_SNR_WEIGHTED=1` opt-in (default unset; required for 3-way)
- All new code paths gated on `d_apply_htltf_avg`
- `IEEE80211_HTLTF_AVG_DEBUG=1` for diagnostic logging (default OFF)
- Phase 113 baseline preserved when env vars absent

## Phase 115+ Next Steps (per user "new architecture" directive)

1. **Decision-directed equalizer**: 用 decoded HT-SIG0 bits refine H52,
   apply for HT-SIG1 + DATA. Bypass 1.77 rad floor by using known TX bits.
2. **HT-LTF 重新估计 H**: 用 HT-LTF 真实 P-matrix(不是 L-LTF proxy),
   可能得到更准确的 H phase。
3. **Phase tracking via Kalman**: 把 1.77 rad 噪声建模为 Wiener process,
   per-symbol 跟踪(Phase 111 T3 部分实现)。
4. **Time-series H prediction**: 跨 frame 用 H[t-1] 预测 H[t],降低单 frame 噪声。

## Related

- Phase 114 stack verdict: `docs/superpowers/notes/2026-07-08-phase114-stack-verdict.md`
- Phase 114 root cause: `extract_call_count=7 (post) htltf_52_saved=1` but
  earlier calls at counter=3/4 had `htltf_52_saved=0`
- Phase 77c: 2-way SNR-weighted baseline (still working alongside 3-way)
- Phase 112 R1: 1.77 rad per-SC phase noise ceiling (CONFIRMED — 3-way can't break it)
- USRP log: `/tmp/p115_usrp_step2_60s.log`
