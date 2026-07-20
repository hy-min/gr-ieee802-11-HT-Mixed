# Phase 158: COPY-State Smart Re-Detection ("Refractory but Not Blind") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the residual ~20% arrival loss caused by sync_short COPY-state capture: allow a *clearly stronger* real L-STF plateau to re-trigger a `wifi_start` tag during a false COPY episode, WITHOUT shortening the protective refractory period.

**Architecture:** Add an opt-in, env-gated re-detection path inside `sync_short.cc`'s COPY branch. Three independent gates must ALL pass before a re-detect tag fires: (1) power-EMA gate (we are in a noise-like trap, not inside a real frame), (2) correlation strength gate (`in_cor > FACTOR × effective_threshold`, default 5×), (3) sustained plateau gate (>MIN_PLATEAU consecutive qualifying samples — noise boxcar excursions are capped at ~16 samples, real L-STF plateau is ~128). The existing gap detector and COPY episode length are UNTOUCHED (Phase 157 proved the 0.01 long-COPY refractory is load-bearing).

**Tech Stack:** C++ GNU Radio block (`lib/sync_short.cc`), Python TDD unit test (synthetic stream, no hardware), USRP A/B batch validation (`batch_usrp_validate.py`, N=16 mean±std ruler per Phase 148).

---

## Background (read first — zero-context primer)

**The problem (Phase 153/154 funnel):** ~20% of real frames are missed because sync_short gets trapped in a long COPY episode (~6ms) after a noise-burst false detection. While trapped, noise power hovers at the gap detector's 0.01 threshold, resetting the gap counter, so the state machine never returns to SEARCH — and any real L-STF arriving during the trap produces NO `wifi_start` tag (COPY state has no detection logic).

**Why not just exit COPY sooner (Phase 155/157 verdicts — READ THESE BEFORE CODING):**
- Phase 155: raising gap `POWER_THRESHOLD` 0.01→0.3 REGRESSED batch mean 200→102.5. REFUTED, reverted.
- Phase 157 root cause: the long COPY episode is a **protective refractory period** against noise-burst re-triggering. Gap 0.3 dismantled it: rapid re-triggers (<5k sample gaps) exploded 10→218 (22×) on air → false `wifi_start` tag bombardment → sync_long yanked out of alignment (good L-SIG -13%, FCS -14%). Truncation model REFUTED on SMA cable (zero truncation).
- **Conclusion (binding):** Do NOT shorten COPY episodes. Attack = "refractory but not blind": keep the episode, but let COPY re-detect ONLY a clearly stronger real L-STF plateau.

**Key facts:**
- `lib/sync_short.cc` is a 2-state block (SEARCH/COPY). Inputs: `in` (signal), `in_abs` (unused for CFO now), `in_cor` (16-sample boxcar autocorr from `sync_short_fused`). Output: signal + `wifi_start` tags.
- Boxcar values: noise ~0.13-0.2, real L-STF ~1.4-2.3 (Phase 96-98 cable logs). Noise excursions ≤16 samples (boxcar window); real L-STF plateau ~128 samples. Adaptive threshold = `max(p90×1.5, 0.01, 0.2)`, updated ONLY in SEARCH.
- Real-frame RF power mean is 3-28 (Phase 157 ultrathink); trap-state noise power hovers ~0.005-0.02.
- Downstream mechanism for re-tags ALREADY EXISTS: `sync_long.cc:307-332` — a `wifi_start` tag during sync_long's COPY state with `d_count >= 1000` transitions sync_long directly to SYNC (re-search). Tags during SYNC are logged + ignored (Phase 135).
- **Iron rules:** env vars default OFF; `make` MUST be followed by `make install` (else Python loads stale .so); loopback regression gate (`examples/test_direct_loopback.py` → `Final: OK=1 FAIL=0`) must pass after any sync_short change (Phase 151e: `consume_each` arg = actually-processed samples).

**File map:**
- Modify: `lib/sync_short.cc` (only file with logic changes)
- Create: `p158_redetect_unit.py` (TDD unit test, repo root, alongside p147_race_repro.py etc.)
- Regression: `examples/test_direct_loopback.py` (run, not modified)
- USRP A/B: `batch_usrp_validate.py` + `usrp_realtime_validate.sh` (run, not modified)
- Docs: `docs/superpowers/notes/2026-07-20-phase158-copy-redetect-verdict.md`, memory, `CLAUDE.md`

