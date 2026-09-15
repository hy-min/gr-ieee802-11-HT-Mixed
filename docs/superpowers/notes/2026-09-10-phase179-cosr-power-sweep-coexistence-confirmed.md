# Phase 179 verdict: Co-SR 干扰功率扫描——远几何共存全功率范围 CONFIRMED（2026-09-10）

VERDICT: 远几何（1m 箱距）Co-SR 共存 CONFIRMED——干扰箱 tx-gain 0→31.5dB
全程受害链路 FCS_OK 平坦（满功率 76.6% vs OFF 基线 70.4%），无膝点、无单调
劣化、len89 异帧污染全 12 点为零。P178「1m 箱距 box-to-box 干扰 negligible」
的 4 点粗扫被系统化 12 点回文曲线证实。

## VERDICT（逐字粘贴批次输出原文）

命令：`python3 p179_cosr_power_sweep.py --a-env HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1 --b-env HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1 --tag otaenv`
（受害 A=192.168.10.2 跨板 A:0 TX→B:0 RX2，MCS0 len38→PSDU 66，tx-gain 31.5 固定；
干扰 B=192.168.20.3 同构跨板，MCS0 len61→PSDU 89，tx-gain 回文扫描；
5890 MHz 共信道，空口 1m，governor=performance）

```
========== P179 SWEEP SUMMARY ==========
idx  B_gain  arm    A_FCS_OK/est  rate%  len89_contam  attempts
  0     OFF  asc      284/450      63.11      0  1
  1       0  asc      358/450      79.56      0  1
  2       8  asc      360/450      80.00      0  1
  3      16  asc      394/450      87.56      0  1
  4      24  asc      406/450      90.22      0  1
  5    31.5  asc      355/450      78.89      0  1
  6    31.5  desc     334/450      74.22      0  1
  7      24  desc     305/450      67.78      0  1
  8      16  desc     329/450      73.11      0  1
  9       8  desc      49/450      10.89      0  1
 10       0  desc     395/450      87.78      0  1
 11     OFF  desc     350/450      77.78      0  1
[SWEEP] OFF repeat control: first=63.11% last=77.78%  |delta|=14.67pp -> DRIFT SUSPECT
[SWEEP] curve: /home/hy/captures/p179_cosr_power_sweep_20260910_165003_otaenv/p179_curve.png
```

同增益升/降臂配对均值（%）：

| B tx-gain | asc | desc | 均值 |
|---|---|---|---|
| OFF | 63.1 | 77.8 | 70.4 |
| 0 | 79.6 | 87.8 | 83.7 |
| 8 | 80.0 | 10.9* | 80.0（*离群剔除）|
| 16 | 87.6 | 73.1 | 80.4 |
| 24 | 90.2 | 67.8 | 79.0 |
| 31.5 | 78.9 | 74.2 | **76.6** |

## 判定

**远几何 Co-SR 共存: CONFIRMED（表征测量，非 A/B）**
- 曲线在干扰功率 0→31.5dB 全范围平坦（77-84% 带内）；满功率点 76.6% 不低于
  OFF 基线 70.4%——无膝点、无单调劣化。P178 净判定「1m 箱距 box-to-box 干扰
  negligible，空间复用天然共存」获曲线级证实
- len89_contam 全 12 点 = 0：B 帧从未通过 A 的 FCS——近场几何捕获效应保护
  受害链路（P178「捕获=天线位置物理必然」的直接证据）
- idx 9（8dB 降臂 10.9%）= **测量系统瞬态**，非干扰：物理上不可能（更高功率
  16/24/31.5 同期无效应），且相邻 0dB 点 87.8% 完全恢复。读数中已标注剔除。
  ⚠️ 原写「信道衰落 episode」，经 2026-09-14 组件级调试更正——机制为 splitter
  force-transition 循环（见 `2026-09-14-phase180-sir-axis-coexistence-confirmed.md`
  「工具教训 2」），与环境信道无关
- DRIFT SUSPECT（预期内）：首尾 OFF 漂移 14.7pp——OTA 信道方差 >
  任何潜在干扰信号，构成该几何下曲线分辨力上限
