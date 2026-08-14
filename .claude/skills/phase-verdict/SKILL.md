---
name: phase-verdict
description: Use when an experiment phase concludes in gr-ieee802-11 and results need archiving — writing verdict documents to docs/superpowers/notes/, updating env-vars.md / CLAUDE.md tables / hookify patterns, creating memory entries, or after any ABAB batch produces a VERDICT line (CONFIRMED / NOT CONFIRMED / REFUTED).
---

# Phase Verdict 归档（gr-ieee802-11）

## Overview

实验跑完只是半成品；归档链走完才算完成。判定三分支（CONFIRMED /
NOT CONFIRMED / REFUTED）的归档动作**不同**，按分支执行，缺一项不算完。
判定标准本身见 usrp-experiment skill / `.claude/rules/methodology.md`。

## 通用产物（三分支都要）

### 1. Verdict 文件

路径：`docs/superpowers/notes/YYYY-MM-DD-phase<N>-<slug>.md`
（slug 用判定词结尾，如 `-refuted` / `-not-confirmed`；CONFIRMED 可带 `-verdict`）

骨架：
```markdown
# Phase <N> verdict: <一句话标题含判定词>（YYYY-MM-DD）

## VERDICT（逐字粘贴批次输出原文，含 VERDICT: 行及其上下文段；禁止转述）
<原文>

## 判定
<env var / 改动>: <判定词>
- N=<n> 配对交错 ABAB，新鲜背靠背对照，governor=performance，电缆 --tx-scale 0.1
- 预注册主终点（<机制层级> → <终败|DS>）：mean diff <x>（paired t p=<y>）
- 次终点：<z>
- Loopback 门：OFF/ON 双臂 Final: OK=1 FAIL=0
- 分子分母窗口：<真值计数与 est_sent 各自覆盖的时间窗，写清楚>

## 机制与实现
<一句话机制 + 改动文件/函数 + env var 默认状态>

## 决策
<后续动作及理由，逐字记录——如"CONFIRMED 但保持 opt-in OFF（理由）">
```

### 2. env-vars.md 行

格式：`| \`ENV_VAR=值\` | 作用 | P<N> <判定词>（N=<n> ABAB <主终点 diff>, p=<p>）；<默认状态> |`
放进对应分层表（检测/同步、均衡器、解码、诊断）。

### 3. Memory（两步）

- 主题文件 `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p<N>_<slug>.md`，
  frontmatter 必须含 `metadata: type: project`；正文含证据（VERDICT 原文）与决策
- `MEMORY.md` 索引一行（<200 字符）：判定 emoji（✅/❌/⚠️/🔒）+ 关键数字 +
  verdict 文件路径指针

### 4. Commit

verdict 文件 + env-vars.md +（未提交的）实验代码同一 commit；
message 遵循 repo 风格 `docs(p<N>): ...` 或 `feat(p<N>): ...`，含判定词与关键数字。
memory 文件在仓库外，不进此 commit。

## 分支专属动作

| 判定 | 额外动作 |
|------|---------|
| **CONFIRMED 且翻默认** | 更新 CLAUDE.md「Harness 默认环境」表 |
| **CONFIRMED 但保持 opt-in** | verdict「决策」节逐字记录不翻默认的理由（CONFIRMED 是翻默认的必要非充分条件）|
| **NOT CONFIRMED** | 若轴关闭：进 CLAUDE.md「禁止方向」表 |
| **REFUTED / 轴关闭** | 进「禁止方向」表 **+ 同步** `.claude/hookify.warn-refuted-direction.local.md` 的 pattern（新 env var 加进正则）|

## 诚实清单（明确"没动什么"）

归档时列出**故意不动**的文件，防止后续误读。典型：
- 未翻默认 → 不动 CLAUDE.md harness 表
- 非关闭方向 → 不动禁止方向表和 hookify pattern
- 单个非默认翻转 phase → 不动 CLAUDE.md 顶部「当前状态」

## Red Flags

-  verdict 文件里转述 VERDICT 行而非逐字粘贴（Transparency 铁律 2）
- memory 主题文件缺 `metadata: type: project`
- REFUTED 方向只更新禁止方向表、忘了同步 hookify pattern（护栏会出现漏洞）
- 百分比数字不写分子分母窗口
- 归档拆成多个 commit 或与无关改动混在一起
