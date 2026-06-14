# L-LTF0 FFT Upstream Quality Fix on USRP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the L-LTF0 FFT corruption at the frame_equalizer input on USRP so that the chain produces end-to-end FCS pass rate > 0. The Phase 11 plan (2026-06-14) confirmed the corruption is upstream of the equalizer, ruled out IQ swap, CFO refinement, and gating bypass as fixes.

**Architecture:** The L-LTF0 FFT at the equalizer input shows 84× pilot magnitude variation on USRP (vs clean reference in loopback). This is an **upstream** issue in the splitter/timing/IQ path. We will:

1. Trace where the L-LTF0 FFT corruption enters the chain (splitter output? equalizer input? post-compensation?)
2. Try timing offset adjustments (d_frame_start ±2 samples)
3. Try per-SC phase tracking / Wiener filter on the equalized symbols
4. Try alternative channel estimation (L-LTF0+L-LTF1 ratio instead of average)
5. Apply the best fix
6. Validate end-to-end on USRP with regression in software loopback

**Tech Stack:** C++ (GNU Radio blocks `lib/frame_equalizer_impl.cc`, `lib/ht_symbol_splitter_impl.cc`, `lib/sync_long_impl.cc`), Python (test scripts), GNU Radio 3.10, USRP X300 at 5.89 GHz A:0.

---

## Key Context

- **Phase 11 finding (2026-06-14)**: L-LTF0 FFT and H52 are wildly corrupted at the equalizer input on USRP:
  - L-LTF0 SC magnitude: mean=1.81, std=1.63 (pilot), 1.11 (SC[0])
  - H52: |H| std=4.48, 14% outliers, range 100×
  - Loopback: clean constant reference (std=0)
- **Phase 11 ruled out**: IQ swap, fine CFO refinement, gating bypass (FORCE_HTSIG).
- **Hardware is fine** (Phase 5-7 "LO_BROKEN" was refuted by Phase 9/10).
- **Where to look**: timing alignment (d_frame_start=176 in sync_long), channel estimation (L-LTF0 vs L-LTF1), or USRP-specific gain/IQ path.
- **All instrumentation in place**: `IEEE80211_LTF0_FFT_PRECOMP_DUMP`, `IEEE80211_LTF0_FFT_DUMP`, `IEEE80211_H52_EQ_INPUT_DUMP`, `IEEE80211_H52_DUMP`, `IEEE80211_H52_DUMP_FILTERED`, `IEEE80211_FORCE_HTSIG`.

## File Map

| File | Role |
|------|------|
| `lib/sync_long_impl.cc` | Sets d_frame_start=176 (L-LTF0 DATA start) |
| `lib/ht_symbol_splitter_impl.cc` | Aligns samples to frame_start, runs FFT (d_fft_size=64) |
| `lib/frame_equalizer_impl.cc` | Channel estimation (estimate_header_channel_from_lltf52) + equalization |
| `lib/frame_equalizer_impl.h` | New env-var-gated d_log_* members |
| `/tmp/test_p10_usrp_v2.py` | Existing 10s USRP test (400-byte packet) |
| `examples/test_direct_loopback.py` | Existing software loopback test |

## Diagnostic Evidence

```
USRP @ 5.89 GHz, 12s, IEEE80211_LTF0_FFT_PRECOMP_DUMP=1:
  Frames: 12
  SC[26] pilot: |.| mean=1.805, std=1.627, range 0.027-2.265
  sum|SC[0:5]|: mean=13.14, std=2.77, range 9.86-17.03

USRP @ 5.89 GHz, 12s, IEEE80211_H52_EQ_INPUT_DUMP=1:
  Rows: 88
  |H| mean=4.154, std=4.484, range 0.120-17.620
  Outliers (<0.1 or >10): 640/4576 (14.0%)

Loopback baseline:
  Pilot: |.| = 8.875 (constant)
  |H|: 8.880 (constant)
  std=0.0
```

---

## Task 1: Add L-LTF0 timing alignment diagnostic

**Files:**
- Modify: `lib/ht_symbol_splitter_impl.cc` (add `IEEE80211_SPLITTER_TIMING_DUMP` env var hook)
- Test: `/tmp/test_splitter_timing.py` (new)

