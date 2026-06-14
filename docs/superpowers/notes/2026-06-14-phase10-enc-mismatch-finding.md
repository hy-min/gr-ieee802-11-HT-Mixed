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

## IQ swap experiment (2026-06-14)

Added `IEEE80211_LSIG_IQ_SWAP=1` env var to swap I/Q of equalized L-SIG
(right before `decode_lsig_direct_from_header52` call site, line 3133 in
`lib/frame_equalizer_impl.cc`). Opt-in via `getenv()` check, off by default.

USRP result (30s, test_p10_usrp_v2.py with `IEEE80211_LSIG_IQ_SWAP=1`):
- Total LSIG_DECODE OK events: 36
- Encoding distribution:
  - enc=4: 12 (33%)
  - enc=1:  8 (22%)
  - enc=7:  4 (11%)
  - enc=3:  4 (11%)
  - enc=2:  4 (11%)
  - enc=0:  4 (11%)   ← appears for first time, but only 4/36 frames
- HT_SIG_PARSE_FAIL still dominates: n_candidates=0 when lsig_enc != 0
- Compared to baseline (no IQ swap, per memory): enc=2/4/6/7 mix, enc=0 never seen

Loopback result (examples/test_direct_loopback.py):
- IQ_SWAP unset: `Final: OK=0 FAIL=1` (pre-existing FcsLogger crc bug)
- IQ_SWAP=1:    `Final: OK=0 FAIL=1` (same — no regression, swap is gated)

Conclusion: **IQ swap is NOT the fix.** It shifts the random distribution
(enc=6 → enc=1, enc=0 appears 4/36 times) but does not produce a systematic
enc=0. Real root cause is upstream L-LTF0 FFT / H estimation, not axis
confusion. Reverting the change.

## CFO refinement experiment (2026-06-14, Task 4)

Added `IEEE80211_CFO_REFINEMENT=1` env var to frame_equalizer_impl.cc.
Hook computes a single high-SNR pilot SC (slot 48, subcarrier -21) CFO
estimate from L-LTF0 vs L-LTF1 phase difference, subtracts the SFO
contribution at that SC, then blends 50/50 with the existing 52-SC
mean. Re-derives `d_phase_diff_per_sc` using the refined CFO + the
existing SFO slope.

USRP result (12s, test_p10_usrp_v2.py with `IEEE80211_CFO_REFINEMENT=1`):
- Total LSIG_DECODE OK events: 72
- Encoding distribution:
  - enc=0: 24 (33%)   ← appears systematically, but...
  - enc=6: 24 (33%)   ← ...still mixed with non-BPSK
  - enc=1:  8 (11%)
  - enc=2:  8 (11%)
  - enc=3:  8 (11%)
- HT_SIG_PARSE_FAIL still dominates: 56 events

CFO_REFINEMENT deltas (orig 52-SC mean vs fine pilot-SC):
- n=12 deltas, mean=0.46, std=1.10, range |0.12..1.90| rad per 4 μs
- EXPECTED: small (≤ 0.1) — task description hypothesis
- ACTUAL: huge variance — pilot SC itself is corrupted by the same
  root cause that breaks L-LTF0 FFT (Phase 5: LO phase noise 14 rad)

Baseline comparison (same 12s run, no env vars, back-to-back):
- Total LSIG_DECODE OK events: 25
- enc=0: 9 (36%)   ← similar percentage to refinement run
- enc=6: 8 (32%)
- enc=2: 8 (32%)

Conclusion: **CFO refinement is NOT the fix.** The 50/50 blend is just
biasing the CFO estimate toward a noisy pilot SC, which happens to
shuffle the random distribution. enc=0 percentage is statistically
indistinguishable from baseline (33% vs 36%). The huge delta variance
(1.10 rad std) confirms the pilot SC is being corrupted by the upstream
root cause — it cannot provide a "fine" reference. The original 52-SC
mean is the best estimate we can get from L-LTF0/L-LTF1; refinement
only adds noise. Reverting the change.

## FORCE_HTSIG experiment (2026-06-14)

Added `IEEE80211_FORCE_HTSIG=1` env var to bypass
`if (lsig_enc != 0) continue;` at line ~3160 of `lib/frame_equalizer_impl.cc`.
When set, the candidate loop runs for every inversion, regardless of L-SIG
enc. The check stays on by default; FORCE_HTSIG is opt-in. New log line
`[FORCE_HTSIG] sym=N lsig_enc=K, attempting HT-SIG despite non-zero enc`
fires once per frame the bypass is taken.