---

## New env vars (all opt-in, defaults preserve baseline)

| Env var | Default | Meaning |
|---|---|---|
| `IEEE80211_SYNC_SHORT_COPY_REDETECT` | unset = OFF | Master switch for COPY-state re-detection |
| `IEEE80211_SYNC_SHORT_COPY_REDETECT_FACTOR` | `5.0` | Strength gate: `in_cor > FACTOR × max(d_adaptive_thresh, d_threshold)` |
| `IEEE80211_SYNC_SHORT_COPY_REDETECT_EMA_MAX` | `0.5` | Power-EMA gate ceiling: re-detect armed only when `ema < EMA_MAX` (trap ~0.005, real frame ~3-28) |

---

## Task 1: Failing TDD unit test (synthetic stream, no hardware)

**Files:**
- Create: `p158_redetect_unit.py`

The block reads env vars AT CONSTRUCTION, so each scenario constructs fresh blocks. The test feeds scripted `(power, in_cor)` streams directly into `sync_short`'s 3 input ports (bypassing `sync_short_fused`) and counts `wifi_start` tags on a `vector_sink`.

Stream conventions used below (deterministic, chunk-invariant — tag offsets are computed from `nitems_written`/`nitems_read`, independent of scheduler chunking):
- Adaptive threshold ON, noise cor=0.15 → after 4096-sample window fill, `adaptive_thresh = max(0.15×1.5, 0.01, 0.2) = 0.225`. Strong gate = `0.225 × 5 = 1.125`.
- Detection fires on the **25th** consecutive above-threshold sample (MIN_PLATEAU=24 semantics: `d_plateau` counts 1..24, fires when already 24).
- In SEARCH, `insert_tag(nitems_written(0), ...)` → first tag lands at output offset 0 (nothing output yet in SEARCH).

- [ ] **Step 1: Write the failing test**

