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

## 2026-08-13 终测：决定性测量推翻 file 诊断 + B 关闭（结案）

### 测量设计（用户同意的"一次决定性测量"）

精确测 splitter L-LTF0 窗相对真实 L-LTF 位置的偏移（±1 样本），两模式对照。
事前约定：两模式偏移相同 → 收束；不同 → 继续 B（sync_long 自适应 d_frame_start）。

### 结果（FFT 相位斜率法，52 SC 拟合 ±0.2 样本）

| 模式 | 测得的 Δ | 有效性 |
|------|---------|--------|
| LIVE | +4.2±6（逐帧抖动）| ✅ 有效（splitter 处理真帧）|
| FILE | +8.3..+13.4 | ❌ **无效**（见下）|

### file 臂被证伪（三层）

1. **捕获污染**：p171_file_rx.fc32 每 TX 间隔（2M 样本）有一 0.675 幅度 **DC 瞬变**
   （谱峰 bin 0，0 MHz，时长 ≥187µs，无周期-16 结构），真实帧仅 0.044 幅度——
   splitter 的"file 帧"匹配到的 raw 位置（209 / 266164）全是**噪声段**（无周期结构）。
   此前"Δ=+11.4"是在 DC 信号上测的，作废。
2. **链路停摆**：OFF=0 时 file 回放只处理 1 帧（文件起始噪声段）后 sync_long
   不再发 wifi_start（rx_reset×7）——P146 类 SYNC 停摆。FRAME_START_OFFSET 扫描
   （DS 2→180）的数字建立在噪声帧计数上，"file 需要 -12 工作点"结论撤回。
3. **两模式不同偏移的前提不成立**——file 臂没有有效测量。

### live 臂：B 直接证伪（0/155 救回）

对 live 捕获 p170_fate_iq.fc32 全部 155 帧：基线 L-SIG 解码 148/155；
按逐帧 Δ 平移网格（K=-round(Δ)，H 与 L-SIG 窗同步平移）**148/155，救回 0 帧**。
7 个失败帧画像：2 帧满强度（E≈89，周期16相关 1.17，Δ=-5.2/+2.7 均在安全区——
失败**不是网格**）、2 帧弱（E≈13-16，衰落/撕裂）、3 帧中弱（E≈32-49）。
→ 残余失败 = **内容退化**（TX 欠载洞/衰减），非对齐问题。对齐轴关闭。

### 结案结论

- Task 4-B（sync_long 自适应 d_frame_start / splitter 自动平移）**关闭**：live 0/155
  救回，file 臂实验床本身无效。
- 残余 0.45% = TX 欠载撕裂（P170，USRP 侧）+ LO 相位噪声尾（硬件）+ 罕见内容
  退化帧。软件对齐已穷尽；99.9% 唯一路径 = 外部 10 MHz 参考/GPSDO +
  UHD TX 欠载缓解（缓冲配置）。
- 保留诊断工具（SPLITTER_LSIG_OFFSET / FS_CORRECT / computed_fs tag）为 opt-in。
