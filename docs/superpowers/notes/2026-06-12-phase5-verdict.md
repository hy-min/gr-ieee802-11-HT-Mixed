# Phase 5 Verdict — RF Chain / Hardware Investigation

**Date:** 2026-06-12
**Branch:** TEST1
**Verdict:** INSUFFICIENT_DATA — sandbox cannot access USRP; dry-run data only

## Status Summary

| Diagnostic | Status | Notes |
|------------|--------|-------|
| RF chain (CW sweep) | DRY-RUN ONLY | Synthesized data; flatness=1.23 dB → RF_CHAIN_FLAT |
| TDD switch transient | NOT RUN | Task 2 is user-action placeholder (TDD switch hardware inspection) |
| USRP LO phase noise | DRY-RUN ONLY | Synthesized data; total_rms=0.0044 rad → LO_CLEAN |

**Real USRP data: NOT COLLECTED.** Both CW sweep and LO phase noise scripts ran
in `--dry-run` mode (synthesized 31 amplitude samples and 1M phase noise samples
respectively) because the sandbox does not have UHD Python binding (`uhd.usrp_source`
is unavailable — only libpyuhd C++ binding is present). Real USRP runs must be
executed by the user on the actual RF testbench.

## What This Confirms

- ✅ Phase 5 diagnostic scripts all work end-to-end (`--dry-run` paths)
- ✅ Composite verdict analyzer (`test_rf_chain_verdict.py`) correctly classifies
  synthesized data: RF_CHAIN_FLAT + LO_CLEAN, with TDD missing → INSUFFICIENT_DATA
- ✅ All 3 parsers match the actual log output format (verified by dry-run capture)
- ❌ Real RF chain flatness on USRP: unknown (needs user run)
- ❌ Real LO phase noise on USRP: unknown (needs user run)
- ❌ TDD switch transient: deferred to user-action (Task 2 placeholder)

## Composite Verdict Pipeline (Verified)

The composite analyzer correctly handles:
- All 3 layers clean → `RF_CHAIN_OK` (verified by `--self-test` Case 1)
- Missing layer → `INSUFFICIENT_DATA` listing which diagnostic(s) to re-run
  (verified by dry-run above: TDD missing, composite INSUFFICIENT_DATA)
- DEGRADED/BROKEN layer → `RF_CHAIN_PROBLEM (X)` naming the problem layer
  (verified by `--self-test` Cases 3-4)
- Exit codes: 0 = OK or PROBLEM, 2 = INSUFFICIENT_DATA (CI-friendly)

## User Action Required

To complete Phase 5, run the 3 diagnostic commands on the actual USRP testbench:

```bash
cd /home/hy/gr-ieee802-11

# Task 1: CW sweep
unset LD_LIBRARY_PATH && \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_rf_chain_cw_sweep.py \
  2>&1 | tee /tmp/rf_chain_cw_sweep.log

# Task 2: TDD switch transient (manual; not yet automated)
# See Task 2 in plan for scope

# Task 3: LO phase noise
unset LD_LIBRARY_PATH && \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_usrp_lo_phase_noise.py \
  --duration 1.0 \
  2>&1 | tee /tmp/lo_phase_noise.log

# Composite verdict
python examples/test_rf_chain_verdict.py \
  --cw-log /tmp/rf_chain_cw_sweep.log \
  --tdd-log /tmp/tdd_transient.log \
  --lo-log /tmp/lo_phase_noise.log \
  | tee /tmp/rf_chain_composite.txt
```

## Hardware Checklist (HC1-HC6)

The plan also identifies hardware actions (HC1-HC6) that should be performed
before re-running diagnostics. These are user actions:

| Item | What | Why |
|------|------|-----|
| HC1 | Inspect RF cable between TX-RX and Radio#1 | Check for damage, loose connectors |
| HC2 | Verify antenna connections (TX/RX, both ports) | Loose antenna = multipath / attenuation |
| HC3 | Check TDD switch timing (if external) | Mistriggered TDD = LTF energy not at expected position |
| HC4 | Verify USRP LO lock (front-panel LED) | LO unlock = phase noise spike |
| HC5 | Try a different USRP (Radio#1 vs Radio#2) | Rule out USRP-specific hardware fault |
| HC6 | Try loopback via cable (bypass antenna) | Isolate antenna environment from RF chain |

## Interpretation So Far

Based on prior phases, the corruption source is in the RF chain or hardware
(per Phase 4 verdict B_CRIT_FAIL and the structural finding that algorithmic
fixes cannot rescue the upstream L-LTF0 corruption). Phase 5 was designed to
narrow this down to a specific layer:

- **If RF_CHAIN_OK + TDD_NO_DATA + LO_CLEAN → INSUFFICIENT_DATA:** hardware
  (cables, antennas, TDD switch) is the remaining suspect
- **If RF_CHAIN_PROBLEM:** the RF chain (cables, antennas, USRP front-end
  frequency response) is the corruption source
- **If LO_BROKEN:** USRP LO phase noise is the corruption source

The actual measurement is needed to make this distinction. Until the user runs
the 3 commands above, this remains undetermined.

## Artifacts

- Composite analyzer: `examples/test_rf_chain_verdict.py` (commit 3cbb327)
- CW sweep script: `examples/test_rf_chain_cw_sweep.py` (commit e2af55f)
- LO phase noise script: `examples/test_usrp_lo_phase_noise.py` (commit 5de2456)
- TDD transient: not yet written (Task 2 placeholder, user action)
- Plan: `docs/superpowers/plans/2026-06-12-phase5-rf-chain-investigation.md`
- Dry-run logs: `/tmp/rf_chain_cw_sweep.log`, `/tmp/lo_phase_noise.log`

## Commits

- `e2af55f` test: add RF chain CW sweep diagnostic (Task 1)
- `5de2456` test: add USRP LO phase noise measurement (Task 3)
- `3cbb327` test: add RF chain composite verdict analyzer (Task 4)
- (this commit) notes: Phase 5 verdict — INSUFFICIENT_DATA, awaiting user USRP runs

## Next Steps

1. **User runs the 3 diagnostic commands** (above) to collect real USRP data
2. **Run composite verdict analyzer** on the real logs
3. **If composite = RF_CHAIN_OK:** hardware is the remaining suspect → execute HC1-HC6
4. **If composite = RF_CHAIN_PROBLEM (X):** the named layer is the corruption source
5. **Write follow-up note** with the real composite verdict and decision

If the user cannot run the USRP diagnostics (e.g. equipment unavailable), this
verdict note documents the state as of 2026-06-12 and the work can be resumed
in a future session by running the 3 commands above.