```python
#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 158 TDD unit test: COPY-state smart re-detection ("refractory but not blind").

Feeds scripted (power, cor) streams straight into ieee802_11.sync_short and
counts wifi_start tags on the output. No hardware, fully deterministic.

Scenarios:
  A (feature ON, trap + real L-STF mid-trap)  -> expect 2 tags: [0, lstf_out_start + 24]
  B (feature ON, clean real frame)            -> expect 1 tag:  [0]
     (L-LTF's strong corr + CP-like 16-sample corr spikes must NOT re-trigger)
  C (feature OFF, same stream as A)           -> expect 1 tag:  [0]  (baseline preserved)

Run (from repo root, AFTER make && make install):
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p158_redetect_unit.py
"""
import cmath
import math
import os
import sys

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH'] = '1'

from gnuradio import gr, blocks
import ieee802_11

MIN_PLATEAU = 24


def run_stream(power, cor, redetect_on):
    if redetect_on:
        os.environ['IEEE80211_SYNC_SHORT_COPY_REDETECT'] = '1'
    else:
        os.environ.pop('IEEE80211_SYNC_SHORT_COPY_REDETECT', None)
    in_sig = [cmath.rect(math.sqrt(p), 0.0) for p in power]
    src0 = blocks.vector_source_c(in_sig, False)
    src1 = blocks.vector_source_c(list(in_sig), False)  # in_abs: unused (CFO disabled)
    src2 = blocks.vector_source_f(list(cor), False)
    ss = ieee802_11.sync_short(0.01, MIN_PLATEAU, False, False)  # reads env NOW
    sink = blocks.vector_sink_c()
    tb = gr.top_block()
    tb.connect(src0, (ss, 0))
    tb.connect(src1, (ss, 1))
    tb.connect(src2, (ss, 2))
    tb.connect(ss, sink)
    tb.run()
    return sorted(t.offset() for t in sink.tags())


def build_scenario_a():
    """fill(4500) + false-detect(30) + trap(3000) + L-STF(160) + data(400) + gap(600)."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)          # fill 4096-sample adaptive window, no detection
    seg(0.005, 0.40, 30)            # weak false detection (25th sample -> COPY)
    # trap: noise hovers below gap threshold, power spike every 100 samples
    # resets the gap counter -> COPY never exits (the Phase 153 trap)
    for k in range(30):
        seg(0.005, 0.15, 99)
        seg(0.02, 0.15, 1)          # gap-counter reset spike
    lstf_in_start = len(power)      # = 4500+30+3000 = 7530
    seg(3.0, 1.8, 160)              # real L-STF arrives DURING the trap
    seg(3.0, 0.3, 400)              # rest of frame (high power, weak corr)
    seg(0.005, 0.1, 600)            # gap -> exit COPY
    # Output starts at input index 4524 (25th false-detect sample, tag1 at out 0).
    # Re-detect tag lands at out (lstf_in_start - 4524) + 24.
    expected_redetect_out = (lstf_in_start - 4524) + 24
    return power, cor, [0, expected_redetect_out]


def build_scenario_b():
    """fill(4500) + L-STF(160) + L-LTF(160 strong corr) + data w/ CP spikes(2000) + gap(600)."""
    power, cor = [], []

    def seg(p, c, n):
        power.extend([p] * n)
        cor.extend([c] * n)

    seg(0.005, 0.15, 4500)
    seg(3.0, 1.8, 160)              # L-STF -> detection at 25th sample
    seg(3.0, 1.8, 160)              # L-LTF: strong corr, must NOT re-trigger (EMA high)
    seg(3.0, 0.3, 960)              # data part 1
    seg(3.0, 1.8, 16)               # CP-like corr spike #1 (16 < 25 -> rejected)
    seg(3.0, 0.3, 464)
    seg(3.0, 1.8, 16)               # CP-like corr spike #2
    seg(3.0, 0.3, 544)
    seg(0.005, 0.1, 600)
    return power, cor, [0]


def main():
    failures = []

    # Scenario A: trap + real L-STF mid-trap, feature ON
    power, cor, expected = build_scenario_a()
    tags = run_stream(power, cor, redetect_on=True)
    print(f"[A] redetect ON  tags={tags} expected={expected}")
    if tags != expected:
        failures.append(f"A: tags={tags} expected={expected}")

    # Scenario B: clean real frame, feature ON -> exactly 1 tag
    power, cor, expected = build_scenario_b()
    tags = run_stream(power, cor, redetect_on=True)
    print(f"[B] redetect ON  tags={tags} expected={expected}")
    if tags != expected:
        failures.append(f"B: tags={tags} expected={expected}")

    # Scenario C: same stream as A, feature OFF -> baseline (1 tag, no re-detect)
    power, cor, expected_a = build_scenario_a()
    tags = run_stream(power, cor, redetect_on=False)
    print(f"[C] redetect OFF tags={tags} expected=[0]")
    if tags != [0]:
        failures.append(f"C: tags={tags} expected=[0]")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print("\nPASS: all 3 scenarios")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run test to verify it fails (RED)**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python p158_redetect_unit.py
```

Expected: FAIL on scenario A (`tags=[0]`, expected `[0, 3030]`), because the re-detection feature does not exist yet. Scenarios B and C should already pass (1 tag each). If A passes or B/C fail at this point, the test itself is wrong — fix the test before proceeding.

- [ ] **Step 3: Commit the failing test**

```bash
cd /home/hy/gr-ieee802-11
git add p158_redetect_unit.py
git commit -m "test(p158): failing TDD unit test for COPY-state smart re-detection"
```

---

## Task 2: Env parsing + member state in sync_short.cc

**Files:**
- Modify: `lib/sync_short.cc`

- [ ] **Step 1: Add env parsers** after `parse_gap_power_threshold()` (line 85):

