# Phase 171 诊断：file 模式 L-SIG 全败机制（2026-08-13）

## 方法

用修好的 `--rx-file` 离线回放工具（Task 1），对 file-TX 模式的新鲜 RX 捕获
（`/home/hy/captures/p171_file_rx.fc32`）做双模式对照。

## 判定链（全部有证据）

1. **splitter 网格自洽（HTSIG_TIMING delta=0）**：
   file 与 live 模式 `[HTSIG_TIMING] L-LTF0/1/L-SIG/HT-SIG0... rel_idx=63/143/223/303 delta=0`。
   → splitter 输出的符号网格相对 wifi_start 标签正确。

2. **信号本身完好（Python 电池）**：
   15/15 帧独立 ltf/lsig 扫描全部 metric=0 可解。
   → 不是信号损坏、不是 LO 噪声、不是物理墙。

3. **最佳偏移高度一致（系统性错位，非随机撕裂）**：
   `ltf_off=-8`（L-LTF 需提前 8 样本），`lsig_off=+8`（L-SIG 需推后 8 样本），
   15 帧中 14 帧完全一致（burst[0] 边界帧 -0/+16）。
   → L-LTF→L-SIG 真实间距 = 160+16 = 176 样本，**非标准 160**。

4. **排除项**：
   - FINE_ROT 8×45°：无效（星座非 45° 倍数旋转；E_I≈E_Q 是错位 ISI 不是旋转）
   - FRAME_START_OFFSET -32..+32 全扫：无效（它整体平移标签，两窗同移，
     帧内间距不变——eq=LSIG/H 中相位斜坡抵消，只留 ISI）
   - splitter 锚点校正（computed_fs）：按本诊断不适用（问题在帧内间距，
     不在锚点）

## 根因

**file 模式帧的 L-LTF→L-SIG 间距被拉伸 16 样本**（相对 802.11n 标准 160）。
splitter 固定网格（L-LTF0 窗 rel 63、L-SIG 窗 rel 223，间距 160）按标准间距
取窗 → L-SIG 窗错位 8 样本 → 落入非 CP 区域 → ISI → 星座散（E_I=49.3
E_Q=36.6 vs live 56.7/19.7）→ viterbi 全败（enc=1 len=667 确定性错码）。

拉伸来源待查（候选：TX 链捕获文件 p170b_tx 的帧结构含 1 样本偏差 + USRP
播放/捕获时钟差；或 file 波形构建时帧间距 2481 vs 标准 2480）。

## 修复方向（Task 3A 修正版）

splitter 增加 L-SIG（及后续符号）窗边界偏移参数：`rel 223 → 223+K`，
K 默认 0（基线不变），file 模式实测需要 K=+8。这是**只调 L-SIG 之后窗**、
不调 L-LTF 窗的定向修复（对应"帧内拉伸"机制）。opt-in env var，
ABAB 验证后决定是否翻默认。

## 2026-08-13 更新：根因修正（模板匹配 + 偏移扫描）

- 模板匹配（L-LTF 已知序列，相关 0.79-0.90）：splitter L-LTF0 窗比真实
  晚 8-18 样本（file 模式）。
- 新鲜捕获 FRAME_START_OFFSET 扫描（此前损坏文件上的全扫 0 结论作废）：
  OFF=-10..-18 平台期 DS ~180/8s（OFF=0 仅 2），最优 ≈ -12。
- 但 live 模式 OFF=-12 → DS 618（基线 788，-22%）——**两种模式需要
  不同工作点**，不能改默认。
- 修正机制表述：不是"帧内间距拉伸"，而是 **sync_short 检测位置依赖
  帧间内容**（欠载噪声 vs 干净静音）→ sync_long SYNC 窗口相位不同 →
  强制 d_frame_start=174 相对真实 L-LTF 的偏差在两模式不同。
- Task 4（sync_short 检测去耦）是根源修复；splitter LSIG_OFFSET
  （IEEE80211_SPLITTER_LSIG_OFFSET）保留为诊断工具。
