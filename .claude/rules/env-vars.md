# Env Var 完整目录（gr-ieee802-11）

格式：`名称` — 默认 — 作用 — 判定（verdict 文件）。
判定词：CONFIRMED / NOT CONFIRMED / REFUTED（定义见 methodology.md §7）。
所有 env var 默认 OFF（opt-in），harness setdefault 例外见 CLAUDE.md。

## Harness 默认 ON（setdefault，C++ 默认仍 OFF）

| env var | 作用 | 判定 |
|---------|------|------|
| `IEEE80211_LSIG_RATE_FORCE=0xD` | 强制 L-SIG rate=BPSK 1/2 | P18，首个 e2e PASS |
| `IEEE80211_TIMING_OFFSET_APPLY=0` | 关闭 P34 retroactive δ 修正 | P159b CONFIRMED（δ 是 L-SIG 抽签根因）|
| `IEEE80211_HDR_COMP_DISABLE=1` | 跳过头部 CFO/SFO 补偿（噪声主导，补偿=加噪）| P145c |
| `IEEE80211_H52_2WAY_DEFAULT=0` | 关闭 P139 两路 H52 平均（电缆基线下 L-LTF0/1 相位独立，平均有害）| P145c |
| `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1` | 16 样本 boxcar 原始周期-16 自相关检测器 | P89 SUCCESS |
| `IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1` | 阈值=max(p90×1.5, 0.2)，尾随窗口 | P89+P160 |
| `IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=24` | 25 连续超阈值样本才触发 | P154：到达率 3.4× |
| `IEEE80211_SYNC_SHORT_TRIGGER_MARGIN=2.5` | 平台门=2.5×阈值，切噪声陷阱 | P159：陷阱 5015→18 |
| `IEEE80211_DATA_SOFT_VITERBI=1` | 数据路径 \|H\|² 加权软判决 viterbi | P162：终败 -62% |
| `IEEE80211_LSIG_VITERBI_CANDIDATE=1` | L-SIG 4-rot（90° 步长）候选搜索 | P165c：PDU 99.35→99.55% |

## 检测/同步层（opt-in，默认 OFF）

| env var | 作用 | 判定 |
|---------|------|------|
| `IEEE80211_SYNC_SHORT_FUSED_DUMP=1` | boxcar 诊断 dump | P88 诊断用 |
| `IEEE80211_SYNC_SHORT_FUSED_BOXCAR_LEN=N` | boxcar 窗口（默认 16）| P158-W32 REFUTED（32 无差异）|
| `IEEE80211_SYNC_SHORT_FUSED_SCHMIDL_COX=1` | S&C \|P\|²/R² 检测 | P132/P159：杂散饱和，REFUTED |
| `IEEE80211_SYNC_SHORT_GAP_POWER_THRESHOLD` | COPY 态 gap 功率阈值（默认 0.01 承重）| P155 REFUTED（0.3 回退）|
| `IEEE80211_SYNC_SHORT_COPY_REDETECT=1` 系列 | COPY 态智能重检 | P158 NOT CONFIRMED（ABAB p=0.485）|
| `IEEE80211_SYNC_SHORT_COPY_REDETECT_FACTOR/EMA_MAX/DIAG` | 重检参数/诊断 | 同上 |
| `IEEE80211_SYNC_SHORT_ADAPTIVE_EMA_ALPHA` | 阈值 EMA 平滑 | P151c：p148_funnel 崩盘 |
| `IEEE80211_SYNC_SHORT_COR_FLOOR` | wifi_start 绝对 max_cor 地板门 | P162b REFUTED（DS -46%）|
| `IEEE80211_SYNC_SHORT_CONFIRM_*` | 内联前窥确认门 | P163 NOT CONFIRMED |
| `IEEE80211_SYNC_LONG_SCHMIDL_COX=1` + `_THRESHOLD` | sync_long 多特征门 | P133/P135：架构修复，性能中性 |
| `IEEE80211_SYNC_LONG_CHUNK_INVARIANT=1` | chunk 不变性累积 | P151：部分有效 |
| `IEEE80211_SYNC_LONG_EARLYOUT=0` | 关闭 P146 噪声早退（默认 ON）| 性能优化，行为一致 |
| `IEEE80211_SYNC_LONG_INPUT_DUMP=1` | 输入样本诊断 dump | P31b 诊断用 |
| `IEEE80211_SYNC_LONG_TAG_ALIGNED=1` | 标签驱动 d_frame_start | P166a：根本限制（标签恒在窗口位置 0），无害空操作 |
| `IEEE80211_SYNC_LONG_PRE_OUTPUT=N` | d_frame_start 前多输出 N 样本 | P167 实验；与 splitter 边界耦合，loopback 破 |
| `IEEE80211_SYNC_LONG_USE_COMPUTED_FS=1` | 用 computed_fs 替代强制 174 | P167 REFUTED：DS 646→464 |
| `IEEE80211_FRAME_START_OFFSET=N` | d_frame_start 微调 | P167：-16 灾难（DS 646→398）|

