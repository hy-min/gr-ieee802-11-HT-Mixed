# Phase 158: COPY 态智能重检（"不应期但不瞎"）— 初步阳性

**日期：** 2026-07-20
**状态：** 🟡 **PRELIMINARY POSITIVE** — 机制确认在空口触发 + 两种 governor
状态下方向一致为正；统计功效不足（实验组 n=2+1），未达 CONFIRMED。
feature 保持 opt-in 默认 OFF。

---

## 假设与设计（Phase 157 处方）

不缩短保护性 COPY 不应期；在 COPY 态增加 4 门重检，仅当全部通过才发新
`wifi_start` 标签（sync_long COPY 态 handler d_count≥1000 → 直接 SYNC 重搜）：

1. **seen_drop**：COPY 进入后 corr 曾跌落强门以下（防同一 L-STF 尾部重触发）
2. **!cooldown**：发射后须 EMA ≥ 0.5 才解除（防同一 L-STF 双标签；隐含锁存：
   陷阱 EMA~0.005 永远达不到 → 每个陷阱最多 fire 一次）
3. **功率 EMA < 0.5**（alpha 1/512；陷阱 ~0.005 vs 真帧 3-28，区分"陷阱中新
   L-STF"与"正确帧内 L-LTF/CP 强相关"）
4. **corr > 5× 有效阈值，持续 >MIN_PLATEAU(24) 样本**（噪声 boxcar 偏移 ≤16）

实现：`lib/sync_short.cc`（唯一改动文件），commits 982d417 + 5c98910 +
诊断 1ff4970。env：`IEEE80211_SYNC_SHORT_COPY_REDETECT`（主开关）、
`_FACTOR`（默认 5.0）、`_EMA_MAX`（默认 0.5）、`_DIAG`（episode 级诊断）。

## TDD 单测（p158_redetect_unit.py，确定性 ×3）

- A（陷阱中真 L-STF 到达）：tags=[0, 8030] 精确命中 ✓
- B（正确帧内 L-LTF 强相关 + 16 样本 CP 尖峰）：无误重触发 ✓
- C（feature OFF）：基线不变 ✓
- 过程发现并修正两个计划缺陷：GR 3.10 `tag_t.offset` 是属性不是方法；
  SEARCH 分支整 chunk 前视填自适应窗会污染 p90（测试陷阱 3000→8000 规避）。
- **这个前视机制本身值得注意**：检测用阈值受其所在 chunk 未来样本影响，
  是 Phase 89 既有行为，ON/OFF 相同，不是 A/B 混淆项。

## Loopback 回归门

OFF / ON 均 `Final: OK=1 FAIL=0`（双配置，独立验证两次）。

## USRP A/B（2026-07-20，5250 MHz 空口，MIN_PLATEAU=24）

**powersave 数据集（全部同条件，内部可比）：**

| 组 | n | mean ± std | 值 |
|---|---|---|---|
| 控制 OFF | 11 | **169.7 ± 13.9** | 169,165,150,166,174,197,172,167,156,160,191 |
| 实验 ON | 2 | **195.0** | 204, 186 |

**Δ = +25.3（+14.9%）**；两轮实验均 > 控制均值，204 > 控制最大值。

**performance governor 抽查（方向一致）：** 控制 n=3 → 146.0（139/145/154），
实验 n=1 → 163，Δ = +17。

**机制证据（DIAG 轮，45s，FACTOR=5）：** fire 17 次，corr 1.03-2.68，
ema 0.008-0.49（门正常工作）；1928 个 episode 统计落库；2 个离群 fire
（corr=44.8/105.3，启动瞬态尖峰——观察项，若正式评估显示有害再加 corr
上限守卫）。

## Verdict：PRELIMINARY POSITIVE（未 CONFIRMED）

- ✅ 机制在空口按设计触发（不是死代码）
- ✅ 两个 governor 状态下实验-控制差值方向一致为正（+25 / +17）
- ⚠️ 实验组总 n=3，未达预注册 N=16 判据；需完整 A/B 才能 CONFIRMED
- 代码保留 opt-in 默认 OFF（项目惯例）

## 过程教训（重要，影响未来所有批次）

1. **harness stderr 在 `/tmp/rt_validate.err`**（每轮覆盖），batch 的
   `run_XX.err` 只是 validate 脚本自身的 stderr（基本为空）。早先
   "fires=0" 结论是 grep 错文件的伪影。数 fires 要用 DIAG 轮或直接
   抓 `/tmp/rt_validate.err`。
2. **今日基线偏低（162-170 vs 历史 200）的真因 = governor=powersave**
   （不是设备漂移）。批次前必须检查
   `/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`；
   `sudo systemctl start gr-cpu-performance.service` 恢复。
3. **batch 脚本无 hang 超时**：kill 正在跑的批次会留 USRP 坏状态 →
   下一次 harness UHD init 无限挂起（进程 Sl 态，CPU 不涨）。
   恢复 = kill 残留进程 + `uhd_usrp_probe` nudge。
4. governor 修复后控制组反而更低（146 vs 169.7）——说明基线还受
   时段/环境调制，**背靠背对照永远是判据**。

## 下一步

- 完整 N=16 A/B（performance governor，fires 用 DIAG 轮统计）
- 若 CONFIRMED：考虑 FACTOR 扫描（5→3/4 可能捕获更多弱帧，需噪声
  plateau 数据支撑）+ 离群 fire 上限守卫
- 若 INCONCLUSIVE：diag episode 数据已落库，可分析 0.4-0.8 max_cor
  episode 群是弱真帧还是噪声

**产物：** `batch_results/p158_control/`, `p158_on/`, `p158_control_perf/`,
`p158_on_perf/`, `/tmp/rt_validate.err`（DIAG 轮）, `p158_redetect_unit.py`
**相关：** [[Phase 157 refractory]], [[Phase 154 MIN_PLATEAU=24]], [[Phase 153 funnel]], [[Phase 152 RFNoC]]
