---
name: require-new-env-var-conventions
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: lib/.*\.(cc|h|hpp)$
  - field: new_text
    operator: regex_match
    pattern: getenv\("IEEE80211_
---

⚠️ **新增/修改 `IEEE80211_*` env var — 项目约定检查清单**

1. **默认 OFF（opt-in）**：不设该 env 时行为必须与基线逐比特一致。
   验证：不设该 env 跑 loopback 回归门（`Final: OK=1 FAIL=0`）且与基线一致。
2. **登记目录**：在 `.claude/rules/env-vars.md` 对应分层表中新增一行
   （名称 / 默认 / 作用 / 判定），实验后填判定词。
3. **harness setdefault 例外**：若要进 CLAUDE.md 的 harness 默认表，必须有
   ABAB VERDICT 支撑，不能只凭回放结果。

另提醒：`lib/*.cc` 改动禁止函数级 `static` 可变缓冲区（P147 Heisenbug），
scratch buffer 必须栈/成员私有。
