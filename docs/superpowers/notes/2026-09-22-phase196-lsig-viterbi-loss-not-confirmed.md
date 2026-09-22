# Phase 196: 「L-SIG viterbi 失败解释掉率」NOT CONFIRMED —— 及沿途修好的一个 SIGSEGV（2026-09-22）

**类型：根因定位（自我证伪）+ 诊断仪表修复 + 新线索**

## 一句话

**丢帧的因果链定位成功**（L-SIG viterbi 失败 → HT-SIG 零候选 → 超时丢整帧），
**但把它当作「掉率的直接解释」被预注册判据否定**：L-SIG 失败数是非丢帧数的
**11.7×（A）/ 32.7×（B）**，能对应到真实丢帧的只有 **≤8.6% / ≤3.1%**。
**91–97% 的 L-SIG 失败落在噪声 episode 上，属空转。**
新线索：**B 箱的噪声 episode 是 A 箱的 4.2 倍**（2462 vs 589）。

---

## VERDICT（预注册判据 + 原始输出逐字粘贴）

**跑之前声明的判据**（写在 /tmp/p195e_split.sh 与当轮对话中）：

```
LSIG_PARSE_FAIL 数 ≈ missing 数   → L-SIG 失败几乎全落在真帧上 ⇒ 掉率直接由此解释
LSIG_PARSE_FAIL 数 ≫ missing 数   → 大部分落在噪声 episode 上 ⇒ 真帧那部分才是来源
```

```
VERDICT: 判据落在第二种 —— LSIG_PARSE_FAIL 数 3995(A)/19188(B) 对应真丢帧 342(A)/587(B)，
         即 11.7× / 32.7×。故「L-SIG viterbi 失败 = 掉率的直接解释」NOT CONFIRMED。
```

原始输出（逐字，两臂同配置 tx-gain 16 / len1000 / 100ms / MCS0 / 200s 测量窗）：

```
########## soloA ##########
--- TX 帧数 / L-SIG 失败 ---
  TX帧(=TX_LSIG_Original/2) 2200
  LSIG_PARSE_FAIL viterbi_fail  3995
  LSIG_PARSE_FAIL 其他 reason   3995
--- [P158-DIAG] episode 强度分布 ---
  强(>500)=2198  中(100-500)=7  弱(<=100)=589
--- 丢帧真值（fate 工具）---
seqs decoded: 1856  range [1..2198] = 2198 frames  missing=342 (15.56% loss)
detections: 2794 trigger lines, episodes=2794 (strong/real=2205 weak/noise=589)
lattice: period=100.0875ms concentration=0.990

########## soloB ##########
--- TX 帧数 / L-SIG 失败 ---
  TX帧(=TX_LSIG_Original/2) 2200
  LSIG_PARSE_FAIL viterbi_fail  19188
  LSIG_PARSE_FAIL 其他 reason   19188
--- [P158-DIAG] episode 强度分布 ---
  强(>500)=2212  中(100-500)=1  弱(<=100)=2462
--- 丢帧真值（fate 工具）---
seqs decoded: 1611  range [1..2198] = 2198 frames  missing=587 (26.71% loss)
detections: 4675 trigger lines, episodes=4675 (strong/real=2213 weak/noise=2462)
lattice: period=100.0875ms concentration=0.991
```

### 分子分母窗口声明

- **分子**：`FCS_OK`（harness `[RESULT]` 行累加），窗口 = `--warmup 20` 之后的 **20 × 10s = 200s**
- **分母**：`est_sent`，同一 200s 窗（`--interval 100` ⇒ 每 10s 窗 est_sent~100 ⇒ 2000）
  ⇒ soloA 1688/2000 = **84.4%**，soloB 1470/2000 = **73.5%**
- **fate 工具的 `missing`**：分母是**已解出帧的 seq 区间** `[1..2198]`（2200 帧 TX，本轮**未回绕**——
  12 位 seq 上限 4096，本次运行 2200 帧，这是刻意把测量窗压到 200s 的原因）
- **`LSIG_PARSE_FAIL` / episode 计数**：覆盖整个进程生命周期（含 20s warmup），与上面两行**不同域**，
  故只用于相对比较（A vs B），不与其他百分数混用

---

## 因果链（本轮定位成功，三重数值精确吻合）

```
帧被检测到 → 进均衡器
   ↓
【L-SIG viterbi 失败】reason='viterbi_fail'（rate_or_length_invalid = 0 次）
   ↓
L-SIG 无输出 ⇒ HT-SIG 解码一个候选都没试（n_candidates=0）
   ↓
d_have_ht_header 恒为 false
   ↓
d_sym_idx ≥ d_data_start_rel+5 → [EQ_FRAME_END] HT-SIG timeout
   ↓
丢弃该帧剩余全部符号 → 整帧报废
```

**三重吻合**（同一轮插桩运行，1089 / 2146 逐一对应）：

| 观测 | soloA | soloB |
|---|---|---|
| `n_candidates == 0` | 1089 | 2146 |
| `best_metric < 0` | 1089 | 2146 |
| `LSIG_PARSE_FAIL reason='viterbi_fail'` | 1089 | 2146 |

