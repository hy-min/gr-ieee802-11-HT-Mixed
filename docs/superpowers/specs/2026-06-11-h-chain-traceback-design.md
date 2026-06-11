# H Chain Traceback (Phase 3) — Design Document

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Locate the upstream corruption that causes H52 to be both-magnitude-and-phase broken on USRP, and apply one targeted fix. Phase 3 success = USRP 30s run with ≥1 frame successfully decoded.

**Architecture:** 4-stage sequential forward trace (L-LTF0 RX → splitter → CFO/SFO domain → H estimate in/out), each stage is read-only env-flagged C++ dump + offline Python analysis. Early stop on first `STAGE_BROKEN_*` verdict. Fix is implemented in a follow-up task once the root cause is identified.

**Tech Stack:** C++ (GNU Radio blocks), Python (analysis scripts), UHD (USRP), atomic `snprintf` logging pattern (from `9ebd74f`), env-flag opt-in pattern (from `dea4805`).

**Context:** Phase 1a REFUTED the CFO/SFO phase hypothesis (commit `bb82feb`, see `docs/superpowers/notes/2026-06-10-phase-noise-decision-1a.md`). Phase 2 (H52 diagnosis, commit `75a6629`, see `docs/superpowers/notes/2026-06-10-h52-diagnosis.md`) confirmed H estimation is `H_BOTH_BROKEN`: |H| ratio pass 35/52 (67.3%), argH diff pass 8/52 (15.4%), per-SC |H| std 8.64 (loopback 0). Root cause is upstream of H estimate math.

**Out of scope of this spec:** Comprehensive multi-fix attempts. Per user clarification (B: 诊断 + 1 个最可能修复), this spec targets the first identified root cause only. If the first fix is incorrect, a new spec is needed.

**Branch:** TEST1.

---

## 1. Architecture (3 layers)

```
┌─────────────────────────────────────────┐
│ C++ (read-only, env-flagged dumps)      │  ← 4 new env flags
│   + [LTF0_RX_DUMP]      Stage 1         │
│   + [SPLITTER_DUMP]     Stage 2         │
│   + [CFO_DOMAIN_DUMP]   Stage 3         │
│   + [H_IN_OUT_DUMP]     Stage 4         │
└─────────────────┬───────────────────────┘
                  │ stderr (atomic snprintf)
                  ▼
┌─────────────────────────────────────────┐
│ /tmp/loopback_*.log (baseline)          │
│ /tmp/usrp_*.log (30s captures)          │
└─────────────────┬───────────────────────┘
                  │ file
                  ▼
┌─────────────────────────────────────────┐
│ Python analysis scripts                  │
│   test_ltf0_spectrum.py    Stage 1      │
│   test_splitter_timing.py  Stage 2      │
│   test_cfo_domain.py       Stage 3      │
│   test_h_inout.py          Stage 4      │
│   test_phase3_verdict.py   aggregator   │
└─────────────────────────────────────────┘
```

**Why 3 layers:** Same separation of concerns as H52 spec. C++ layer never touches the viterbi/equalizer path during diagnosis. Capture layer is reused. Analysis layer is per-stage but shares a common verdict format.

**Why sequential with early stop:** The user has been doing sequential debug all along (Phase 1a, Phase 1b, Phase 2). Each stage can be fully validated before moving on, so partial failures don't poison later stages. If Stage 1 finds a clear root cause, we stop and fix instead of running Stages 2-4.

---

## 2. Components

### 2.1 C++ side (4 new env flags, 4 dump points, 0 viterbi-path changes during diagnosis)

1. `lib/frame_equalizer_impl.h`: 4 new `bool d_log_*` members
2. `lib/sync_long.h/cc`: 1 new dump point (Stage 1)
3. `lib/ht_symbol_splitter_impl.h/cc`: 1 new dump point (Stage 2)
4. `lib/frame_equalizer_impl.cc`: 3 new dump points (Stages 3 and 4)
5. Constructor: read 4 env flags, opt-in, default OFF

### 2.2 Dump format (per stage, atomic snprintf, ~1-2KB/frame)

**Stage 1** (`[LTF0_RX_DUMP]`):
```
[LTF0_RX_DUMP] counter=N |LLTF|=52floats arg(LLTF)=52floats mean|LLTF|=F std|LLTF|=F
```

