# 实验方法论（gr-ieee802-11）

每条规则附：证据来源 + 验证方式。违反任何一条的结论无效。

## 1. 单变量

一次实验只改一个 env var 或一处代码改动。
验证：`git diff --stat` 只有一个文件，或实验臂只差一个 env var。
反例：P165 曾把"电缆 + tx-scale + LSIG 候选"混在一起，无法归因。

## 2. 先合成后 USRP

新想法必须先过 loopback 回归门（`Final: OK=1 FAIL=0`）再上 USRP。
验证：loopback 输出原文。
反例：P167 computed_fs 直用没先跑 loopback——其上 USRP DS 646→464。

## 3. 实时配对 ABAB 是金标准

命令：
```bash
python3 p158_abab_batch.py --pairs 4 --tag <name> --exp-env NAME=VALUE
```
判据（预注册）：CONFIRMED 当且仅当 paired t p<0.05 且 mean diff>0。
验证：输出含 `VERDICT:` 行；把该行原文贴进结论。

为什么必须配对：未配对跨区块比较有 ±30 时间漂移混淆
（P158：初步 +25.3 被 ABAB 证为混淆，p=0.485）。
为什么必须实时：离线回放正收益 ≠ 实时正收益
（P163：回放垃圾 L-SIG 减半，实时反增 +12）。

## 4. 分母同域

真值计数与 est_sent 必须覆盖同一时间窗。
教训：P159b "448/450=99.6% 目标达成" 是 warmup 分母伪影——
est_sent 只算测量窗，DECODE_SUCCESS 真值含 20s warmup 的 ~200 帧，
真实率 ~69%（P160 修正）。
验证：报告任何百分比前，写出分子分母各自的统计窗口。

## 5. 终点指标按机制层级选

解码级机制（作用于已到达帧）的信号在 DS 上被到达率噪声（±15-30）稀释，
必须看终败数；到达率机制看 DS/arrival。
教训：P162 软 viterbi 在 DS 上 p=0.13（看似无效），在终败上 p=0.0047
（真实 -62%）。
验证：预注册主终点时写明"该机制作用于哪一层"。

## 6. 新鲜背靠背对照

设备漂移真实存在（P158-W32：对照 162 vs 历史 200）。
任何 A/B 的对照臂必须当次新鲜采集，禁止引用历史基线。
验证：对照与实验的时间戳间隔 < 30 分钟。

## 7. 判定词表（只用这三档）

- **CONFIRMED**：配对 ABAB p<0.05 且方向为正
- **NOT CONFIRMED**：p≥0.05 或方向不定
- **REFUTED**：方向为负且显著，或机制级证伪

禁止词汇：「理论上应该有效」「机制上有帮助」「初步看起来不错」
作为结论。这些只能作为提出假设的动机。

## 8. 中途被打断的批次

batch 脚本无 hang 超时——杀死批次可能把 X310 留在坏状态，下一次
UHD init 会挂。恢复：杀残留进程组 + `uhd_usrp_probe` nudge
（详见 usrp-operations.md）。

harness stderr 落在 `/tmp/rt_validate.err`（每轮覆写）。
批次脚本的 `run_XX.err` 只有脚本自身 stderr——在那里数触发次数
会得到假零（P158 教训）。
