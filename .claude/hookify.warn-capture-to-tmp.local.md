---
name: warn-capture-to-tmp
enabled: true
event: bash
pattern: capture_usrp_txrx\.py[^\n]*\/tmp\/|\/tmp\/\S*\.(fc32|cfile|iq|dat|raw)\b
---

⚠️ **捕获文件写入 /tmp（P150 教训：/tmp 重启会被清空）**

IQ 捕获文件应放在 `/home/hy/captures/`。/tmp 下的捕获在重启后丢失，历史上有过教训。

如只是临时调试片段、不需要保留，可忽略本警告。