## 均衡器/H 估计层（opt-in，默认 OFF）

| env var | 作用 | 判定 |
|---------|------|------|
| `IEEE80211_H52_2WAY_DEFAULT=1` | P139 两路 H52 平均（L-SIG 墙首破）| CONFIRMED on air；电缆基线设 0 |
| `IEEE80211_HT_SIG_PILOT_REFINE=N` | 3-way/4-way 导频精化 | P139：PARTIAL |
| `IEEE80211_HTLTF_AVG=1` | 3-way（2 LTS + HT-LTF）| P122 REFUTED（跨板破 L-SIG）|
| `IEEE80211_H52_CROSS_FRAME_TRACK=N` | 跨帧 H52 FIFO 平均 | P123 INCONCLUSIVE / P140 file-replay PASS |
| `IEEE80211_PHASE140_ON=N` | P127 L-SIG 跨帧 FIFO 叠加在 2-way 上 | P140 file-replay PASS，USRP 未验 |
| `IEEE80211_WIENER_H52=1` 系列 | Wiener MMSE 收缩 | P141 PARTIAL；P166c δ-OFF 重测 REFUTED |
| `IEEE80211_H52_FREQ_LOWPASS=1` + `_K` | H52 频域低通 | P138/P166c REFUTED（PDU 150→37 灾难）|
| `IEEE80211_HTSIG_H_AVERAGE=1` | HT-SIG 导频增强 H 平均 | P118b：metric 13→12（最佳均衡器结果）|
| `IEEE80211_DDE_HT_SIG=1` / `_PER_SC=1` | 判决导向均衡 | P120a/P121 REFUTED |
| `IEEE80211_HTSIG_PER_SYMBOL_DELTA=1` | 逐符号 δ 跟踪 | P79 REFUTED |
| `IEEE80211_HTSIG_PER_SC_LUT=path` | 每 SC 相位校准 LUT | P80b REFUTED |
| `IEEE80211_HTSIG_NULL_SCS=...` + `_PILOT_MASK` | 稳定 null SC 掩码 | P137 REFUTED |
| `IEEE80211_CONST_CPE_APPLY=1` | 恒定 CPE 旋转 | P108 REFUTED（相位随机非恒定）|
| `IEEE80211_HTSIG_PILOT_CPE=1` | 逐符号导频 CPE | P35/P36 REFUTED |
| `IEEE80211_H52_KALMAN_TRACK=1` 系列 | Kalman H52 跟踪 | P111：4 导频不可行 |

## 解码层（opt-in，默认 OFF）

