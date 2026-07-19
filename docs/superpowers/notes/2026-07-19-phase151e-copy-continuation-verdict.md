# Phase 151e: sync_short COPY Continuation + Over-Consumption Fix

**日期：** 2026-07-19
**状态：** ✅ FIXED + loopback 回归 PASS + USRP 4/4 PASS（mean 59.5±1.7）
**方法：** 回归门驱动的 systematic-debugging（红 → 根因 → 绿）

---

## 改动意图

消除帧起始处的调度/chunk 边界：SEARCH 态检测到帧后不再立即
`return 0`，而是在同一次 `general_work` 调用内以 COPY 语义继续拷贝
当前 chunk 的剩余样本，使消费 = 处理区域，与 chunk 划分无关
（p148/p151 系列 chunk-partition 非确定性主线的延续）。

## 回归门捕获的真 bug（loopback 是必需的）

初版实现 `consume_each(ninput); return o;` —— **消费整个输入块但只
产出 o 个样本**。当 `ninput > noutput`（无节流 loopback 的大 chunk 下
必然发生）时，`o < noutput` 上限使循环提前停止，多达
`ninput - copy_start - noutput` 个**从未写入输出的帧体样本被静默丢弃**
→ 帧中间出洞 → sync_long 收到残帧 → **loopback 确定性 OK=0**。

COPY 分支原本就是 `consume_each(o)`（只消费已产出），151e 违反了
这个记账约定。gap-break / MAX_SAMPLES 提前退出路径有同样问题。

**为什么 USRP 上之前"看着能跑"**：UHD 实时供数下 ninput ≈ noutput，
多数检测 rem ≤ noutput 不触发上限 → 大部分帧存活；但 chunk 划分
抖动时仍吃帧 → 表现为均值偏低、方差偏大。

## 修复（单变量）

`consume_each(copy_start + o)` —— 只消费"跳过前缀 + 实际拷贝"的
处理区域，未处理尾部留缓冲区给下一次调用（COPY 分支继续）。确定性
意图保留，越界消费消除。

## 验证

| 测试 | 修复前 | 修复后 |
|---|---|---|
| loopback 回归 (`test_direct_loopback.py`) | **OK=0 FAIL=0（帧全毁）** | **OK=1 FAIL=0** |
| USRP batch（infra-excluded） | mean=50.88±6.96（43-63, n=8） | **mean=59.5±1.7（57-61, n=4）**，arrival ~13.2% |

修复后均值 +8.6、std 紧 4 倍 —— 与"越界消费在 USRP 上也非确定性
吃帧"的根因一致。

## 教训（强化 retrospective 教训 1/2）

1. **回归门不可省**：此 bug 在 USRP 上完全隐性（均值只略低），在
   loopback 大 chunk 下确定性致命。若跳过 loopback 直接提交，就埋下
   又一个 chunk-partition 依赖 Heisenbug（p148 同类）。
2. **general_work 记账铁律**：`consume_each` 的参数必须是"实际处理
   的样本数"，不是"本次拿到的样本数"。产出被 noutput 截断时绝不能
   消费未产出的输入。

## 文件

- `lib/sync_short.cc`（151e COPY 续拷 + consume 记账修复）
- 验证批：`batch_results/20260719_160245/`（修复前对照）、
  修复后 4 跑（57/60/61/60）