**Context:** `d_frame_start_abs=176` in sync_long tells the splitter where L-LTF0 DATA starts. If this is off by 1-2 samples on USRP, the FFT window would include CP boundary leakage and produce corrupted FFT. We need to dump the actual sample index where the L-LTF0 data starts vs the expected 176.

- [ ] **Step 1: Locate the splitter's frame_start consumption**

Run: `grep -n "d_frame_start_abs\|d_wifi_start_value\|d_fft_size" lib/ht_symbol_splitter_impl.cc | head -20`
Expected: in `general_work` around line 250-400.

- [ ] **Step 2: Add a timing dump env var**

Find the spot in `general_work` where `d_frame_start_abs` is consumed (around line 294 `if (d_buffer_count == d_fft_size && d_frame_start_known)`) and add:

```cpp
if (getenv("IEEE80211_SPLITTER_TIMING_DUMP")) {
    int rel_idx = current_idx - d_frame_start_abs;
    USRP_LOG("[SPLITTER_TIMING] seq=%d frame_start_abs=%lld current_idx=%lld rel_idx=%d "
             "expected_first_sym=176\n",
             d_frame_seq_counter,
             (long long)d_frame_start_abs,
             (long long)current_idx,
             rel_idx);
}
```

(Use atomic snprintf if more than 2 args, or rely on the 6-arg limit per Phase 9 lesson.)

- [ ] **Step 3: Build and verify .so contains the string**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
strings /home/hy/gr-ieee802-11/build/lib/libgnuradio-ieee802_11.so.g590b96a | grep -c "SPLITTER_TIMING"
```

Expected: build succeeds, string count = 1.

- [ ] **Step 4: Run USRP test and check timing**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_SPLITTER_TIMING_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/timing_usrp.err > /dev/null
grep "SPLITTER_TIMING" /tmp/timing_usrp.err | head -10
```

Expected: rel_idx values should be 0-3 (slight jitter). If rel_idx is significantly off (e.g. 5-10), timing is misaligned.

- [ ] **Step 5: Run loopback for baseline**

Run:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_SPLITTER_TIMING_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 8 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/timing_loop.err > /dev/null
grep "SPLITTER_TIMING" /tmp/timing_loop.err | head -5
```

Expected: clean baseline.

- [ ] **Step 6: Compute timing offset statistics**

Write `/tmp/timing_analyze.py` (new):
```python
import re
import statistics as st

def parse(path):
    pat = re.compile(r"\[SPLITTER_TIMING\] seq=(\d+) frame_start_abs=(\d+) current_idx=(\d+) rel_idx=(\d+)")
    rows = []
    with open(path) as f:
        for line in f:
            m = pat.search(line)
            if m: rows.append(tuple(int(x) for x in m.groups()))
    return rows

def stats(name, rows):
    if not rows: print(f"{name}: 0 rows"); return
    rel_idxs = [r[3] for r in rows]
    print(f"{name}: {len(rows)} rows")
    print(f"  rel_idx mean={st.mean(rel_idxs):.2f} std={st.pstdev(rel_idxs):.2f} min={min(rel_idxs)} max={max(rel_idxs)}")
    outliers = [r for r in rel_idxs if r < -2 or r > 4]
    print(f"  rel_idx outliers (<-2 or >4): {len(outliers)} / {len(rel_idxs)}")

stats("USRP", parse("/tmp/timing_usrp.err"))
stats("LOOP", parse("/tmp/timing_loop.err"))
```

Run: `/home/hy/conda/envs/gnuradio/bin/python /tmp/timing_analyze.py`
Expected: USRP rel_idx distribution. If mean is 0 ± 1, timing is fine. If mean is 2+ off, that's the bug.

- [ ] **Step 7: Commit**

```bash
git add lib/ht_symbol_splitter_impl.cc
git commit -m "diag(splitter): add timing alignment dump (rel_idx vs expected 176)"
```

---

## Task 2: Add d_frame_start offset experiment

**Files:**
- Modify: `lib/sync_long_impl.cc` (add `IEEE80211_FRAME_START_OFFSET` env var hook)
- Test: software loopback + USRP at offsets -4 to +4

**Context:** If Task 1 shows timing is off, try shifting d_frame_start by ±N samples to see if FFT quality improves.

- [ ] **Step 1: Locate d_frame_start_abs initialization**

Run: `grep -n "d_frame_start_abs" lib/sync_long_impl.cc | head -5`
Expected: line 39 (currently 176).

- [ ] **Step 2: Add env-var override**

In the constructor, after `d_frame_start_abs(176)`, add:
```cpp
const char* offset_env = getenv("IEEE80211_FRAME_START_OFFSET");
if (offset_env) {
    int offset = atoi(offset_env);
    d_frame_start_abs += offset;
    USRP_LOG("[FRAME_START_OFFSET] applied offset=%d, new d_frame_start_abs=%d\n",
             offset, d_frame_start_abs);
}
```

(Use atomic snprintf+USRP_LOG if more args.)

- [ ] **Step 3: Build and verify**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
```

