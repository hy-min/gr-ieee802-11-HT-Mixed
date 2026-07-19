# Phase 154: MIN_PLATEAU=16 — 到达率 2.1× 突破

**日期：** 2026-07-19
**状态：** ✅ **BREAKTHROUGH** — USRP batch mean 59.5±1.7 → **124.5±16.7**（arrival ~13% → ~28%），6/6 PASS
**来源：** Phase 153 漏斗测量的直接导出（先根因后修复的又一次验证）

---

## 改动（单变量，免重编译）

`IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16`（Phase 89 预留旋钮）：
sync_short 检测需要 **17 个连续**超门限样本（原 MIN_PLATEAU=2 → 仅需 3 个）。
真帧 L-STF 平台 ~1600 样本必过；噪声 boxcar（16 样本平滑）的尖峰很难
持续 17 个连续样本。

已设为 `test_usrp_rxonly_instrumented.py` 的 `os.environ.setdefault`
默认值（与 145c 获胜配置同位置），外部可覆盖。C++ 默认不变（其他用户
不受影响）。

## 结果

| 指标 | 基线（=2） | =16 | 变化 |
|---|---|---|---|
| USRP batch DECODE_SUCCESS | 59.5±1.7（57-61） | **124.5±16.7（110-155）** | **2.1×** |
| arrival（est） | ~13.2% | **~27.7%** | +14.5pp |
| loopback 回归 | OK=1 | OK=1 | 无回归 |
| 总检测数（假检测） | 2828 | 1709 | -40% |
| COPY 占用 | 22.5% | 16.5% | -6pp |
| 强帧检出率 | 71.5% | 79.8% | +8.3pp |
| frame-start（假帧） | 3171 | 1442 | **-55%** |
| good-len L-SIG | ~96 | **198** | **2.1×** |
| HT-SIG CRC OK | 74 | **163** | **2.2×** |

## 机制（修正 Phase 153 的归因深度）

收益**不是**主要来自检出率提升（+8.3pp），而是 **L-SIG 级翻倍**
（96→198）：MIN_PLATEAU=2 时假检测（46/s，真帧仅 10/s）在**真帧对齐
窗口附近/期间发出假 wifi_start 标签**，通过 sync_long 的 COPY 态标签
处理器把 sync_long 从真帧对齐中拽出，导致真帧 L-SIG viterbi 拿到错位/
污染的符号窗口 → 垃圾 len。Phase 153 看到的"52% L-SIG 垃圾"中，
**链状态污染是大头，不只是 1.77 rad 噪声墙**。

证据链：假检测 -40% → 假 frame-start -55% → good L-SIG 2.1× →
HT-SIG OK 2.2× → FCS 2.1×。各环节增益一致。

## 附带验证

- **Phase 152 加固重试首次在真实故障上生效**：run_05 attempt 1 RFNoC
  init 失败 → uhd_usrp_probe nudge → attempt 2 成功（110）。
- 诊断跑确认（DECODE_SUCCESS=143）：新漏斗各计数与机制解释一致。

## 实验过程教训

第一次 batch 的 env 没生效（`MIN_PLATEAU_OVERRIDE` 只在
`wifi_phy_hier.py` 读取，RX-only harness 硬编码 2）—— run_01=53 与
基线一致暴露了这一点。**验证"处理变量真的到达了被测系统"是单变量
实验的前提。**

## 产物

- `test_usrp_rxonly_instrumented.py`：MIN_PLATEAU env 支持 + 默认 16
- `sync_short_fused.cc`：SPIKE dump 加 `pos=`（env-gated 诊断，Phase 153）
- 验证批：`batch_results/`（=16 6 跑 mean 124.5）

**相关：** [[Phase 153 arrival funnel]], [[Phase 151e]], [[Phase 152]]

---

## Phase 154b 补遗：M 值扫描 → 最优 24（同日稍后）

| M | mean DECODE_SUCCESS/45s | arrival |
|---|---|---|
| 2 | 59.5±1.7（n=4） | ~13% |
| 16 | 124.5±16.7（n=6） | ~28% |
| **24** | **200.0±8.5（n=3）** | **~44%** |
| 32 | 202.3±15.7（n=3） | ~45% |
| 48 | 128.7±11.1（n=3） | ~29% |

- 24-32 为宽平台区，48 出现悬崖（开始误伤真帧 onset 相关性爬升段）。
  **默认定为 24**（与 32 同均值，std 更紧 8.5 vs 15.7，离悬崖更远）。
- 残余漏检签名分析（=16 诊断跑，131/650 漏）：56% 直接 COPY 捕获
  （det before=3-12718）+ 44% 长 COPY episode 远端触发（before 高达
  548k；噪声功率围绕 gap 检测器 0.01 阈值波动 → gap 计数器被重置 →
  假 COPY episode 平均 ~6ms）→ 残余 20% 本质仍全是 COPY 捕获，
  更高 M 直接对症（扫描证实）。
- **10 MHz loopback 伪影**：`test_direct_loopback.py` 用
  `wifi_phy_hier(bandwidth=10e6)`，10 MHz 下 L-STF 周期为 8 样本，
  16-lag boxcar 失配 → M=24 在 loopback 漏检（OK=0）；M=16 勉过。
  **loopback 回归门在默认配置（M=2）下不受影响（OK=1 已验证）**；
  sync 调谐是 20 MHz USRP 专属。boxcar+adaptive env 本身就会使
  10 MHz loopback OK=0（配置域不同，与 M 无关）。
- 已提交为 harness setdefault 默认 24（可 env 覆盖，C++ 默认不变）。
