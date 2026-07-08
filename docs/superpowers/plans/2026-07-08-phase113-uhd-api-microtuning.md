# Phase 113 T5.A — UHD API Micro-Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply 3 low-level UHD API calls to USRP X310 + UBX-160 v2 to attack the 1.77 rad per-SC phase noise floor (Phase 112 R1 ceiling) and measure SNR improvement on actual hardware.

**Architecture:** Add a single argparse flag `--uhd-tune` to `test_usrp_minimal_loopback.py` that, when True, invokes `set_rx_dc_offset(False)`, `set_rx_iq_balance(False)`, and `set_rx_lo_source('internal')` on the UHD source after `set_bandwidth`. Default OFF preserves Phase 112 baseline exactly.

**Tech Stack:** GNU Radio 3.10 + UHD 4.7.0.HEAD (Python UHD 4.9.0.0) + USRP X310 + UBX-160 v2 + Python 3.10

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `test_usrp_minimal_loopback.py` | Modify | Add `--uhd-tune` flag and 3 UHD API calls |
| `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md` | Create | Verification results |
| `memory/project_p113_uhd_api_microtuning.md` | Create | Memory index entry |

No new files in lib/ or include/ — C++ untouched.

---

## Task 1: Add `--uhd-tune` argparse flag

**Files:**
- Modify: `test_usrp_minimal_loopback.py:308-310` (after --t7e-k)

- [ ] **Step 1: Add the argparse argument**

Insert after line 309 (after `--t7e-k`):

```python
    # Phase 113: T5.A UHD API micro-tunings (DC offset, IQ balance, LO source)
    # Default OFF — Phase 112 baseline preserved when flag absent.
    parser.add_argument('--uhd-tune', action='store_true',
                        help='Phase 113 T5.A: disable RX DC offset + IQ balance '
                             'calibration, force LO source internal. Attacks 1.77 rad '
                             'analog chain noise floor (Phase 112 R1 ceiling).')
```

- [ ] **Step 2: Verify file parses**

Run: `python -c "import ast; ast.parse(open('/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py').read())"`
Expected: no error (silent exit 0)

- [ ] **Step 3: Verify flag appears in --help**

Run: `python /home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py --help 2>&1 | grep uhd-tune`
Expected: `--uhd-tune` line visible in help output

- [ ] **Step 4: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add test_usrp_minimal_loopback.py
git commit -m "feat(p113 t1): add --uhd-tune argparse flag for T5.A

Phase 113 T5.A: opt-in flag for UHD API micro-tunings. Default OFF
preserves Phase 112 baseline exactly. When enabled, calls
set_rx_dc_offset(False), set_rx_iq_balance(False), and
set_rx_lo_source('internal') to attack 1.77 rad analog chain
noise floor (Phase 112 R1 ceiling).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Add UHD API block after set_bandwidth

**Files:**
- Modify: `test_usrp_minimal_loopback.py:183-184` (after set_bandwidth call)

- [ ] **Step 1: Locate the insertion point**

Verify line 183 is the `set_bandwidth` call:
```python
            self.uhd_usrp_source.set_bandwidth(args.rate * 1e6, rx_ch)
```
This must be the last UHD source call before the diagnostic prints (lines 186-189).

- [ ] **Step 2: Insert the UHD API micro-tuning block**

Insert immediately after line 183 (before the diagnostic prints at line 186):

```python
            # Phase 113 T5.A: UHD API micro-tunings (default OFF via --uhd-tune flag)
            # Direct low-level UHD calls to attack 1.77 rad per-SC phase noise floor
            # (Phase 112 R1 ceiling). try/except prevents experiment interruption if
            # UHD 4.7.0.HEAD rejects any specific API on this hardware/driver combo.
            if args.uhd_tune:
                print("[TEST] UHD micro-tunings ENABLED (Phase 113 T5.A): "
                      "DC=off, IQ=off, LO=internal")
                try:
                    self.uhd_usrp_source.set_rx_dc_offset(False, 0)
                    self.uhd_usrp_source.set_rx_iq_balance(False, 0)
                    self.uhd_usrp_source.set_rx_lo_source('internal', 0)
                    print("[TEST] UHD micro-tunings applied successfully")
                except RuntimeError as e:
                    print(f"[TEST] UHD API micro-tuning failed (non-fatal): {e}")
```

- [ ] **Step 3: Verify file parses**

Run: `python -c "import ast; ast.parse(open('/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py').read())"`
Expected: no error

- [ ] **Step 4: Verify the block is in the correct scope**

