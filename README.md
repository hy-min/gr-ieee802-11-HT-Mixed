# gr-ieee802-11-HT-Mixed

**802.11n 代际升级 × USRP X310 实时化 × 自治 Agent 研发工作流**

![License](https://img.shields.io/badge/license-GPL--3.0-blue) ![GNU Radio](https://img.shields.io/badge/GNU%20Radio-3.10-green) ![Platform](https://img.shields.io/badge/platform-USRP%20X310%20%2B%20UBX--160-orange)

> **English**: A fork of [bastibl/gr-ieee802-11](https://github.com/bastibl/gr-ieee802-11) (IEEE 802.11a/g/p) upgraded to **802.11n HT (HT-Mixed mode)** with **realtime USRP X310** operation — built and operated entirely through a **human–agent R&D workflow** (Claude Code): mechanical guardrail hooks, a three-tier verdict vocabulary (CONFIRMED / NOT CONFIRMED / REFUTED), paired ABAB experiments, and an autonomous experiment agent. **99.55%** realtime FCS on single-board cable; cross-device link (two X310s, independent clocks) brought from **0% → 97.1%** (MCS0). All **146 experiment verdicts** ship with raw outputs and reproduction commands under `docs/superpowers/notes/`.

## 成果头条（2026-08-25，全部附可复现依据）

| 指标 | 结果 | 判定文档 |
|---|---|---|
| 单板电缆直连 端到端 FCS 通过率 | **99.55%**（2990/2983 × 2 轮，无噪声风暴轮） | P165 |
| 跨设备链路（两台 X310，独立时钟，电缆） | **0% → MCS0 97.1% / MCS1 84.6% / MCS2 76.2%** | P176 / P176b / P177 |
| 有效帧检出率 | **3.4×**（均值约 200 帧 / 45s） | P154 |
| 数据路径 \|H\|² 加权软判决 Viterbi | 终败 **−62%**（N=8 配对 ABAB，p=0.0047）；300s 长验证 −83% | P162 |
| 调度器 stall 修复 | 实时吞吐 **0.035 → 200+ MHz**，端到端实时业务首次跑通 | P146 |
| 多线程 Heisenbug | ASan 定位双实例共享 static 缓冲区竞争，修复后满负载零崩溃 | P147 |

工程规模：**986 次提交**（2025-10 起）· **C++ 净增约 1.9 万行** · **Python 4 万余行** · **146 份书面实验结论**（截至 2026-08）。

## 本仓库的真正主角：人-Agent 协同研发系统

这个仓库同时是一个**单人 + 硬件在环项目的 AI 协同研发实验场**。物理层攻坚只是载体；
真正的系统是那套保证 AI 产能可被工程纪律约束的工作流（全部配置随仓库公开，可审计）：

```
人（定方向 / 裁决）
  │  每会话约定：4 份领域规则（.claude/rules/）+ 7 条方法论铁律（CLAUDE.md）
  ▼
Claude Code ── 护栏钩子 ×5（.claude/hookify.*，机械拦截禁入方向与危险命令）
  │           跨会话持久记忆 ×190+（memory/，新会话零成本接续）
  ▼
自治实验 Agent（.claude/agents/usrp-abab-runner）
  │  硬件预检 → 配对 ABAB 批次 → 判定 → 归档，全流程自治
  ▼
三档判定词表：CONFIRMED / NOT CONFIRMED / REFUTED（配对 t 检验 p<0.05 才算数）
  ▼
146 份判定文档（docs/superpowers/notes/，每份附原始输出 + 复现命令）
```

**这套系统拦截 AI 幻觉的两个实例**：

- 一项「+25.3」的到达率收益，配对 ABAB 证明只是设备漂移混淆（p=0.485）——未配对比较的结论不可信（P158）；
- 「99.6% 目标达成」被证为分母统计伪影（est_sent 不含 warmup 窗口，真实 69%）——真值计数与估计必须同域（P159b）。

两条教训都被固化为机械护栏与方法论铁律，而非停留在记忆里。

## 相对上游的技术增量（802.11n HT）

- **5 个新 C++ 模块**补全 HT 收发路径：HT-SIG 信令、TX 训练字段插入、RX 符号分流、LLR 软解映射、QC-LDPC 编译码
  ——其中 LDPC 经配对 ABAB 判定**无显著增益**，作为 opt-in 特性保留（负结果如实归档，P166d）。
- **帧检测器重写**：16 样本 boxcar 周期自相关 + 自适应阈值 + plateau 确认，检出率 3.4×（P154/P159/P160）。
- **信道估计**：双训练符号 SNR 加权，HT-SIG 输入 SNR 2-3 → 8.78 dB（P139）。
- **解码层**：|H|² 加权软判决 Viterbi（P162）；L-SIG 4-rot 候选搜索（P165c，PDU 99.35% → 99.55%）。
- **跨设备链路三连修**：数据符号逐符号 CFO/SFO 补偿（P176，0% → 65.6%，终败 −437/45s，p<0.0001）
  → 互相关帧起点精定位（P176b，→ 93.7%，DS +19.5/45s，p=0.0298）
  → MCS1 帧尾 pad 对齐（P177，17.2% → 84.6%，+67.4pp，p<0.0001）。
- **测试基建**：全部实验开关走 env var opt-in（`docs`/CLAUDE.md 有完整目录与判定状态）；
  100 个测试文件 + 91 个实验脚本支撑 150 余轮受控 A/B 实验。

## 一键复现

硬件：USRP X310 + UBX-160，5.25 GHz 电缆直连（`--tx-scale 0.1` 软件衰减 −20 dB 防过驱动）。

```bash
# 单板基线（预期 PASS: DECODE_SUCCESS >= 15 / 45s）
IEEE80211_LSIG_VITERBI_CANDIDATE=1 ./usrp_realtime_validate.sh --tx-scale 0.1

# 跨设备（两台 X310；MCS 扫描见 mcs_sweep.py；MCS1 需加 IEEE80211_TX_PAD_ALIGN=8）
HDR_COMP_DISABLE=0 SYNC_LONG_XCORR_FS=1 ./usrp_realtime_validate.sh --tx-addr <A> --rx-addr <B> --tx-scale 0.1

# 单变量 A/B 金标准（配对 ABAB + paired t 检验，输出 VERDICT 行）
python3 p158_abab_batch.py --pairs 4 --exp-env NAME=VALUE
```

头条数字的可复现口径：

```bash
git log --all --since=2025-10-01 --author=newuser443 --format=%h | wc -l   # → 986
git log --all --since=2025-10-01 --format= --numstat -- '*.cc' '*.h'       # → 净增 30,620 − 11,273 = 19,347
ls docs/superpowers/notes/ | grep -ci verdict                              # → 146（截至 2026-08）
```

## 已知物理极限（诚实声明）

- 残余误码归因于 UBX-160 射频本振**随机相位噪声**（~1.77 rad/子载波，每帧每子载波独立实现、
  帧间相关 |r|≈0.17、帧内无结构——无结构所以软件不可校准，P112/P168/P177）。
- 高阶调制（16QAM/64QAM）跨设备崩零 = 同一堵 LO 相位噪声墙。
- 冲击 99.9% 的唯一原理路径是**外部 10 MHz 参考 / GPSDO**；软件路径已逐项排除，
  15 条已判定封闭方向登记在 CLAUDE.md 的禁止方向表（每条附判定文档链接）。

## 文档地图

| 内容 | 位置 |
|---|---|
| 项目综合报告 | `docs/superpowers/notes/2026-08-11-project-synthesis-report.md` |
| 146 份判定原文 | `docs/superpowers/notes/`（每份附原始输出与复现命令） |
| Agent 工作约定（规则/判定表/禁止方向） | `CLAUDE.md` + `.claude/rules/` |
| 跨会话记忆实例 | `memory/` |

## 姊妹项目

[cpp-sentinel](https://github.com/hy-min/cpp-sentinel) —— 「静态优先、LLM 去噪」的 C++ 代码审查 Agent。
与本仓库同一套工程哲学的两面：**不信任 LLM 裸输出；给 Agent 造评测和笼子。**

## License 与上游

GPL-3.0（继承自上游）。上游项目 [bastibl/gr-ieee802-11](https://github.com/bastibl/gr-ieee802-11)
的安装依赖（GNU Radio 3.10、gr-foo 等）见其
[原始 README](https://github.com/bastibl/gr-ieee802-11#readme)；本仓库在其基础上开发，兼容性约束相同。