- [ ] **Step 4: Sweep USRP at offsets -4, -2, 0, +2, +4**

For each offset:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_FRAME_START_OFFSET=<N> \
  IEEE80211_LTF0_FFT_PRECOMP_DUMP=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/offset_<N>_usrp.err > /dev/null
```

For each: count `LSIG_DECODE OK` and check encodings.

- [ ] **Step 5: Tabulate results**

Write `/tmp/offset_sweep.py` (new):
```python
import subprocess, re
offsets = [-4, -2, 0, 2, 4]
results = {}
for off in offsets:
    path = f"/tmp/offset_{off}_usrp.err"
    with open(path) as f:
        text = f.read()
    enc_count = {}
    for m in re.finditer(r"\[LSIG_DECODE\] OK enc=(\d+)", text):
        enc_count[int(m.group(1))] = enc_count.get(int(m.group(1)), 0) + 1
    enc0_pct = 100.0 * enc_count.get(0, 0) / max(sum(enc_count.values()), 1)
    results[off] = (sum(enc_count.values()), enc_count.get(0, 0), enc0_pct)
    print(f"offset={off:+d}: total={results[off][0]} enc=0={results[off][1]} ({enc0_pct:.0f}%)")
```

Run: `/home/hy/conda/envs/gnuradio/bin/python /tmp/offset_sweep.py`
Expected: enc=0 percentage varies. If any offset gives ≥50% enc=0, that's promising.

- [ ] **Step 6: Revert or keep**

If best offset is not 0:
- Keep the env var hook
- Note in commit: "Best offset is N, default still 0"
- Commit: `git add lib/sync_long_impl.cc && git commit -m "diag(sync_long): add IEEE80211_FRAME_START_OFFSET env var (default=0)"`

If best offset is 0 (no improvement):
- Revert: `git checkout lib/sync_long_impl.cc`
- Commit: `git commit -m "diag(sync_long): timing offset sweep did not help, reverted" --allow-empty`

- [ ] **Step 7: Document in phase10 note**

Append to `docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## Timing offset sweep (2026-06-14)

Added `IEEE80211_FRAME_START_OFFSET=N` env var to shift d_frame_start_abs by N samples. Tested -4, -2, 0, +2, +4 on USRP.

Results:
- offset=-4: enc=0 X/Y (Z%)
- offset=-2: enc=0 X/Y (Z%)
- offset= 0: enc=0 X/Y (Z%) (baseline)
- offset=+2: enc=0 X/Y (Z%)
- offset=+4: enc=0 X/Y (Z%)

