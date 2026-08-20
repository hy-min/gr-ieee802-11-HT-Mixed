# Phase 176b verdict: sync_long 互相关峰帧起点精定位 CONFIRMED(2026-08-19,细化版 08-20)

## VERDICT(批次输出原文,逐字粘贴)

**细化搜索版(2026-08-20,现行实现) — ABAB N=4,两臂 HDR_COMP_DISABLE=0,XCORR_FS 唯一变量:**

```
[ABAB] pairs=4  A=control(IEEE80211_SYNC_LONG_XCORR_FS unset)  B=experiment(IEEE80211_SYNC_LONG_XCORR_FS=1)
ARRIVAL(enc=0 len=72): A=[1268, 1306, 1272, 1350]
ARRIVAL(enc=0 len=72): B=[1582, 1608, 1528, 1558]
ARRIVAL(enc=0 len=72): per-pair diff (B-A)=[314, 302, 256, 208]
ARRIVAL(enc=0 len=72): mean diff = +270.00  std = 48.30  t(3) = 11.18  paired t p = 0.0015  wilcoxon p = 0.1250
DECODE_FAIL(LDPC terminal): A=[77, 82, 91, 104]
DECODE_FAIL(LDPC terminal): B=[1, 2, 0, 0]
DECODE_FAIL(LDPC terminal): per-pair diff (B-A)=[-76, -80, -91, -104]
DECODE_FAIL(LDPC terminal): mean diff = -87.75  std = 12.55  t(3) = -13.98  paired t p = 0.0008  wilcoxon p = 0.1250
VERDICT: CONFIRMED: experiment improves DECODE_SUCCESS (+179.2/45s, p=0.0000; arrival +270.0)
```

支撑数据(细化版,单臂 300s):PDU 984/984/987 = 2955/3000 = **98.5%**,DECODE_FAIL=9;
60s 轮 PDU 583/600,DECODE_FAIL=0,全体帧 L-LTF 残差 p99=0.093(粗搜失败帧 median 0.241)。

**粗搜初版(2026-08-19,已被细化版取代) — ABAB N=4:**

```
ARRIVAL(enc=0 len=72): mean diff = +131.50  std = 28.63  t(3) = 9.19  paired t p = 0.0027
DECODE_FAIL(LDPC terminal): mean diff = +30.25  std = 21.70  t(3) = 2.79  paired t p = 0.0685
VERDICT: CONFIRMED: experiment improves DECODE_SUCCESS (+19.5/45s, p=0.0298; arrival +131.5)
```

支撑数据(探索期,非 ABAB):

```
单臂 45s:  PDU 421/450 = 93.6%  DECODE_FAIL=32
单臂 300s: PDU 2811/3000 = 93.7%  DECODE_FAIL=160
loopback:  Final: OK=1 FAIL=0
单板基线:  HDR_COMP_DISABLE=1 默认下行为不变(互相关修正 opt-in 默认 OFF)
```

## 判定

`IEEE80211_SYNC_LONG_XCORR_FS=1`:**CONFIRMED**(粗搜版 p=0.0298;细化版 p=0.0000 再确认)
- 细化版:N=4 配对交错 ABAB,新鲜背靠背对照,governor=performance,电缆 --tx-scale 0.1
- 预注册主终点(到达率机制 → ARRIVAL):mean diff **+270.0/45s**(paired t **p=0.0015**,4/4 对全正)
- 次终点:DECODE_SUCCESS **+179.2/45s**(p=0.0000);DECODE_FAIL **-87.75**(p=0.0008,终败近零)
- Loopback 门:Final: OK=1 FAIL=0
- 分子分母窗口:45s 测量窗 est_sent~450;计数为 err 全程累计(含 20s warmup),配对差不受影响
- 细化相对粗搜的增量未做独立配对(代码内改进,非 env 变量;单臂 93.7%→98.5% 为探索数据)

## 机制与实现

**机制**:sync_long 的 `search_frame_start()` 用 FIR 峰对检测 L-LTF 后,强制
`d_frame_start=174` 覆盖计算值——该值在单板电缆 regime 校准,跨设备下真实帧起点
在 95-250 范围乱跳(实测),强制 174 导致 ~23% 帧的 FFT 窗插进符号中间(ISI 毁数据段)。
**修复**:互相关 L-LTF0/L-LTF1 两半(64 样本,lag 80),snap 到相关峰 = 物理 ground truth,
不受 FIR 群延迟/标签位置影响。两个 SYNC_LENGTH 分支(chunk_invariant + 默认)都插入。

改动文件:`lib/sync_long.cc`(初版 957f15f;细化版 fc4f797:步长 2→1、范围 ±32→±96、
两分支提取公共 helper `xcorr_refine_fs`)+ `lib/frame_equalizer_impl.cc`
(诊断仪表 IEEE80211_LLTF_RESID_DUMP=1,逐帧 L-LTF 拟合残差,序配对用)
环境变量:`IEEE80211_SYNC_LONG_XCORR_FS`(新增,opt-in 默认 OFF),
`IEEE80211_LLTF_RESID_DUMP`(新增诊断,默认 OFF)

**注意**:第一个 ABAB(p176_xcorr_fs,两臂都未设 HDR_COMP)配置错误(外层未设
HDR_COMP_DISABLE=0,harness setdefault 补 1,两臂都在无补偿裸奔 regime),DECODE_FAIL
500+ 证明。正确配置(外层设 HDR_COMP_DISABLE=0,两臂继承)重新跑,CONFIRMED。
**教训:跨设备 ABAB 必须外层显式设 HDR_COMP_DISABLE=0,harness setdefault 只在
未设时补 1,不会把 0 翻成 1。**

## 决策

- 保持 opt-in OFF(默认):单板 99.55% 路径依赖强制 174 的既有行为;跨设备模式显式传 1。
- 单板基线复测待做(需设备1 自回环接线),确认互相关修正不破坏单板最优路径。
- 剩余 ~6% 失败(xcorr 修正后):失败模式从"载荷中后段系统性损坏"(相位累积)
  变为"符号 0/1 起坏"(80% 帧)——帧级一开始就坏,指向 H 估计或 xcorr 修正
  未修到最优。判别实验待做(看失败帧 L-LTF 残差是否还大)。
- 异帧 18/146(12%)仍存在(物理,频点免疫)。

## 诚实清单(故意未动)

- 未翻 CLAUDE.md「Harness 默认环境」表(互相关修正 opt-in OFF)
- 非关闭方向:未动「禁止方向」表与 hookify pattern
- 未改 CLAUDE.md 顶部「当前状态」(单板 99.55% 不变)
- 未做单板基线复测(需换线,待用户配合)
