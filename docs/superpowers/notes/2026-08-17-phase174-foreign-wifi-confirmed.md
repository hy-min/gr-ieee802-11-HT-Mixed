# Phase 174 verdict: 外部 WiFi 异帧发现与定量 CONFIRMED——P173"设备模拟域"勘误 + 残余分解第三次修正（2026-08-17）

## VERDICT（逐字粘贴测量输出原文）

**CFO 指纹（p174_episode_cfo.py / p174_rx1.fc32 + p172_tb_c1.fc32）：**
```
c1 (8-14):  我方强帧 +65..+77kHz(首帧) → 众数 +40..+60kHz；异帧 −67..−71kHz(全长组)
            与 −88..−92kHz(短帧组, extent 865-875)
p174_rx1:   我方众数 +49.8kHz(分布 +30..+80, 合成器偏置随次漂移)；异帧 −65kHz 群
            peak 2.15-2.83 (slots 262/567/771/774/873 成对出现) + ~0kHz 弱群
噪声底:     episode 附近 0.0213 vs 别处 0.0213（RX 增益无跌落）
```

**seq 线程地面真值（DECODE_SEQ=1 + FAIL_PSDU_DUMP=1，90s）：**
```
stderr: DECODE_SUCCESS=893 DECODE_FAIL=4 SEQ_lines=893 FAIL_PSDU=2
seq thread: ours_decoded=893 ours_lost(gaps)=7 foreign_decoded=0
FAIL_PSDU: ours=1 foreign=1
  OURS:    08000000424242424242232323232323ffffffffffff8022787878...  (真终败)
  FOREIGN: b5b7af0efd2d6343920802d153fbad3d31d10b96021cf679cf61b042...  (垃圾head)
VERDICT: H5 CONFIRMED —— 残余中的"episode/弱帧"含外部 WiFi 异帧（CFO 指纹异号），
         DECODE_SUCCESS 零污染（0/893），DECODE_FAIL 污染 1/2 帧；
         本跑残余 0.78% 分解：异帧碰撞 ~0.56% + A族 0.11% + 真终败 0.11%
```

**逐槽命运图（p174_rx1，900 槽，宽 CFO 带分类器）：**
```
EMPTY=0  TORN-OUR=1 (slot 449, hole@344 —— A 族锁定位置再现)
碰撞槽: slot 262/567/771/873 各有 −65kHz 强异帧×2 + 我方弱帧(peak 1.7-1.9) 同槽
       → 我方帧 L-SIG 被撞毁，静默丢弃（seq gap，不到 decode_mac）
```

## 判定

- **H5（episode 帧=外部异帧）：CONFIRMED**。判别证据三链：CFO 指纹异号且零重叠
  （−65/−89kHz vs 我方 +45..+75kHz 群）、噪声底不变（排除 RX 增益跌落）、
  短帧 extent 865-875（异帧本身短，非"截断"）。
- **P173"设备模拟域"勘误**：P173 推理的隐藏假设（弱帧=我方帧退化）证伪；
  TX 数字链干净是因为我方帧本就完好。verdict 文件已加勘误块（commit bdec10d）。
- **计数污染定量**：DECODE_SUCCESS 通胀 0/893（seq 线程证）；DECODE_FAIL 污染
  1/2 帧（FAIL_PSDU head 鉴定）。4 行 DECODE_FAIL = 2 帧 × (Conv+LDPC fallback
  双行)——行数≠帧数。
- **残余分解（第三次修正，本跑 900 发送/窗=全 90s 含 warmup）**：893 解码 +
  7 gap = 900 自洽；7 gap = 1 A 族撕裂(slot 449, hole@344) + 1 真终败(我方 head
  完整) + ~5 异帧碰撞静默丢弃。异帧碰撞是最大残余成分（~0.56%）。
- **分子分母窗口**：发送数 = seq 线程 ours+lost（全 90s）；DECODE_SUCCESS/FAIL =
  全程计数；异帧普查 = 全捕获 940 bursts 的 CFO 分类。

## 机制与实现

- 异帧物理通路：用户确认 RF A 两橙色线直通头对接（真电缆链路），但异帧幅度
  仅比我方低 ~-1.2dB（peak 2.74 vs 2.07 中位数）→ 直通头/线缆屏蔽有实际泄漏，
  源头是 5250MHz(ch50) 上的真实 802.11 设备（环境中有 ch36/149/157/161 AP 可见，
  ch50 发射体未在扫描缓存中——可能隐藏 AP/客户端/实验室设备）。
- **CFO 指纹使用注意**：我方 CFO 非恒定（首帧 +75kHz → 众数 +45kHz，PLL pull-in/
  温漂）；判别必须数据驱动（当跑众数/seq 线程），固定带会误判（本轮已踩一次）。
- **计数去污染修复（已部署）**：decode_mac.cc 全部 6 处 DECODE_SUCCESS/FAIL 行
  追加 `ourmac=0/1`（addr1==0x42×6 判定），日志级改动不改解码路径；
  loopback 门 Final: OK=1 FAIL=0；USRP 基线 PASS DS=631，ourmac 标记实测在线。
- 工具：`p174_episode_cfo.py`（CFO 指纹+噪声底+extent）、`p174_seq_fate.py`
  （seq 线程分类+FAIL_PSDU 归属+空口普查）。

## 决策

- 异帧方向不关闭但**物理优先**：拧紧/屏蔽直通头、远离 WiFi 设备（或换干净频点）
  可消除最大残余成分（碰撞 ~0.56%）；软件侧 SIC 级修复不做（超出范围）。
- ourmac 标记保持常开（日志格式增强，非 env 开关）；harness 计数不受影响
  （按行头子串计数，追加字段向后兼容）。
- A 族地位不变（H4 定时突发候选，待高发日）；99.9% 路径重估：物理屏蔽异帧后
  残余 ≈ A 族 0.11% + 真终败 0.11% + LO 相位噪声尾——GPSDO 仍是终局路径。
