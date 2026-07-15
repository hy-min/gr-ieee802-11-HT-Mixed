# Phase 147 Verdict: Realtime Segfault Heisenbug — ROOT-CAUSED + FIXED

**Date:** 2026-07-15
**Branch:** TEST1
**Status:** ✅ **FIXED + VALIDATED on USRP realtime.**
**Method:** superpowers:systematic-debugging (Iron Law: root cause before fix) +
AddressSanitizer + TDD (failing repro first).

---

## Goal

Phase 146 achieved the first USRP realtime FCS_OK but only "FCS_OK=2 in 30s".
Determine why the realtime FCS rate was so low, and fix it.

## Journey (funnel → reframe → root cause)

1. **Offline funnel of `/tmp/p146_rxonly_cap.fc32`** (30s, 5250 cable, the Phase
   146 capture) proved the **RX decoder is correct and effective**: 26 frames
   reach decode_mac → 21 FCS_OK (81% on decode_mac'd frames, all len=66). The
   decoder was NOT the bottleneck.
2. **Hardware hardening revealed an intermittent segfault** (~50% per ~12s run)
   that killed realtime runs early. Combined with message-queue undercounting,
   this — not the decoder — explained the low reported FCS.
3. **Root-cause hunt** (systematic-debugging): the crash was a Heisenbug
   (vanished under gdb AND libSegFault). A gdb long-run backtrace pointed at TX
   `mac_impl::app_in`, but that was a *victim* of heap corruption, not the cause.
4. **ASan offline replay was clean** (600M samples, zero OOB) → the OOB is
   realtime-specific, not data-content-triggered.
5. **ASan realtime pinpointed the true source.**

## ROOT CAUSE (ASan-pinpointed)

**`static float sorted_buf[4096]` in `lib/sync_short.cc:124`** (the
adaptive-threshold p90 recompute in `general_work`) was a function-local
**`static` shared across ALL sync_short instances**. A realtime transceiver has
**TWO sync_short instances**:
- `wifi_phy_hier.py:91` (TX hier's RX path), and
- the RX-only chain's `sync_short`,

running on **separate GNU Radio threads**. Their **concurrent
`memcpy`/`std::sort` on the shared buffer raced** → `std::sort` walked
out-of-bounds → **SIGSEGV**.

ASan realtime report:
```
ERROR: AddressSanitizer: SEGV on unknown address
  #5 std::sort<float*>                       stl_algo.h:4772
  #6 sync_short_impl::general_work           lib/sync_short.cc:126
```

### Why it was a Heisenbug
- **Offline replay never crashed** → it has only ONE sync_short instance (no
  second instance to race). Realtime has two.
- **Vanished under gdb / libSegFault** → the race is masked by slowed,
  serialized execution.
- The earlier `mac_impl::app_in` backtrace was a *different crash site* — heap
  corruption can manifest anywhere depending on heap layout; ASan catches the
  source.

## FIX (minimal, single-variable)

`lib/sync_short.cc:124` — removed `static`, making `sorted_buf` a
**stack-private buffer** (fully initialized by the following `memcpy`). Each
thread/instance gets its own → race eliminated. Functionally identical output.

```cpp
// before:  static float sorted_buf[4096];   // shared across instances -> race
// after:   float sorted_buf[4096];          // stack-private -> thread/instance safe
```

## Validation

| Stage | Result |
|---|---|
| **TDD red** | `p147_race_repro.py` (two sync_short on noise sources) crashed with buggy code — ASan reports the same `sync_short.cc:126` sort SEGV (hardware-free) |
| **TDD green** | after fix, **5/5 trials no crash** |
| **USRP realtime** | EXIT=0, **45s sustained, 3 windows FCS_OK=16/14/13 (PDU) / ground-truth DECODE_SUCCESS=46, DECODE_FAIL=26** — no segfault |
| **Message-queue** | PDU(43) vs ground-truth(46) ≈ no more undercount |
| **Regression** | file replay still decodes (FCS_OK=12); full-lib scan shows no other function-local static arrays |

## Impact (reframes the project)

- **Before:** Phase 146 "FCS_OK=2 in 30s"; runs crashed ~50%/12s.
- **After:** **46 FCS_OK in 45s, stable ~1/s, zero crashes.**
- **The decoder + realtime pipeline WORKS.** The "low FCS" was this segfault +
  message-queue undercount — NOT the decoder and NOT the 1.77 rad noise wall.
- Per-frame FCS on decode_mac'd frames ~64%. The remaining gap (frames not
  reaching decode_mac, ~10% overall) is the *actual* remaining attack surface —
  now unblocked for sustained realtime equalizer/noise work.

## Environment gotchas discovered (for future phases)

- 32 cores; CPU governor = **powersave** (memory says performance essential;
  can't change — sudo needs password). 
- gr-uhd usrp_sink/source have **NO `async` message port** (only
  `system`,`command`) → cannot capture underflow/overflow programmatically; must
  grep UHD stderr.
- Capture |r| max = 26% of ADC range → **NO RX clipping** (tx-gain 0, rx_scale 40).
- USRP RFNoC degrades after crashed runs ("blocks timed out during flush",
  intermittent `Failure to create rfnoc_graph`). Recover via `uhd_usrp_probe`
  nudge; device = NEW X310 serial 36C26DB, FPGA 39.3, FW 6.1.

## Harnesses / artifacts

- `p147_race_repro.py` — synthetic 2-instance race repro (regression test).
- `test_usrp_rxonly_instrumented.py` — build-once + `gain.set_k()` rx_scale
  sweep + windowed PDU counting + est_sent. Avoids USRP re-init segfault.
- `p147_replay_funnel.py` — offline RX-only replay of a capture → funnel counts.
- ASan flow: `cmake -DCMAKE_CXX_FLAGS="-fsanitize=address -g -fno-omit-frame-pointer"`,
  `make && make install`, run with `LD_PRELOAD=<conda>/lib/libasan.so`.
  Restore working .so from `/tmp/libgnuradio-ieee802_11.so.g50e6907.bak` or
  rebuild without the flag.

## Code changes

- `lib/sync_short.cc` — static → stack-private `sorted_buf` (the fix).
- New harnesses (above). Backup working .so at `/tmp/...g50e6907.bak`.

## Next directions (unblocked now)

- Improve the **decode rate** (frames reaching decode_mac): the real remaining
  attack surface. Equalizer/noise work (2-way H52 baseline from Phase 139,
  Wiener, cross-frame) can now be evaluated in *sustained* realtime.
- Consider CPU governor = performance (needs sudo) to reduce TX underflow.
- `test_usrp_rxonly_realtime.py` (Phase 146) benefits automatically from this fix.

## Related

- Phase 146: `docs/superpowers/notes/2026-07-15-phase146-scheduler-stall-rootcause.md`
- Phase 145c: `docs/superpowers/notes/2026-07-14-phase145c-file-replay-breakthrough.md`
- User goal: USRP realtime FCS_OK (feedback_no_closure_usrp_fcs_ok)