```cpp
// Phase 158: COPY-state smart re-detection ("refractory but not blind").
// Phase 157 confirmed the long COPY episode is a protective refractory period
// against noise-burst re-triggering (gap 0.3 -> 22x re-trigger explosion ->
// false wifi_start bombardment -> sync_long de-aligned). Do NOT shorten COPY
// episodes. Instead, keep the refractory but let a CLEARLY STRONGER real L-STF
// plateau arriving during a false COPY re-trigger a wifi_start tag so
// sync_long re-syncs (sync_long.cc COPY-state wifi_start handler, d_count>=1000
// -> direct SYNC). Master switch default OFF.
static bool parse_copy_redetect_enabled() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT");
    return env && *env && env[0] != '0';
}

// Strength gate: re-detect requires in_cor > FACTOR * effective threshold.
// Noise boxcar ~0.13-0.2; real L-STF boxcar ~1.4-2.3; adaptive floor 0.2.
// 5 x 0.2 = 1.0 sits between. Must be > 1.0.
static float parse_copy_redetect_factor() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT_FACTOR");
    if (!env || !*env) return 5.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v <= 1.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid COPY_REDETECT_FACTOR '%s', using 5.0\n", env);
        return 5.0f;
    }
    return static_cast<float>(v);
}

// Power-EMA gate ceiling: re-detect armed only when the COPY-state power EMA
// (alpha 1/512) is below this. Trap noise ~0.005-0.02; real frame power 3-28.
// This is what distinguishes "L-STF of a NEW frame while trapped" from "L-LTF /
// CP correlation inside a correctly-detected frame" (both have strong corr).
static float parse_copy_redetect_ema_max() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT_EMA_MAX");
    if (!env || !*env) return 0.5f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v <= 0.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid COPY_REDETECT_EMA_MAX '%s', using 0.5\n", env);
        return 0.5f;
    }
    return static_cast<float>(v);
}
```

- [ ] **Step 2: Add constructor initializers.** In the constructor init list (lines 96-117), append after `d_adaptive_ema_alpha(parse_adaptive_ema_alpha())`:

```cpp
          d_adaptive_ema_alpha(parse_adaptive_ema_alpha()),
          d_copy_redetect(parse_copy_redetect_enabled()),
          d_copy_redetect_factor(parse_copy_redetect_factor()),
          d_copy_redetect_ema_max(parse_copy_redetect_ema_max())
```

(Mind the comma after `parse_adaptive_ema_alpha()`.)

- [ ] **Step 3: Add member declarations** in the `private:` section after `d_adaptive_ema_alpha` (line 417):

```cpp
    // Phase 158: COPY-state smart re-detection state (opt-in).
    const bool d_copy_redetect;
    const float d_copy_redetect_factor;
    const float d_copy_redetect_ema_max;
    float d_copy_power_ema = 0.0f;      // EMA (alpha 1/512) of COPY-state sample power
    int d_redetect_plateau = 0;         // consecutive above-strong-threshold samples
    bool d_redetect_cooldown = false;   // set after a re-detect fires; clears when EMA >= EMA_MAX
    bool d_redetect_seen_drop = false;  // corr dropped below strong gate at least once since COPY entry
```

- [ ] **Step 4: Build and install**

```bash
cd /home/hy/gr-ieee802-11/build && cmake .. -DCMAKE_BUILD_TYPE=Release >/dev/null && make -j$(nproc) && sudo -n make install 2>/dev/null || make install
```

Expected: compiles clean. **Do not skip `make install`** (stale .so is the classic trap).

- [ ] **Step 5: Run unit test — still RED on A only**

Same command as Task 1 Step 2. Expected: identical results (state added, no logic yet). A fails, B/C pass.

