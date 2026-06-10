# H52 Diagnosis Spec (Phase 2) — Design Document

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether the L-SIG viterbi failure on USRP is caused by H52 channel estimation failure. If H52 is the root cause, identify whether magnitude, phase, or both are wrong.

**Architecture:** 3-layer separation — C++ dump (env-flagged, atomic, read-only), capture (loopback + USRP 30s each), offline comparison script (per-SC statistics + verdict). No changes to viterbi path or equalization. Diagnosis only — fixes are out of scope.

**Tech Stack:** C++ (GNU Radio blocks), Python (analysis scripts), UHD (USRP), atomic `snprintf` logging pattern (from `9ebd74f`).

**Context:** Phase 1a (commit `bb82feb`, see `docs/superpowers/notes/2026-06-10-phase-noise-decision-1a.md`) REFUTED the "CFO/SFO residual phase rotation" hypothesis. 22/22 frames had `std_phase > 1.5 rad` (NOISE_LIKE 72.7%, MODEL_INCOMPLETE 27.3%). This means there is no coherent phase to compensate upstream. Root cause is **upstream of the equalizer**. Top candidate: H52 channel estimation failure.

**Out of scope of this spec:** Fixes (per-SC H interpolation, L-LTF0 compensation, FFT scaling fixes). If verdict is "H broken", a separate spec/plan is created for the fix.

**Branch:** TEST1 (this is the branch where Phase 1 work lives).

---

## 1. Architecture (3 layers)

```
┌─────────────────────────────────────┐
│ frame_equalizer (C++)               │  ← existing code, READ-ONLY
│   + [H52_DUMP] atomic log            │  ← NEW
│     (env: IEEE80211_H52_DUMP=1)     │
└─────────────────┬───────────────────┘
                  │ stderr (USRP_LOG)
                  ▼
┌─────────────────────────────────────┐
│ /tmp/loopback_h52.log               │  ← loopback capture
│ /tmp/usrp_h52.log                   │  ← USRP 30s capture
└─────────────────┬───────────────────┘
                  │ file
                  ▼
┌─────────────────────────────────────┐
│ examples/test_h52_compare.py        │  ← NEW analysis script
│   per-SC mean/std compare, verdict  │
└─────────────────────────────────────┘
```

**Why 3 layers:** Separation of concerns. C++ layer never touches viterbi path. Capture layer runs twice. Analysis layer is reusable, easy to extend.

**Why env flag opt-in:** Matches Phase 1 pattern ([PHASE_RESIDUAL] uses `IEEE80211_PHASE_RESIDUAL=1`). Default OFF — no impact on regression tests.

---

## 2. Components

### 2.1 C++ side (3 additions, 0 viterbi-path changes)

1. `lib/frame_equalizer_impl.h`: Add `bool d_log_h52;` member
2. `lib/frame_equalizer_impl.cc`:
   - Constructor: read `IEEE80211_H52_DUMP=1` → set `d_log_h52 = true`
   - In `estimate_header_channel_from_lltf52` (line 576-610) AFTER H52 is computed, BEFORE equalization: insert `[H52_DUMP]` atomic log
3. NO changes to viterbi/decoder/equalization — diagnosis only

### 2.2 Dump format (1 line/frame, atomic snprintf)

```
[H52_DUMP] counter=N |H|=%.3f,%.3f,...,52floats arg(H)=%.3f,%.3f,...,52floats mean|H|=%.3f std|H|=%.3f mean(argH)=%.3f std(argH)=%.3f
```

Approx 1.5KB/frame, 30 frames ≈ 45KB total.

### 2.3 Python side (1 new file)

`examples/test_h52_compare.py` (~150 lines):
- Parse [H52_DUMP] lines → per-SC mag/phase lists
- Aggregate N frames: per-SC mean, std (across frames)
- Compare two logs: per-SC diff table + aggregate verdict

### 2.4 Capture layer (0 new code)

Reuse existing test scripts:
- `examples/test_direct_loopback.py` (loopback baseline)
- `test_usrp_minimal_loopback.py --duration 30` (USRP)

Both run with `IEEE80211_H52_DUMP=1` env, stderr redirected to log file.

---

## 3. Data Flow

### 3.1 Per-frame data flow

```
1. ht_symbol_splitter outputs L-LTF0 FFT (counter=0)
2. estimate_header_channel_from_lltf52() computes H52 [52 complex]
3. ★ [NEW] Dump H52 immediately (H is computed, viterbi not yet run)
4. H52 → equalization → eq_lsig → viterbi (UNCHANGED)
```