- 分子分母窗口：分母 est_sent~150/15s 窗 ×3 窗 = 450/点（interval 100ms，
  warmup 20s 已由 harness 排除）；分子 FCS_OK = [RESULT] per-window 计数器
  差值（P159b 分母同域规避）；len89 计数 = A 侧 stderr 全程 DECODE_SUCCESS
  len=89 行数（PSDU 89=B 帧，长度归因，P178 方法）

## 机制与实现

- 新脚本 `p179_cosr_power_sweep.py`（纯编排，零 C++/harness 改动，loopback 门
  N/A——mcs_sweep.py 先例）：回文 12 点交错抗 OTA 漂移（2026-09-09 同批漂移
  23-55pp 教训）；B 先启动 25s 持续发射，A 测 3×15s 窗；挂死 killpg + probe
  nudge ×2（P152 同策）；matplotlib 出 PNG 曲线
- 首次启动基线 22.7% 被拒（<50% 健康门）→ 根因 = 跨板空口需 OTA env
  `HDR_COMP_DISABLE=0 + SYNC_LONG_XCORR_FS=1`（跨板独立 PLL 有真实相位漂移
  可跟踪，P178 阶段 1 同配置），重跑基线 63.1% 达标
- 干扰源 = 标准 instrumented harness（帧干净；P178 的 tx_only_interferer.py
  帧 FCS 全败混淆被排除），长度归属 len66/len89 分离 A/B 帧

## 决策

- 共存结论支持 P178「该几何无需功率协商」；要演示「碰撞有威胁 → 需协商」
  仍需更近几何或电缆 2×2 可控 SIR 信道（P178 净判定原文，维持不变）
- 无默认翻转、无新 env var、无禁止方向变更
- 下一步候选：近几何（干扰天线贴近受害 RX）重跑同脚本看捕获门限膝点
  （离线预测 MCS0 ≈ +3dB SIR），或电缆 2×2 精确 SIR 控制

## 诚实清单（故意不动）

- 无新 env var → 不动 env-vars.md（lint 用既有 IEEE80211_HDR_COMP_DISABLE 过门）
- 无默认翻转 → 不动 CLAUDE.md harness 表与「当前状态」
- 非关闭方向 → 不动禁止方向表与 hookify pattern
- 曲线 PNG 与全部日志在仓库外：`/home/hy/captures/p179_cosr_power_sweep_20260910_165003_otaenv/`
- 首跑（harness 默认 env）数据弃用：`/home/hy/captures/p179_cosr_power_sweep_20260910_164635/`
  （仅 idx0 一个点，基线不健康即中止，未产生有效测量）

## 更正 2026-09-15（P182 后追加）：测量成立，「共存」解读不成立

**本文件的测量结论仍然有效**：干扰箱 tx-gain 0→31.5 dB 全程受害链路平坦。
**但把它解读为「Co-SR 空间复用天然共存」是不成立的**——P182 证明了这个平坦
另有原因，与本文件当初的「捕获效应/近场几何保护」解释不同。

**真实原因 = 占空比**（详见 `2026-09-14-phase182-cosr-continuous-noise-confirmed.md`）：

- MCS0 帧长 ~120µs / 周期 100ms → 占空比 **0.12%**
- 两条**独立频闪**链路的帧在时间上重叠的概率仅 **2×120µs/100ms = 0.24%**
- 即：**受害帧与干扰帧几乎从不在时间上相遇**，干扰从未获得作用机会
- 本 phase 实测「B帧@A ≈ 2139-3311 次/臂」是接收机**检测到**干扰前导的次数，
  与「干扰帧覆盖受害帧」是两回事

**因此本文件从未构造出 Co-SR 的前提条件**（两系统同时发射）。P182 用连续噪声
（占空比 100%）在同一根 tx-gain 轴上重测：**6→8 dB 骤降 52pp，满功率 −65pp**。

**P178 离线之所以测出捕获效应**，是因为它人为把两帧按 τ=0 对齐混合——那才是
真正的「同时」；本 phase 的实时扫描自动产生该巧合的概率只有 0.24%。

**修正后的表述**：本 phase 的结论应为「**不相遇的两条链路互不干扰**」，
而非「共存成立」。