Read lines 180-195 and confirm the new block is:
- Inside `MinimalUSRPTest.__init__`
- After `set_bandwidth` (line 183)
- Before the diagnostic prints (line 186-189)
- Inside the `if args.cross_board / else` block (line 175-180) — must be after this conditional
  and after `set_gain` / `set_center_freq` / `set_bandwidth` calls

- [ ] **Step 5: Commit**

```bash
cd /home/hy/gr-ieee802-11
git add test_usrp_minimal_loopback.py
git commit -m "feat(p113 t2): insert UHD API micro-tuning block (T5.A)

Adds 3 UHD API calls after set_bandwidth:
- set_rx_dc_offset(False, 0): disable ADC DC offset auto-calibration
- set_rx_iq_balance(False, 0): disable I/Q imbalance auto-calibration
- set_rx_lo_source('internal', 0): force internal LO

try/except RuntimeError keeps experiment alive if any API fails.
Default OFF (--uhd-tune flag required) preserves Phase 112 baseline.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Loopback verification — baseline unchanged without flag

**Files:**
- Run: `test_usrp_minimal_loopback.py` (no --uhd-tune)
- Capture: `/tmp/p113_t3_loopback_baseline.log`

- [ ] **Step 1: Confirm no USRP hardware is required for loopback**

Read lines 142-189 of `test_usrp_minimal_loopback.py` to confirm loopback requires UHD devices at startup. Loopback is software-side; if UHD finds no devices, the script will fail BEFORE running the modified block. **Skip this task if no UHD devices available** — proceed to Task 4 (file-level review only).

- [ ] **Step 2: Run loopback baseline (no --uhd-tune)**

```bash
cd /home/hy/gr-ieee802-11
timeout 90 python test_usrp_minimal_loopback.py \
    --t7e-on --t7e-k 5 --freq 5250 --warmup 5 --duration 30 \
    --rx-subdev A:0 2>&1 | tee /tmp/p113_t3_loopback_baseline.log
```

Expected: `FCS_OK >= 1` (Phase 89 + Phase 112 baseline — T7e ON must not regress loopback).

- [ ] **Step 3: Verify no `UHD micro-tunings` print appears**

Run: `grep "UHD micro-tunings" /tmp/p113_t3_loopback_baseline.log`
Expected: no output (flag not set → block skipped)

- [ ] **Step 4: Verify FCS_OK count**

Run: `grep "FCS_OK=" /tmp/p113_t3_loopback_baseline.log | tail -1`
Expected: `FCS_OK=N` where N >= 1

- [ ] **Step 5: Record result**

If PASS, continue to Task 4. If FAIL (FCS_OK=0 unexpected on loopback), investigate before proceeding.

---

## Task 4: Loopback verification — `--uhd-tune` does not regress

**Files:**
- Run: `test_usrp_minimal_loopback.py` (with --uhd-tune, loopback mode)
- Capture: `/tmp/p113_t4_loopback_uhdtune.log`

- [ ] **Step 1: Run loopback with --uhd-tune**

```bash
cd /home/hy/gr-ieee802-11
timeout 90 python test_usrp_minimal_loopback.py \
    --t7e-on --t7e-k 5 --uhd-tune --freq 5250 --warmup 5 --duration 30 \
    --rx-subdev A:0 2>&1 | tee /tmp/p113_t4_loopback_uhdtune.log
```

Note: UHD source will fail to find a device → script may exit early. **Expected on loopback**: UHD API calls never execute (no device), so script behavior is identical to Task 3 except for `--help` parsing. If script fails with "No UHD Devices Found", that is acceptable — it confirms the API block is guarded by UHD source availability.

- [ ] **Step 2: Document loopback result**

If script ran with `--uhd-tune` flag parsed:
- Verify `grep "UHD micro-tunings ENABLED" log` shows the print statement (proves flag was read)
- Verify FCS_OK remains >= 1

If script failed at UHD source init (no device):
- Confirm failure is at `uhd_usrp_source = uhd.usrp_source(...)` line 165, NOT at the new UHD API block (line 184+)
- This is acceptable for loopback verification

- [ ] **Step 3: Skip USRP verification if no hardware**

If USRP X310 not reachable (`uhd_find_devices --args="type=x300,addr=192.168.10.2"` returns nothing), document and stop here. The code change is complete and reversible; USRP run is the user's responsibility.

---

## Task 5: USRP baseline reproduction (Phase 112 recap)

**Files:**
- Run: `test_usrp_minimal_loopback.py` (no flag, USRP cable)
- Capture: `/tmp/p113_t5_usrp_baseline.log`

**Prerequisites:** USRP X310 at `192.168.10.2` reachable, UBX-160 cable connected A:0 TX/RX → A:0 RX2, 5250 MHz.

- [ ] **Step 1: Verify USRP reachable**

Run: `uhd_find_devices --args="type=x300,addr=192.168.10.2"`
Expected: lists X310 with serial+revision

- [ ] **Step 2: Run baseline USRP test**

```bash
cd /home/hy/gr-ieee802-11
timeout 180 python test_usrp_minimal_loopback.py \
    --freq 5250 --tx-gain 0 --rate 20 --warmup 60 \
    --rx-subdev A:0 --duration 30 2>&1 | tee /tmp/p113_t5_usrp_baseline.log
