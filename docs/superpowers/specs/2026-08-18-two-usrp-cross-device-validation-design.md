# 双 X310 跨设备真链路验证 — 设计规格

**日期:** 2026-08-18
**状态:** 已批准(用户 2026-08-18 确认)
**分支:** TEST2
**前置状态:** 硬件拓扑全部实测验证通过(见 §2)

---

## 1. 目标

在两台 X310 上验证 gr-ieee802-11 的**跨设备真链路 FCS_OK**:设备2 TX →
电缆 → 设备1 RX。证明 PHY 不依赖同板回环,并把验证体系扩展到真实两节点
regime(独立晶振 → 真实 CFO,这是同板 99.55% 基线从未覆盖的领域)。

**成功标准(预注册):**
- 跨设备基线(FCS_OK 率)拿到数字即成功——不预设 99%,独立时钟是新领域,
  首轮目标是建立跨设备基线;
- 变体实验一律走配对 ABAB 金标准,判定词只用 CONFIRMED / NOT CONFIRMED /
  REFUTED;
- 单设备基线(99.55% 复现路径)不被任何改动破坏。

**非目标(本设计不含):** 共享 10 MHz 参考实验(第二轮,C 臂);
并行 ABAB 能力(未来);真实数据链路(ether_encap→IP 流量,未来)。

## 2. 硬件拓扑(2026-08-18 实测验证)

| 项 | 设备2(TX 角色) | 设备1(RX 角色) |
|---|---|---|
| 硬件 | X310 rev 13, product 30818, 无 GPSDO | 同左(对称) |
| IP | **192.168.20.3** | **192.168.10.2** |
| 主机网口 | USB 千兆 RTL8153 `enxec1ac3009118` | 板载 2.5GbE `enp4s0` |
| 主机侧 IP | 192.168.20.1/24(NM profile `USRP2`,持久) | 192.168.10.1/24(NM profile `USRP`) |
| RF 端口 | A:0 **TX/RX** | A:0 **RX2** |
| 频点 | **5240 MHz**(扫频数据驱动:噪声底 -69.6 dBFS,全候选最安静;UNII-1 无 DFS;离 5250 基线 10 MHz) | |

**验证证据(本规格落笔时已测):**
- 双设备 UHD probe 通过(X-Series, rev 13 双确认);
- RF 通路:TX 开 -35.18 dBFS vs TX 关 -57.27 dBFS,**SNR 22.09 dB**;
- 噪声底扫描:5180 -57.7(有 WiFi)/ 5220 & 5240 -69.6 / 5260 -69.2 /
  5280 -68.7 / 5300 -67.9 / 5320 -66.8 dBFS;
- 设备2 EEPROM `ip-addr0` 已烧写 192.168.20.3(避开其自身 SFP+0 默认
  192.168.20.2),power-cycle 生效;
- USB 千兆网卡 20 Msps 连续 RX 8s 压测 0 overflow(null sink)——但
  **关键路径(RX 连续流)仍走 2.5GbE 板载网卡**,USB 网卡只承载 TX 突发
  (~1 Mbit/s 平均),避开 USB 抖动风险。

**布线教训(已验证的坑):** 设备每个槽位两个 SMA 口长得一样,
TX 必须插 **TX/RX** 口、RX 插 **RX2** 口;插反 = 纯噪声底(实测 -57.5 dBFS
无信号,端口对调后 22 dB SNR)。UFW 阻断 UHD 广播发现,探测/实验必须
显式 `addr=`。

## 3. 方案一:最小 harness 参数化(本规格实现范围)

**改动文件(共 2 个,~25 行):**

1. `test_usrp_rxonly_instrumented.py`
   - 两处硬编码 `addr=192.168.10.2`(usrp_sink 第 ~207 行、usrp_source
     第 ~293 行)改为 `--tx-addr` / `--rx-addr` 参数;
   - 现有 `--freq` 参数(5250 默认)保持不变,跨设备模式传 5240;
   - 默认值均 192.168.10.2 → 单设备行为逐比特不变。
2. `usrp_realtime_validate.sh`
   - 加 `--tx-addr` / `--rx-addr` / `--freq` 解析与透传
     (FREQ 变量当前硬编码 5250,需参数化);
   - 计数/判定逻辑(PASS 阈值、GT_OK/GT_FAIL、warmup)零改动。

**数据流(跨设备模式,单进程单流图):**