**Critical timing constraint:** Dump MUST be after H52 is estimated, BEFORE equalization. At this point H52 is the "raw estimate" and is the same H52 that viterbi will use. If dumped after equalization, H52 may have been normalized.

### 3.2 Capture commands

```bash
# A. Loopback capture (software baseline)
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_H52_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/loopback_h52.log

# B. USRP capture
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_H52_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python test_usrp_minimal_loopback.py --duration 30 \
  2> /tmp/usrp_h52.log

# C. Analyze
/home/hy/conda/envs/gnuradio/bin/python examples/test_h52_compare.py \
  /tmp/loopback_h52.log /tmp/usrp_h52.log
```

### 3.3 Analysis output

```
Per-SC statistics:
  SC | |H|_loopback | |H|_usrp | ratio | argH_loopback | argH_usrp | diff_rad
  -26 | 0.45±0.02 | 0.43±0.05 | 0.96 | 0.10±0.05 | 0.12±0.20 | +0.02
  ...
  +26 | 0.51±0.02 | 0.49±0.06 | 0.96 | -0.05±0.04 | -0.08±0.25 | -0.03

Verdict:
  H_FINE / H_MAGNITUDE_BROKEN / H_PHASE_BROKEN / H_BOTH_BROKEN
  |H| ratio in [0.5, 2.0]: 51/52 (98%)  ← 80% threshold
  argH diff < 0.5 rad:    42/52 (81%)  ← 80% threshold
```

### 3.4 Multi-thread safety

Reuse Phase 1 atomic snprintf pattern (commit `9ebd74f`, refined `cac6fff`): single `snprintf` to 2KB buffer, single `USRP_LOG("%s", buf)` call. Prevents `sync_short` thread stdout interleaving shredding.

---

## 4. Error Handling

### 4.1 Five error scenarios

1. **H52 not estimated (no L-LTF0)**: No dump, no error. `estimate_header_channel_from_lltf52` failure is logged elsewhere; no double-log.
2. **snprintf truncation**: Reuse Phase 1 pattern (`cac6fff`) — stop when `pn >= sizeof(buf) - 16`, add `\n` terminator, keep partial data, no overflow.
3. **Log file missing/empty**: Script detects 0 frames → explicit error "no [H52_DUMP] lines", exit 1.
4. **Different frame counts in two files**: Script aggregates by SC index independently (per-SC mean/std across frames), not requiring equal frame counts. Output annotates "loopback: N frames, USRP: M frames".
5. **Partial/corrupt lines**: Reuse Phase 1 `parse_log` pattern — regex match, ValueError → skip, report "X lines parsed, Y skipped".

### 4.2 Threshold violations

- Verdict always gives one of 4 categories, no exception, no non-zero exit
- Print warning if 80% threshold badly missed (e.g., <30% SCs ok) — "H estimation likely broken; investigate H estimate source"

### 4.3 Verdict decision table

```
|H| ratio ∈ [0.5, 2.0] % SCs | argH diff < 0.5 rad % SCs | Verdict
       ≥ 80%                  |       ≥ 80%              | H_FINE
       < 80%                  |       ≥ 80%              | H_MAGNITUDE_BROKEN
       ≥ 80%                  |       < 80%              | H_PHASE_BROKEN
       < 80%                  |       < 80%              | H_BOTH_BROKEN
```

**Why 80%:** Phase 1a had 22/22 = 100% frames NOISE_LIKE — no tolerance for noise-like. 80% leaves 20% margin for occasional outliers, sufficient to detect systematic differences. Configurable via `--threshold` script arg, default 0.8.

---

## 5. Testing

### 5.1 Level 1: Synthetic log test (no hardware, ~30s)

Write 4 synthetic logs covering each verdict category:
- Flat |H|=1.0, arg=0 → expect H_FINE
- |H|=10x deviated, arg=π deviated → expect H_BOTH_BROKEN
- |H|=5x deviated only → expect H_MAGNITUDE_BROKEN
- arg=π deviated only → expect H_PHASE_BROKEN

Run `test_h52_compare.py` against each, verify verdict matches.

### 5.2 Level 2: Software loopback regression (no USRP, ~60s)

- Run `examples/test_direct_loopback.py` (no env flag): expect "OK=0 FAIL=1" (FcsLogger crc bug per memory, not a regression)
- Run with `IEEE80211_H52_DUMP=1`: expect ≥1 frame worth of [H52_DUMP] lines
- Software loopback H52 should: (a) not break viterbi path, (b) produce ≥1 [H52_DUMP] per frame

### 5.3 Level 3: USRP 30s capture (with hardware, ~90s)

