# Phase 161: 残余失败根因定案 —— 频带边缘子载波衰落尾巴 + CPE 跟踪器 no-op

**日期：** 2026-08-05
**状态：** ✅ **根因定案（systematic-debugging 全程）+ CPE 修复 REFUTED**
当前真实端到端 **98.9%**（PDU 2968/3000，双轮一致）；99.9% 的残余
失败机制已定位到物理层，唯一原理性修复路径（软判决 viterbi）留作
独立后续 phase。

---

## 1. 残余失败根因（99.9% 的攻击面）

3000 帧中 ~32 帧损失 = LDPC 终端 21 + 检测/其他 ~11。LDPC 失败的
完整证据链：

1. **失败帧是我方帧**（FAIL_PSDU dump：mac 头 0x42/0x23/0xff 完好）——
   排除外部杂帧/干扰。
2. **错误集中在最后载荷字节**（byte 61 在 20/37=54% 的失败帧中出错）。
3. **失败帧的最弱子载波深度**：min|H| p50=**13.7** vs OK 帧 28.7；
   argmin 位置两者都在 **SC −28/−27（频带边缘滚降区）**。
4. **失败帧总误比特 47-115**（vs OK p90=30）——超过硬判决 viterbi
   纠错预算（~40/1144）。

**机制**：SC −28/−27 位于 20MHz 信道/滤波器滚降边缘，天然最弱；H 小
→ ZF 均衡 1/H 放大噪声 → 该 SC 比特错误 → 解交织器摊入全码字 →
深度实现时（min|H|~13）超出 viterbi 预算 → FCS 失败。**这是物理信道
滚降 + 硬判决解码的组合，非随机解码器 bug。**

## 2. CPE 跟踪器 no-op bug + 修复 REFUTED

- `estimate_ht_data_cpe_rad_from_sym64` 在 `kTxOrder52`（仅数据 SC
  数组，设计上排除导频 -21/-7/7/21）里查导频 → h_idx 恒 -1 → acc=0 →
  **cpe≡0（每帧每符号精确 0.0）**，逐符号相位跟踪从未生效。
- 逐符号相位漂移实测（eq[0] arg 斜率）：p50=-4.5°/sym，p90 ±25°/sym。
- **M-power（平方）CPE 估计器修复尝试 REFUTED**：N=3 ABAB DS
  −43.7/45s（p=0.0053，3/3 全负）——与 HDR_COMP_DISABLE 同构：
  含噪相位估计作为修正引入的噪声大于消除的漂移。**CPE 修正轴关闭。**

## 3. 伪影教训（方法论）

- **HARD-vs-TX 参考比对**受 seq_nr/FCS 陈旧污染：参考文件是最后一帧，
  seq 递增 → FCS 变 → sym7（seq 区）/sym21（FCS 区）出现假"错误"。
  一度误判为"末符号解码器缺陷"。**真实定位靠常量载荷字节比对
  （FAIL_PSDU 对照 'x'=0x78），不是参考文件比对。**
- 20MHz 干净 loopback 的"末符号错误"亦为此伪影——loopback 不能作为
  失败机制的解剖台（除证明 FCS 可通过外）。

## 4. 排除清单（全部实测）

外部杂帧、符号截断（CAPTURE 计数全到齐）、TX 斜坡（功率剖面平）、
CPE 漂移（估计器本就 no-op）、viterbi 尾部（已强制零态终止）、
CFO/SFO（估计值两群同样微小）、时段聚集（iid）、TX 双群体（奇偶相同）。

## 5. 新增诊断仪表（均 opt-in env）

- `IEEE80211_SYM52_DUMP`（=last/all）：逐数据符号全 52-SC eq dump
- `IEEE80211_CPE_DEBUG`：导频箱原值 + |H| + |acc|（CPE 估计器透视）
- `IEEE80211_FAIL_PSDU_DUMP`：FCS 失败帧全文 PSDU
- mapper TX 参考修复（168235c）：`maybe_dump_ht_mcs0_debug` 传
  effective_mcs（原传 -1 恒早退），HARD-vs-TX 诊断得以生效

## 6. 下一步（独立 phase 评估）

**软判决 viterbi（|H|² 加权 LLR）** 是唯一有原理依据的 99.9% 路径：
弱频带边缘 SC 降权 → 硬判决的"全强度错比特"变"低置信软比特"。
**风险**：Phase 129 LLR 有 REFUTED 前科（彼时 δ-on/1.77rad 旧体制）；
在 δ-off 新基线上重试需独立评估。工作量中等。

**产物：** 本文件、提交 9fb8381（仪表）+ 168235c（mapper 修复）、
`/tmp/p161_long{5,6}.rt.err`（300s 失败帧证据）
**相关：** [[Phase 160 trailing-window]]、[[Phase 159b δ]]、
[[Phase 157 refractory]]（前 1% 根因史）
