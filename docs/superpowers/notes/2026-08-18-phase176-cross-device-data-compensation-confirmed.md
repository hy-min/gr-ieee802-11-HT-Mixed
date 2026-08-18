# Phase 176 verdict: 跨设备数据路径 CFO/SFO 补偿 CONFIRMED(2026-08-18)

## VERDICT(批次输出原文,逐字粘贴)

```
[ABAB] pairs=4  A=control(IEEE80211_HDR_COMP_DISABLE unset)  B=experiment(IEEE80211_HDR_COMP_DISABLE=0)
[ABAB] order: odd pairs A->B, even pairs B->A (drift cancellation)
ARRIVAL(enc=0 len=72): per-pair diff (B-A)=[-146, -176, -310, -358]
ARRIVAL(enc=0 len=72): mean diff = -247.50  std = 102.52  t(3) = -4.83  paired t p = 0.0169  wilcoxon p = 0.1250
DECODE_FAIL(LDPC terminal): A=[543, 559, 597, 571]
DECODE_FAIL(LDPC terminal): B=[117, 140, 148, 116]
DECODE_FAIL(LDPC terminal): per-pair diff (B-A)=[-426, -419, -449, -455]
DECODE_FAIL(LDPC terminal): mean diff = -437.25  std = 17.44  t(3) = -50.14  paired t p = 0.0000  wilcoxon p = 0.1250
VERDICT: CONFIRMED: experiment improves DECODE_SUCCESS (+443.8/45s, p=0.0000; arrival -247.5)
```

支撑数据(探索期,非 ABAB):

```
跨设备 45s 单臂:    HDR_COMP_DISABLE=1 → DECODE_SUCCESS=0   (0/450)
                    HDR_COMP_DISABLE=0 → DECODE_SUCCESS=435  (含 warmup 伪影)
跨设备 300s 长测:   PDU 口径 FCS_OK=1968/3000 ≈ 65.6%(656/652/656 每 100s 窗恒定)
                    DECODE_FAIL=1009 行 ≈ 505 帧(ourmac=0:557 行 / ourmac=1:481 行)
loopback 门:        修复前后均 Final: OK=1 FAIL=0
单设备基线:         HDR_COMP_DISABLE=1 默认下门控生效(改动前复测 DS=644 PASS;改动后待复测)
```

## 判定

`IEEE80211_HDR_COMP_DISABLE=0`(跨设备模式):**CONFIRMED**
- N=4 配对交错 ABAB,新鲜背靠背对照,governor=performance,电缆 --tx-scale 0.1
- 预注册主终点(解码级机制 → 终败):mean diff **-437.25/45s**(paired t **p=0.0000**,4/4 对全负)
- 次终点:DECODE_SUCCESS +443.8/45s(p=0.0000);ARRIVAL **-247.5(p=0.0169,负向,open question)**
- Loopback 门:双臂 Final: OK=1 FAIL=0
- 分子分母窗口:45s 测量窗 est_sent~450;DECODE_FAIL 为 err 全程累计计数含 20s warmup
  (A/B 双臂 warmup 相同,配对差不受影响;绝对成功率以 PDU 口径 300s 长测 ~65.6% 为准)

## 机制与实现

**机制**:frame_equalizer 数据符号路径(`extract_ht_data52_direct_tx_order`)从不应用
`d_phase_diff_per_sc × counter` 的每符号 CFO/SFO 相位补偿——头部补偿代码只覆盖
counter<8(L-LTF0/1, L-SIG, HT-SIG0/1);同板 CFO≈0 掩盖(99.55% 不需要);
跨设备真实 CFO(~0.09 rad/符号量级,两板独立晶振)下数据符号相位残差随符号数线性放大
(len=1 帧 19% vs len=38 帧 0%;星座 sym=5 虚部 0.28→0.82 SC 斜坡、sym=20 转 90° 佐证)。
修复:调用点按 `HDR_COMP_DISABLE=0` 门控,计算 phase_rot[i]=exp(-j·phase_diff[i]×(7+data_sym_idx))
传入数据符号提取函数逐 SC 应用。文件级 static `g_hdr_comp_disable_data`(general_work
内的头部路径 static 作用域不可达)。附带:M-power 分支升级为公共+SC 斜坡两参数加权
拟合(`IEEE80211_DATA_CPE_MPOWER=1`,每符号残差修正;实验显示单独 M-power 远逊于
确定性补偿,叠加有害,保持 opt-in OFF)。

改动文件:`lib/frame_equalizer_impl.cc`(commit 9efdda1,+80/-8)
环境变量:`IEEE80211_HDR_COMP_DISABLE`(既有)——跨设备模式传 0(harness 默认仍 1)

## 决策

- **保持 harness 默认 `HDR_COMP_DISABLE=1`(单板 regime 最优)**——跨设备是独立
  regime,由 `--tx-addr/--rx-addr` 显式进入,env 组合(`=0`)在跨设备运行命令中显式给出。
  CONFIRMED 是翻默认的必要非充分条件:单板基线 99.55% 依赖默认 1,翻默认会破坏单板。
- **ARRIVAL 负向列为 open question**(补偿 ON 时正确 L-SIG 计数减少 -247.5,
  p=0.0169;疑似计数口径/日志路径差异,非主指标影响;后续用 DECODE_SEQ 命运图核对)。
- 剩余 ~34% 失败成分:一半 ourmac=0(外部异帧,5240 长跑不干净——扫频 0.2s 采样
  错过 WiFi 突发),下一步候选:换 5220 频点 / CFO 估计精化 / 帧级残余分析。
- 单板 USRP 基线复测待做(需设备1 自回环接线),确认门控下单板行为不变。

## 诚实清单(故意未动)

- 未翻 CLAUDE.md「Harness 默认环境」表(HDR_COMP_DISABLE 默认 1 保持)
- 非关闭方向:未动「禁止方向」表与 hookify pattern
- 未改 CLAUDE.md 顶部「当前状态」(单板 99.55% 状态未变;跨设备是新增 regime)
- M-power 两参数拟合保留为 opt-in(实验 C/E 显示叠加有害),未纳入任何默认
- harness GT_OK 的 warmup 累计计数口径缺陷未修(独立问题,记录在案)