```

Expected: matches Phase 112 baseline (`Sent=~60, Recv=0, FCS_OK=0, HT_SIG_PARSE_FAIL>=2`).

- [ ] **Step 3: Record baseline metrics**

Capture: Sent, Recv, FCS_OK, HT_SIG_CAND count, HT_SIG_PARSE_FAIL count, FRAME_DETECT count, any avg_snr dumps (if `--uhd-tune` not needed for these).

---

## Task 6: USRP `--uhd-tune` experimental run

**Files:**
- Run: `test_usrp_minimal_loopback.py` (with --uhd-tune, USRP cable)
- Capture: `/tmp/p113_t6_usrp_uhdtune.log`

- [ ] **Step 1: Wait 10 seconds between runs**

UHD UDP socket buffer release per Phase 112 notes.

```bash
sleep 10
```

- [ ] **Step 2: Run experimental USRP test**

```bash
cd /home/hy/gr-ieee802-11
timeout 180 python test_usrp_minimal_loopback.py \
    --uhd-tune --freq 5250 --tx-gain 0 --rate 20 --warmup 60 \
    --rx-subdev A:0 --duration 30 2>&1 | tee /tmp/p113_t6_usrp_uhdtune.log
```

- [ ] **Step 3: Verify API calls executed**

Run: `grep "UHD micro-tunings" /tmp/p113_t6_usrp_uhdtune.log`
Expected: shows "ENABLED (Phase 113 T5.A)" and "applied successfully"

- [ ] **Step 4: Compare metrics to baseline**

| Metric | Task 5 (baseline) | Task 6 (--uhd-tune) | Delta |
|--------|--------------------|-----------------------|-------|
| Sent | (record) | (record) | |
| Recv | (record) | (record) | |
| FCS_OK | (record) | (record) | |
| HT_SIG_PARSE_FAIL | (record) | (record) | |

- [ ] **Step 5: Document result**

If `FCS_OK >= 1` → **T5.A SUCCESS**. Stop here, write verdict.
If `HT_SIG_PARSE_FAIL < baseline` but `FCS_OK=0` → **T5.A PARTIAL**. SNR improved but not enough for viterbi pass. Document and recommend T3.B.
If metrics regressed → **T5.A REFUTED**. Revert (Task 7) and recommend T3.B.
If no change → **T5.A REFUTED**. Revert and recommend T3.B.

---

## Task 7: Conditional revert (only if Task 6 regressed)

**Files:**
- Revert: `test_usrp_minimal_loopback.py:183-200` (Task 2 insertion)

- [ ] **Step 1: Revert Task 2 commit**

```bash
cd /home/hy/gr-ieee802-11
git revert --no-edit HEAD
```

This reverts the UHD API block insertion (Task 2) but keeps Task 1 (--uhd-tune flag, harmless).

- [ ] **Step 2: Verify baseline restored**

Run: `python -c "import ast; ast.parse(open('/home/hy/gr-ieee802-11/test_usrp_minimal_loopback.py').read())"`
Expected: no error

- [ ] **Step 3: Document T5.A REFUTED verdict**

Write to `docs/superpowers/notes/2026-07-08-phase113-t5a-refuted-verdict.md`:
- Task 6 metrics (regression evidence)
- Conclusion: UHD API micro-tuning does not break 1.77 rad floor
- Next: T3.B L-LTF averaging (50-100 line C++ change)

---

## Task 8: Write final verdict

**Files:**
- Create: `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md`

- [ ] **Step 1: Write verdict (PASS / PARTIAL / REFUTED)**

Use template:
```markdown
# Phase 113 T5.A UHD API Micro-Tuning Verdict (2026-07-08)

**Branch**: TEST1
**Status**: 🟢/🟡/🔴 [PASS/PARTIAL/REFUTED]

## TL;DR
[2-3 sentence summary of result]