**Stage 2** (`[SPLITTER_DUMP]`):
```
[SPLITTER_DUMP] counter=N lltf0_mag=52floats lsig_mag=52floats timing_offset=INT
```

**Stage 3** (`[CFO_DOMAIN_DUMP]`):
```
[CFO_DOMAIN_DUMP] counter=N pre_mag=52floats pre_arg=52floats post_mag=52floats post_arg=52floats
```

**Stage 4** (`[H_IN_OUT_DUMP]`):
```
[H_IN_OUT_DUMP] counter=N lltf_in=52floats H_out=52floats ratio_mag=52floats
```

### 2.3 Python side (5 new files)

| File | Stage | Purpose |
|------|-------|---------|
| `examples/test_ltf0_spectrum.py` | 1 | Compare L-LTF0 magnitude/phase against known QPSK-like theoretical pattern |
| `examples/test_splitter_timing.py` | 2 | Compare L-LTF0 (counter=0) and L-SIG (counter=2) magnitude/phase, check timing_offset |
| `examples/test_cfo_domain.py` | 3 | Compare L-LTF0 before/after CFO compensation, check domain match with L-SIG |
| `examples/test_h_inout.py` | 4 | Verify H = LLTF / TX_ref relationship, check ratio_mag uniformity |
| `examples/test_phase3_verdict.py` | (aggregator) | Read all 4 stage logs, output root cause candidate ranking |

### 2.4 Fix task (post-diagnosis, conditionally added)

When Stage N returns `STAGE_BROKEN_*`, an additional task is added to:
- Implement the targeted fix
- Add regression test
- Verify USRP ≥1 frame decoded (success criterion B)

The fix is **not** part of the diagnosis plan; it is appended after the root cause is known.

---

## 3. Data Flow

### 3.1 Stage 1: L-LTF0 RX dump

**Dump point**: `sync_long::general_work` output (L-LTF0 FFT extracted from sync correlation).

**Verification**: A correctly-extracted L-LTF0 FFT should have:
- `|LLTF[k]| ≈ 1.0` (normalized) across all 52 subcarriers
- `arg(LLTF[k])` follows the known QPSK-like 802.11n pattern (deterministic, sequence-defined)
- Per-frame stability: std(|LLTF|) across 25 frames ≈ 0

**Verdict**:
- `STAGE_FINE` — pattern matches theory, |LLTF| std ≈ 0
- `STAGE_BROKEN_TIMING` — |LLTF| has high-frequency subcarrier corruption (sub-sample misalignment)
- `STAGE_BROKEN_GAIN` — |LLTF| uniform but off-scale (e.g., 0.5x or 2x of expected)
- `STAGE_BROKEN_FREQRESP` — |LLTF| has frequency-selective distortion (e.g., dropoff at edges)
- `STAGE_AMBIGUOUS` — data is too noisy to classify

### 3.2 Stage 2: Splitter dump

**Dump point**: `ht_symbol_splitter::general_work` output, capturing both L-LTF0 (counter=0) and L-SIG (counter=2) FFT outputs from the same input frame.

**Verification**:
- `timing_offset` from splitter is consistent across frames
- L-LTF0 and L-SIG magnitude profiles are similar (same channel response)
- L-LTF0 → L-SIG phase difference is consistent with CFO model (linear growth of 2 symbols)

**Verdict**:
- `STAGE_FINE` — timing_offset consistent, phase difference matches CFO
- `STAGE_BROKEN_OFFSET` — timing_offset varies unexpectedly
- `STAGE_BROKEN_CFO` — phase difference is discontinuous or wildly wrong
- `STAGE_AMBIGUOUS`

### 3.3 Stage 3: CFO/SFO domain dump

**Dump point**: `frame_equalizer::general_work`, before/after the CFO/SFO compensation application point for L-LTF0.

**Verification**: The current code path is:
```cpp
lltf_for_H = d_ltf_compensated_valid[0] ? d_ltf_compensated[0] : d_early_eqsym[kLltf0Rel];
```
- If `d_ltf_compensated_valid[0]=0`: H uses RAW (uncompensated) L-LTF0
- If `d_ltf_compensated_valid[0]=1`: H uses compensated L-LTF0

