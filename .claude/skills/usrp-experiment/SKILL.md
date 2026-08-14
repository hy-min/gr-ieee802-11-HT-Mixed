---
name: usrp-experiment
description: Use when testing any env var, code change, or hypothesis against USRP realtime FCS_OK in gr-ieee802-11 — running loopback regression gates, p158_abab_batch.py paired ABAB experiments, usrp_realtime_validate.sh baselines, or reaching experiment verdicts (CONFIRMED / NOT CONFIRMED / REFUTED). Archiving of finished verdicts is handled by the phase-verdict skill.
---

# USRP 实验执行序列（gr-ieee802-11）

## Overview

方法论**理由**已在 CLAUDE.md「方法论铁律」和 `.claude/rules/methodology.md`（每次会话
自动加载，不重复）。本 skill 只提供**执行序列 + 模板**，把散落在 4 个文件里的步骤
串成一条链。任何声称实验效果之前，必须走完对应链路。

## 执行序列

### 0. Pre-flight（每次硬件批次前）

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # 须 performance
sysctl -n net.core.wmem_max net.core.rmem_max               # 须 2453333
ping -c1 192.168.10.2
git status                                                   # 单变量：除实验改动外须 clean
```

### 1. Loopback 回归门（双臂，上 USRP 前）

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py
# 预期末行：Final: OK=1 FAIL=0（OFF 臂必须与基线逐比特一致；ON 臂也须过）
```

代码改动后先 `cd build && make -j$(nproc) && make install`（install 不可省）。

### 2. USRP 实验

基线健康检查（可选）：`./usrp_realtime_validate.sh --tx-scale 0.1` → `PASS: DECODE_SUCCESS >= 15`。

单变量金标准：
```bash
python3 p158_abab_batch.py --pairs 4 --tag <name> --exp-env NAME=VALUE
```
`--pairs 4` 是能达到 p<0.05 的最小 N，不可再减。输出必含 `VERDICT:` 行。

### 3. 终点指标（预注册，跑批次前声明）

| 机制作用层 | 主终点 | 依据 |
|-----------|--------|------|
| 解码级（作用于已到达帧）| 终败数（diff 须为负）| P162：DS 被到达率噪声 ±15-30 稀释 |
| 到达率级 | DS / arrival | P159 |

### 4. 判定（只用三档）

CONFIRMED / NOT CONFIRMED / REFUTED，定义唯一源 = `.claude/rules/methodology.md` §7。
速记：CONFIRMED = 配对 t p<0.05 且方向为正（预注册主终点）。

### 5. 归档链（全部做完才算完成）

**REQUIRED SUB-SKILL:** 用 `phase-verdict` skill 执行归档——verdict 文件骨架、
env-vars.md 行格式、memory frontmatter、三分支（CONFIRMED / NOT CONFIRMED /
REFUTED）的差异化动作（含禁止方向表 + hookify pattern 同步）、commit 分组。

## Verdict 模板

唯一源 = `phase-verdict` skill「通用产物 1」。此处不再留存副本（防漂移）。

## Red Flags — 出现即违规

- 用历史基线当对照（P158：±30 漂移混淆，+25.3 曾被 ABAB 证伪 p=0.485）
- 未配对单次对比就下结论
- 用离线回放正收益直接推实时结论（P163：方向可能相反）
- 「理论上应该有效」「机制上有帮助」「初步看起来不错」作为结论词
- 报百分比但不写分子分母窗口（P159b 的 "99.6%" 是 warmup 分母伪影）
- `--pairs` 小于 4 就声称显著性
- 时间不够就跳过 ABAB 直接给"乐观"结论——正确做法是声明
  「loopback PASS；USRP 效果 UNTESTED，勿启用」
- **NOT CONFIRMED 后反复重跑直到 p<0.05**（optional stopping / p-hacking，
  跑 ~3 次假阳性率升至 15-20%）。唯一合法路径：一次**预注册**的更大 N 批次
  （如 `--pairs 8`），并事先承诺接受结果——p≥0.05 即终判，不再有第三次
- **未 CONFIRMED 就翻转 harness 默认值**——「方向对」「不会有害」不是依据
  （P169 预加重正是"方向对"却显著有害）。默认值翻转唯一前提 = CONFIRMED

判定原文与每个方向的完整历史：`docs/superpowers/notes/`。