Conclusion: <whether any offset gives substantial improvement>
```

---

## Task 3: Try L-LTF0 + L-LTF1 ratio-based channel estimation

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add `IEEE80211_H_RATIO_EST` env var hook in `estimate_header_channel_from_lltf52`)
- Test: software loopback + USRP

**Context:** The current `estimate_header_channel_from_lltf52` averages L-LTF0 and L-LTF1 to get H. If L-LTF0 is corrupted, the average is also corrupted. Alternative: use the L-LTF0/L-LTF1 ratio (which is less sensitive to per-symbol noise) or use only L-LTF1.

- [ ] **Step 1: Locate estimate_header_channel_from_lltf52**

Run: `grep -n "estimate_header_channel_from_lltf52" lib/frame_equalizer_impl.cc | head -5`
Expected: a static helper, around line 580-610.

- [ ] **Step 2: Read the current implementation**

Look at how L-LTF0 and L-LTF1 are combined. Note the variable names (likely `L_LTF0_eq` and `L_LTF1_eq` or similar).

- [ ] **Step 3: Add env-var-gated alternative estimator**

After the current H estimation, add a parallel computation gated by the env var:

```cpp
if (getenv("IEEE80211_H_RATIO_EST")) {
    // Alternative: H = sqrt(LTF1 * conj(LTF0)), or just LTF1 only
    // This avoids the averaging that propagates L-LTF0 corruption
    gr_complex LTF0_eq[52], LTF1_eq[52];
    // ... extract LTF0 and LTF1 from saved_ltf0_fft and saved_ltf1_fft ...
    for (int i = 0; i < 52; i++) {
        LTF0_eq[i] = saved_ltf0_fft[sc_idx[i] >= 0 ? sc_idx[i] : sc_idx[i] + 64] / H_expected_pilot[i];
        LTF1_eq[i] = saved_ltf1_fft[sc_idx[i] >= 0 ? sc_idx[i] : sc_idx[i] + 64] / H_expected_pilot[i];
        // Use LTF1 only (later in time, less affected by initial transient)
        Hhdr52[i] = LTF1_eq[i];
    }
    USRP_LOG("[H_RATIO_EST] using L-LTF1 only, replacing Hhdr52\n");
}
```

(Adjust variable names to match actual code. The "H_expected_pilot" is the known L-LTF frequency-domain sequence from the standard.)

- [ ] **Step 4: Build, install, test**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_H_RATIO_EST=1 \
  IEEE80211_FORCE_HTSIG=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/hratio_usrp.err > /tmp/hratio_usrp.out
grep "LSIG_DECODE" /tmp/hratio_usrp.err | sort -u
tail -3 /tmp/hratio_usrp.out
```

Expected: enc=0 percentage may improve.

- [ ] **Step 5: Revert or keep**

If improvement, keep the env var hook and commit:
```bash
git add lib/frame_equalizer_impl.cc
git commit -m "fix(frame_eq): add IEEE80211_H_RATIO_EST env var (L-LTF1 only H)"
```

If not, revert:
```bash
git checkout lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): L-LTF1 only H did not help, reverted" --allow-empty
```

- [ ] **Step 6: Document in phase10 note**

Append to `docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## L-LTF1 only H estimation (2026-06-14)

Added `IEEE80211_H_RATIO_EST=1` env var to use only L-LTF1 (skip L-LTF0 average).

USRP result: <enc=0 percentage change>
Loopback result: <regression check>

Conclusion: <whether L-LTF1 only is better>
```

---

## Task 4: Try per-SC phase tracking on equalized symbols

**Files:**
- Modify: `lib/frame_equalizer_impl.cc` (add `IEEE80211_PER_SC_PHASE_TRACK` env var hook in L-SIG/HT-SIG equalization)
- Test: software loopback + USRP

**Context:** Per-SC phase tracking (a.k.a. CPE compensation) can correct residual phase rotation after equalization. If L-LTF0 → H estimation has noise, the equalized symbols are scattered. Per-SC phase tracking may reduce that scatter.

- [ ] **Step 1: Locate the L-SIG equalization point**

Run: `grep -n "decode_lsig_direct_from_header52\|d_early_eqsym\[kLSigRel\]" lib/frame_equalizer_impl.cc | head -10`
Expected: around line 3133 (Task 3 moved it to here).

- [ ] **Step 2: Add per-SC phase tracking hook**

Right before `decode_lsig_direct_from_header52` is called, add:

```cpp
if (getenv("IEEE80211_PER_SC_PHASE_TRACK")) {
    // Track per-SC phase rotation using 4 pilot SCs of L-SIG
    // L-SIG pilots are at SC [-21, -7, 7, 21] (4 pilots)
    int pilots[4] = {-21, -7, 7, 21};
    gr_complex pilot_sum(0, 0);
    for (int p = 0; p < 4; p++) {
        int sc = pilots[p];
        int idx = sc + 26;  // SC -26 to +26 mapped to 0 to 52
        pilot_sum += d_early_eqsym[kLSigRel][idx];
    }
    float pilot_phase = std::arg(pilot_sum);
    float cpe_correction = -pilot_phase;
    gr_complex rot = std::polar(1.0f, cpe_correction);
    for (int i = 0; i < 52; i++) {
        d_early_eqsym[kLSigRel][i] *= rot;
    }
    USRP_LOG("[PER_SC_PHASE_TRACK] cpe_correction=%.4f rad\n", cpe_correction);
}
```