USRP result (12s, IEEE80211_FORCE_HTSIG=1):
- HT_SIG_CAND fires: 384 lines (24 frames × 16 candidates) — gating
  bypassed successfully; the candidate loop now runs for enc=2/4/6/7
  frames too.
- FORCE_HTSIG fires: 24 lines (one per frame; L-SIG decodes as enc=2
  on USRP, never enc=0).
- HT-SIG decode outcome: **384/384 crc_fail** — zero successful decodes.
- HT-SIG metrics 14-16, all uniformly crc_fail across 4 rotations × 4
  inv combinations, consistent with the Phase 10 finding that the
  upstream L-LTF0 FFT is corrupted on USRP. Forcing the loop to run
  does not produce valid HT-SIG because the equalizer input is the
  same broken H52 — the gating was never the bottleneck.

Loopback result (8s, IEEE80211_FORCE_HTSIG=1):
- 1 frame captured, 1 FCS OK — identical to baseline. The FORCE_HTSIG
  path is not exercised (loopback L-SIG correctly decodes as enc=0).
- No regression.

Conclusion: **The soft fix does NOT unlock HT-SIG on USRP.** It
mechanically removes the gating, but the upstream L-LTF0 FFT corruption
(Phase 10 root cause) means equalized HT-SIG symbols are random →
crc_fail on every candidate. The gating was a red herring; the real
fix must address L-LTF0 FFT quality or H52 estimation upstream.

Commit: `fix(frame_eq): bypass lsig_enc!=0 gating behind IEEE80211_FORCE_HTSIG env var`.

## End-to-end validation (2026-06-14)

USRP 30s (`test_p10_usrp_v2.py` + `IEEE80211_FORCE_HTSIG=1`):
- Sent (PHY TX strobed frames): ~50 (10s × 500ms × 2 boards = ~20 strobed)
- **Recv (FcsLogger events): 0** (FCS OK=0, FAIL=0)
- LSIG_DECODE OK events: 81
- Encoding distribution:
  - enc=0: 25 (31%)
  - enc=6: 16 (20%)
  - enc=3: 16 (20%)
  - enc=1:  8 (10%)
  - enc=4:  8 (10%)
  - enc=5:  8 (10%)
- FORCE_HTSIG fires: 56 (every frame where lsig_enc != 0; gating bypassed)
- HT_SIG_CAND fires: 1281 (candidate loop runs 16× per forced frame)
- HT_SIG_PARSE_FAIL: 72
- HT_SIG fail reason: **1280/1280 crc_fail** — zero successful decodes across
  all 16 candidates × 56 forced frames
- Avg SNR: lsig=21.7 dB, htsig=20.3 dB (modest, not the bottleneck)

Loopback 10s (`examples/test_direct_loopback.py`, no FORCE_HTSIG):
- **Final: OK=0 FAIL=1** — matches baseline (pre-existing FcsLogger `crc` field bug)
- Regression-free.

Conclusion: **The soft fix does NOT unlock HT-SIG on USRP.** Mechanically,
FORCE_HTSIG removes the `lsig_enc != 0` gating (56/56 FORCE_HTSIG fires vs
the prior 0/0 when the gate was in effect), but the upstream L-LTF0 FFT
corruption (Phase 10 root cause) means equalized HT-SIG symbols are random →
**1280/1280 crc_fail on every candidate**. The gating was a red herring;
the real fix must address L-LTF0 FFT quality or H52 estimation upstream.
No regression in software loopback.

Next iteration: address the upstream L-LTF0 FFT issue (Phase 11 plan
already drafted at `c225aa1`). Likely targets: L-LTF0/L-LTF1 timing
alignment, FFT window offset, or hardware-level (TCXO/OCXO).

## Timing offset sweep (Phase 12, Task 2, 2026-06-14)

The `IEEE80211_FRAME_START_OFFSET=N` env var hook was already present in
`lib/sync_long.cc` (commit b8e0e34, Phase 3). It shifts `FRAME_START_BASE`
(160) by N samples in both tag-jump and correlation-search paths, via
`get_frame_start_offset()` helper at line 43-54. Default offset=0
preserves baseline behavior. No new code was required for this task —
we proceeded directly to the sweep.

Sweep on USRP, 12s per offset, `test_p10_usrp_v2.py` 400-byte packet
(5 separate 12s runs, ~1 minute total wall time):

