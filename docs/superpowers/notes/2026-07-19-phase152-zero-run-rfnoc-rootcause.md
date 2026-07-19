# Phase 152: "0-Run" 双模态根因 — UHD RFNoC Init 抖动（非解码器）

**日期：** 2026-07-19
**状态：** ✅ ROOT-CAUSED + 修复已实施并验证（真实 batch + 合成故障注入）
**方法：** systematic-debugging（先根因后修复）

---

## 症状

USRP 实时验证 batch 统计呈双模态：大多数跑 DECODE_SUCCESS=37-63，
间歇出现 0 跑（当日 26 跑中 5 次，~19%）。零跑位置不定（run_01/02/03/04
均出现），污染 mean/std 统计，且曾被怀疑是 151e（sync_short COPY
continuation）引入的不稳定。

## 根因（证据链闭环）

1. **全部 5 个零跑签名一致**：harness exit rc=1，启动后 **~2 秒即死**
   （正常跑 ~70 秒；由相邻 run 日志 mtime 差确认）。
2. **唯一保留 harness stderr 的零跑**（150100 run_01，stderr-dump 功能
   14:53 才加入）：
   `RFNOC::GRAPH ... RfnocError: OpFailed: Management operation failed`
   → `RuntimeError: Failure to create rfnoc_graph`，死于
   `test_usrp_rxonly_instrumented.py:92` 的 `uhd.usrp_sink` 创建。
3. **位置模式**：每个零跑紧跟一个正常跑（含跨批次）→ 上一次会话
   teardown 使 X310 RFNoC 控制面进入坏状态；失败的 init 尝试本身将其
   复位 → 再下一次自然恢复（历史 4/4 次零跑后下一跑无干预自愈）。
4. **无第二零模式**：全部 26 跑中健康跑最低值 = 37；**不存在"满时长
   但解码 0"**。解码器、同步、均衡器完全不涉及。**151e 嫌疑洗清。**

即 retrospective 记载的"USRP RFNoC 崩溃后坏状态"，首次在统计层面
定量（~19%/run）并与解码器故障完全区分。

## 修复（batch_usrp_validate.py，单变量）

1. **重试路径加固**：RFNoC init 失败时先 `uhd_usrp_probe` nudge
   （retrospective 记载的恢复手段）+ sleep 5s 再重试（原仅 sleep 2s，
   且该重试逻辑 15:09 才加入、从未经真实故障检验）。
2. **统计卫生**：3 次尝试全败的 infra 失败单独计数
   （`infra_failures`），从 mean/std 中排除，batch 退出码对其中性
   （infra 失败 ≠ 解码器回归）。
3. **可测试性**：`BATCH_VALIDATE_SCRIPT` env 覆盖，支持合成故障注入。

## 验证

| 测试 | 结果 |
|---|---|
| 真实 batch 8 跑（160245） | **8/8 PASS，mean=50.88±6.96，min=43，max=63，0 零跑**（未触发重试，统计干净） |
| 合成 recover 模式 | attempt1 RFNoC 失败 → probe nudge → attempt2 恢复 PASS，attempts=2 ✅ |
| 合成 always 模式 | 3 次全败 → infra_fail=1 单独计数、排除出统计、exit=0 ✅ |

重试路径在真实故障下未被触发（本批 0 故障），但机制经合成故障注入
端到端验证；历史 4/4 次"零跑后下一跑自愈"证据支持其对真实故障有效。

## 统计结论（对项目主线）

- 151e 之后的健康基线：**mean ≈ 51-53 DECODE_SUCCESS/45s（arrival ~11-14%）**，
  较 151d 时期（mean ~35-45，含零跑污染）的真实提升比之前估计的更清晰
  —— 早期批次的低 mean 部分是零跑污染所致。
- 后续所有 batch 统计应使用 infra-excluded 口径。

## 文件

- `batch_usrp_validate.py`（重试加固 + infra 统计 + env 覆盖）
- 合成夹具：`/tmp/fake_validate.sh`（FAKE_MODE=recover|always）
- 真实验证批：`batch_results/20260719_160245/`（8/8 PASS）

**相关：** [[Phase 150 realtime path]], [[Phase 151d gap-stuck fix]],
retrospective "USRP RFNoC 坏状态" 条目。
