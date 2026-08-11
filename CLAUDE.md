# gr-ieee802-11 Project Instructions

> **📌 先读这个：** [项目阶段性反思总结（100+ phase 经验教训）](docs/superpowers/notes/2026-07-15-project-retrospective.md) —
> 核心教训：解码器早已正确；真正的瓶颈是"帧到不了解码器"（调度 stall / 段错误 / 同步），不是均衡器。先根因后修复；每次卡住先定位"瓶颈在哪一层"。

> **📌 当前状态（2026-08-11）：USRP realtime FCS_OK 已达成且稳健。
> Phase 166 系统验证完成：四条额外软件攻击线全部封闭。**
> **电缆最优配置 = 稳定 99.55%**：
> ```bash
> IEEE80211_LSIG_VITERBI_CANDIDATE=1 ./usrp_realtime_validate.sh --tx-scale 0.1
> ```
> 残余 0.47%（~15/3198 帧/300s）= 链损 ~0.25%（sync_short 流压缩 →
> sync_long L-LTF 到达时间抖动 → d_frame_start=174 强制导致 ISI）
> + 解码终败 ~0.22%（频带边缘 SC H 估计质量，\|H\|² 软判决和 LDPC 均已
> 证伪为不可进一步改善）。Phase 166 完整记录：
> - 166a: 标签驱动 d_frame_start — ❌ 根本限制（标签始终在窗口位置 0）
> - 166b: 频域时域偏移搜索 — ⚠️ 边际（只能修 CP 内移位，不能修 ISI）
> - 166c: H-retest δ-OFF 基线 — ❌ Wiener/Freq-smooth/FINE_ROT 全部推翻
> - 166d: LDPC 编码 ABAB — ❌ NOT CONFIRMED（限制在 H 质量，非码强度）
> **突破 99.9% 的唯一原理路径 = 外部 10 MHz 参考/GPSDO**（压 LO 相位噪声）。
> 收官报告：`docs/superpowers/notes/2026-08-11-project-synthesis-report.md`。

## Project Goal: USRP Realtime FCS_OK (ABSOLUTE — DO NOT ACCEPT CLOSURE)

**This is the user's explicit, binding goal: 实现 USRP realtime FCS_OK
(end-to-end through USRP X310 + UBX-160 hardware).**

The user has explicitly rejected "closure" / "现状" / "止损" as a final
state of this project. After Phase 110 (2026-07-07), the user stated
verbatim: **"不可能接受现状，我的目标是实现USRP FCS OK"**.

When evaluating any proposed solution, investigation, or verdict, the
following hierarchy applies:

1. **Primary success metric**: USRP realtime `FCS_OK ≥ Sent/N` (per-frame
   success ratio on actual air path through X310 + UBX-160 hardware).
2. **Acceptable intermediate evidence**:
   - Software loopback 3/3 PASS (decoder correctness validation only — not
     sufficient alone)
   - Synthetic unit tests / cross-check (algorithm correctness validation
     only — not sufficient alone)
3. **NOT acceptable** (and these are not acceptable as **final** states):
   - Concluding "BLOCKED" without an upstream-attack plan that targets
     the actual USRP gate
   - Closing an investigation with "use loopback for verification" as the
     final state
   - Treating `d_is_ht=false` (HT-SIG viterbi upstream gate) as a permanent
     architectural barrier — it is a problem to solve, not to accept
   - Suggesting "equalizer layer is CLOSED" / "stop attacking equalizer" /
     "30+ REFUTED so accept closure" — these are FORBIDDEN as final states
   - Phrases like "the project has reached its limit", "we have done
     everything possible", "give up on USRP realtime", or any variant

## Implications for Phase 166+（2026-08-11 更新）

- **软件层已穷尽（Phase 166 系统验证）**：检测 100%、帧提交 100%、
  解码 ~99.9%（P162 软 viterbi）、L-SIG 旋转帧已救（P165c 4-rot 候选）。
  四条额外软件攻击线在 Phase 166 全部封闭：
  - 标签驱动 d_frame_start（P166a）：❌ 标签始终在 FAST_SYNC 到达（窗口位置 0）
  - 频域时域偏移搜索（P166b）：⚠️ 只能修 CP 内移位，不能修 ISI
  - H-retest δ-OFF（P166c）：❌ Wiener/Freq-smooth/FINE_ROT 全部推翻
  - LDPC 编码 ABAB（P166d）：❌ NOT CONFIRMED（限制在 H 估计质量）
- **残余 0.47%（~15/3198 帧/300s）= 链损 ~0.25% + 解码终败 ~0.22%**
  - 链损根因：sync_short SEARCH 态流压缩 → sync_long L-LTF 到窗时间抖动 → d_frame_start=174 强制导致 ISI
  - 解码终败根因：频带边缘 SC H 估计质量（min|H|~13.7），软判决和 LDPC 均无法补偿
- **突破 99.9% 的唯一原理路径 = 硬件**：外部 10 MHz 参考/GPSDO（压 LO，唯一有
  原理）；增益喇叭（救 SNR 相关成分，对 LO 无效，预期 99.5→99.6-99.7%）。
- **新假设仍须纪律**：单变量、先合成后 USRP、实时配对 ABAB 金标准。
  已证伪方向（避免重复）；Phase 166 新增：
  Wiener H52 δ-OFF 重测（P166c）、Freq Lowpass H52 δ-OFF 重测（P166c）、
  LSIG FINE_ROT δ-OFF 重测（P166c）、标签驱动 d_frame_start（P166a）、
  LDPC 编码（P166d）。
  历史已证伪：HT-SIG 软判决（P44/P129/P164）、L-SIG FINE_ROT 45°
  （P165d/P166c，4-rot 已最优）、sync_short 侧门控（P162b/P163）、Wiener/
  cross-frame H 平均（P141/P140）、CPE 修正族（P161）。
- Software loopback 3/3 PASS 保留为回归门，不能替代 USRP 验证。
- 电缆模式（P165）：裸缆过驱动（+5dBm 超 UBX-160 线性区 20dB）→
  `--tx-scale 0.1`（TX 软件衰减）修复；rx-gain 低会推 boxcar 贴地板
  （勿降到 <20）。

## Project-Specific Conventions

- **make install** must run after every `make` (else Python loads stale .so).
- **GRC ban**: never generate Python from `wifi_phy_hier.grc` (segfaults);
  edit `wifi_phy_hier.py` directly.
- **Thread-safe logging**: `USRP_LOG` is non-atomic. Multi-value dumps use
  `snprintf` + `USRP_LOG("%s", buf)` per commit `e90e3f5`.
- **env vars default OFF**: all new env vars opt-in to preserve baseline
  (EXCEPT harness-setdefault ones below: DATA_SOFT_VITERBI, LSIG_VITERBI_CANDIDATE
  — C++ defaults unchanged).
- **Harness env defaults (test_usrp_rxonly_instrumented.py setdefault, env-overridable)**:
  `IEEE80211_LSIG_RATE_FORCE=0xD TIMING_OFFSET_APPLY=0 HDR_COMP_DISABLE=1
  H52_2WAY_DEFAULT=0 SYNC_SHORT_FUSED_USE_BOXCAR=1 SYNC_SHORT_USE_ADAPTIVE_THRESH=1
  MIN_PLATEAU=24 TRIGGER_MARGIN=2.5 DATA_SOFT_VITERBI=1 LSIG_VITERBI_CANDIDATE=1`
- **Standard USRP test config** (preserved across all phases):
  `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_TIMING_OFFSET_APPLY=0
  --freq 5890 --tx-gain 20`
  (Phase 159b, 2026-08-05: `TIMING_OFFSET_APPLY` flipped 1→0 — the Phase 34
  retroactive δ correction was the root cause of the L-SIG 51/49 lottery;
  see the Phase 159b entry below. Phase 65: removed
  `IEEE80211_LLTF_OFFSET_CORRECT=14` — 14-sample shift already correctly
  applied at `sync_long.cc FRAME_START_BASE 160→174`.)
