# Phase 172b verdict: 定时突发 TX（BurstTagger）NOT CONFIRMED——实现修复无副作用，效力因事件率塌缩不可分辨（2026-08-14）

## VERDICT（逐字粘贴批次输出原文）

```
arm g1 rc=0  timed_burst=0  DECODE_SUCCESS: 616   LDPC FCS error: 0
arm h1 rc=0  timed_burst=1  DECODE_SUCCESS: 683   LDPC FCS error: 0   (marker: [P172] timed-burst TX ENABLED)
arm g2 rc=0  timed_burst=0  DECODE_SUCCESS: 577   LDPC FCS error: 0
arm h2 rc=0  timed_burst=1  DECODE_SUCCESS: 700   LDPC FCS error: 0

强帧帧中洞扫描（预注册主终点，A 族 = 强帧 L-SIG 区 100-400 样本洞）：
  g1 OFF: bursts=658  A-family=2 (burst[0] 376/509, burst[450] 346/57)
  g2 OFF: bursts=602  A-family=0
  h1 ON : bursts=748  A-family=0
  h2 ON : bursts=739  A-family=0
  (c1 OFF 前期臂: bursts=632, A-family=0)
VERDICT: NOT CONFIRMED —— ON 臂 0/1487 vs OFF 臂 2/1892，A 族今日基础率 ~0.1%
         （8月13 为 0.3-0.5%），4 臂交错无法分辨；定时突发无副作用（DS 健康）。
```

## 判定

- `IEEE80211_TX_TIMED_BURST=1`（test_usrp_rxonly_instrumented.py，TX 侧）：**NOT CONFIRMED**。
- N=4 臂交错（OFF/ON/OFF/ON，新鲜背靠背，governor=performance，电缆 --tx-scale 0.1）。
- 预注册主终点（到达率机制 → A 族撕裂率+洞位置）：事件率过低，统计不可分辨。
- 次终点：DS ON 683/700 vs OFF 616/577（到达率噪声 ±30，不作效力依据；仅证明无害）。
- Loopback 门：Final: OK=1 FAIL=0（改动为 harness 侧，off 臂与基线一致）。
- 分子分母窗口：撕裂率分母 = 各臂全捕获 bursts（20s warmup+60s 测量）；DS = harness
  地面真值计数（含 warmup 帧，est_sent 只算测量窗——已知 warmup 分母伪影，DS 仅作健康度）。
- 实现修复记录（两次 bug）：① 缺 `import numpy`（P170 遗留，接线即 NameError）；
  ② tx_time 流位置锚定对突发源失效（样本位置≠墙钟 → 全部 late → d1 臂 DS=0、
  空口仅 8 bursts/80s），改为墙钟锚定 `t0_dev + (host_now−h0) + 50ms` 后正常。

## 机制与实现

H4' 模型：帧间 100ms 空闲 → uhd_sink/FPGA 管道见底 → 下一帧冷启动 stall，固定排水
深度后出洞（洞起点锁定 ~345，时长可变）。定时突发用空闲期预填整帧入 FPGA 缓冲，
起跑非冷启动——形状对症。但今日 A 族率塌缩，无法验证；B/C episode 设备侧（见
phase172 姊妹篇），本不指望定时突发治。

## 决策

- **保持 opt-in 默认 OFF**（效力 NOT CONFIRMED；red-flag 规则：未 CONFIRMED 不翻默认）。
- 轴不关闭：A 族率有日际波动（0.1-0.5%），高发日可用本 env 重测效力（预注册终点
  不变：A 族撕裂率）。
- 禁止反复重跑凑 p<0.05（optional stopping）——如要复测须一次预注册更大 N 批次。