```
[TX] ether_encap→mac→mapper→…→IFFT→CP ──UHD──▶ 设备2(USB网卡, A:0 TX/RX)
                                                  │ 电缆 @5240 MHz
[RX] 设备1(enp4s0, A:0 RX2)──UHD──▶ sync_short_fused→sync_short→sync_long
        →splitter→FFT→frame_equalizer→decode_mac→parse_mac→ether_encap
```

- 两 UHD 块各连一台设备,计数天然同域(P160 教训:分母同域);
- harness stderr 落点 `/tmp/rt_validate.err` 不变,fate 分析
  (DECODE_SEQ / FAIL_PSDU_DUMP)全保留。

**错误处理:** 任一设备 probe 失败 → harness rc≠0 → PASS 判定自动失败
(现有逻辑覆盖);新增启动时打印两端 addr 与 UHD 版本(诊断用,不影响判定)。

**回归风险:** loopback 门不含 UHD(纯软件),预期不受影响;单设备基线
复跑是参数化正确性的验证(§5 门 2)。

## 4. 实验矩阵

| # | 实验 | 内容 | 判定 |
|---|------|------|------|
| A | 跨设备基线 | 默认 harness 配置 @5240,tx-scale 0.1;首轮 harness 默认 45s(3×15s),数据有效后 300s 长测 | 拿数字即成功;记录 FCS_OK 率/DS/终败 |
| B | 头部补偿回翻 | `HDR_COMP_DISABLE=0`(同板电缆时代关闭;跨设备有真实 CFO ±50-100kHz,头部 CFO/SFO 补偿可能必须开) | 配对 ABAB;执行依赖方案二的 batch 参数透传落地(B 在方案二之后跑) |
| C | 共享 10 MHz 参考 | 设备1 REF OUT → 设备2 REF IN,第二轮再做 | 第二轮;检验"99.9% 唯一路径"假设 |

**跨设备 regime 差异(实验解读的关键):** ①真实 CFO(两板独立 TCXO);
②LO 相位噪声 = TX⊕RX 两源(√2 恶化,共享参考可部分抵消);
③外部异帧污染面更大(P174 教训:失败帧分类必须先 ourmac + CFO 指纹判别);
④harness 默认值全部是在同板电缆 regime 调的,跨设备下**默认值本身即假设**,
B 臂是第一个要验证的回翻。

## 5. 测试门(顺序执行,全过才进入实验)

1. **loopback 回归门:** `LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/
   bindings:python:examples /home/hy/conda/envs/gnuradio/bin/python
   examples/test_direct_loopback.py` → `Final: OK=1 FAIL=0`
2. **单设备基线复跑:** 默认参数 `./usrp_realtime_validate.sh --tx-scale 0.1`
   @5250 → `PASS: DECODE_SUCCESS >= 15`(证明参数化未破坏现有路径)
3. **跨设备首轮(实验 A):** `./usrp_realtime_validate.sh --tx-scale 0.1
   --tx-addr 192.168.20.3 --rx-addr 192.168.10.2 --freq 5240`
   → 记录 FCS_OK 率/DS/终败,不设通过线,数字即结果。

**批次前检查(每轮必做):** governor=performance、wmem/rmem=2453333、
双设备 ping 通。

## 6. 方案二(后续,本规格不实现)

`p176_cross_device.py` purpose-built harness:双 addr 参数化、时钟模式
(独立/共享 REF)、每设备独立增益/tx-scale、预注册成功标准、内置 seq 命运
分析,成为后续跨设备实验(含 ABAB 跨设备化)的标准工具。在方案一拿到
首轮数据后启动。

## 7. 风险与对策

| 风险 | 对策 |
|------|------|
| 跨设备基线低(独立时钟 regime) | 预期内;A 拿数字,不设通过线;真实 CFO 下 B 臂是主要杠杆 |
| USB 网卡 TX 突发欠载 | TX 占空比 0.17%,余量巨大;若出现 → 把 TX 移回 enp4s0(设备2 加第二网卡)或双 SFP+ |
| 外部异帧污染(5240 附近 WiFi) | P174 工具链:ourmac 标记 + CFO 指纹 + FAIL_PSDU 分类;污染加重则换 5220 |
| harness 改动破坏单设备路径 | 门 2 单设备基线复跑 + 默认值不变 |
| 设备挂死/坏状态 | usrp-recovery skill 唯一源;probe 空 + ping 通 = 占用非离线 |

## 8. 方法论继承(项目铁律,全部适用于本设计)

单变量(一次一个 env)、先合成后 USRP(loopback 门)、实时配对 ABAB 金标准、
分母同域、终点指标按机制层级选、新鲜背靠背对照、判定词三档、改动 >50 行
先输出计划、声称效果必贴测量原文。