- **Same-board default**: `A:0` TX → `A:0` RX2 (per Phase 53 verdict,
  cross-board is 2.4x weaker).
- **禁止 `--rate 5`** (Phase 58 REFUTED, 48× more overflows than `--rate 20`).
- **Phase 145c USRP file-replay validation (NEW 2026-07-14)**:
  Standardized one-command validation for USRP IQ. Captures with TX
  enabled but **no wifi_phy_rx in capture flowgraph** (avoids realtime
  RX chain backpressuring USRP source), then replays through file replay
  test with winning config.
  ```bash
  ./usrp_validate_replay.sh 10 5250 0
  ```
  Result: `FCS_OK=5` on fresh USRP capture (5250 MHz cable, tx-gain 0).
  Winning config: `IEEE80211_HDR_COMP_DISABLE=1` +
  `IEEE80211_H52_2WAY_DEFAULT=0` + `IEEE80211_LSIG_RATE_FORCE=0xD` +
  `IEEE80211_TIMING_OFFSET_APPLY=1`.  (Phase 159b later REFUTED the last
  one: δ-OFF is the 2026-08-05 baseline; 145c's replay validation did not
  catch the δ harm because replay chunk dynamics differ.)
  **Realtime capture blocking root cause**: wifi_phy_rx chain (sync_long
  stuck in SYNC state consuming data without producing output)
  backpressures USRP source, limiting capture to ~0.04 MHz instead of
  20 MHz. `capture_usrp_txrx.py` removes wifi_phy_rx from capture
  flowgraph to get complete captures.
- **Phase 146 realtime ROOT CAUSE (NEW 2026-07-15, CORRECTS the above)**:
  Systematic-debugging found the TRUE mechanism is NOT "sync_long
  consuming without producing" and NOT compute. It is a **GNU Radio
  scheduler STALL**: `wifi_phy_rx` is a full transceiver hier used for RX
  only; its IDLE TX path contains a tag-starved `ofdm_cyclic_prefixer`
  (waits for a `packet_len` tag that never arrives because `mac_in` is
  unconnected) which stalls the ENTIRE flowgraph ~5000×.
  **Evidence (hardware-free, reproducible):** manual RX-only chain runs
  **207–263 MHz AND decodes (38 L-SIG + FCS_OK)** on real USRP IQ, while
  the same blocks inside `wifi_phy_hier` run ~0.035 MHz; all threads show
  0 CPU during the stall; adding a single idle `ofdm_cyclic_prefixer` to a
  fast chain stalls it regardless of buffer size. Culprit isolated via
  `p146_bisect.py` (depth 0–5 + `--with-idle-hier` / `--with-ofdm-cp`).
  **Implication: the decode algorithm is fast and correct; the realtime
  blocker is a fixable flowgraph-structure bug, NOT the 1.77 rad noise
  floor.** Fix = RX-only RX path (no idle TX blocks). Harnesses:
  `p146_rx_throughput_probe.py`, `p146_bisect.py`. REFUTED lever: L2
  sync_long early-out (sync_long is fast, 263 MHz front-end).
  Verdict: `docs/superpowers/notes/2026-07-15-phase146-scheduler-stall-rootcause.md`.
- **Phase 147 realtime segfault ROOT-CAUSED + FIXED (NEW 2026-07-15)**:
  The intermittent realtime segfault (the true cause of Phase 146's
  "FCS_OK=2 in 30s", NOT the decoder) was a **data race on `static float
  sorted_buf[4096]` in `sync_short.cc:124`** (adaptive-threshold p90 recompute).
  The static was shared across ALL sync_short instances; a realtime transceiver
  has TWO (wifi_phy_hier RX path `wifi_phy_hier.py:91` + RX-only chain) on
  separate GNU Radio threads, so their concurrent `memcpy`/`std::sort` raced →
  std::sort OOB → SIGSEGV (Heisenbug, vanished under gdb). ASan realtime
  pinpointed it; offline replay has ONE instance so never crashed. **Fix =
  stack-private buffer** (removed `static`). Validated: USRP realtime 45s
  sustained **FCS_OK=46** (was 2/30s + ~50% crash), no crash; file-replay
  regression intact. ASan flow: `cmake -DCMAKE_CXX_FLAGS="-fsanitize=address -g"`,
  `make && make install`, run with `LD_PRELOAD=<conda>/lib/libasan.so`. Harnesses:
  `p147_race_repro.py` (2-instance race repro), `test_usrp_rxonly_instrumented.py`
  (build-once + gain.set_k sweep + windowed PDU count). **Lesson: any
  function-local `static` mutable buffer in a multi-instance block is a
  cross-instance race — keep scratch buffers stack/member-private.** Verdict:
  `docs/superpowers/notes/2026-07-15-phase147-sync-short-race-fix-verdict.md`.
- **Phase 150 realtime path SOLIDIFIED (NEW 2026-07-16)**: The working realtime
  FCS_OK path is now reproducible + regression-gated + reboot-persistent via
  one command: **`./usrp_realtime_validate.sh`** (~65s; PASS if ground-truth
  `DECODE_SUCCESS >= 15` across 45s). RF config (antenna/air): `freq=5250
  tx-gain=0 rx-gain=31.5 rx-scale=40 interval=100ms`. Decoder env = 145c winning
  (LSIG_RATE_FORCE=0xD + TIMING_OFFSET_APPLY=1 + HDR_COMP_DISABLE=1 +
  H52_2WAY_DEFAULT=0 + boxcar + adaptive thresh). **Underflow fix persisted
  across reboot**: `/etc/sysctl.d/99-gr-ieee80211-uhd.conf`
  (wmem/rmem_max=2453333) + systemd `gr-cpu-performance.service` (governor
  powersave→performance). Best this session: `DECODE_SUCCESS=55/45s`, arrival
  12.2%, 0 underflow/overflow. **Ceiling = H52 1.77 rad phase-noise wall
  (UBX-160 internal LO)** — software/streaming REFUTED on ground-truth testbed
  (2-way marginal, Wiener/cross-frame hurt); only real lever = external 10 MHz
  ref clock / GPSDO (unavailable). Offline statistical ruler: `p148_parse.py` /
  `p148_stats.py` / `p148_funnel.py` / `p150_count_frames.py`. Verdict:
  `docs/superpowers/notes/2026-07-16-phase150-realtime-path-solidified.md`.
- **Phase 154 MIN_PLATEAU=24 BREAKTHROUGH (NEW 2026-07-19)**: Arrival 3.4×
  (sweep M ∈ {2,16,24,32,48} → mean {59.5, 124.5, **200.0**, 202.3, 128.7}
  DECODE_SUCCESS/45s; default locked at **24**, arrival ~44%).
  `IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=24` is now the harness setdefault
  (test_usrp_rxonly_instrumented.py; env-overridable, C++ default unchanged).
  Detection requires 25 consecutive above-threshold samples (was 3); real
  L-STF is 160 samples at 20 MHz (10x16) giving a ~128-sample boxcar
  plateau, noise boxcar excursions are capped at ~16 (window length). Mechanism
  (from Phase 153 funnel): false wifi_start tags from noise detections yanked
  sync_long out of real-frame alignment — most L-SIG garbage was chain-state
  pollution, not only the 1.77 rad wall. Residual ~20% detection loss is still
  COPY capture (long ~6ms episodes: noise power hovers at gap-detector 0.01
  threshold, resetting the gap counter). **10 MHz loopback artifact**:
  test_direct_loopback.py uses bandwidth=10e6 (8-sample L-STF period mismatches
  the 16-lag boxcar) — M=24 misses there (M≤16 squeaks by); the regression
  gate at default M=2 is unaffected, and boxcar+adaptive env alone breaks
  10 MHz loopback (different config domain). Phase 153 funnel (measured):
  450 sent → 71.5% sync_short detect → 97% frame-start → 69% L-SIG viterbi →
  17% good L-SIG → 96% HT-SIG → 81% FCS. **Phase 155 REFUTED**: raising
  COPY-state gap-detector POWER_THRESHOLD 0.01→0.3 regressed batch mean
  200→102.5 (revert restored 217.5, best run 249=55.3%) — 0.01 is
  load-bearing, do not retune without a verified model. Verdicts:
  `docs/superpowers/notes/2026-07-19-phase153-arrival-funnel-verdict.md` +
  `2026-07-19-phase154-min-plateau-16-verdict.md` +
  `2026-07-19-phase155-gap-threshold-refuted.md`.
