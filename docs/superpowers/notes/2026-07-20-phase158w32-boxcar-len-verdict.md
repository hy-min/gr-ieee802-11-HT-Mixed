# Phase 158-W32: Boxcar 窗口 16→32 实验 — 预判 CONFIRMED（无显著差异）

**日期：** 2026-07-20
**状态：** ❌ 无效果（预判 CONFIRMED）——W=32 均值差 -0.63（0.4%），远在 1σ 内

---

## 假设与预判

用户提议：拉长 `sync_short_fused` 的 boxcar 滑动平均窗口（16→32）抑制噪声峰。
Claude 预判（实验前锁定）：**均值差 < 1σ**，因为：

1. 自适应阈值（p90×1.5）跟踪噪声分布本身，白噪声方差收益大部分自我抵消
   （合成实测：白噪声裕度仅改善 1.18×，非 √2=1.41×）。
2. 陷阱源是**结构化突发**（DC 偏移/LO 杂散，相干积分——boxcar 对
   period-16 结构是相干积分器，窗口越长积分越多），不是白噪声尾部。
3. 残余 ~20% 损失发生在 **COPY 态**（检测器不运行），输入侧窗口够不着。
4. Phase 88 长窗方向（MA(48)/MA(64)）已 REFUTED 于同一轴。

## 实现

`IEEE80211_SYNC_SHORT_FUSED_BOXCAR_LEN`（opt-in，默认 16，支持 8/16/32/64，
bitmask 环）。commit eb76de7。合成 sanity（p158_boxcar_len_sanity.py）PASS：
平台 16→32（2×）、噪声均值 2×、噪声 std √2×。Loopback 回归双配置
OK=1 FAIL=0。

## USRP A/B（背靠背，各 N=16，5250 MHz 空口，MIN_PLATEAU=24）

| 组 | mean ± std | min | max | infra_fail |
|---|---|---|---|---|
| 控制 W=16 | **162.44 ± 15.93** | 140 | 195 | 0 |
| 实验 W=32 | **161.81 ± 15.86** | 137 | 194 | 0 |

**Δ = -0.63（-0.4%）**，均值标准误 ~5.6 → 差异是噪声的 1/9。预判 CONFIRMED。

注：本次控制组 162.4 低于 Phase 154b 的 ~200——设备状态漂移，
新鲜背靠背对照是正确判据（相对历史基线比较会得出错误结论）。

## 结论

1. **boxcar 窗口轴关闭**：16 是与 L-STF 周期匹配的最优点；加长对白噪声
   收益被自适应阈值抵消，对结构化突发无效（理论+实测双重确认）。
   代码保留 opt-in（默认 16）。
2. 残余 ~20% COPY 捕获的攻法维持 Phase 157 处方：**COPY 态智能重检**
   （"不应期但不瞎"，计划 `docs/superpowers/plans/2026-07-20-phase158-copy-redetect.md`）。

**产物：** `batch_results/w32_control/`, `batch_results/w32_experiment/`,
`p158_boxcar_len_sanity.py`
**相关：** [[Phase 88 MA(48)/MA(64) FLAWED]], [[Phase 89 boxcar]], [[Phase 154 MIN_PLATEAU=24]], [[Phase 157 refractory]]