三者严格等价（前两者是同一条件的两种记法，第三者独立来自另一条日志路径）。

---

## 附录 A：`lib/sync_short.cc:657` 缺实参致 SIGSEGV（已修）

`[P158-DIAG] episode_end ...` 在 `MAX_SAMPLES` 分支漏传 `d_episode_start`：
格式串 7 个转换符、实参 6 个 ⇒ 变参错位 ⇒ 末尾 `%s` 解引用垃圾指针 ⇒ SIGSEGV。
另外三处同款打印（522/558/622 行）均正确。

**隔离实验（逐字）**：

```
=== control ===
  exit=0  windows=2  FCS_OK=66  err_bytes=55090614
=== decode_seq ===
  exit=0  windows=2  FCS_OK=80  err_bytes=72870277
=== copy_diag ===
  段错误（核心已转储）
  exit=139  windows=0  FCS_OK=0  err_bytes=22490613
=== both ===
  段错误（核心已转储）
  exit=139  windows=0  FCS_OK=0  err_bytes=2819362
```

⇒ `IEEE80211_SYNC_SHORT_COPY_REDETECT_DIAG=1` 单独即可复现；`IEEE80211_DECODE_SEQ=1` 安全。

**爆炸半径**：`[P158-DIAG]` 是 `p163b_fate_analysis.py` 的唯一数据源，而该分支只在
「COPY episode 跑满采样上限」时触发——**干净短帧链路永不触发，恰在需要诊断的坏链路上必崩**。

**验证**：修复后 `make` + `make install`（md5 与 build 一致）、
loopback 门 **`Final: OK=1 FAIL=0`**、两臂复跑 `exit=0 windows=60/60` 无崩溃。

---

## 附录 B：本轮证伪的四个假设（全部是我自己提出的）

| # | 假设 | 证伪方式 |
|---|---|---|
| ① | 噪声 episode 把均衡器打进 discard 态「吃掉」真帧 | 无直接测量；且定量对不上（见下） |
| ② | `is_ht_frame=0` 导致 HT-SIG 不被尝试 | `d_is_ht_frame &&` 在 `frame_equalizer_impl.cc:7461` **是注释掉的**，非生效门 |
| ③ | `d_sym_idx` 与 `d_internal_symbol_counter` 失步使门永不满足 | 两者在 6152/6154 **成对归零**、10073/10074 **成对递增**，结构上不可能失步 |
| ④ | L-SIG viterbi 失败即掉率的直接解释 | **本文件主判定**：≤8.6% / ≤3.1% 可对应 |

**教训**：①②③ 都是「读代码/看计数觉得自洽就采信」——正是 P192 记过的
「代码里读到的机制必须用计数验证」。只有④走了预注册判据，而它恰恰把自己否掉了。

---

## 附录 C：三处测量伪影（都会伪造结论）

1. **`best_fail='none'` 在 `best_metric<0` 时零信息**——它只在 `cand_metric>=0` 时才更新，
   故「一个候选都没试」与「试了全失败」显示成同一串 `none`。
2. **fate 工具的 `missing%` 在 seq 回绕时不可用**——12 位 / 4096；本轮 2200 帧刻意避开。
   （P195 的 6000 帧臂 `missing=8.86%` 即此伪影，真值 82.43%。）
3. **fate 工具的 `DETECTION/CHAIN loss` 分类饱和**——发帧 10/s、时隙亦 10/s，
   每个时隙都有强检测，判据恒真（本轮仍打印 `DETECTION loss=0 CHAIN loss=200`）。
   **该行不可用。**

---

## 决策

- **「L-SIG 失败解释掉率」轴不关闭**：定位成立、量级否定。真正待查的是**噪声 episode 的因果角色**。
- **新线索（优先级最高）**：**B 箱噪声 episode 是 A 的 4.2 倍**（2462 vs 589），
  而两箱检测到的真帧数几乎相同（2198 vs 2212）且检测率≈100%；
  额外 1873 个噪声 episode 对应额外 245 个丢帧（≈0.13 帧/ episode，
  但 A 是 342/589 = 0.58，比例不一致 ⇒ **因果未证**）。
  判别实验：**对调两块网卡 / 对调两箱**，可分离「箱子硬件」与「网络适配器」。
- **`sync_short.cc:657` 修复保留**（`[P158-DIAG]` 仪表由此可用）。
- **不翻任何默认**：本轮无 ABAB、无 env var 默认变更。

## 诚实清单（明确没动什么）

- 未翻 CLAUDE.md「Harness 默认环境」表任何一行
- 未进 CLAUDE.md「禁止方向」表（轴未关闭），未动 hookify pattern
- 未动 CLAUDE.md 顶部「当前状态」（单 phase 诊断，不构成状态变更）
- `lib/` 只有 `sync_short.cc` 一处 1 行修复，无其他代码改动
- **§附录 B 的①②③ 是我本 session 内提出的临时假设，不是预注册实验臂**，记录在此仅作教训
