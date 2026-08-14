---
name: usrp-recovery
description: Use when the USRP X310 (192.168.10.2) is unresponsive — uhd_usrp_probe reports "No devices found", a batch was killed mid-run, UHD/RFNoC init hangs or crashes, DECODE_SUCCESS comes back ~0 across runs, or before rerunning experiments after any interrupted batch.
---

# USRP 挂死恢复（X310 @ 192.168.10.2）

## Overview

先诊断后动手。**「ping 通但 probe 报 No devices found」= 设备被占用或坏状态，
不是离线，更不是固件问题。** 本症状的恢复是秒级操作；reflash 不仅不对症，
还占整个硬件窗口并有变砖风险。

## 症状 → 诊断

| 症状 | 诊断 | 依据 |
|------|------|------|
| ping 通 + probe "No devices found" | 残留进程持有设备 / 坏状态 | P158 教训 #3 |
| probe 正常 + 批次全 0（"0 跑"）| UHD RFNoC init 秒崩（~19%/run）| P152 |
| ping 不通 | 才是真的网络/电源问题，查物理层 | — |

## 恢复序列（按序执行，每步验证后再升级）

```bash
# 1. 杀残留进程组（批次被 kill 后必做）
pkill -9 -f 'python.*usrp|python.*gnuradio|python.*ieee802'
sleep 3                                    # 不可省：等设备句柄释放

# 2. 确认无残留
pgrep -af 'usrp|gnuradio|ieee802' || echo "clean"

# 3. probe nudge —— 看到 X300 设备信息即恢复
uhd_usrp_probe --args addr=192.168.10.2

# 4. 仍失败：查 stray UHD 进程（含非 python 的 uhd 进程），杀干净后重复 3。
#    两轮完整清理（= 步骤 1-3 完整执行两次）仍失败 → 升级电源重启（power-cycle）。
#    本症状永远不要 reflash FPGA。
```

## 恢复验证门（重跑批次前全过才算恢复）

1. `uhd_usrp_probe` 打印完整设备信息含 X300
2. Pre-flight：governor=performance、wmem/rmem=2453333、ping 通
3. 一次 smoke 验证而非直接上批次：
   `IEEE80211_LSIG_VITERBI_CANDIDATE=1 ./usrp_realtime_validate.sh --tx-scale 0.1`
   预期 `PASS: DECODE_SUCCESS >= 15` 且零 under/overflow。
   若 DS≈0 且报 init 错 = P152 零跑模态 → 再 probe nudge 一次重试一轮。

## 预防

- 批次跑在 nohup/tmux 下，输出落文件——终端被杀不会孤儿化进程组
- harness stderr 在 `/tmp/rt_validate.err`（每轮覆写）；批次脚本的 `run_XX.err`
  只有脚本自身 stderr，在那里数触发次数会得到假零（P158）

## Red Flags — 出现即停

- 对「ping 通 + probe 空」症状 reflash FPGA 或先 power-cycle
- 省掉 `sleep 3` 直接 probe
- 恢复后跳过 smoke 验证直接上 ABAB 批次
- 在 `run_XX.err` 里数触发次数下结论

详细背景：`.claude/rules/usrp-operations.md`（其中挂死恢复节已降级为指针，
本 skill 是恢复序列的唯一源）。