- [ ] **Step 6: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/sync_short.cc
git commit -m "feat(p158): env parsing + state for COPY-state re-detection (logic next)"
```

---

## Task 3: Re-detection logic in the COPY loops

**Files:**
- Modify: `lib/sync_short.cc`

The logic goes in a member helper called from BOTH copy loops (the main COPY branch AND the Phase 151e SEARCH-continuation branch — with vector sources or large chunks, an entire trap can be processed inside the continuation branch in one call).

Gate design (all four must hold to count the plateau; fire on the 25th consecutive qualifying sample, mirroring SEARCH's `d_plateau < MIN_PLATEAU` semantics):
1. `d_redetect_seen_drop` — corr must have dropped below the strong gate at least once since COPY entry. Prevents re-firing on the TAIL of the same L-STF that triggered this COPY (detection fired at L-STF sample 25; the remaining ~135 strong samples would otherwise re-trigger immediately).
2. `!d_redetect_cooldown` — after a fire, disarm until EMA has risen to `EMA_MAX` at least once (prevents double-tag within the re-detected L-STF).
3. `d_copy_power_ema < EMA_MAX` — we are in a noise-like trap, not inside a real frame (real frame EMA ≈ 3; trap ≈ 0.005; EMA lags, so the first ~90 samples of an arriving L-STF still pass).
4. `cor > strong_thresh` where `strong_thresh = max(d_adaptive_thresh, d_threshold) × FACTOR`.

- [ ] **Step 1: Add the helper** as a private member function, placed just before `insert_tag` (line 388):

```cpp
    // Phase 158: COPY-state smart re-detection step ("refractory but not blind").
    // Called once per copied sample from both COPY loops. See verdict:
    // docs/superpowers/notes/2026-07-19-phase157-refractory-model-verdict.md
    // out_idx/in_idx are the ABSOLUTE stream positions of this sample for tagging.
    inline void copy_redetect_step(float power, float cor, uint64_t out_idx, uint64_t in_idx)
    {
        if (!d_copy_redetect) return;
        d_copy_power_ema += (power - d_copy_power_ema) * (1.0f / 512.0f);
        if (d_redetect_cooldown && d_copy_power_ema >= d_copy_redetect_ema_max) {
            d_redetect_cooldown = false;
        }
        const float strong_thresh =
            std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
            d_copy_redetect_factor;
        if (cor <= strong_thresh) {
            d_redetect_seen_drop = true;
            d_redetect_plateau = 0;
            return;
        }
        if (!d_redetect_seen_drop || d_redetect_cooldown ||
            d_copy_power_ema >= d_copy_redetect_ema_max) {
            d_redetect_plateau = 0;
            return;
        }
        if (d_redetect_plateau < static_cast<int>(MIN_PLATEAU)) {
            d_redetect_plateau++;
            return;
        }
        // FIRE: clearly stronger sustained L-STF plateau inside a false COPY.
        // Re-tag frame start here; sync_long's COPY-state wifi_start handler
        // (d_count >= 1000) re-syncs from this point. Episode NOT shortened.
        insert_tag(out_idx, 0.0, in_idx);
        d_copied = 0;  // new frame gets the full MAX_SAMPLES budget
        d_below_threshold = 0;
        d_plateau = 0;
        d_redetect_plateau = 0;
        d_redetect_cooldown = true;
        char p158buf[192];
        snprintf(p158buf, sizeof(p158buf),
                 "[SYNC-SHORT-P158] COPY re-detect: out=%llu corr=%.4f "
                 "strong_thresh=%.4f ema=%.4f\n",
                 (unsigned long long)out_idx, cor, strong_thresh, d_copy_power_ema);
        USRP_LOG("%s", p158buf);
        fprintf(stderr, "%s", p158buf);
    }
```

- [ ] **Step 2: Reset re-detect state at COPY entry.** In the SEARCH branch detection site (lines 237-245, where `d_state = COPY; d_copied = 0;`), add after `d_plateau = 0;`:

```cpp
                        d_plateau = 0;
                        // Phase 158: arm re-detection for the new COPY episode.
                        d_redetect_plateau = 0;
                        d_redetect_cooldown = false;
                        d_redetect_seen_drop = false;
```

- [ ] **Step 3: Call the helper in the SEARCH-continuation copy loop.** Inside the `while (o < rem && o < noutput && d_copied < MAX_SAMPLES)` loop (lines 271-295), after the `min_cor`/`max_cor` tracking lines and BEFORE `out[o] = in_rem[o];`:

```cpp
                    if (in_cor[copy_start + o] < min_cor) min_cor = in_cor[copy_start + o];
                    if (in_cor[copy_start + o] > max_cor) max_cor = in_cor[copy_start + o];

                    // Phase 158
                    copy_redetect_step(power, in_cor[copy_start + o],
                                       nitems_written(0) + o,
                                       nitems_read(0) + copy_start + o);

                    out[o] = in_rem[o];
