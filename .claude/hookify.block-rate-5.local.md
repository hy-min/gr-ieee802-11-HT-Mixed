---
name: block-rate-5
enabled: true
event: bash
pattern: --rate[=\s]+5\b
action: block
---

🚫 **已阻断：`--rate 5` 禁止使用（P58：溢出 48×）**

本项目 USRP 采样率固定 `--rate 20`。`--rate 5` 会导致 48 倍溢出，实验数据全部无效。

请改用 `--rate 20` 重新执行。