- **Phase 157 refractory model CONFIRMED (NEW 2026-07-19)**: Phase 155's
  gap-threshold regression root-caused — the 0.01 long COPY episode is a
  protective **refractory period** suppressing noise-burst re-triggering of
  false `wifi_start` tags. Gap 0.3 dismantles it: rapid re-triggers (<5k
  sample gaps) exploded **10→218 (22×)** on air → sync_long yanked out of
  alignment (good L-SIG -13%, FCS -14%). Truncation model REFUTED on SMA
  cable (decode attempts == HT-SIG OK, zero truncation). **Do not shorten
  COPY episodes; the residual ~20% COPY capture must be attacked with
  "refractory but not blind" (COPY-state re-detect only for a clearly
  stronger real L-STF plateau).** New env
  `IEEE80211_SYNC_SHORT_GAP_POWER_THRESHOLD` (opt-in, default 0.01).
  Phase 156 (cable experiment): COPY capture is noise-driven (occupancy
  16.5%→2.4%, detection 86.6% on cable) but arrival only 48%→52% — the
  bottleneck has substantively moved to the **1.77 rad LO phase-noise wall**;
  cable rx-gain must be ≥20 (lower pushes boxcar near the 0.2 floor).
  Verdicts: `docs/superpowers/notes/2026-07-19-phase156-cable-experiment-verdict.md`
  + `2026-07-19-phase157-refractory-model-verdict.md`.
- **Phase 158 COPY-state smart re-detection PRELIMINARY POSITIVE (NEW 2026-07-20)**:
  Phase 157's "refractory but not blind" prescription implemented in
  `lib/sync_short.cc` (commits 982d417/5c98910/diag 1ff4970). 4-gate
  re-detect inside COPY (seen_drop / cooldown / power-EMA<0.5 /
  corr>5×thresh sustained >MIN_PLATEAU) re-tags `wifi_start` for a clearly
  stronger L-STF during a false COPY trap WITHOUT shortening the refractory.
  TDD unit test 3/3 GREEN (`p158_redetect_unit.py`); loopback OK=1 both
  configs. USRP A/B: control n=11 169.7±13.9 vs experiment n=2 mean 195.0
  (**+25.3/+14.9%**), perf-governor spot check +17 — consistent direction,
  mechanism confirmed firing on air (17 fires/45s, corr 1.03-2.68), but
  experiment n=3 total → **PRELIMINARY, not CONFIRMED** (needs full N=16).
  New env (all opt-in, default OFF): `IEEE80211_SYNC_SHORT_COPY_REDETECT`,
  `_FACTOR` (5.0), `_EMA_MAX` (0.5), `_DIAG` (per-episode stats).
  **Operational lessons**: harness stderr lands in `/tmp/rt_validate.err`
  (overwritten per run; batch `run_XX.err` is only script stderr — counting
  fires there gives false zeros); check CPU governor before every batch
  (2026-07-20 low baseline 162-170 vs historical 200 was powersave, NOT
  device drift — `sudo systemctl start gr-cpu-performance.service`);
  batch script has no hang timeout — killing a batch mid-run can leave the
  X310 in a bad state and hang the next UHD init (recover: kill stale
  processes + `uhd_usrp_probe` nudge). Verdict:
  `docs/superpowers/notes/2026-07-20-phase158-copy-redetect-verdict.md`.
  Next: full N=16 A/B under performance governor; if CONFIRMED, FACTOR
  sweep (5→3/4) + outlier-fire (corr 44.8/105.3 transients) upper guard.