## Results Table
| Metric | Phase 112 baseline | Phase 113 T5.A |
|--------|---------------------|------------------|
| Sent   | (record)            | (record)         |
| Recv   | (record)            | (record)         |
| FCS_OK | 0                   | (record)         |
| HT_SIG_PARSE_FAIL | (record) | (record)         |

## Conclusion
[Interpretation of results, next step recommendation]

## Files Modified
- `test_usrp_minimal_loopback.py:308-309` — argparse
- `test_usrp_minimal_loopback.py:184-200` — UHD API block

## Related
- design: `docs/superpowers/specs/2026-07-08-phase113-uhd-api-microtuning-design.md`
- Phase 112 verdict: `docs/superpowers/notes/2026-07-08-phase112-t7e-usrp-verification-verdict.md`
- R1 ceiling: 1.77 rad per-SC phase noise
```

- [ ] **Step 2: Commit verdict**

```bash
cd /home/hy/gr-ieee802-11
git add docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md
git commit -m "docs(p113): add T5.A UHD API micro-tuning verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Update memory index

**Files:**
- Modify: `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md`
- Create: `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p113_uhd_api_microtuning.md`

- [ ] **Step 1: Create memory file**

```bash
cat > /home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p113_uhd_api_microtuning.md <<'EOF'
---
name: project-p113-uhd-api-microtuning
description: "Phase 113 T5.A 2026-07-08 — UHD API micro-tuning (set_rx_dc_offset=False, set_rx_iq_balance=False, set_rx_lo_source=internal) to attack 1.77 rad USRP analog chain noise floor. [PASS/PARTIAL/REFUTED]. Verdict: docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md."
metadata:
  type: project
---

# Phase 113 T5.A — UHD API Micro-Tuning (2026-07-08)

**Status**: 🟢/🟡/🔴 [outcome]

## Approach
- 3 low-level UHD API calls on `uhd_usrp_source`:
  - `set_rx_dc_offset(False, 0)` — disable ADC DC offset auto-calibration
  - `set_rx_iq_balance(False, 0)` — disable I/Q imbalance auto-calibration
  - `set_rx_lo_source('internal', 0)` — force internal LO source

## Files Changed
- `test_usrp_minimal_loopback.py:308-309` — argparse flag
- `test_usrp_minimal_loopback.py:184-200` — UHD API block

## Default OFF
- `--uhd-tune` flag required; absent flag = Phase 112 baseline
- No C++ changes, no env var changes

## Verification
- Loopback: baseline preserved
- USRP 5250 cable: [record metrics]

## Related
- [[project-p112-t7e-usrp-verification]] — R1 1.77 rad ceiling
- [[project-p112-r1-argh-rootcause]] — per-SC phase noise floor
- [[feedback-no-closure-usrp-fcs-ok]] — user hard constraint
- Spec: `docs/superpowers/specs/2026-07-08-phase113-uhd-api-microtuning-design.md`
- Verdict: `docs/superpowers/notes/2026-07-08-phase113-uhd-api-microtuning-verdict.md`
EOF
```

- [ ] **Step 2: Add MEMORY.md index entry**

Append single-line entry to `/home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md`:
```
- [Phase 113 T5.A UHD API Micro-Tuning 2026-07-08](project_p113_uhd_api_microtuning.md) — **2026-07-08** — [PASS/PARTIAL/REFUTED]. 3 UHD API calls (DC=off, IQ=off, LO=internal) to attack 1.77 rad floor. [outcome sentence].
```

- [ ] **Step 3: Verify index**

Run: `grep "Phase 113" /home/hy/.claude/projects/-home-hy-gr-ieee802-11/memory/MEMORY.md`
Expected: new line appears

---

## Self-Review Checklist

- [x] Spec coverage: every requirement in spec has a task
  - Argparse flag → Task 1
  - UHD API block → Task 2
  - Loopback verification → Tasks 3-4
  - USRP baseline + experiment → Tasks 5-6
  - Conditional revert → Task 7
  - Verdict writing → Task 8
  - Memory update → Task 9
- [x] No placeholders (TBD/TODO/"implement later")
- [x] Type/name consistency: `args.uhd_tune` (snake_case) used everywhere; `set_rx_dc_offset`, `set_rx_iq_balance`, `set_rx_lo_source` UHD API names spelled consistently
- [x] All file paths absolute
- [x] All commands complete with expected output
- [x] DRY: same code blocks not repeated; new UHD block shown once
- [x] YAGNI: no extra features beyond spec
- [x] TDD: tests (Tasks 3-6) come after code (Tasks 1-2), frequent commits per task