```

- [ ] **Step 4: Call the helper in the main COPY loop.** Inside the `while (o < ninput && o < noutput && d_copied < MAX_SAMPLES)` loop (lines 335-366), after the `min_cor`/`max_cor` tracking and BEFORE `out[o] = in[o];`:

```cpp
                if (in_cor[o] < min_cor) min_cor = in_cor[o];
                if (in_cor[o] > max_cor) max_cor = in_cor[o];

                // Phase 158
                copy_redetect_step(power, in_cor[o],
                                   nitems_written(0) + o, nitems_read(0) + o);

                out[o] = in[o];  // CFO compensation disabled - no real CFO in simulation
```

NOTE: `power` is already computed at the top of both loops for the gap detector — reuse it, do not recompute.

- [ ] **Step 5: Build and install** (same command as Task 2 Step 4). Expected: compiles clean.

- [ ] **Step 6: Run unit test — GREEN**

```bash
cd /home/hy/gr-ieee802-11
PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python p158_redetect_unit.py
```

Expected: `PASS: all 3 scenarios` — A gives `[0, 3030]` (re-detect fired at the 25th L-STF sample), B gives `[0]` (no intra-frame re-trigger), C gives `[0]` (baseline preserved).

If A fires at a wrong offset or fires twice: check the cooldown/clear logic. If A never fires: check that `d_redetect_seen_drop` is being set (corr 0.15-0.4 in trap is below strong_thresh 1.125) and EMA stays < 0.5 during the first 25 L-STF samples (0.005 + 25/512×3 ≈ 0.15 ✓). Debug by temporarily bumping the `fprintf` into every gate rejection.

- [ ] **Step 7: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add lib/sync_short.cc p158_redetect_unit.py
git commit -m "feat(p158): COPY-state smart re-detection — refractory but not blind"
```

---

## Task 4: Loopback regression gate (feature OFF and ON)

**Files:** none modified — run `examples/test_direct_loopback.py`.

Phase 151e iron rule: any sync_short change must pass the deterministic loopback gate. This runs at default MIN_PLATEAU=2 via `wifi_phy_hier.py` (10 MHz loopback artifact is documented; do NOT set MIN_PLATEAU_OVERRIDE here).

- [ ] **Step 1: Baseline (feature OFF)**

```bash
cd /home/hy/gr-ieee802-11/examples
PYTHONPATH=/home/hy/gr-ieee802-11/build/python/bindings:/home/hy/gr-ieee802-11/python:/home/hy/gr-ieee802-11/examples \
  /home/hy/conda/envs/gnuradio/bin/python test_direct_loopback.py 2>&1 | tail -3
```

Expected last line: `Final: OK=1 FAIL=0`

- [ ] **Step 2: Feature ON**

```bash
cd /home/hy/gr-ieee802-11/examples
IEEE80211_SYNC_SHORT_COPY_REDETECT=1 \
PYTHONPATH=/home/hy/gr-ieee802-11/build/python/bindings:/home/hy/gr-ieee802-11/python:/home/hy/gr-ieee802-11/examples \
  /home/hy/conda/envs/gnuradio/bin/python test_direct_loopback.py 2>&1 | tail -3
```

Expected last line: `Final: OK=1 FAIL=0` (identical to baseline — a clean loopback frame has continuous high power, so the EMA gate keeps re-detection disarmed inside the frame).

- [ ] **Step 3: Commit (nothing to commit if no files changed — skip)**

If either step fails: STOP. Use superpowers:systematic-debugging — a sync_short change that breaks the deterministic loopback is an over-consumption/tag-placement bug class (see Phase 151e).

---

## Task 5: USRP A/B batch validation (REQUIRES hardware: X310 @ 192.168.10.2)

**Files:** none modified — run `batch_usrp_validate.py`. Env vars pass through (script inherits `os.environ`).

Statistical ruler (Phase 148): compare on the MEAN of N=16 runs, excluding `infra_fail` runs (UHD RFNoC init failures are not decoder measurements, Phase 152). Reference baseline from Phase 154b at MIN_PLATEAU=24: mean ≈ 200 DECODE_SUCCESS/45s (arrival ~44%); best 249.

Pre-flight hardware hygiene:
```bash
uhd_usrp_probe --args addr=192.168.10.2 >/dev/null 2>&1   # nudge; Phase 152 recovery
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor  # must be "performance"
sysctl -n net.core.wmem_max                                # must be 2453333
```

