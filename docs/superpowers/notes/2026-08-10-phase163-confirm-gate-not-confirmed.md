# Phase 163: 确认门（confirm gate）—— NOT CONFIRMED on USRP（轻微反效）

**日期：** 2026-08-10
**状态：** ❌ **NOT CONFIRMED / 轻微反效**（N=8 ABAB：DS −3.4 p=0.032，垃圾
L-SIG +12）。Feature 保留 opt-in 默认 OFF，不进 harness。**本轴
（sync_short 侧门控噪声）第 2 次连续失败，建议暂停。**

---

## 1. 承接与机制

Phase 162b 定位：到达率残余 = 噪声检出风暴（43–1548/300s，36× 摆动）→
sync_long 链污染。162b 触发点地板门 REFUTED（触发点强度与噪声重叠：
真帧 p50=37 vs 噪声 ≤23，爬坡 17×）。本 phase = 其"爬坡后取值"修正形态：
触发时前窥 in_cor[trigger, trigger+K) 测爬坡后峰值，峰 < floor 即拒
（noise episode 整条丢弃，不到 sync_long）。

## 2. 实现与离线证据（这部分有效）

内联前窥（无 PENDING 缓冲/无 tag 位移）。**初版缓冲 PENDING 实现引入了
chunk 边界相关的 L-SIG 错位（loopback 2/3 失败）；内联前窥消除了整类问题。**
TDD `p163_confirm_gate_unit.py` 5/5（含流丢弃证据）；门控 loopback 8/8 确定
性 OK=1；subagent 代码审查 **SAFE**（consume 记账全路径精确、peek 无越界、
OFF 路径逐字节一致）。真实捕获回放：垃圾 L-SIG 64→32（**减半**），DS +2。

## 3. USRP ABAB：NOT CONFIRMED（与回放矛盾）

N=8 交错 ABAB（floor=200 vs OFF），8/8 对有效：

| 指标 | A(OFF) | B(floor200) | 配对差 | 检验 |
|---|---|---|---|---|
| DS | mean 646.0 | mean 642.6 | **−3.38** | t(7)=−2.66, **p=0.032** |
| ARRIVAL | 648.0 | 645.4 | −2.62 | p=0.0135 |
| 终败 | 2.0 | 2.6 | +0.62 | p=0.18 (ns) |
| 垃圾 L-SIG | 39.1 | **51.4（+12.2）** | — | 与回放相反 |

**回放（垃圾减半）与实时（垃圾反增）矛盾。** 机制：实时流的 chunk 边界
动态使两个泄漏路径生效（subagent 审查发现 #4/#10）：①边缘默认确认——长
风暴突发横跨 chunk 尾时因 `avail<K` 被默认确认放行；②被拒噪声重触发并
计入自适应窗，阈值微抬。固定回放流没有这种动态 → 回放呈 Intended 效果。
**实时 ABAB（配对、活流、N=8）为金标准 → 以它为准。**

## 4. 结论与方法论

- **本轴（sync_short 侧门控噪声）两连败**：162b（触发点地板）+ 163（确认门）。
  根因是结构性的：触发点统计量与噪声重叠、chunk 动态脆弱、压缩流上
  restart 承重。按 systematic-debugging 规程（≥2-3 次失败质疑方案），
  **此微观优化轴暂停**。
- 系统维持 ~99.5–99.8%（静轮）/ ~97.8%（吵轮）；解码级 ~99.9%（P162 软
  viterbi 已入 harness 默认）。到达率残余主要是环境噪声风暴驱动。
- 若要继续攻到达率，剩余可行方向是 sync_long 侧接受门（对最脆弱文件动刀，
  风险高、收益边际递减）——需用户明确立项再评估。
- 162b/163 两个 feature 均保留 opt-in 默认 OFF，零基线影响。

**产物**：`p163_confirm_gate_unit.py`（5/5）、`p162b_analyze.py`（每臂机制
指标提取）、ABAB `batch_results/p163_confirm_gate/20260810_152528/`、代码审查
SAFE 记录。提交 38a32ce（实现）+ 本次（定案+注释修正+banner）。

**相关**：[[Phase 162b 地板门 REFUTED]]、[[Phase 162 软判决 viterbi]]、
[[Phase 159 margin 门]]（触发点门唯一成立的先例——门控与证据同点位）。
