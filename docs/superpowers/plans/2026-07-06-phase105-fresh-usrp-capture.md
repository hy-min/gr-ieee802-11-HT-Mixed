# Phase 105 — Fresh 60s USRP Capture + File-Replay Test Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether the file-replay pipeline (Phase 103) can decode a FRESH USRP capture, not just the 8-day-old Phase 55 captures. Pass = `FCS_OK ≥ 1` on `/tmp/p105_usrp_capture_60s.bin`.

**Architecture:** Reuse `examples/p68_capture_raw_iq.py` (existing UHD capture-only harness, 60s default) to get a fresh IQ dump. Then run the file-replay harness on the new file with all current algorithm fixes (Phase 89 sync_short, Phase 34 δ, Phase 18 L-SIG).

**Tech Stack:** Python 3.8, GNU Radio, UHD (existing).

---

## Background

Per Phase 104 verdict (`docs/superpowers/notes/2026-07-06-phase104-diff-verdict.md`):
- Clean IQ (`/tmp/p103_iq.bin`, regenerated today): `FCS_OK=1` (algorithm chain works)
- 3 old USRP captures (Phase 55, 2026-06-29): `0 frames` (fails)
- 2 old USRP captures re-run with Phase 89 boxcar fix: `0 frames` (fails)
- **INCONCLUSIVE** because old captures are 8 days stale and 34–156 ms long

**Phase 105 hypothesis**: If we take a FRESH 60 s USRP capture with the current algorithm suite and re-run file-replay, we will see `FCS_OK ≥ 1` on the fresh capture (because the algorithm chain is provably correct on clean IQ; the issue with old captures was staleness, not algorithm).

**If hypothesis confirmed**: HARD CONSTRAINT is achievable via the file-replay pipeline. The "USRP realtime" framing becomes "USRP capture → file-replay" — a softer constraint but still validates the RX chain on actual USRP IQ.

**If hypothesis refuted**: The damage to USRP IQ is structural and the file-replay pipeline cannot rescue it. Phase 106 needs a different attack (e.g., inline sync_short debug, per-stage log capture).

---

## Hardware Pre-requisite

The user must have the USRP X310 (`addr=192.168.10.2`) ON and connected, with a TX source available on the same board (e.g., `test_usrp_minimal_loopback.py` running concurrently in a separate process, or a self-loopback cable per Phase 82).

If no TX is active, the capture will only contain noise — useful for testing that the pipeline degrades gracefully but NOT for testing FCS_OK.

---

## File Structure

**Files to create:**
- `/home/hy/gr-ieee802-11/docs/superpowers/plans/2026-07-06-phase105-fresh-usrp-capture.md` (this file)
- `/home/hy/gr-ieee802-11/docs/superpowers/notes/2026-07-06-phase105-fresh-usrp-capture-verdict.md` (Task 4)

**Files to reuse (no modifications):**
- `examples/p68_capture_raw_iq.py` (UHD capture harness, 60s default)
- `examples/test_file_replay_e2e.py` (file-replay harness with `--diag`)
- `examples/diff_diag_csv.py` (Phase 104 diff script)

**Output files:**
- `/tmp/p105_usrp_capture_60s.bin` (60s USRP capture, ~960 MB at 20 MHz complex64)
- `/tmp/p105_diag_fresh_usrp.csv` (per-frame metrics from file-replay)
- `/tmp/p105_diff_summary.md` (extended diff with fresh capture)

---

## Tasks

### Task 1: User runs USRP capture

**Hardware:** USRP X310 + UBX-160, A:0 subdev, TX active (concurrent `test_usrp_minimal_loopback.py` or self-loopback cable).

**User-runs-this command:**

```bash
rm -f /tmp/p105_usrp_capture_60s.bin
/home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/p68_capture_raw_iq.py \
    --freq 5250 --rate 20 --rx-gain 20 --rx-scale 40 \
    --rx-subdev A:0 --antenna RX2 \
    --duration 60 \
    --out /tmp/p105_usrp_capture_60s.bin
```

**Expected output:** A 60s UHD capture at 5250 MHz, written to `/tmp/p105_usrp_capture_60s.bin` (~960 MB).

**Frequency note:** 5250 MHz chosen per Phase 81/94/96 history (cleanest 5 GHz band, +5.7 dB avg_snr_htsig vs 5890 air). The user may change to 5890 MHz if preferred, but 5250 is the project's "best" frequency.