- **Phase 160 trailing-window fix + "99.6%" artifact correction (NEW
  2026-08-05)**: **⚠️ Correction first** — P159b's "448/450 = 99.6%, goal
  met" was a denominator artifact: `usrp_realtime_validate.sh`'s est_sent
  counts only the 3×15s measurement windows but the DECODE_SUCCESS ground
  truth counts stderr lines INCLUDING the 20s warmup (~200 frames). True
  rate at the time was ~69% (PDU counts, which exclude warmup, cross-check:
  311/450 = 69.1%; 300s run PDU 2030/3000 = 67.7%). The δ fix itself
  stands (paired ABAB p<1e-4). **Lesson: ground-truth counts and est_sent
  denominators must cover the same time domain.**
  **Root-cause fix (detection self-poisoning)**: the remaining ~28%
  detection misses were all SEARCH-state with L-STF strength identical to
  detections (646.1 vs 646.4, fresh 60s capture + GT extraction
  `p160_gt_extract.py` + real-chain replay DIAG per-slot join). Cause:
  sync_short's SEARCH branch filled the 4096-sample adaptive window with
  the WHOLE current chunk BEFORE scanning (look-ahead), so a strong
  frame's own ~2000-sample correlation region pushed p90 to the frame's
  level (646) and threshold ~969 killed its own detection (374/76586
  calls with p90>100 measured). Fix = trailing window (fill after scan,
  scanned prefix only; frame body never enters the window). TDD
  `p160_poison_unit.py` 4/4 (was RED: strong frame deterministically
  undetected); loopback OK=1; replay misses 100→2 (99.7%); USRP PDU
  146-148/150 per window (was ~104). Commit `a03c3ad`.
  **Margin re-sweep on δ-OFF baseline**: 1.0→389.5 / 1.5→433.0 /
  2.0→433.8 / **2.5→453.2** (vs 1.0 p=0.016) — 2.5 stays the default;
  "2.5 over-gates weak frames" REFUTED.
  **Current true budget (300s x2)**: detection ~99.7% / chain 98.3% /
  FCS 97.6% / **end-to-end 98.9%** (PDU 2968/3000). Residual 32/3000 for
  the 99.9% goal: LDPC terminal 21 (4 is_ht misclass + 17 payload bit
  errors — needs decode_mac TX-reference wiring for bit-level
  localization) + ~10 detection edge cases (replay GT#212/#375).
  Verdict: `docs/superpowers/notes/2026-08-05-phase160-trailing-window-verdict.md`.
- **Phase 161 residual-failure root cause (NEW 2026-08-05)**: systematic-
  debugging of the residual ~1% (32/3000) localized the LDPC-terminal
  failures (21/3000) to a **band-edge subcarrier fade tail**: failing frames
  are our own (FAIL_PSDU dump: mac header intact), with min|H| p50=**13.7**
  vs OK 28.7, argmin always at **SC -28/-27** (20MHz channel/filter rolloff
  edge). Weak edge SC → ZF 1/H amplifies noise → bit errors → deinterleaver
  spreads them → deep realizations exceed the hard-decision viterbi budget
  (~40/1144) → FCS fail. NOT a decoder bug. **CPE tracker found to be a
  permanent no-op** (`estimate_ht_data_cpe` looks up pilot SCs in the
  data-only `kTxOrder52` → h_idx always -1 → cpe≡0); per-symbol phase drift
  exists (p50 -4.5°/sym) but the **M-power CPE fix is REFUTED** (ABAB DS
  −43.7, p=0.005 — noise-adding correction, same class as HDR_COMP_DISABLE).
  The only principled path to 99.9% is soft-decision viterbi (|H|²-weighted
  LLR) — has a Phase 129 REFUTED history (δ-on era), kept as a separate
  future phase. New opt-in diagnostics: `IEEE80211_SYM52_DUMP`,
  `IEEE80211_CPE_DEBUG`, `IEEE80211_FAIL_PSDU_DUMP`. Verdict:
  `docs/superpowers/notes/2026-08-05-phase161-residual-failure-rootcause-verdict.md`.
- **Phase 159b δ-correction REFUTED = ROOT CAUSE of L-SIG lottery — PROJECT
  GOAL ESSENTIALLY MET (NEW 2026-08-05)**: systematic-debugging on the
  post-margin ~49% chain failure localized it to `frame_equalizer_impl.cc:7466`:
  the Phase 34 retroactive δ (timing-offset) correction rewrites cached
  L-SIG/HT-SIG symbols at counter=4 using a per-frame δ estimate from the
  noise-dominated H52 phase slope (1.77 rad/SC); ~50% of frames get a bad
  estimate and their otherwise CLEAN constellation (pre-δ dump template
  hamming 0-3/48) is converted to garbage (post-δ viterbi input hamming
  25/48). All rival hypotheses refuted with evidence (alignment / magnitude /
  CFO / SFO / time-clustering / TX populations / truncation). N=8 interleaved
  ABAB (`TIMING_OFFSET_APPLY` 1→0): DS 231.9→**453.8/45s** (+221.9, t=34.55,
  **p<1e-4**, 8/8 pairs +198..+251), arrival 240.6→**464.0 (~100% of sent)**,
  decode-of-arrived 97.8%. Validation on new default: **DECODE_SUCCESS=448/450
  = 99.6%**, 0 UF/OF, loopback OK=1 (loopback always ran δ-off; C++ default
  was already OFF). **Harness default flipped to 0** (setdefault,
  env-overridable). **The "1.77 rad LO wall" was substantially this
  correction's artifact** — P150's "software-unbreakable" conclusion and the
  30+ equalizer-layer REFUTED phases must be reinterpreted on the δ-OFF
  baseline. Lesson for the Phase 34 legacy: any retroactive symbol rewrite
  driven by a noise-sensitive per-frame estimate needs a per-frame validity
  gate. Verdict:
  `docs/superpowers/notes/2026-08-05-phase159b-timing-offset-refuted-verdict.md`.
- **Phase 159 trigger margin CONFIRMED BREAKTHROUGH (NEW 2026-08-04)**: First
  arrival-focused win after user redirected the main task to 到达率 (arrival).
  **Arrival budget newly measured** (episode-start position logging in
  P158-DIAG + TX-lattice fit, `p159_lattice_analysis.py`): detection 78% /
  sync_short trap-blindness 0.15% (dead axis post-M24) / **chain success
  35% (main bottleneck)** / decode-of-arrived 95% (LO wall only ~5%).
  Chain-failure mechanism: SEARCH-state sample dropping compresses the
  episode stream into sync_long (100ms → ~3ms), so real-frame tags almost
  always arrive mid-COPY → 687 FAST_SYNC restarts + 136 HT_MIXED ignores per
  65s; **46% of sync_long's diet is noise-trap episodes**. Separation band:
  trap max_cor ≤0.36 (1.3-1.8× floor) vs real ≥500 (2500×) — 0.4-10 empty.
  **Lever**: `IEEE80211_SYNC_SHORT_TRIGGER_MARGIN` (plateau gate =
  margin×threshold, default 1.0). N=8 interleaved ABAB: DS 176.8→232.5
  (**+55.8, p=0.0001**, 8/8 pairs positive), arrival +46.8 (p=0.0002);
  traps 5015→**18** (-99.6%), strong episodes -13%, chain success 36%→51%.
  TDD `p159_margin_unit.py` 4/4; loopback OK=1. **Harness default flipped
  to 2.5** (setdefault in test_usrp_rxonly_instrumented.py, env-overridable,
  C++ default 1.0) — future A/B controls run on the 2.5 baseline.
  **P132 S&C REFUTED same day**: amplitude-normalized |P|²/R² saturates on
  weak narrowband spurs (|P|/R→1, ms-long plateaus) → 71661 tags/45s flood;
  P88 MA-ratio story again (synthetic-only). Next per new budget: 49%
  chain failure (real→real restart preamble truncation / L-SIG robustness)
  > 22% missed detection (two-threshold hysteresis) > margin fine-tune.
  Verdict: `docs/superpowers/notes/2026-08-04-phase159-trigger-margin-verdict.md`.
- **Phase 158-ABAB NOT CONFIRMED — axis CLOSED (NEW 2026-08-04)**: User-approved
  time-saving replacement for the full N=16 A/B: **N=8 interleaved ABAB**
  (`p158_abab_batch.py` — odd pairs OFF→ON / even ON→OFF cancels time drift;
  240s hang timeout + killpg + probe retry per attempt fixes P158 lesson #3;
  pre-registered paired t p<0.05 & mean>0 → CONFIRMED). 8/8 pairs valid,
  0 retries, 0 UF/OF: diffs [−27,−25,+32,−8,+8,+7,+4,−40] → **mean −6.12,
  t(7)=−0.74, p=0.485 (Wilcoxon 0.547)**. 95% CI [−25.8,+13.5] puts the
  preliminary +25.3 OUTSIDE → it was cross-block environmental confounding
  (control arm drifted 165–221 within 20 min; drift ±30 > the "effect").
  Mechanism analysis: ON-arm fires only ~2.9/45s (vs 17 in the preliminary
  DIAG run) — even 100% conversion is +1.6% arrival, so FACTOR=5 was never
  a lever; FACTOR sweep not recommended (approaches P155/P157 refractory-
  damage region; P156 showed the bottleneck is the LO wall). Feature stays
  opt-in default OFF. **Methodology upgrade: `p158_abab_batch.py` is now the
  STANDARD harness for all single-variable A/B** (paired design removes the
  ±30 time-drift confound that made un-paired cross-block comparisons
  unreliable). Verdict:
  `docs/superpowers/notes/2026-08-04-phase158-abab-not-confirmed.md`.
- **Phase 158-W32 boxcar window axis CLOSED (NEW 2026-07-20)**: User-proposed
  boxcar smoothing window 16→32 tested as single-variable A/B (pre-registered
  prediction: <1σ difference — CONFIRMED). USRP back-to-back N=16 each:
  W=16 162.44±15.93 vs W=32 161.81±15.86 (Δ=-0.4%, ~1/9 of the standard
  error). Mechanism: adaptive threshold (p90×1.5) cancels white-noise √W
  gains (synthetic margin only 1.18×); trap-causing structured bursts
  (DC/LO spurs) integrate coherently so windows don't suppress them
  (re-confirms Phase 88); residual ~20% loss lives in COPY state where the
  detector isn't running. **Do not retest longer windows.** New env
  `IEEE80211_SYNC_SHORT_FUSED_BOXCAR_LEN` (opt-in, default 16; 8/32/64
  selectable). Lesson: control was 162 vs historical 200 — device drift is
  real, always run fresh back-to-back controls. Verdict:
  `docs/superpowers/notes/2026-07-20-phase158w32-boxcar-len-verdict.md`.
  Next: Phase 158 COPY-state smart re-detection, plan
  `docs/superpowers/plans/2026-07-20-phase158-copy-redetect.md`.
- **IEEE80211_HDR_COMP_DISABLE=1** — Phase 145c (opt-in, default OFF):
  Skips header CFO/SFO compensation for L-SIG/HT-SIG0/HT-SIG1. On USRP,
  L-LTF0/L-LTF1 phase_diff is dominated by ~1.77 rad per-SC noise, so
  applying it as "CFO/SFO compensation" adds noise instead of removing it.
  **Required for USRP file-replay validation.**
- **IEEE80211_H52_2WAY_DEFAULT=0** — Phase 145c: Disables Phase 139
  2-way L-LTF0+L-LTF1 SNR-weighted H52 averaging. L-LTF0 and L-LTF1 have
  independent phase noise on USRP; averaging corrupts phase for most
  frames. **Required for USRP file-replay validation.**
- **Phase 82+ cable test config (NEW 2026-07-04, user accepted)**:
  Direct SMA cable (NO attenuator) is Phase 82 default per user
  decision after Phase 81 verdict. 30 dB SMA attenuator (HAT-30+)
  no longer required upfront. 5250 MHz cable gives +5.7 dB
  avg_snr_htsig boost vs 5890 air (9.61 dB vs 4.25 dB), well above
  6 dB viterbi threshold. **HW risk**: bare cable at --tx-gain 0
  sends ~+5 dBm into RX2 (20 dB above UBX-160 -15 dBm max). **Limit
  total cable runs ≤5** until 30 dB attenuator arrives.
  Test command: `test_usrp_minimal_loopback.py --freq 5250
  --tx-gain 0 --rate 20 --warmup 60 --rx-subdev A:0`
  (no `--cross-board`; same-board TDD with bare SMA male-male cable
  connecting TX/RX port to RX2 port on A:0).
- **Phase 82 attack direction (NEW 2026-07-04)**: Root-cause fix of
  Phase 34 δ correction algorithm at 5250 MHz cable. Bottleneck
  shifted from "viterbi wall" (5890 air, 4 dB SNR) to "Phase 18
  strict rate=0xD check rejects 0x9 decode at 5250" (Phase 81).
  Strategy: fix δ estimator so 5250 produces correct rate=0xD;
  HT-SIG decoder then unlocks naturally without LUT or rate-accept
  knob. Plan: T1 clean raw IQ capture → T2 offline δ analysis →
  T3 root-cause ID → T4 minimal fix → T5 USRP realtime verification.
  **VERDICT 2026-07-04: REFUTED** (`docs/superpowers/notes/2026-07-04-phase82-verdict.md`).
  ε-scan over [-32, +32]/64 produces at most 10/149 frames (6.7%) at
  rate=0xD — no clean shift. SNR on this capture (-2.6 dB) is 10 dB
  below Phase 81's reported 7.11 dB; LTF ref division (T3.5) confirmed
  it is NOT a Python analysis bug. **Equalizer-layer attack surface
  EXHAUSTED on δ-tuning (20+ REFUTED including Phase 82).** Despite this,
  per user's 2026-07-07 directive "不可能接受现状", equalizer-layer attacks
  with NEW architectures (DD / Kalman / alternative H estimation) MUST
  continue — no closure.
- **IEEE80211_HTSIG_PER_SYMBOL_DELTA=1** — Phase 79 per-symbol δ tracking
  for HT-SIG0/1 + data symbols (QBPSK-aware grid-search over 64-point δ).
  Default OFF. **REFUTED on USRP** (Phase 79 verdict 2026-07-02,
  FCS_OK=0/90, avg_snr_htsig=2.80 dB). Estimator works (synthetic 4/4 PASS)
  but USRP structural noise dominates. Kept as opt-in for triage.
- **IEEE80211_HTSIG_DELTA_DUMP=1** — Phase 79 diagnostic: logs δ_htsig0,
  δ_htsig1, per-data-symbol δ. Default OFF.
- **IEEE80211_HTSIG_PER_SC_LUT=/path/to/lut.json** — Phase 80b per-SC phase
  calibration LUT (median over N≥30 USRP frames, applied at HT-SIG0/HT-SIG1 +
  data symbol equalizer output). Default OFF (env unset). **REFUTED on USRP**
  (Phase 80b verdict 2026-07-04, USRP 5250 MHz 60s tx-gain 0: Sent=120
  Recv=0, HT_SIG_CAND=16/16 crc_fail). C++ preserved for future use if
  upstream gates ever unblock. **Equalizer attacks MUST continue** despite
  this REFUTED — see Project Goal section.
- **IEEE80211_HTSIG_H_AVERAGE=1** — Phase 118b HT-SIG pilot-augmented H52 averaging
  (opt-in, default OFF). Computes 2 LTS + 1 H_htsig0 + 1 H_htsig1 weighted average
  using Phase 39's estimate_H_from_htsig_pilots inner kernel, dampening 4→52
  interpolation overshoot. Goal: reduce HT_SIG metric from 13 (Phase 117) toward
  ≤10 viterbi threshold. **PARTIAL** (Phase 118b verdict 2026-07-08): metric 13→12
  (best 4 candidates), 5 HTSIG_H_AVERAGE fires, HT_SIG_CAND 144→48, avg_snr_ht
  2.81→2.58. C++ preserved as opt-in. **Phase 119 (per-bin safety filter)
  REFUTED on USRP** — safety filter rejecting |H_pilot-Hhdr52|>50% did NOT
  improve metric (still 12-17). Pilot-based H does not overshoot significantly
  enough. Phase 118b H_AVERAGE already at theoretical per-symbol H refinement
  limit from 4 pilots. Verdict: `docs/superpowers/notes/2026-07-08-phase119-h-average-safe-verdict.md`.
- **IEEE80211_DDE_HT_SIG=1** — Phase 120a Decision-Directed Equalizer
  (scalar DDE, opt-in, default OFF). Uses BPSK hard decisions from HT-SIG0
  to estimate a single complex H value (averaged over 48 data + 4 pilot
  SCs), applied to all 52 SCs of HT-SIG1. Goal: break the 1.77 rad
  per-SC noise floor (Phase 112 R1) via 52-sample averaging.
  **REFUTED on USRP** (Phase 120a verdict 2026-07-08): metric 13-16/13-18
  (no improvement over Phase 118b's 12-16). Scalar H loses frequency
  selectivity; BPSK hard decisions at 1.77 rad noise give ~20% bit error
  rate → magnitude loss 0.58 + noise 0.26 rad → effective per-bit SNR
  -9.6 dB (worse than 1.77 rad baseline). Verdict:
  `docs/superpowers/notes/2026-07-08-phase120-dde-verdict.md`. Next:
  per-SC DDE with phase outlier filter / soft DDE with LLR weighting /
  iterative DDE.
- **IEEE80211_DDE_HT_SIG_PER_SC=1** — Phase 121 Per-SC DDE with dot-product
  filter (opt-in, default OFF). H_est[sc] = rx52_a[sc] / constellation[sc],
  filter `Re(conj(Hhdr52)*H_est) > 0` to reject wrong-bit SCs (inverted H).
  Goal: preserve per-SC frequency selectivity + reject inverted H from
  wrong bits. **REFUTED on USRP** (Phase 121 verdict 2026-07-08): metric
  14-17 (WORSE than Phase 118b's 12-16 and Phase 120a's 13-18). H_est and
  Hhdr52 have same noise level (1.77 rad); per-SC DDE keeps inverted H at
  50% of wrong-bit SCs (filter noise margin too small at 1.77 rad). DDE
  fundamentally limited at 1.77 rad ceiling. Verdict:
  `docs/superpowers/notes/2026-07-08-phase121-dde-per-sc-verdict.md`.
  **Phase 118b H_AVERAGE remains the best equalizer-layer result (metric 12)**.
- **IEEE80211_HTLTF_AVG=1** — Phase 114/115 3-way H52 averaging: 2 LTS + 1 HT-LTF
  (opt-in, default OFF). Phase 115 same-board verdict: 3-way fires 2x, metric
  14-16 (slight improvement). **Phase 122 cross-daughterboard verdict 2026-07-08:
  REFUTED** — 3-way BREAKS L-SIG viterbi (LSIG_DECODE_OK 27→0, HT_SIG_CAND
  144→0). Cross-board has INDEPENDENT LOs → 0.5-1 rad drift between L-LTF and
  HT-LTF (5-6 symbols). 3-way averaging adds drift penalty > noise reduction.
  **2 LTS only (Phase 117 baseline) is best for cross-board**. Verdict:
  `docs/superpowers/notes/2026-07-08-phase122-htltf-avg-revisit-verdict.md`.
- **IEEE80211_H52_CROSS_FRAME_TRACK=N** — Phase 123 cross-frame H52 tracking
  (opt-in, default OFF). N ∈ {1..8}. Stores refined H_a_ptr from previous N
  frames in FIFO ring buffer; averages with current frame's H_a_ptr.
  Chains AFTER Phase 118b H_AVERAGE: σ_post_avg / sqrt(N). N=4 → σ ~ 0.44 rad
  (theoretical break below 1 rad viterbi wall). Frequency-keyed reset (1 Hz
  threshold). **Phase 123 verdict 2026-07-08: INCONCLUSIVE on USRP** —
  implementation correct (compile OK, loopback 1/1 PASS), but USRP test
  Recv=0/120 due to sync_short detection starving the HT-SIG chain (apply
  block is gated behind `if (lsig_ok)`). File-replay validation needed for
  Phase 124. Verdict: `docs/superpowers/notes/2026-07-08-phase123-cross-frame-h-verdict.md`.
- **IEEE80211_FFT_WINDOW_DUMP=1** — Phase 108 diagnostic (opt-in, default OFF):
  dumps abs_in_off, d_data_start_rel, sym_idx_at_h52, d_internal_symbol_counter
  at the H52 compute site. Used to verify upstream FFT window alignment.
  Confirmed upstream is sample-stable per-frame on USRP (all 8 frames have
  identical d_data_start_rel=7). Verdict: 2026-07-06-phase108-fft-window-fix-verdict.md.
- **IEEE80211_CONST_CPE_APPLY=1** — Phase 108 fix (opt-in, default OFF):
  constant CPE rotation at L-SIG boundary to absorb a "static" phase offset.
  **REFUTED on USRP** — per-SC phase_offset varies from -180° to +180° across
  2733 measurements (linear std=81.6°, circular std=79.1° ≈ random); not a
  constant 30° as Phase 107 hypothesized. Fix DOES reduce |eq|^2 max outlier
  by ~100x (18827 → 175) but mean FCS_OK is unchanged (11 → 11 across 5 runs
  each). Preserved as opt-in for debugging. Phase 108 verdict 2026-07-06.

- **IEEE80211_SYNC_LONG_TAG_ALIGNED=1** — Phase 166a tag-aligned d_frame_start
  estimation (opt-in, default OFF). Uses wifi_start tag position to derive expected
  L-LTF T1 position, replacing forced d_frame_start=174. **Fundamentally limited:**
  tag always arrives during COPY→SYNC FAST_SYNC transition (window position 0),
  provides no per-frame timing discrimination. Feature is a harmless no-op on USRP
  (tag_rel=-1 → falls back to baseline forcing). Code in `lib/sync_long.cc`.
- **IEEE80211_LSIG_TIME_OFFSET_SEARCH=1** — Phase 166b frequency-domain time-offset
  search for L-SIG viterbi (opt-in, default OFF). Tries τ ∈ {-4,-2,0,+2,+4} FFT
  window offsets via phase ramp `exp(-j·2π·k·τ/64)` on H52, picks best L-SIG decode.
  Marginal: 18.7% frames choose non-zero τ (all negative) but no significant DS
  improvement. Corrects CP cyclic shift but cannot fix ISI (chain loss root cause).
  Code in `lib/frame_equalizer_impl.cc`.
- **IEEE80211_USE_LDPC=1** (env) / **--ldpc** (CLI) — Phase 166d LDPC encoding
  (opt-in, default OFF = BCC). Passes `use_ldpc=True` to wifi_phy_hier TX mapper.
  RX chain auto-detects via `use_ldpc` stream tag. **NOT CONFIRMED: N=4 ABAB
  DS -5.7% (p=0.30), FAIL +2.5 (p=0.23).** LDPC's stronger error correction
  doesn't help because limit is H estimation quality (input LLR), not code strength.
  Supported in `test_usrp_rxonly_instrumented.py` and `usrp_realtime_validate.sh`.

## Reference

- Phase 41 closure: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md`
- Phase 59 BLOCKED: `docs/superpowers/notes/2026-06-29-phase59-h52-null-interp-verdict.md`
  (call site unreachable due to `d_is_ht` gate; Phase 60 must attack
  upstream).
- Phase 77 closure (equalizer ceiling): `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
- Phase 80b REFUTED (per-SC LUT): `docs/superpowers/notes/2026-07-04-p80b-verdict.md`
- Phase 86 verdict (L-LTF0 audit, Phase 78b 5-stable-nulls REFUTED):
  `docs/superpowers/notes/2026-07-04-phase86-verdict.md`
- Phase 87 verdict (sync_short L-STF detection FAILS, sync_long noise fallback):
  `docs/superpowers/notes/2026-07-04-phase87-verdict.md`
- Phase 88 verdict (sync_short_fused MA(48)/MA(64) ratio FLAWED):
  `docs/superpowers/notes/2026-07-04-phase88-verdict.md`
- [Phase 89 verdict (sync_short detector replacement SUCCESS)](docs/superpowers/notes/2026-07-04-phase89-verdict.md)
- **IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1** — Phase 89 fix (opt-in, default OFF):
  replaces |MA(48)/MA(64)| with 16-sample boxcar-smoothed raw period-16 autocorr.
  Boxcar at noise (σ²=0.008) = 0.13; boxcar at L-STF (BPSK ±1) = 16. 100× margin.
- **IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1** — Phase 89 fix (opt-in, default OFF):
  threshold = max(median(last 4096 samples)*10, 0.01). Startup gate uses 3.0
  until window fills to suppress early-window false positives.
- **IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE** — Phase 89 fix (opt-in, default 2).
  Set to 16 to match L-STF plateau structure.
- **Repl results (80M samples, 4s)**: 24 detections at corr=1.95-20876 (was 174
  noise at 0.02-0.18 in Phase 88). HT_SIG_CAND: 16 entries (one frame through
  viterbi). Loopback 1/1 PASS unchanged.
- 5250 MHz cable run still required for USRP realtime FCS_OK ≥ 1 (HARD CONSTRAINT).
  HT-SIG viterbi needs avg_snr_htsig > 6 dB (currently 2-3 dB at 5890 air).
- 30+ REFUTED equalizer-layer hypotheses documented in MEMORY.md
  `禁止方向` section. **Equalizer layer is NOT closed** — per user's 2026-07-07
  directive "不可能接受现状", equalizer attacks MUST continue. New
  architectures (DD + Kalman + alternative H estimation + per-frame phase
  tracking) must be tried. Phase 87 found root cause UPSTREAM: sync_short
  fails L-STF detection → sync_long correlation search fallback
  (sync_long.cc:555) produced 156 NOISE frames in /tmp/p28_loopback_iq.fc32.
  **Phase 84 51% rate=0x9 was the equalizer's response to NOISE, not a
  channel property.** Phase 88 IDENTIFIED the algorithm flaw; Phase 89
  REPLACED the detector (not just threshold tune) — sync_short now works
  on USRP IQ (corr=3.163 detected). Equalizer itself is the next target.
- **IEEE80211_SYNC_SHORT_FUSED_DUMP=1** — Phase 88 diagnostic: logs
  batch_power, noise_floor, max_cor, n>0.001, n>0.01 per call. Default OFF.
- [Phase 133 sync_long multi-feature detector](docs/superpowers/notes/2026-07-09-phase133-sync-long-multi-feature-verdict.md) —
  `IEEE80211_SYNC_LONG_SCHMIDL_COX=1` adds Schmidl-Cox |P|²/R² at lag=80 to
  sync_long's plateau acceptance. Opt-in (default OFF). File-replay validates
  gating; USRP needs fast-path removal (see Phase 135) to actually run.
- **IEEE80211_SYNC_LONG_SCHMIDL_COX_THRESHOLD** — Phase 133 threshold (opt-in,
  default 0.05). Lower accepts more candidates (more noise risk). Higher is
  stricter (rejects more valid peaks at low SNR).
- [Phase 135 sync_long wifi_start fast-path REMOVAL](docs/superpowers/notes/2026-07-09-phase135-fast-path-removal-verdict.md)
  (commit 4486bc4, 2026-07-09). The SYNC+wifi_start→COPY fast-path (Phase 14 /
  31b additions, sync_long.cc lines 173-186 in pre-P135) BYPASSED
  search_frame_start() and therefore the Phase 133 multi-feature gate.
  Per user direction "拆掉 fast-path (推荐)", P135 removed the SYNC-state
  direct COPY transition. Now all frame-start transitions flow through
  search_frame_start() at the SYNC_LENGTH boundary, where the P133 gate runs.
  T4c USRP verification (5250 MHz cable, --tx-gain 0): P133 fires 18x in 20s
  (3 ACCEPTED + 15 REJECTED) — first time P133 multi-feature gate runs on
  real USRP. Pre-P135 (Phase 134 verdict): gate never fired. P135 is
  ARCHITECTURAL FIX (not performance fix); downstream 1.77 rad ceiling
  (Phase 112 R1) unchanged. Phase 136+ continues to attack upstream
  noise reduction with P133 gate now wired into USRP continuous streaming.
  COPY-state wifi_start handler (lines 297+) is preserved — different code
  path (COPY→SYNC for new frame), not the bypass problem.

- [Phase 139 architecture rewrite L-SIG upstream gate 2026-07-10](docs/superpowers/notes/2026-07-10-phase139-architecture-rewrite-verdict.md) —
  **PARTIAL: L-SIG wall BROKEN for first time in 30+ REFUTED attempts.**
  2-way L-LTF0+L-LTF1 SNR-weighted H52 + opt-in 3-way/4-way/5-way pilot
  refinement. LSIG_DECODE_OK 0/8 → 4/4 (T3 USRP 5250); HT_SIG_CAND 0 → 16-32;
  avg_snr_htsig 2-3 dB → 8.78 dB; best metric 14 → 13 (4-way lucky event,
  not reproduced in T3e). 0 FCS_OK at 1.77 rad per-SC noise floor. 5/5 cable
  budget EXHAUSTED. New env vars:
  - **IEEE80211_H52_2WAY_DEFAULT=1** (default ON as of Phase 139) — enables
    2-way L-LTF0+L-LTF1 SNR-weighted H52 averaging. Opt-out via =0.
    σ 1.77 → 1.25 rad. Affects Hhdr52_for_lsig at line 7710+ (replaces
    single-source L-LTF0). Default flip is the only behavioral change that
    affects ALL users — necessary because L-SIG viterbi upstream gate is
    architecture-level, not user-specific.
  - **IEEE80211_HT_SIG_PILOT_REFINE=N** (opt-in, default OFF). N ∈ {1,2}:
    N=1 → 3-way (2 LTS + HT-SIG0 4 pilots); N=2 → 4-way (+ HT-SIG1 4 pilots).
    Combined with IEEE80211_H52_2WAY_DEFAULT=1 gives 5-way when
    IEEE80211_HTLTF_AVG=1 also set (σ → 0.84 rad theoretical, not observed
    on USRP). Phase 122 cross-board warning: 5-way may break cross-board
    L-SIG viterbi — keep opt-in for cross-board config.
  - **IEEE80211_H52_2WAY_LOG=1** — Phase 139 diagnostic: logs
    H52_2WAY / H52_5WAY fires with counter and source. Default OFF.
  8 commits (6f226d1, acf20b6, aba4cc8, 405d90d, 5756f7b, aa8cb41b,
  d176749, aa2e18b) plus 3 verdicts (T1-T2 file-replay 1/1 PASS, T3
  2-way USRP PARTIAL, T3b-e 3-way/4-way/5-way K-sweep PARTIAL). Architectural
  significance: FIRST architectural rewrite in equalizer layer (Phase 60-138
  were layer tweaks on single L-LTF0 source). 2-way is now the new baseline;
  future equalizer-layer attacks should build on it. Next direction per user
  directive: 30 dB SMA attenuator install (HW, $50, strongest path forward)
  or Wiener filter / multi-frame averaging (architectural).

- [Phase 140 2-way + Cross-Frame H52 (verdict)](docs/superpowers/notes/2026-07-10-phase140-verdict.md) —
  **PARTIAL: file-replay 1/1 PASS for all N ∈ {0,1,2,4,8}; USRP deferred** (5/5 cable budget
  already exhausted in Phase 137/138/138-B/139; 30 dB SMA attenuator install was
  user-excluded per "排除一下衰减器" directive). Phase 140 stacks Phase 127 L-SIG
  cross-frame FIFO averaging AFTER Phase 139's 2-way L-LTF0+L-LTF1 H52 averaging.
  New env vars:
  - **IEEE80211_PHASE140_ON=N** (opt-in, default OFF). N=0 is a no-op (2-way default
    since Phase 139 is unaffected); N ∈ {1,2,4,8} enables FIFO averaging at L-SIG
    viterbi. Theoretical σ reduction at full FIFO (n_xf = 1+N samples averaged;
    σ_post = 1.25/√(1+N)):

    | N | σ_post at full FIFO (1.25/√(1+N) rad) | vs Viterbi threshold (0.52 rad) |
    |---|----|----|
    | 1 | 0.884 | -0.364 rad above (still fails) |
    | 2 | 0.721 | -0.201 rad above (fails) |
    | 4 | 0.559 | -0.039 rad above (borderline) |
    | 8 | 0.417 | +0.103 rad below (THEORETICAL PASS) |

    N=8 (0.417 rad) is the FIRST time the equalizer-layer has a theoretical path
    below the 0.52 rad viterbi threshold. N=4 (0.559 rad) is borderline. C++ preserved
    as opt-in.
  - **IEEE80211_LSIG_H52_CROSS_FRAME_LOG=1** — Phase 140 σ-reduction diagnostic log
    (opt-in, default OFF). Logs n_avg, depth, sigma_est_input, sigma_est_post at the
    L-SIG cross-frame entry site when N ∈ {1,2,4,8}. Default OFF. Properly gated via
    `d_lsig_h52_cross_frame_log` member added in review issue C1; without this env
    var the snprintf does not fire (no log noise).

- [Phase 137 stable-null-aware masking with alternative CPE](docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md)
  (NEW 2026-07-09). 3-layer opt-in fix targeting Phase 78b's 5 stable null SCs
  {-21,-13,-7,+7,+21}:
  - **L1**: `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21'` — extended env format
    accepts signed SC values (-26..+26). Backward-compat with old loop-position
    format `12` preserved.
  - **L2**: `IEEE80211_HTSIG_NULL_PILOT_MASK=1` — opt-in flag. Skips 4 null
    pilots {-21,-7,+7,+21} (kScIndex52 positions 48..51) in CPE estimator.
  - **L3**: Auto data-SC CPE fallback when all 4 pilots masked/invalid (no env).
  Default OFF. USRP T4-T5 REFUTED: T5 #1 best metric=11 (avg_snr_htsig=1.93 dB),
  T5 #2 best metric=14 (avg_snr_htsig=5.05 dB); both > 10 viterbi threshold.
  Phase 112 R1 1.77 rad ceiling dominates. 3 cable runs (within ≤5 budget).
- **IEEE80211_H52_FREQ_LOWPASS=1** + **IEEE80211_H52_FREQ_LOWPASS_K=N** —
  Phase 138 H52 frequency-domain low-pass filter (opt-in, default OFF).
  Exploits OFDM channel sparsity in frequency domain:
  - `IEEE80211_H52_FREQ_LOWPASS=1` enables the filter
  - `IEEE80211_H52_FREQ_LOWPASS_K=N` sets K value (default 10, range 1..51)
  - Algorithm: DFT(52) → zero bins >= K → IDFT(52). Theoretical σ reduction:
    K=5: 0.55 rad, K=10: 0.78 rad, K=20: 1.12 rad (from 1.25 rad baseline)
  - 4 call sites: (0) L-LTF-only H52 affecting ratio_ht path [line 6238,
    Phase 138-B], (1) 3-way HT-LTF primary (counter==6), (2) L-LTF0 lazy,
    (3) Kalman update. Call site 0 is the ONLY one that runs on USRP real
    tests (others are gated by d_apply_htltf_avg / d_have_ht_header conditions
    that never trigger).
  Default OFF. Phase 138 USRP tests REFUTED 2026-07-09 (3 dead-code call
  sites). Phase 138-B PARTIAL 2026-07-09 (call site 0 active, K=20 produces
  16-32 HT_SIG_CAND events, best metric=13-15 still > 10 viterbi threshold,
  0 FCS_OK). Phase 138-B uses 5 cable runs (K=5/10/15/20×2). Cumulative
  13 cable runs total across Phase 137/138/138-B — exceeds ≤5 budget.
  Verdict: `docs/superpowers/notes/2026-07-09-phase138-freq-lowpass-verdict.md`
  + `docs/superpowers/notes/2026-07-09-phase138b-call-site-0-verdict.md`.
  Phase 139+ options: 30 dB SMA attenuator install (HW, $50, would reduce
  noise to 0.5-0.7 rad — strongest path forward), Wiener filtering using
  H52 statistics from multiple frames, data-SC-only multi-frame averaging,
  external ref clock (HW, user-excluded).

*Last updated: 2026-07-10 (Phase 140 stacked cross-frame on 2-way — file-replay 1/1 PASS for all N ∈ {0,1,2,4,8}, USRP deferred) — 7 implementation commits + verdict
(`docs/superpowers/notes/2026-07-10-phase140-verdict.md`). IEEE80211_PHASE140_ON=N
(N ∈ {1,2,4,8}) layers Phase 127 L-SIG cross-frame FIFO averaging on top of
Phase 139's 2-way H52 baseline. N=8 theoretical σ=0.417 rad at full FIFO
(σ_post = 1.25/√(1+N) = 1.25/3 = 0.417, FIRST path below the 0.52 rad viterbi
threshold). 5/5 cable budget already exhausted in
Phase 137/138/138-B/139; 30 dB SMA attenuator install user-excluded
("排除一下衰减器"). Phase 139 (preceding) — 8 commits
(6f226d1, acf20b6, aba4cc8, 405d90d, 5756f7b, aa8cb41b, d176749, aa2e18b)
plus 4 verdicts (T1-T2 file-replay 1/1 PASS, T3 2-way USRP PARTIAL, T3b-e
3-way/4-way/5-way K-sweep PARTIAL, final consolidated verdict).
Phase 139 is the FIRST architectural rewrite in the equalizer layer (Phase 60-138
were layer tweaks on single L-LTF0 source). 2-way L-LTF0+L-LTF1 SNR-weighted
H52 averaging achieves breakthrough:
- LSIG_DECODE_OK 0/8 → 4/4 (USRP 5250 cable, --tx-gain 0) — L-SIG wall BROKEN
- HT_SIG_CAND 0 → 16-32 — HT-SIG chain REACHED viterbi for first time
- avg_snr_htsig 2-3 dB → 8.78 dB (+6 dB structural improvement)
- best metric 14 → 13 (1 unit via 4-way, lucky single-frame event, not reproduced in T3e)
- 0 FCS_OK (gated by 1.77 rad per-SC noise floor, Phase 112 R1)
5/5 cable budget EXHAUSTED across Phase 137/138/138-B/139.
IEEE80211_H52_2WAY_DEFAULT=1 flips default to 2-way averaging for ALL users
(opt-out via =0). IEEE80211_HT_SIG_PILOT_REFINE=N opt-in 3-way/4-way pilot
refinement. The 2-way H52 is now the new baseline for all future equalizer-layer work.
Phase 138 (preceding) — REFUTED on USRP. 3 dead-code call sites. Phase 138-B PARTIAL with
new call site 0 at line 6238 (estimate_header_channel_from_lltf52 output).*

Phase 136 (preceding) — Phase 128 inner condition bug FIXED. Commit 4192b49:
kHtTrain1Rel=6 (UNREACHABLE when viterbi fires at sym=5) → kHtTrain0Rel=5.
For 1×1 HT-MF pilots are equivalent per 802.11n Table G.13. USRP 5250
validation INCONCLUSIVE due to extreme signal variability (ratio_ht 0.199-8.575
across 5 runs). T1a pre-fix showed 16 HT_SIG_CAND but 0 delta_htltf —
confirms Phase 128 was no-op on USRP continuous streaming. Fix is code
correctness improvement (commit 4486bc4).*

- [Phase 141 Wiener H52 MMSE filter](docs/superpowers/notes/2026-07-11-phase141-verdict.md) —
  **PARTIAL on USRP, same-board breakthrough**. Adds per-SC Wiener shrinkage
  `G[k] = R_hh[k] / (R_hh[k] + σ²/|y_ltf[k]|²)` to H52 estimation. T1-T6
  implemented + unit/file-replay PASS. New env vars (opt-in, default OFF):
  `IEEE80211_WIENER_H52=1`, `IEEE80211_WIENER_FIFO_N=N`,
  `IEEE80211_WIENER_G_MIN=G`, `IEEE80211_WIENER_NULL_SCS=...`,
  `IEEE80211_WIENER_LOG=1`. Added `--cross-board-rx2` flag to
  `test_usrp_minimal_loopback.py` for A:0 TX → B:0 RX2 wiring.
  **Cross-board** was unstable (run-to-run variance larger than algorithmic
  effect). **Same-board A:0 → A:0 RX2 is RF-stable and repeatable.**
  **BREAKTHROUGH**: Wiener + `IEEE80211_HTSIG_H_REESTIMATE=1` reaches
  `avg_snr_htsig = 6.16–11.17 dB` (>6 dB viterbi threshold) on same-board;
  `HTSIG_H_REESTIMATE h0=ok h1=ok` fires. However, HT-SIG viterbi still fails
  (`best_metric=N/A` for all 16 candidates), constellation dumps show large
  imaginary-axis outliers (`std_im` up to 4.8), and **0 FCS_OK**. Root cause:
  HT-SIG path uses `Hhdr52` from L-SIG call site; dedicated HT/Data Wiener call
  sites on `d_H52_tx_order` do not run until after HT-SIG is decoded. Next:
  apply Wiener directly to HT-SIG pilot H re-estimate, combine with
  `IEEE80211_HTSIG_FINE_ROT=1` / `IEEE80211_HTSIG_PILOT_CPE=1`, investigate
  `best_metric=N/A`, and keep same-board for all future equalizer experiments.

- [Phase 143 BPSK-HT-SIG fallback](docs/superpowers/notes/2026-07-12-phase143-bpsk-htsig-fallback-verdict.md) —
  **IMPLEMENTED, USRP NOT YET FCS_OK**. Replaces QBPSK with BPSK for
  HT-SIG0/HT-SIG1 in a TX/RX-coordinated fallback mode to double angular
  margin against the 1.77 rad per-SC phase-noise floor. New opt-in env var
  `IEEE80211_HTSIG_BPSK_FALLBACK=1` and CLI flag `--htsig-bpsk-fallback`.
  Code compiles and installs cleanly; fallback reaches HT-SIG viterbi and
  reduces RX overflows, but best metric stays at 13–18 (>10 threshold) and
  **0 FCS_OK**. Verdict: `docs/superpowers/notes/2026-07-12-phase143-bpsk-htsig-fallback-verdict.md`.
- [Phase 144 L-SIG stability diagnosis](docs/superpowers/notes/2026-07-12-phase144-lsig-stability-verdict.md) —
  **BLOCKED by USRP analog/streaming instability**. L-SIG viterbi decodes
  garbage `lsig_len` values (100×–1000× too large) and `avg_snr_lsig` varies
  from 2 dB to 77 dB run-to-run. TX underflows occur at ~1 Hz regardless of
  configuration. Next attack must be hardware/UHD streaming layer, not more
  equalizer tweaks. Verdict:
  `docs/superpowers/notes/2026-07-12-phase144-lsig-stability-verdict.md`.

**USRP realtime FCS_OK is the absolute goal.** "Equalizer layer is CLOSED"
language REMOVED; equalizer attacks MUST continue. After 30+ REFUTED at
equalizer layer, equalizer layer is **STILL THE TARGET** — new architectures
(DD + Kalman + alternative H estimation) must be tried. Loopback-only
verification is NOT an acceptable outcome. Per user directive: "不可能接受
现状，我的目标是实现USRP FCS OK".*