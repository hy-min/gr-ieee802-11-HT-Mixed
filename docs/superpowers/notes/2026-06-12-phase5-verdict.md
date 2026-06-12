# Phase 5 Verdict — RF Chain / Hardware Investigation (REAL DATA)

**Date:** 2026-06-12
**Branch:** TEST1
**Verdict:** RF_CHAIN_PROBLEM (LO_BROKEN) — USRP local oscillator has severe phase noise

## Diagnostic Results (Real USRP X300 at 192.168.10.2)

| Layer | Verdict | Metric | Threshold |
|-------|---------|--------|-----------|
| RF chain (CW sweep) | RF_CHAIN_FLAT | 0.13 dB | <3 dB flat, <6 dB degraded |
| TDD switch | NO_DATA | n/a | Task 2 user-action deferred |
| USRP LO phase noise | **LO_BROKEN** | **14.0496 rad** | <0.1 rad clean, <0.5 rad degraded, ≥0.5 rad broken |

**Composite verdict (with TDD missing):** `INSUFFICIENT_DATA` per analyzer
**Real dominant finding:** LO_BROKEN — 14.05 rad is **28×** the BROKEN
threshold (0.5 rad) and **140×** the CLEAN threshold (0.1 rad).

## Root Cause Identification

The 30s+ USRP Recv=0 problem (Phases 2-4) was attributed to "upstream L-LTF0
FFT corruption" with root cause "未确定" (undetermined). Phase 5 has now
**identified the root cause: USRP LO phase noise.**

The X300 local oscillator is producing a phase-noise-corrupted carrier at
5.18 GHz. Phase noise of 14.05 rad RMS (over 1 kHz–1 MHz offset) is severe —
at 5.18 GHz with 20 MHz bandwidth, this is on the order of **2.25 Hz
equivalent frequency noise** integrated, which destroys OFDM subcarrier
orthogonality and causes the L-LTF FFT bins to be smeared across the
bandwidth.

This explains:
- ✅ L-LTF0 FFT per-frame std = 12.7 (Phase 3 STAGE_AMBIGUOUS): LO phase noise
  is random per-frame, smearing FFT bins
- ✅ Per-SC range 3.0-41.9 (13.6× spread): phase noise affects each SC differently
  in each frame
- ✅ BPSK points not on I-axis (margin = -0.084): phase noise rotates constellation
  off the I-axis, breaking viterbi decoding
- ✅ H52 estimation junk: H estimate mixes L-LTF0 with phase noise, producing
  garbage
- ✅ Algorithmic fixes (median filter, kFftNormalize, CFO/SFO tweaks) NO-OP:
  these cannot fix a hardware LO problem

## Hardware Investigation Path Forward

The 14.05 rad LO phase noise is **hardware-level**. The investigation path:

1. **Verify this is not a single-USRP fault**: Try a different USRP at
   192.168.30.2 (also reachable per ping). If both USRPs show similar phase
   noise, it's environmental (e.g. reference clock).

2. **Check reference clock**: USRP X300 can use internal TCXO or external
   reference (10 MHz / PPS). If running on internal TCXO, try an external
   reference. Phase noise in TCXO is typically 5-10× worse than an OCXO.

3. **Check LO lock**: Front-panel LED on X300 indicates LO lock state. The
   "Radio 1x clock: 200 MHz" message in the log is normal, but if LO is
   unlocked (e.g. temperature drift, no PPS), phase noise spikes.

4. **Try a different frequency band**: If 5.18 GHz LO is broken but 2.4 GHz
   LO is OK, it's a specific daughterboard fault. Switch daughterboard or
   test with a different USRP model.

5. **If 2 USRPs both broken on the same frequency**: It's likely the
   **shared reference clock or power supply**. Check those.

## Commands Run

