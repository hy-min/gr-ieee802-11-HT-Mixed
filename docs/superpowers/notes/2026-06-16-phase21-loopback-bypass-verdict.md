# Phase 21 — Software Loopback Energy-Gate Bypass (2026-06-16)

## TL;DR

Phase 21 (5 tasks) added `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1` env-var bypass for `sync_short_fused`'s energy gate. **The original hypothesis (energy gate mis-fires in loopback) was REFUTED.** The actual root cause of the loopback regression is a metadata bug in `decode_mac.cc:1182-1183` where `crc=1` is NOT set on the FCS-OK publish path, causing FcsLogger to mis-count successful frames as FAILs. The env-var bypass is a defensive workaround for the per-batch energy gate design (which IS still flawed in frames-only scenarios) but does NOT fix the loopback regression itself. **Phase 22 will fix the actual `decode_mac.cc` metadata bug.**

## Test Results

| Run | Test              | Env Var         | `*** FCS OK ***` | HT_SIG_PARSE_FAIL | Notes                          |
|-----|-------------------|-----------------|------------------|-------------------|--------------------------------|
| 1   | Software loopback | (none)          | 0                | n/a               | regression CONFIRMED           |
| 2   | Software loopback | BYPASS=1        | 0                | n/a               | env-var bypass active (Task 3) |
| 3   | USRP 5 GHz A:0    | (none)          | 0                | 80                | no regression                  |

**Critical Task 3 finding**: With env var set, the energy gate IS bypassed (state=0 dominates >99.9% of calls), but `*** FCS OK ***` count remains 0 — same as baseline. The actual frames are decoded successfully (`[DECODE_SUCCESS] Conv FCS OK, publishing message len=48` events fire), but FcsLogger doesn't increment OK counter because `decode_mac.cc:1182-1183` does not set `crc=1` in the PMT metadata on the FCS-OK publish path. This is the `crc` field historical bug noted in CLAUDE.md MEMORY.md.

## Why the original hypothesis was wrong

Phase 19 Task 6 attributed the loopback regression to `sync_short_fused`'s energy gate. Phase 21 Task 1 confirmed `FCS OK = 0` in loopback (the regression symptom), but Task 3's deeper analysis revealed:
- `state=0` (no gating) dominates >99.9% of sync_short calls in BOTH with/without env var
- The energy gate is NOT firing — frames ARE flowing through sync_short
- `DECODE_SUCCESS` events fire 1x per 30s in both runs (real frame decode success)
- FcsLogger fails to count these as OK due to metadata bug

## What was actually fixed in Phase 21

The env-var bypass IS verified functional:
- `getenv("IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE")` correctly read at `lib/sync_short_fused.cc:27-30`
- When env set: `d_energy_gate_factor = 0.0f`, gate check at line 64 skipped
- When env NOT set: default behavior preserved (factor=3.0)
- Both unit tests pass: `test_sync_short_fused_vs_reference` and `test_energy_gating`
- USRP run with env NOT set shows no regression (chain runs, baseline behavior preserved)

The bypass is a **defensive measure** for the per-batch energy gate design issue (which IS real in frames-only inputs), but it does NOT unblock loopback regression testing as originally intended.

## Phase 22: actual loopback regression fix

The real bug is in `decode_mac.cc:1182-1183`: when FCS is OK, the metadata dict adds `dlt` but NOT `crc=1`. FcsLogger at `examples/test_direct_loopback.py:28` reads `crc` field (default 0) and counts frame as FAIL.

Proposed Phase 22 fix: in `decode_mac.cc` FCS-OK publish path, add `crc=1` to the PMT metadata dict. Estimated: 1-2 lines, single commit.

## Code Changes

- Commit `0a21825`: feat(phase21-task2): IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE env-var

## How to use

```bash
# Software loopback regression test (env var bypasses energy gate):
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1 \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python examples/test_direct_loopback.py

# USRP production: env var NOT set, default gating active
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python test_usrp_minimal_loopback.py --duration 30
```

## Related memory

- [[project_p19_htsig_viterbi]] — Phase 19: original loopback regression discovery (wrong attribution)
- [[project_p20_htsig1_per_sc_phase]] — Phase 20: prior CPE-style attempt REFUTED
- [[project_p17_5ghz_a0_subdev]] — Phase 17: 5 GHz A:0 subdev isolation