L-SIG is always compensated before equalization (header CFO compensation is enabled per memory `0a7a9ee`).

**Verdict**:
- `STAGE_FINE` — H and L-SIG in same domain (both compensated or both raw)
- `STAGE_BROKEN_DOMAIN_MISMATCH` — H uses raw, L-SIG uses compensated, **this is the root cause candidate**
- `STAGE_AMBIGUOUS`

### 3.4 Stage 4: H estimation in/out dump

**Dump point**: `estimate_header_channel_from_lltf52` (line 576-610), input = LLTF0 (52 complex), output = H52 (52 complex). Compute ratio = H52 / LLTF0 in the script, should equal TX_ref^-1.

**Verification**:
- `|ratio[k]|` should be constant across k (TX_ref is known constant magnitude)
- `arg(ratio[k])` should be near 0 (or constant known phase)
- Per-frame consistency: ratio stable across 25 frames

**Verdict**:
- `STAGE_FINE` — H math correct
- `STAGE_BROKEN_H_MATH` — H estimation itself has a bug
- `STAGE_AMBIGUOUS`

### 3.5 Early-stop decision table

| Stage | Verdict | Action |
|-------|---------|--------|
| 1 | `STAGE_FINE` | → Stage 2 (1 more USRP run) |
| 1 | `STAGE_BROKEN_*` | **Stop**, implement fix in appended task, verify ≥1 frame decoded |
| 2-4 | `STAGE_FINE` | → Next stage |
| 2-4 | `STAGE_BROKEN_*` | **Stop**, implement fix, verify |
| 1-4 | `STAGE_AMBIGUOUS` | Run longer USRP (60s) retry, or enable additional dump |

### 3.6 Multi-thread safety

All 4 dump points use the atomic `snprintf` + `USRP_LOG("%s", buf)` pattern from `9ebd74f` / `cac6fff`. This prevents `sync_short` stdout interleaving from shredding the dump lines (verified 0 shredding in Phase 1).

---

## 4. Error Handling

### 4.1 Diagnosis phase

| Scenario | Trigger | Action |
|----------|---------|--------|
| Dump file empty/missing | USRP run crashes, 0 frames | STOP, report `[ERROR] no frames captured, check USRP connectivity` |
| Shredded lines (sync_short interleave) | Concurrent stdout writes | Atomic snprintf prevents this; fallback: warn if >10% lines are malformed |
| snprintf truncation | 52-SC float stream too long | Clamp at `pn >= sizeof(buf) - 16`, keep partial data, do not overflow |
| Loopback vs USRP frame count mismatch | Short run variance | Each stage aggregates per-SC independently, annotates counts in output |
| `STAGE_AMBIGUOUS` verdict | Data too noisy to classify | Recommend 60s USRP rerun or enable auxiliary dump |
| Early stop at Stage N, fix doesn't help | Fix direction wrong | Revert fix commit, return to next stage (e.g., Stage 1 broken → fix → still broken → try Stage 2) |

### 4.2 Fix phase

| Scenario | Action |
|----------|--------|
| Fix build failure | Report, do not commit, keep diagnosis code intact |
| Fix breaks software loopback (`OK<8` or `OK=0 FAIL>1`) | Revert fix immediately, report regression scope |
| Fix reduces USRP performance (`Recv=0` was `Recv=N>0`) | Revert fix, report |
| Fix leaves USRP at 0/30 (no improvement, no regression) | **Do not revert immediately**; iterate on fix parameters |
| Fix achieves ≥1 frame decoded | ✅ Commit, write decision note, update memory |

### 4.3 Multi-thread safety (continued)

- All 4 env flags opt-in, default OFF
- No impact on regression tests
- Atomic snprintf pattern (verified by Phase 1 e90e3f5)

### 4.4 USRP availability

- USRP unreachable (`ping 192.168.10.2` fails) → STOP, do not proceed
- USRP frame count < 5 → warn about low confidence, allow continuation
- USRP continuous underflow → report as separate concern (PHY performance, not H chain)

### 4.5 Recovery

- Each stage's dump code is read-only (no viterbi/equalizer modification)
- Each stage's fix is independent commit, can be `git revert`ed
- Software loopback 9/9 (or FcsLogger baseline `OK=0 FAIL=1`) is maintained throughout

---

## 5. Testing