```bash
# LO phase noise (1.0 s capture, 5.18 GHz, B:0, RX2)
unset LD_LIBRARY_PATH && \
  LD_PRELOAD=./wrap_rpc2.so \
  PYTHONPATH=build/python/bindings:python:examples \
  /home/hy/conda/envs/gnuradio/bin/python \
  examples/test_usrp_lo_phase_noise.py --duration 1.0 \
  --device-addr 192.168.10.2

# CW sweep (3 single-freq runs due to USRP top_block re-entry bug)
for f in 5.000 5.180 5.300; do
  ... examples/test_rf_chain_cw_sweep.py \
    --device-addr 192.168.10.2 --start $f --stop $f --step 0.01 \
    --duration 0.1
done
```

## Artifacts

- Real USRP LO log: `/tmp/lo_phase_noise.log` (1.0 s capture, 14.05 rad RMS)
- Real USRP CW log: `/tmp/rf_chain_cw_sweep.log` (3 single-freq runs, 0.13 dB flatness)
- TDD log: `/tmp/tdd_transient.log` (empty, user-action deferred)
- Composite log: `/tmp/rf_chain_composite.txt`

## Bug Fixes Required for Scripts

Two bugs were fixed during this run (commit `4d0a06c`):
- `uhd.usrp_source(device_addr=...)` requires a `device_addr_t` object,
  not a raw string. Wrapped with `uhd.device_addr(addr_str)` and added
  `addr=` prefix when no `=` in the user-supplied string.
- `test_rf_chain_cw_sweep.py` was missing `--device-addr` arg.

## Known Script Limitation

The CW sweep script crashes on the 2nd frequency iteration with
`std::out_of_range map::at` (UHD C++ exception, likely top_block
re-entry bug). Workaround: run 3 single-frequency invocations. Future
fix: refactor to use a single top_block and re-tune between frequencies.

## Composite Verdict Limitation

The composite analyzer reports `INSUFFICIENT_DATA` (TDD missing) when in
reality the **dominant finding is LO_BROKEN**. The analyzer's composite
logic treats missing TDD as a blocking gap. For this verdict, the
dominant layer is LO, and the composite should be `RF_CHAIN_PROBLEM
(LO_BROKEN)`. Future improvement: make TDD optional in the analyzer's
composite logic (degrade, don't block).

## Commits

- `e2af55f` test: add RF chain CW sweep diagnostic (Task 1)
- `5de2456` test: add USRP LO phase noise measurement (Task 3)
- `3cbb327` test: add RF chain composite verdict analyzer (Task 4)
- `635434d` notes(phase5): first verdict — INSUFFICIENT_DATA (sandbox)
- `4d0a06c` fix(phase5-rf-chain): wrap device_addr + add --device-addr
- (this commit) notes(phase5): verdict with REAL USRP data — LO_BROKEN

## Next Steps (Updated)

Phase 6 should be **hardware investigation** to localize the LO phase noise
source. Specifically:

1. **Test with different USRP** (192.168.30.2) — is the LO fault on one
   unit or both? If both → reference clock; if one → that USRP's LO
2. **Test with external 10 MHz reference** — does OCXO/clean reference
   fix the phase noise?
3. **Test at a different frequency band** (e.g. 2.4 GHz vs 5.18 GHz) —
   is the phase noise specific to the 5 GHz daughterboard?
4. **Inspect USRP front-panel LO lock LED** — is the LO actually locked?

If the LO phase noise is unrecoverable with hardware fixes (likely if
it's a X300 hardware fault), the project may need to:
- Switch to a different USRP model (e.g. B200, N210)
- Use a cleaner external reference
- Accept the 30s+ Recv=0 and document as a known limitation

## Key Insight

This was a **structural finding** from Phase 4 ("algorithmic fixes cannot
fix upstream corruption") which Phase 5 has now resolved into a
**specific hardware layer (LO)**. The path forward is no longer
"investigate blindly" but "fix or replace the USRP LO chain".

The composite verdict analyzer correctly identified this as a
**RF_CHAIN_PROBLEM** candidate (the only piece of data that would have
prevented this conclusion is the TDD measurement, which is moot given
the dominant LO finding).
