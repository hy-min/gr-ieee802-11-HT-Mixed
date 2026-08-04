# Phase 158-ABAB: COPY 态智能重检 — NOT CONFIRMED（初步 +25.3 被证为区组混淆）

**日期：** 2026-08-04
**状态：** ❌ **NOT CONFIRMED** — N=8 交错 ABAB 配对检验：mean diff −6.1/45s，
paired t p=0.485，Wilcoxon p=0.547。feature 保持 opt-in 默认 OFF。
**结论精化：** 机制按设计触发（~2.9 fires/45s）但触发率太低，即使 100%
转化也只有 +1.6% arrival，低于本实验检测下限（配对 SE ±8.3）。初步实验的
+25.3 不是 feature 的效果，是**跨时段区组比较的环境混淆**。

---

## 设计（用户批准，替代完整 N=16 A/B）

- 8 对交错 ABAB：每对 = 对照(OFF) + 实验(`IEEE80211_SYNC_SHORT_COPY_REDETECT=1`)
  背靠背；奇数对 OFF→ON、偶数对 ON→OFF（对消线性时漂）。
- 单变量：仅 REDETECT env；其余 env/硬件/脚本完全相同。
- 每轮 = `usrp_realtime_validate.sh`（3×15s，governor=performance 已验证）。
- 新批次脚本 `p158_abab_batch.py`：240s hang 超时 + killpg + probe 重试
  （P158 教训 #3 修复）；infra 失败单独计数（P152 惯例）。
- 预注册判据：配对差 mean>0 且 two-sided paired t p<0.05 → CONFIRMED。

## 数据（8/8 对全有效，0 重试，0 下溢/溢出）

| pair | OFF | ON | diff |
|---|---|---|---|
| 1 | 210 | 183 | −27 |
| 2 | 192 | 167 | −25 |
| 3 | 165 | 197 | +32 |
| 4 | 167 | 159 | −8 |
| 5 | 186 | 194 | +8 |
| 6 | 190 | 197 | +7 |
| 7 | 174 | 178 | +4 |
| 8 | 221 | 181 | −40 |

- mean diff = **−6.12**，std diff = 23.52，t(7) = −0.74
- **paired t p = 0.485；Wilcoxon p = 0.547** → NOT CONFIRMED
- 95% CI(diff) ≈ [−25.8, +13.5]：**初步 +25.3 落在 CI 外** → 初步点估计被
  正式数据 REFUTED；小幅效应（<±13）无法排除但也无证据。
- 对照臂 20 分钟内 165–221（±15%）：时段调制的幅度本身就 ≈±30，
  **大于初步实验的 +25.3 "效应"** —— 未配对的跨区组比较（n=11 控制 vs
  n=2 实验，不同时段）天然不可靠。ABAB 设计正是为此而建。

## 机制转化分析（关键）

ON 臂归档 stderr 中的 fires：pair01=2, 02=12, 03=6, 05=2, 06=1，其余 0，
合计 ~23 fires / 8 runs ≈ **2.9 fires/45s**（初步 DIAG 轮 2026-07-20 是
17 fires/45s —— 噪声环境不同）。本批 fire 的 corr 1.0–2.3、ema 0.03–0.39，
全部行为良好（无 corr=44.8/105.3 启动瞬态离群）。

**含义：** DECODE_SUCCESS ~180/45s 时，3 fires/run 即使 100% 转化为解码
也只有 +3/run（+1.6%），远小于配对 SE（±8.3）。本实验对该机制只可能得到
null —— **FACTOR=5 的触发率决定了它不可能是 arrival 的有效杠杆**。
这也解释了初步 +25.3 不可能由该机制产生。

## Verdict：NOT CONFIRMED，本轴关闭（FACTOR=5）

1. ❌ 初步 +25.3 REFUTED 为区组混淆（非 feature 效果）。
2. ✅ 机制按设计触发且行为良好（无有害证据：mean −6 在噪声内，p=0.49）。
3. ❌ 触发率 ~3/45s → 理论上限 +1.6% arrival，FACTOR=5 不是杠杆。
4. **FACTOR 扫描（5→3/4）预期价值低**：触发更多 = 更接近 P155/P157 的
   refractory 破坏风险区；且 P156 已证明瓶颈在 1.77 rad LO 墙——重同步
   成功的帧仍要过 L-SIG/HT-SIG。不建议继续投入。
5. feature 保留 opt-in 默认 OFF（无回归、机制可复用）。

## 持久产物（方法论升级）

- **`p158_abab_batch.py`**：交错 ABAB + hang 超时 + probe 重试 +
  paired t/Wilcoxon 自动判定。**今后所有单变量 A/B 的标准 harness**
  （替代 `batch_usrp_validate.py` 的独立组设计——P158 教训 #4：
  未配对跨区组比较在本台架上携带 ±30 混淆）。
- 结果：`batch_results/p158_abab/20260804_170642/`（16 runs + 归档 rt.err）。

## 下一步（回到 P156 结论指引的方向）

- 残余 ~20% COPY 捕获的杠杆不在 COPY 态重检（本轴已关），而在
  **1.77 rad LO 相位噪声墙**本身：外部 10 MHz ref clock / GPSDO（硬件，
  当前不可用）是唯一已验证路径。
- 软/流式方向在 ground-truth 台架上已系统性 REFUTED（P150）。
- 若继续无新硬件：可考虑分析本批归档数据中火 episode 的后续走向
  （重同步后的帧死在漏斗哪一级），作为"为何重检不转化"的直接证据。

**相关：** [[Phase 158 PRELIMINARY]]（本文件取代其结论）、[[Phase 157
refractory]]、[[Phase 156 cable]]、[[Phase 150 solidified]]