**Concurrent TX (REQUIRED):** Run this in another terminal BEFORE the capture starts:

```bash
/home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_usrp_minimal_loopback.py \
    --freq 5250 --rate 20 --tx-gain 0 --rx-subdev A:0 \
    --warmup 60
```

If the user does NOT have access to a second terminal / TX source, the capture will only contain noise — Phase 105 will still produce a "0 frames" result, but the diagnostic value is limited.

**After capture completes:**

```bash
ls -la /tmp/p105_usrp_capture_60s.bin
```

Expected: file size ~960,000,000 bytes (60s × 20 MHz × 8 bytes/sample).

---

### Task 2: Run file-replay on the fresh capture

**Command:**

```bash
rm -f /tmp/p105_diag_fresh_usrp.csv
IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1 \
IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1 \
IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE=16 \
/home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/test_file_replay_e2e.py \
    --phase rx --iq-file /tmp/p105_usrp_capture_60s.bin \
    --rx-duration 60 --rate 20 --loop 5 \
    --diag /tmp/p105_diag_fresh_usrp.csv
```

**Phase 89 env vars enabled** so the latest sync_short algorithm is used.

**Expected:**
- File-replay runs for 60s (since the source is 60s, loop=5 means 300s of replay, but head blocks at 60s × 20 MHz samples).
- DiagLogger writes per-frame rows to `/tmp/p105_diag_fresh_usrp.csv`.

**Pass criterion:** `FCS_OK ≥ 1` in the file-replay output.

---

### Task 3: Run extended diff including fresh capture

**Command:**

```bash
/home/hy/conda/envs/gnuradio/bin/python /home/hy/gr-ieee802-11/examples/diff_diag_csv.py \
    --clean /tmp/p104_diag_clean.csv \
    --usrp /tmp/p105_diag_fresh_usrp.csv \
    --out /tmp/p105_diff_summary.md
```

This adds the fresh capture to the Phase 104 diff and shows the comparison.

---

### Task 4: Write Phase 105 verdict

**File:** `docs/superpowers/notes/2026-07-06-phase105-fresh-usrp-capture-verdict.md`

**Contents:**

1. **Capture success/failure**: Did `/tmp/p105_usrp_capture_60s.bin` get created with the expected size?
2. **File-replay result**: `FCS_OK=?, RX=?` from the file-replay run.
3. **Diff summary**: 4-row table comparing clean / old USRP / fresh USRP.
4. **Interpretation**:
   - If `FCS_OK ≥ 1` on fresh: HARD CONSTRAINT achieved via file-replay. Phase 106 = USRP realtime pipeline preservation.
   - If `FCS_OK = 0` on fresh: structural USRP IQ damage, not staleness. Phase 106 = per-stage log diagnostic.
5. **HARD CONSTRAINT status update**: `ACHIEVED (file-replay)` vs `NOT ACHIEVED`.
6. **Phase 106 recommendation**:
   - If achieved: document file-replay as the canonical validation path. Build a `realtime_to_replay` wrapper for the production deployment.
   - If not: rebuild sync_short with verbose state-transition logging, add a capture-side correlation monitor.

---

## Self-Review

**Spec coverage**: Capture script reuse ✓, user command ✓, file-replay ✓, diff ✓, verdict ✓. 4 tasks total.

**Hardware dependency**: Task 1 requires user interaction (USRP hardware). Task 2-4 are automated. This split is necessary because USRP hardware is not accessible from this session.

**Pass/fail criteria explicit**: `FCS_OK ≥ 1` on fresh capture = PASS. Phase 106 direction depends on PASS/FAIL.

**Caveats**:
- If user has no concurrent TX, capture is noise-only and 0 frames is the only possible result.
- The 60s capture is at 5250 MHz per Phase 81 finding (quietest 5 GHz band).
- 30 dB attenuator EXCLUDED per user 2026-07-05.

---

## Execution Handoff

**Status**: Plan complete and saved to `docs/superpowers/plans/2026-07-06-phase105-fresh-usrp-capture.md`. Ready for execution.

**Hardware requirement**: User must run the capture command interactively (Task 1) and report the file size back. Once `/tmp/p105_usrp_capture_60s.bin` exists with the expected size, the rest of the plan runs automatically.
