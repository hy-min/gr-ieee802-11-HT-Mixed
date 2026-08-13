# gr-ieee802-11 Project Instructions

> **当前状态（2026-08-12）：USRP realtime FCS_OK 稳定 99.55%（电缆，无风暴轮）。**
> 软件攻击面已穷尽（Phase 166–168 全部封闭）。残留 0.47% = 随机 LO 相位噪声
> 尾部事件（直接测量：每帧每 SC 独立实现 ~1.8 rad/SC，帧间相关 |r|≈0.17，
> 帧内无结构）。**突破 99.9% 唯一原理路径 = 外部 10 MHz 参考/GPSDO。**
>
> 一键复现最优配置：
> ```bash
> IEEE80211_LSIG_VITERBI_CANDIDATE=1 ./usrp_realtime_validate.sh --tx-scale 0.1
> ```
> 收官报告：`docs/superpowers/notes/2026-08-11-project-synthesis-report.md`

## 项目目标

USRP realtime FCS_OK（X310 + UBX-160 端到端）。
主指标：realtime `FCS_OK / Sent`。Loopback PASS 仅作回归门，不构成 USRP 证据。

## 操作约定（每条附验证方法）

### 构建
- **`make` 后必须 `make install`**，否则 Python 加载旧 .so。
  验证：比较 `build/lib/*.so` 与 conda site-packages 下 .so 时间戳一致。
- **禁止 GRC 生成**：`wifi_phy_hier.grc` 会段错误；直接编辑 `wifi_phy_hier.py`。
  验证：提交的 diff 中不出现 GRC 产物文件。

### 代码
- **多值日志必须原子**：`snprintf(buf,...) + USRP_LOG("%s", buf)` 单次提交
  （commit e90e3f5；USRP_LOG 非原子，多线程逐参数调用会 shred）。
  验证：`grep -rn 'USRP_LOG(' lib/ | grep -cv '"%s"'` 应只含单参数调用与 buf 输出。
- **禁止函数级 `static` 可变缓冲区**（P147：多实例竞态 → std::sort OOB →
  SIGSEGV Heisenbug）。scratch buffer 必须栈/成员私有。
  验证：`grep -n 'static\s\+.*\[[0-9]' lib/*.cc` 逐个确认只读。
- **新 env var 一律 opt-in 默认 OFF**；harness setdefault 例外见下表。
  验证：不设该 env 时 loopback 与基线逐比特一致。

### 测试门（全过才可声称完成）
1. Loopback 回归：
   ```bash
   LD_PRELOAD=./wrap_rpc2.so PYTHONPATH=build/python/bindings:python:examples \
     /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py
   ```
   预期末行：`Final: OK=1 FAIL=0`。
2. USRP 基线：`./usrp_realtime_validate.sh --tx-scale 0.1`，
   预期：`PASS: DECODE_SUCCESS >= 15`。
3. 单变量效果金标准：
   `python3 p158_abab_batch.py --pairs 4 --exp-env NAME=VALUE`，
   输出必含 `VERDICT:` 行；CONFIRMED 要求 paired t p<0.05 且 mean>0。

### USRP 操作
- **禁止 `--rate 5`**（P58：溢出 48×）。固定 `--rate 20`。
- **同板默认** A:0 TX → A:0 RX2（跨板弱 2.4×，P53）。
- **rx-gain 勿 < 20**（boxcar 贴地板伪影，P165）。
- 批次前查 governor：`cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`
  须为 `performance`（P158：powersave 会伪装成设备漂移）。
- 挂死恢复：`pkill -9 -f 'python.*usrp'` 后 `uhd_usrp_probe` nudge；
  ping 通但 probe 空 = 设备被占用，不是离线。

## Harness 默认环境（setdefault，env 可覆盖）

```
IEEE80211_LSIG_RATE_FORCE=0xD  TIMING_OFFSET_APPLY=0  HDR_COMP_DISABLE=1
H52_2WAY_DEFAULT=0  SYNC_SHORT_FUSED_USE_BOXCAR=1  SYNC_SHORT_USE_ADAPTIVE_THRESH=1
MIN_PLATEAU=24  TRIGGER_MARGIN=2.5  DATA_SOFT_VITERBI=1  LSIG_VITERBI_CANDIDATE=1
```

完整 env var 目录（全部 opt-in 开关 + 判定）：@.claude/rules/env-vars.md

## 禁止方向（已判定，勿重复测试）

| 方向 | 判定 |
|------|------|
| HT-SIG 软判决 viterbi | P44/P129/P164 三次未过，轴关闭 |
| L-SIG FINE_ROT 8×45° | P165d/P166c：4-rot 已最优 |
| sync_short 门控族（地板门/确认门/COPY 重检）| P162b/P163/P158 |
| Wiener / cross-frame / freq-smooth H52 | P141/P140/P126A/P166c |
| CPE 修正族（mean/per-SC/M-power）| P35/P36/P38/P161 |
| retroactive δ 修正（TIMING_OFFSET_APPLY=1）| P159b：L-SIG 抽签根因 |
| LDPC 编码 | P166d：限制在 H 质量，非码强度 |
| 标签驱动 d_frame_start | P166a：标签恒在窗口位置 0 |
| 频域时移搜索 τ | P166b/167b：只修 CP 内移位，不修 ISI |
| 用 computed_fs 替代强制 d_frame_start=174 | P167：DS 646→464，强制才是对的 |
| H 源多样性候选（L-LTF0/L-LTF1）| P168：机制触发但无端到端收益 |
| TX 频带边缘预加重 / 功率分配族 | P169：终败 +2.75（p=0.035）显著有害——边缘噪声是相位噪声（∝信号幅度），非热噪声 |
| boxcar 窗口 >16 | P158-W32 |

判定原文：`docs/superpowers/notes/YYYY-MM-DD-phase*.md`。

## 方法论铁律

1. **单变量**：一次只改一个 env var 或一处代码。
2. **先合成后 USRP**：新想法先过 loopback 回归门。
3. **实时配对 ABAB 是金标准**：离线回放正收益 ≠ 实时正收益（P163）；
   未配对跨区块比较有 ±30 漂移混淆（P158）。
4. **分母同域**：真值计数与 est_sent 覆盖同一时间窗（P159b "99.6%" 是
   warmup 分母伪影）。
5. **终点指标按机制层级选**：解码级机制看终败数，到达率机制看 DS（P162）。
6. **设备漂移真实存在**：A/B 必须新鲜背靠背对照（P158-W32）。

## Transparency 规则

1. **改动 >50 行前，先输出实现计划**（文件、位置、理由），用户确认后动手。
2. **声称任何效果前必须贴测量输出原文**（ABAB 的 VERDICT 段或 validate 的
   RESULT 段）。判定词只用三档：CONFIRMED（paired p<0.05）/
   NOT CONFIRMED / REFUTED。禁止以"理论上应该有效"作结论。

## 文档地图

| 内容 | 位置 |
|------|------|
| 项目综合报告 | `docs/superpowers/notes/2026-08-11-project-synthesis-report.md` |
| 100+ phase 经验教训 | `docs/superpowers/notes/2026-07-15-project-retrospective.md` |
| 每 phase 判定原文 | `docs/superpowers/notes/` |
| env var 完整目录 | @.claude/rules/env-vars.md |
| 实验方法论细节 | @.claude/rules/methodology.md |
| USRP 硬件操作手册 | @.claude/rules/usrp-operations.md |
| 构建与测试细节 | @.claude/rules/build-and-test.md |