- [ ] **Step 1: Control batch (feature OFF)**

```bash
cd /home/hy/gr-ieee802-11
python3 batch_usrp_validate.py -n 16 --out-dir batch_results/p158_control
```

Expected: ~16 runs × ~65s each ≈ 18 min. Mean `gt_ok` ≈ 200 ± ~15 (matches Phase 154b baseline). Record mean/std.

- [ ] **Step 2: Experiment batch (feature ON)**

```bash
cd /home/hy/gr-ieee802-11
IEEE80211_SYNC_SHORT_COPY_REDETECT=1 \
  python3 batch_usrp_validate.py -n 16 --out-dir batch_results/p158_on
```

- [ ] **Step 3: Mechanism check — count re-detect fires**

```bash
grep -c "SYNC-SHORT-P158" batch_results/p158_on/run_*.err | head -20
```

Expected: fires present but modest (~1-10 per 45s run — one per recovered trap). If ~0 fires AND mean unchanged: the gate is too tight on air (check `strong_thresh` vs real boxcar in the P158 log lines; consider `IEEE80211_SYNC_SHORT_COPY_REDETECT_FACTOR=4`). If fires >>50/run AND mean regresses: gate too loose on air noise — this is the Phase 155 failure signature; RAISE the factor or lower EMA_MAX, do NOT ship.

- [ ] **Step 4: Verdict rule**

- **CONFIRMED:** experiment mean > control mean by more than ~1 std of the control, no UF/OF increase, fires in the modest range. Theoretical ceiling: recovering the full residual ~20% COPY capture would take arrival ~44% → ~55%+ (mean ~250).
- **REFUTED:** means within 1 std, or regression. Keep the code opt-in OFF (project convention: REFUTED levers stay in tree, documented).
- If ambiguous (0.5-1 std), extend to N=32 before judging.

---

## Task 6: Verdict doc + memory + CLAUDE.md

- [ ] **Step 1: Write the verdict**

Create `docs/superpowers/notes/2026-07-20-phase158-copy-redetect-verdict.md` containing: hypothesis (from Phase 157's "refractory but not blind"), mechanism (3 gates), unit-test results, loopback gate results, USRP A/B table (control vs experiment mean±std, fires count, UF/OF), verdict (CONFIRMED/REFUTED/INCONCLUSIVE per Task 5 Step 4 rule), and next attack.

- [ ] **Step 2: Update CLAUDE.md** — add a Phase 158 entry in Project-Specific Conventions documenting the three new env vars (opt-in, default OFF) and the verdict, matching the style of the Phase 154-157 entries.

- [ ] **Step 3: Write memory** — create `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p158_copy_redetect.md` (type: project) and add a one-line index entry to `MEMORY.md` (keep under ~200 chars, link `[[project_p157_refractory_model]]`).

- [ ] **Step 4: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add docs/superpowers/notes/2026-07-20-phase158-copy-redetect-verdict.md CLAUDE.md
git commit -m "docs(p158): COPY-state re-detection verdict + env conventions"
```

---

## Self-review notes (already applied)

- Spec coverage: Phase 157's prescribed attack ("COPY-state re-detect only for a clearly stronger real L-STF plateau; do NOT shorten episodes") → Tasks 2-3 implement exactly that; refractory preserved (gap detector untouched); TDD red→green (Task 1 before logic in Task 3); regression gate (Task 4); USRP ground-truth A/B with the N-run mean ruler (Task 5); docs/memory (Task 6).
- Type consistency: `d_redetect_plateau` is `int`, compared against `static_cast<int>(MIN_PLATEAU)` (MIN_PLATEAU is `const unsigned int`) — cast used at both sites. `insert_tag(uint64_t, double, uint64_t)` matches helper call args. `power` variable reused from existing loop scope in both call sites (verified present at sync_short.cc:272 and :336).
- Known accepted trade-off (documented, not a bug): a real L-STF arriving within the FIRST samples of a COPY episode before corr drops below the strong gate is not re-detected (`d_redetect_seen_drop` false). Trap episodes are ~120k samples; this window is negligible.