| env var | 作用 | 判定 |
|---------|------|------|
| `IEEE80211_DATA_SOFT_VITERBI=1` | 数据路径软判决（harness 默认 ON）| P162 CONFIRMED |
| `IEEE80211_SOFT_LLR_VITERBI=1` + `IEEE80211_HTSIG_SOFT_LLR_V2=1` | HT-SIG 软 LLR | P44/P129 REFUTED |
| `IEEE80211_HTSIG_SOFT_*`（P164 系列）| HT-SIG 软判决 σ²-free 标定 | P164 NOT CONFIRMED（方向分裂）|
| `IEEE80211_HTSIG_LIST_VITERBI=1..64` | 列表 viterbi | P111 T6a REFUTED（路径共享噪声轨迹）|
| `IEEE80211_LSIG_FINE_ROT=1` | L-SIG 8×45° 旋转候选 | P94/P165d/P166c：4-rot 已最优 |
| `IEEE80211_HTSIG_FINE_ROT=1` | HT-SIG 8×45° | P95：PARTIAL，未破墙 |
| `IEEE80211_LSIG_TIME_OFFSET_SEARCH=1` | 频域 τ 相位斜坡搜索 | P166b：边际（18.7% 帧选非零 τ，无 DS 改善）|
| `IEEE80211_LSIG_HSRC_CANDIDATE=1` | H 源多样性候选（L-LTF0/1 独立噪声）| P168 NOT CONFIRMED（DS -1.0, p=0.25）|
| `IEEE80211_TX_PREEMPHASIS=1` + `_GAIN`（wifi_phy_hier.py，TX 侧）| 边缘 SC ±26..±28 预加重 | P169 REFUTED（终败 +2.75, p=0.035——相位噪声∝幅度，功率族关闭）|
| `IEEE80211_HTSIG_BPSK_FALLBACK=1` | HT-SIG BPSK 回退（TX/RX 协调）| P143：0 FCS_OK |
| `IEEE80211_FORCE_HTSIG=1` | L-SIG 非 0xD 时仍试 HT-SIG | 诊断用 |
| `IEEE80211_USE_LDPC=1` / `--ldpc` | TX LDPC 编码 | P166d NOT CONFIRMED（限制在 H 质量，非码强度）|

## 诊断仪表（opt-in，不影响解码）

| env var | 作用 |
|---------|------|
| `IEEE80211_DECODE_SEQ=1` | decode_mac 记录 MAC seq（逐帧命运图，P163b）|
| `IEEE80211_FAIL_PSDU_DUMP=1` | 失败帧 PSDU dump（P161）|
| `IEEE80211_SYM52_DUMP=1` | 逐符号 52-SC dump（P161）|
| `IEEE80211_CPE_DEBUG=1` | CPE 诊断（P161：CPE 跟踪器永久 no-op 证据）|
| `IEEE80211_H52_DUMP=1` / `_EQ_INPUT_DUMP` / `_FILTERED` | H52 三层 dump |
| `IEEE80211_LTF0_FFT_DUMP=1` / `_PRECOMP_DUMP` | L-LTF0 FFT dump |
| `IEEE80211_HTSIG_BIN_DUMP=1` / `_PILOT_DUMP` / `_EQ_DUMP` | HT-SIG 三层 dump |
| `IEEE80211_DELTA_PER_SYMBOL_DUMP=1` | 逐符号 δ 诊断（P38）|
| `IEEE80211_HTSIG_DELTA_DUMP=1` | δ_htsig0/1 + 数据符号 δ（P79）|
| `IEEE80211_FFT_WINDOW_DUMP=1` | FFT 窗位置（P108：上游逐帧稳定）|
| `IEEE80211_WIENER_LOG=1` / `IEEE80211_H52_2WAY_LOG=1` / `IEEE80211_LSIG_H52_CROSS_FRAME_LOG=1` | 各特性触发日志 |
| `IEEE80211_LSIG_EQ_DUMP=1` | L-SIG 均衡输出全 dump |
| `IEEE80211_T7E_MULTISYM_H=1` | P112 T7e 多符号缓存重解码（PARTIAL）|