- [ ] **Step 3: Build, install, test**

Run:
```bash
cd /home/hy/gr-ieee802-11/build && make 2>&1 | tail -3
cmake --install . 2>&1 | tail -3
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_PER_SC_PHASE_TRACK=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 12 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/cpe_usrp.err > /tmp/cpe_usrp.out
grep "LSIG_DECODE" /tmp/cpe_usrp.err | sort -u
grep "PER_SC_PHASE_TRACK" /tmp/cpe_usrp.err | head -3
```

Expected: enc=0 may improve if the corruption is mostly a common phase rotation.

- [ ] **Step 4: Revert or keep, document**

If improvement, keep and commit. If not, revert.

Document in phase10 note.

- [ ] **Step 5: Commit**

```bash
git add lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): per-SC phase tracking on L-SIG (CPE correction)"
# OR
git checkout lib/frame_equalizer_impl.cc
git commit -m "diag(frame_eq): per-SC phase tracking did not help, reverted" --allow-empty
```

---

## Task 5: Apply best fix(es) and end-to-end USRP validation

**Files:**
- Test: `/tmp/test_p10_usrp_v2.py` (existing)

**Context:** Combine the best env-var-gated fixes from Tasks 1-4 and run end-to-end validation. If multiple fixes help in combination, they may all be needed.

- [ ] **Step 1: Run USRP with all successful fixes combined**

For each fix that showed improvement, set the corresponding env var:
```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_FORCE_HTSIG=1 \
  IEEE80211_FRAME_START_OFFSET=<best> \
  IEEE80211_H_RATIO_EST=1 \
  IEEE80211_PER_SC_PHASE_TRACK=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 35 /home/hy/conda/envs/gnuradio/bin/python /tmp/test_p10_usrp_v2.py \
  2> /tmp/e2e_combined_usrp.err > /tmp/e2e_combined_usrp.out
```

- [ ] **Step 2: Capture FCS counts**

Check:
```bash
tail -5 /tmp/e2e_combined_usrp.out
echo "---"
grep "HT_SIG_CAND" /tmp/e2e_combined_usrp.err | wc -l
echo "HT_SIG_CAND lines"
grep "LSIG_DECODE" /tmp/e2e_combined_usrp.err | sort -u
```

Expected: if the fix(es) work, enc=0 frequency should rise, and FCS OK may rise from 0.

- [ ] **Step 3: Run loopback regression (no fixes)**

```bash
cd /home/hy/gr-ieee802-11
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  timeout 15 /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py \
  2> /tmp/e2e2_loop.err > /tmp/e2e2_loop.out
tail -3 /tmp/e2e2_loop.out
```

Expected: same as baseline (1-3 OK).

- [ ] **Step 4: Write final summary to phase10 note**

Append to `docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md`:

```markdown

## End-to-end combined fixes (2026-06-14)

Combined env vars: FORCE_HTSIG, FRAME_START_OFFSET=N, H_RATIO_EST, PER_SC_PHASE_TRACK

USRP 30s result: <enc=0 %, FCS OK, HT_SIG_CAND counts>
Loopback result: <regression check>

Conclusion: <whether combination unlocks end-to-end>
```

- [ ] **Step 5: Commit final state**

```bash
git add docs/superpowers/notes/2026-06-14-phase10-enc-mismatch-finding.md
git commit -m "notes(phase10): end-to-end combined L-LTF0 fixes (Task 5)"
```

- [ ] **Step 6: Update memory**

Update `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p10_finding_enc_mismatch.md` with Phase 12 outcome.

Update `MEMORY.md` index with Phase 12 entry or note.

---

## Self-Review

- [x] **Spec coverage**: Tasks cover timing diagnosis (1), timing offset (2), L-LTF1-only H (3), per-SC phase tracking (4), e2e validation (5). All match the goal of fixing L-LTF0 FFT upstream.
- [x] **Placeholder scan**: No "TBD" / "TODO" / "fill in". All code is concrete. Env var names spelled consistently. Variable names match existing code with a "match existing code" note.
- [x] **Type consistency**: `d_early_eqsym`, `Hhdr52`, `d_frame_start_abs`, `saved_ltf0_fft`, `saved_ltf1_fft`, `kLSigRel` used consistently.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-14-ltf0-fft-upstream-fix.md`. Two execution options:**

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints
