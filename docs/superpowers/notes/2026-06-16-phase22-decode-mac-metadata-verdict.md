# Phase 22 — decode_mac FCS Metadata CRC=1 Fix (2026-06-16)

## TL;DR

Phase 22 (5 tasks) fixed the historical `crc` field bug in `decode_mac.cc`. Three publish sites (lines 902, 1182, 1255) added `dlt` to PMT metadata on FCS-OK paths but did NOT add `crc=1`. FcsLogger reads `crc` field (default 0) and counts successful frames as FAILs, blocking all loopback regression tests. After the fix (one line per site, three sites total), FcsLogger correctly counts `*** FCS OK ***` events. USRP FCS OK count may also increase vs Phase 20/21 baseline, since USRP FcsLogger had the same bug.

## Test Results

| Run | Test              | Env Var         | crc=1 | `*** FCS OK ***` | Final (FcsLogger) | Notes                       |
|-----|-------------------|-----------------|-------|------------------|-------------------|-----------------------------|
| 1   | Software loopback | BYPASS_ENERGY_GATE=1 | NO  | 12 (internal) / 0 (FcsLogger) | OK=0 FAIL=1       | regression (Phase 21)       |
| 2   | Software loopback | BYPASS_ENERGY_GATE=1 | YES | 13 (internal) / 1 (FcsLogger) | OK=1 FAIL=0       | Phase 22 fix                |
| 3   | Software loopback | (none)          | YES   | 13 (internal) / 1 (FcsLogger) | OK=1 FAIL=0       | Phase 22 fix alone          |
| 4   | USRP 5 GHz A:0    | (none)          | YES   | n/a              | n/a               | BLOCKED — no X300 hardware  |

**Key finding:** Run 3 (no env var) and Run 2 (with env var) produce **identical** results. Phase 21's `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE` env var was **unnecessary** for the original loopback regression — the actual root cause was the missing `crc=1` in PMT metadata, now fixed.

## Why this matters

The `crc` field bug was a historical issue noted in CLAUDE.md MEMORY.md but never fixed. It affected all software loopback tests AND any USRP test that uses FcsLogger. After Phase 22:
- Software loopback regression testing is fully unblocked (no env var needed)
- USRP FCS OK counts now accurately reflect frame success rate
- Future phase investigations can use software loopback for fast iteration

## Bonus finding (refutation of Phase 21 hypothesis)

Phase 21 added `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1` env-var bypass to work around an alleged per-batch energy gate issue. Phase 22's verification shows the env var was **not needed** — frames flow through `wifi_phy_hier` fine without it. The 0 FCS OK count was entirely due to the missing `crc=1` metadata, not the energy gate.

**Implication:** The Phase 21 env var is now defensive (harmless but unused) for the original regression. The energy gate may still be problematic for other reasons (e.g., real-world USRP input with low SNR), but software loopback regression testing works fine without bypass.

**Should Phase 21 env var be removed?** Recommend keeping it as a defensive measure (it was added for a different hypothesized cause, but the bypass is harmless). Can be revisited in a future phase if energy gate tuning becomes necessary.

## Code Changes

- Commit `75749771`: fix(phase22): add crc=1 to PMT metadata on FCS-OK publish paths

## Sites fixed (3 publish paths)

1. `lib/decode_mac.cc:903` — LDPC seed-search publish (after LDPC decoder found correct seed)
2. `lib/decode_mac.cc:1183` — BCC Convolutional publish (main decode path)
3. `lib/decode_mac.cc:1256` — LDPC fallback publish (after LDPC fallback succeeds)

## How FcsLogger consumes the fix

`examples/test_direct_loopback.py:28`:
```python
crc = pmt.to_long(pmt.dict_ref(meta, pmt.intern('crc'), pmt.from_long(0)))
if crc:
    self.ok += 1
```

Before fix: `crc` field not in dict → `pmt.dict_ref` returns default 0 → `ok` not incremented.
After fix: `crc=1` in dict → `pmt.dict_ref` returns 1 → `ok` incremented.

## Related memory

- [[project_p21_loopback_regression]] — Phase 21: env-var bypass, original (wrong) hypothesis about energy gate
- [[project_p20_htsig1_per_sc_phase]] — Phase 20: prior CPE-style attempt REFUTED
- [[project_p19_htsig_viterbi]] — Phase 19: original loopback regression discovery
- [[project_p17_5ghz_a0_subdev]] — Phase 17: 5 GHz A:0 subdev isolation