### 5.1 Level 1: Synthetic log tests (~5 min, no hardware)

Each analysis script must pass 4-5 synthetic input categories:

For `test_ltf0_spectrum.py` (Stage 1):
- `STAGE_FINE`: synthetic L-LTF0 matching theoretical QPSK pattern
- `STAGE_BROKEN_TIMING`: shifted by ±1 sample equivalent
- `STAGE_BROKEN_GAIN`: scaled by 0.5x or 2x
- `STAGE_BROKEN_FREQRESP`: dropoff at subcarrier edges
- `STAGE_AMBIGUOUS`: noise-injected

For `test_splitter_timing.py` (Stage 2): same 5 categories with timing/CFO variants.
For `test_cfo_domain.py` (Stage 3): same 5 categories with domain variants.
For `test_h_inout.py` (Stage 4): same 5 categories with H math variants.

### 5.2 Level 2: Software loopback regression (~3 min, no USRP)

- Run `examples/test_direct_loopback.py` without env flag → expect `OK=0 FAIL=1` (FcsLogger baseline)
- Run with each Stage's env flag → expect ≥1 frame dump per stage
- Run `examples/test_h52_compare.py` (Phase 2 verification) → expect it to still work (no regression to H52 infrastructure)

### 5.3 Level 3: USRP 30s capture (~2 min, with hardware)

- `ping 192.168.10.2` must succeed
- Run `test_usrp_minimal_loopback.py --duration 30` with current Stage's env flag
- Expect ≥20 well-formed dump lines
- Run the Stage's analysis script → expect verdict output

### 5.4 Fix verification (when reached)

1. Compile: `cmake --build` succeeds
2. Regression: software loopback `Final: OK=0 FAIL=1` (FcsLogger baseline)
3. USRP 30s: `Recv ≥ 1` (success criterion B)
4. Stability: 3 consecutive USRP runs, at least 1 shows `Recv ≥ 1` (exclude single-run luck)

### 5.5 Done definition

- [ ] All 4 env flags implemented and tested
- [ ] All 4 analysis scripts pass synthetic tests (5 categories each)
- [ ] At least 1 stage runs through USRP 30s capture with verdict output
- [ ] If `STAGE_BROKEN_*` reached, fix implemented and verified
- [ ] Success criterion met: USRP 30s run with `Recv ≥ 1`
- [ ] Software loopback regression intact
- [ ] Decision note written: `docs/superpowers/notes/2026-06-11-h-chain-traceback.md`
- [ ] Memory updated with new entry

### 5.6 Out of scope (explicitly not done)

- Multiple fix attempts (only 1 fix per spec; iterate in a new spec if needed)
- FFT window timing investigation (deferred to Phase 4 if H chain traceback finishes without root cause)
- Hardware investigation (gain, antenna, etc.) unless `STAGE_BROKEN_GAIN` is the verdict and fix is at the gain layer
- L-LTF1 vs L-LTF0 selection logic (already tried in `dbf8615`, NO-OP)

---

## 6. Success Criteria + Deliverables

### 6.1 Hard success (B criterion, user-confirmed)

USRP 30s run with `Recv ≥ 1` (at least 1 frame successfully decoded, with statistical confidence: 3 runs, ≥1 success).

### 6.2 Soft success

- Root cause identified with verdict
- Fix implemented and committed
- Software loopback regression intact (`OK=0 FAIL=1` baseline)
- Decision note + memory updated

### 6.3 Deliverables

1. `lib/frame_equalizer_impl.h` — 4 new `d_log_*` members
2. `lib/sync_long.h/cc` — 1 dump point (Stage 1)
3. `lib/ht_symbol_splitter_impl.h/cc` — 1 dump point (Stage 2)
4. `lib/frame_equalizer_impl.cc` — 3 dump points (Stages 3, 4) + 4 env flag wiring
5. `examples/test_ltf0_spectrum.py` — Stage 1 analysis
6. `examples/test_splitter_timing.py` — Stage 2 analysis
7. `examples/test_cfo_domain.py` — Stage 3 analysis
8. `examples/test_h_inout.py` — Stage 4 analysis
9. `examples/test_phase3_verdict.py` — aggregator
10. `docs/superpowers/notes/2026-06-11-h-chain-traceback.md` — decision note (post-diagnosis)

