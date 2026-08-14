---
name: warn-refuted-direction
enabled: true
event: bash
pattern: IEEE80211_(HTSIG_SOFT_LLR_VITERBI|HTSIG_SOFT_LLR_V2|HTSIG_LIST_VITERBI|LSIG_FINE_ROT|WIENER_H52|H52_FREQ_LOWPASS|TX_PREEMPHASIS|TX_BURST_TSB|TIMING_OFFSET_APPLY|HTLTF_AVG|CONST_CPE_APPLY|HTSIG_PILOT_CPE|USE_LDPC|SYNC_LONG_USE_COMPUTED_FS)\s*=\s*[1-9]|IEEE80211_SYNC_SHORT_(COR_FLOOR|CONFIRM_)|--ldpc\b
---

⚠️ **检测到已判定（REFUTED / NOT CONFIRMED）的实验方向**

该命令启用的 env var 属于 CLAUDE.md「禁止方向」表中已封闭的攻击轴：

| env var | 判定 |
|---------|------|
| `HTSIG_SOFT_LLR_*` / `HTSIG_LIST_VITERBI` | HT-SIG 软判决 P44/P129/P164 三次未过，轴关闭 |
| `LSIG_FINE_ROT` | P165d/P166c：4-rot 已最优 |
| `WIENER_H52` | P141/P166c δ-OFF 重测 REFUTED |
| `H52_FREQ_LOWPASS` | P166c 灾难（PDU 150→37）|
| `TX_PREEMPHASIS` | P169 REFUTED，显著有害（功率族原理性关闭）|
| `TX_BURST_TSB` | P170/P172 REFUTED 作杠杆：只压 U 虚报不改物理（MTU/缓冲族同封闭）|
| `TIMING_OFFSET_APPLY=1` | P159b：L-SIG 抽签根因 |
| `USE_LDPC` / `--ldpc` | P166d NOT CONFIRMED（限制在 H 质量非码强度）|
| `SYNC_LONG_USE_COMPUTED_FS` | P167 REFUTED（DS 646→464）|
| `SYNC_SHORT_COR_FLOOR` / `SYNC_SHORT_CONFIRM_*` | P162b REFUTED / P163 NOT CONFIRMED |
| `HTLTF_AVG` / CPE 修正族 | P122 / P35/P36/P38 REFUTED |

**在继续之前**：确认你有新机制证据推翻原判定（判定原文见
`docs/superpowers/notes/` 对应 phase 文件）。如果只是想复现历史实验，忽略本警告。