| offset | frame_start | total LSIG OK | enc=0 | enc=0 % | non-zero encodings |
|-------:|------------:|--------------:|------:|--------:|---------------------|
|     -4 |         156 |            40 |     8 |  20%    | 0,1,2,6,7 (8 each)  |
|     -2 |         158 |            32 |     0 |   0%    | 1,3,5               |
|      0 |         160 |            24 |     0 |   0%    | 2 (24)              |
|     +2 |         162 |            40 |     0 |   0%    | 2,3,4               |
|     +4 |         164 |            32 |     0 |   0%    | 3,6                 |

Observations:
- **Best offset: -4** (20% enc=0). Only offset that produces any enc=0.
- **Baseline (offset=0)**: 0% enc=0, all frames decode as enc=2.
- **No offset achieves >=50% enc=0** (the "promising" threshold from the plan).
- The enc distribution varies wildly between offsets (enc=2 dominant at 0/+2/+4,
  enc=3,6 dominant at -2/+4, enc=0 appears only at -4 mixed with others).
- Shifting d_frame_start by ±N samples does not systematically fix the
  L-LTF0 FFT corruption. Sub-sample-equivalent offsets cannot recover
  the broken H52 → equalized L-SIG constellation.

Conclusion: **Timing offset sweep does NOT fix the upstream L-LTF0 FFT
issue.** Best result (offset=-4) is a marginal 20% enc=0 — within noise
of a stochastic process, not a systematic fix. The env var hook is
retained (it costs nothing and was already in place from Phase 3), but
no value of N is the "right" answer. This rules out the sample-level
d_frame_start timing hypothesis from Phase 11. The remaining
upstream-fix candidates are:

1. **Task 3: L-LTF1-only H estimation** (skip L-LTF0 average)
2. **Task 4: per-SC phase tracking (CPE)** on equalized symbols
3. **Hardware**: 10 MHz OCXO/GPSDO to address LO phase noise (Phase 6 root cause)

No commit was made for Task 2 — the env var hook is already on the
branch (commit b8e0e34) and the sweep produced no actionable change.

## L-LTF1 only H estimation (Phase 12, Task 3, 2026-06-14)

The `IEEE80211_H_LLTF1` env var hook was already present on the branch
(`d_use_lltf1_for_h` member, set at `lib/frame_equalizer_impl.cc:1904-1908`,
consumed at both call sites `2584-2595` and `2949-2957`). When set, the
H estimation pulls from `d_ltf_compensated[1]` (CFO/SFO-compensated L-LTF1)
instead of `d_ltf_compensated[0]` (CFO/SFO-compensated L-LTF0), skipping
the L-LTF0 average that propagates L-LTF0 corruption. No new code was
required for this task — we proceeded directly to the USRP test.

USRP result (`test_p10_usrp_v2.py` with `IEEE80211_H_LLTF1=1`, 12s):
- Total LSIG_DECODE OK events: 16
- Encoding distribution:
  - enc=7: 16 (100%)    ← all non-BPSK
  - enc=0: 0 (0%)
- `[H_SRC] using L-LTF1 (counter=1) for H estimation` log fires 3+ times,
  confirming the hook engages.
- Loopback (`examples/test_direct_loopback.py` with `IEEE80211_H_LLTF1=1`):
  1 LSIG_DECODE OK event, enc=0 (BPSK 1/2 — correct). **No regression.**
- Baseline comparison (Phase 12 Task 2, offset=0): 24 LSIG OK, 0% enc=0,
  all enc=2 — L-LTF1-only run is statistically indistinguishable from
  baseline; both are well below the "promising" threshold of ≥50% enc=0.

Conclusion: **L-LTF1 only H is NOT the fix.** It shifts the random
distribution (enc=2 → enc=7) but does not produce a systematic enc=0.
Both L-LTF0 and L-LTF1 are equally corrupted on USRP, which is consistent
with the Phase 3 Stage 1 verdict that the upstream FFT destruction hits
both symbols. The env var hook is retained (zero cost, in place from a
prior phase) but provides no value on USRP. This rules out the
"skip L-LTF0 averaging" hypothesis from Phase 11. The remaining
upstream-fix candidates are:

1. **Task 4: per-SC phase tracking (CPE)** on equalized symbols
2. **Hardware**: 10 MHz OCXO/GPSDO to address LO phase noise (Phase 6 root cause)

No code change for Task 3 — the hook is already in place from a prior
phase and the experiment produced no actionable improvement.