### 6.4 Risks + mitigation

| Risk | Mitigation |
|------|------------|
| All 4 stages return `STAGE_AMBIGUOUS` | Recommend 60s USRP rerun, add auxiliary dump points (defer to new spec) |
| Fix doesn't help (USRP still 0/30) | Don't revert immediately; iterate on parameters; if 3 attempts fail, revert and try next stage |
| New fix breaks software loopback | Revert immediately, report, document as out-of-scope for this spec |
| Diagnosis takes >2 hours | Report status, ask user whether to continue or cut scope |
| USRP offline mid-investigation | Pause, save artifacts, resume when USRP available |

---

## 7. Tasks (rough breakdown — for writing-plans)

Estimated 12-15 tasks depending on early stop:

**Stage 1 (3 tasks)**:
- T1.1: Add `d_log_ltf0_rx` + env flag + `[LTF0_RX_DUMP]` in sync_long output
- T1.2: Write `test_ltf0_spectrum.py` with 5 synthetic test categories
- T1.3: USRP 30s run + analyze → verdict

**Stage 2 (3 tasks, only if Stage 1 returns FINE)**:
- T2.1: Add `d_log_splitter` + env flag + `[SPLITTER_DUMP]` in ht_symbol_splitter output
- T2.2: Write `test_splitter_timing.py` with 5 synthetic test categories
- T2.3: USRP 30s run + analyze → verdict

**Stage 3 (3 tasks, only if Stage 2 returns FINE)**:
- T3.1: Add `d_log_cfo_domain` + env flag + `[CFO_DOMAIN_DUMP]` in frame_equalizer
- T3.2: Write `test_cfo_domain.py` with 5 synthetic test categories
- T3.3: USRP 30s run + analyze → verdict

**Stage 4 (3 tasks, only if Stage 3 returns FINE)**:
- T4.1: Add `d_log_h_inout` + env flag + `[H_IN_OUT_DUMP]` in estimate_header_channel_from_lltf52
- T4.2: Write `test_h_inout.py` with 5 synthetic test categories
- T4.3: USRP 30s run + analyze → verdict

**Fix (3 tasks, only if any Stage returns BROKEN)**:
- TF.1: Implement targeted fix for identified root cause
- TF.2: Verify software loopback regression intact
- TF.3: 3x USRP 30s runs, verify ≥1 frame decoded, write decision note + memory

**Aggregator (1 task, after fix)**:
- TA.1: Write `test_phase3_verdict.py` to summarize all stage logs into a single root cause candidate ranking

**Total**: 4-13 tasks depending on early stop.

**Why this structure:** Mirrors Phase 2 (H52 diagnosis) which used 6 tasks, with the same env-flag pattern, same atomic log pattern, same offline analysis pattern, same decision gate pattern. Each stage is independently committable.

---

## 8. References

- **Phase 2 findings (root cause)**: `docs/superpowers/notes/2026-06-10-h52-diagnosis.md` (commit `75a6629`)
- **Phase 2 spec**: `docs/superpowers/specs/2026-06-10-h52-diagnosis-design.md` (commit `a24575c`)
- **Phase 2 plan**: `docs/superpowers/plans/2026-06-10-h52-diagnosis.md`
- **Phase 1a findings (REFUTED)**: `docs/superpowers/notes/2026-06-10-phase-noise-decision-1a.md` (commit `bb82feb`)
- **H estimation code**: `lib/frame_equalizer_impl.cc` line 576-610 (`estimate_header_channel_from_lltf52`), line 1721-1733 (env flag wiring)
- **Sync long code**: `lib/sync_long.cc`
- **Splitter code**: `lib/ht_symbol_splitter_impl.cc`
- **Memory**: `project_phase_noise_decision_1a`, `project_h52_diagnosis` in `~/.claude/projects/-home-hy-gr-ieee802-11/memory/`
- **Phase 2 reference patterns**:
  - Env flag pattern: `IEEE80211_H52_DUMP=1` (commit `33df3f9`)
  - Atomic log pattern: `[H52_DUMP] counter=%d |H|=` (commit `78432ee`)
  - Snprintf clamp: commit `cac6fff`
  - Offline analysis: `examples/test_h52_compare.py` (commit `eecf6da`)