- Run `test_usrp_minimal_loopback.py --duration 30` with env flag
- Expect ≥20 well-formed [H52_DUMP] lines (based on Phase 1a baseline of 22)
- Run `test_h52_compare.py` against loopback and USRP logs
- Output verdict

### 5.4 Regression gates

- Software loopback passes (build OK, import OK, process not crashed, ≥1 [H52_DUMP] frame)
- Compiled .so contains strings `IEEE80211_H52_DUMP` and `[H52_DUMP] counter=`

### 5.5 Verdict → next phase (Decision Gate 2a)

- **H_FINE** → H hypothesis closed. New Phase 3 spec: FFT window timing investigation.
- **H_MAGNITUDE_BROKEN** → New spec: H estimate source investigation (L-LTF0 FFT scaling, domain consistency).
- **H_PHASE_BROKEN** → New spec: H estimate CFO/SFO consistency investigation.
- **H_BOTH_BROKEN** → Full H estimate chain traceback (L-LTF0 rx → H estimate → equalization).

---

## 6. Success Criteria + Deliverables

### 6.1 Done definition

- [ ] `IEEE80211_H52_DUMP` env flag works, dump appears on stderr
- [ ] 30s USRP run produces ≥20 well-formed [H52_DUMP] lines
- [ ] `test_h52_compare.py` passes synthetic tests (4 verdict categories)
- [ ] Real verdict output with per-SC diff table
- [ ] Software loopback regression unbroken (build + import + 1 frame dump)

### 6.2 Deliverables (4 files)

1. `lib/frame_equalizer_impl.h` — `d_log_h52` member
2. `lib/frame_equalizer_impl.cc` — env flag + atomic dump
3. `examples/test_h52_compare.py` — comparison analysis script
4. `docs/superpowers/notes/2026-06-10-h52-diagnosis.md` — decision note (with verdict)

### 6.3 Out of scope (explicitly not done)

- Fixing H estimation (this spec is diagnosis only)
- Per-SC H interpolation
- Any modification to viterbi path
- FFT window timing investigation (deferred to Phase 3)

### 6.4 Risks + mitigation

- **Risk:** Dump output (~1.5KB/frame × 30 frames = 45KB) interferes with other logs.
  - **Mitigation:** Env flag opt-in, default OFF.
- **Risk:** USRP log noise drowns [H52_DUMP].
  - **Mitigation:** Use `grep "[H52_DUMP]"` to extract, offline analysis only sees extracted.
- **Risk:** Software loopback "9/9" vs "OK=0 FAIL=1" confusion.
  - **Mitigation:** Same as Phase 1 — "OK=0 FAIL=1" is baseline, FcsLogger bug.

---

## 7. Tasks (rough breakdown — for writing-plans)

Roughly 6 tasks, parallel to Phase 1 structure:

1. Add `d_log_h52` member to header
2. Add env flag wiring in constructor
3. Add `[H52_DUMP]` atomic log in H52 estimation path
4. Write `test_h52_compare.py` analysis script
5. Run USRP 30s capture with env flag, validate dump
6. Run analysis, output verdict, write decision note, transition to next phase

**Why this structure:** Mirrors Phase 1 (Tasks 1-6 of `docs/superpowers/plans/2026-06-10-phase-noise-lsig.md`) which worked well. Same env-flag pattern, same atomic log pattern, same offline analysis pattern, same decision gate pattern.

---

## 8. References

- **Phase 1a findings:** `docs/superpowers/notes/2026-06-10-phase-noise-decision-1a.md` (commit `bb82feb`)
- **Phase 1 spec:** `docs/superpowers/specs/2026-06-10-phase-noise-lsig-design.md` (commit `ec08fd4`)
- **Phase 1 plan:** `docs/superpowers/plans/2026-06-10-phase-noise-lsig.md` (gitignored)
- **H estimation code:** `lib/frame_equalizer_impl.cc` line 576-610 (`estimate_header_channel_from_lltf52`)
- **Memory:** `project_phase_noise_decision_1a`, `project_lsig_viterbi_2026_06_10` in `~/.claude/projects/-home-hy-gr-ieee802-11/memory/`
- **Phase 1 reference patterns:**
  - Env flag: `IEEE80211_PHASE_RESIDUAL=1` (commit `dea4805`)
  - Atomic log: `[PHASE_RESIDUAL] counter=%d eq_phase=` (commit `d6ecf36`)
  - Snprintf clamp: commit `cac6fff`
  - Offline analysis: `examples/test_phase_residual_offline.py` (commit `6c2d706`)
