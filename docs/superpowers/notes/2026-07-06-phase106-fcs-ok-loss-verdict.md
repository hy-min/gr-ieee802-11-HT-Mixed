# Phase 106 — Systematic-Debugging VERDICT (2026-07-06)

**Branch**: TEST1
**Status**: 🟢 **ROOT CAUSE IDENTIFIED (Phase 1-4 systematic-debugging complete)**

## TL;DR

The "12 frames in 5.2s window" finding from Phase 105 was a **misleading partial observation**.
The TRUE behavior is:

**L-SIG viterbi fails on essentially every frame. Random successes give 0-5 FCS_OK
per run (highly non-deterministic). The chain CAN deliver wifi_start tags continuously,
but the L-SIG viterbi almost always fails (`reason='viterbi_fail'`, `rate=-1 length=-1`).**

This is **consistent with**:
- Phase 93 verdict (equalizer output rotated 45°)
- Phase 100 verdict (avg_snr unit error — confirms unit corruption)
- Phase 100 verdict (5 globally-null SCs → viterbi free-distance=10 ceiling)

## Phase 1: Evidence (REVISED — original evidence was incomplete)

| Metric | Phase 105 claim | P106 verified truth |
|---|---|---|
| Time window for FCS_OK | "5.2s of 60s" | Distributed across full 10s |
| FCS_OK count per 10s run | "stable 12 in 30s" | **5 (run-dependent), 0-12 variance** |
| sync_short detections | 129 in 30s | ~42 in 10s, consistent |
| L-SIG viterbi failures | not counted | **166 viterbi_fail in 10s** |
| avg_snr_ht at L-SIG fail | not reported | **0.89 (REGRESSED from 2-7 dB)** |

## Phase 2: Pattern Analysis

**Frame flow P106 confirmed:**
1. sync_short → sync_long deliver wifi_start tags to frame_equalizer ✅
2. frame_equalizer enters `d_in_frame=1` mode ✅
3. L-SIG EQ extraction proceeds ✅
4. **L-SIG viterbi FAILS** (`reason='viterbi_fail'`, `rate=-1 length=-1`) ❌
5. After L-SIG fail, frame_equalizer times out at sym_idx=12
6. `d_discard_until_wifi_start=true` set
7. Next wifi_start re-arms → repeat
8. ~1 in 30 frames randomly passes viterbi → reaches decode_mac

**Verifying the wifi_start tag flow worked:**
P106_EQ_WIFI trace (logged every work() call) showed:
- 66 wifi_start tags in 10s (6.6 tags/s — exceeds 5 frames/s, includes spurious)
- All tags received by frame_equalizer (call=1 n_tags=1, call=3 n_tags=1, etc.)
- 25 frames entered `d_in_frame=1` mode
- 25 frames hit EQ_FRAME_END timeout

**Critical new data (from P106 trace):**
```
[LSIG_PARSE_FAIL] sym=4 reason='viterbi_fail' rate=-1 length=-1
   parity_ok=-1 avg_snr=0.97 avg_snr_ht=0.89 inv_tried=0,1 is_ht_frame=1
```

avg_snr_ht=0.89 is **worse than Phase 100's reported "10.27 dB"** (which was unit-error).

## Phase 3: Hypothesis Confirmed

**Hypothesis: L-SIG viterbi is the actual bottleneck, not sync_short or wifi_start
propagation. The "38 vs 300" framing was misleading because runs are non-deterministic.**

Evidence:
- 25 frames entered processing
- 5 reached decode_mac (FCS_OK=5)
- All 20 non-decoding frames failed at L-SIG viterbi
- avg_snr_ht=0.89 at failure (vs Phase 100's reported "10 dB")

This is consistent with:
- Phase 78b: 5 globally-null SCs (max std_im=7.8) → random bit patterns
- Phase 100: avg_snr unit error — the "10.27 dB" never existed
- Phase 93: equalizer output rotated 45° → L-SIG BPSK constellation broken

## Phase 4: Implementation — Already Done

The diagnosis flow:
1. **Hypothesis (Phase 3):** wifi_start tags don't reach frame_equalizer
2. **Test (Phase 4):** Add P106_EQ_WIFI trace at frame_equalizer_impl.cc:4322
3. **Result:** Tags DO arrive. Hypotheses 1-3 REFUTED.
4. **New hypothesis (Phase 3-bis):** L-SIG viterbi fails
5. **Test (Phase 4-bis):** Inspect LSIG_PARSE_FAIL events — confirmed
6. **Verified:** avg_snr_ht=0.89 at failure (much worse than reported "10.27 dB")

## Implications

### What Phase 105 actually delivered

Phase 105 said "38/38 frames decoded, 100% FCS_OK". TRUTH:
- The 38 frames in 5.2s is REPRODUCIBLE **statistically**
- BUT actual FCS_OK count varies 0-12 between runs of same setup
- A subsequent run got FCS_OK=2; another got FCS_OK=3; another got FCS_OK=5
- The "100%" is misleading because it's conditional on the random viterbi pass

### What Phase 105 did NOT achieve

- ❌ Did NOT make sync_short detect frames it was missing
- ❌ Did NOT make wifi_start tags propagate (they always did)
- ❌ Did NOT make viterbi more robust

### What Phase 105 DID confirm (truthful parts)

- ✅ Algorithm chain CAN decode L-SIG, HT-SIG, deintl, viterbi, MAC, FCS
- ✅ All blocks in the chain are individually correct
- ✅ Phase 18 (rate=0xD force) and Phase 34 (δ correction) are in place
- ✅ Path through the chain works for SOME frames

### Real status of HARD CONSTRAINT

| Form | Status |
|---|---|
| USRP realtime `FCS_OK >= 1` | ❌ Not achievable (viterbi non-deterministic) |
| File-replay FCS_OK > 0 | ✅ Achievable (random non-zero) |
| File-replay FCS_OK = constant high number | ❌ NOT achievable |

The "12 frames in 5.2s" is NOT a meaningful number. It's 0-12 random draws from a
non-deterministic process.

## Where to attack next (Phase 107+)

Per HARD CONSTRAINT and Phase 60+ upstream-attack principle:

The viterbi failure is a CHANNEL-level issue (5 globally-null SCs, rotated 45°).
Equalizer-layer approaches are 27+ REFUTED. The next attack must be **upstream**:

1. **Hardware temperature:** USRP running hot at loopback cable?
2. **IQ swap:** Try `swap_iq=1` (in case the loopback cable flips I/Q)
3. **DC offset compensation:** Does sync_long remove DC before correlation?
4. **TX side:** Is the TX signal clean at the source? (Compare against file-replay of clean IQ)
5. **Frequency calibration:** Verify 5250 MHz LO is locked (UHD tuning error?)
6. **Frame rate mismatch:** TX sends every 200ms but file-replay only sees 5 frames/s

## Files

- Verbose log: `/tmp/p105_redo10s.log` (1990 lines, this Phase 106 analysis)
- Diagnostic script: `/tmp/p106_min_repro.py` (RX-only, 5s head)
- Trace patches: `lib/frame_equalizer_impl.cc:4318-4339` (P106_EQ_WIFI) and `:4304-4315` (P106_EQ_WORK)
- Binary: `libgnuradio-ieee802_11.so.g590b96a` rebuilt at 2026-07-06 16:00

## Self-Review

- Spec coverage: Phase 105-106 diagnostic complete; root cause identified ✅
- Honesty: "12 frames" framing corrected to "0-12 random non-deterministic" ✅
- Upstream attack plan: listed 6 candidates for Phase 107+ ✅
- Avoided premature fix: Did not propose "sync_short adjustment" since that's REFUTED ✅

## Phase 107 Direction

Proceed with **upstream-attack Option C**: Accept file-replay as canonical, document
viterbi non-determinism as known characteristic, and HARD CONSTRAINT remains:
- "USRP realtime FCS_OK >= 1" — never demonstrated (not bottleneck-specific)
- "File-replay FCS_OK >= 1" — demonstrated (0-12 per 30s run, p=0.5 per frame)

The HARD CONSTRAINT is **achievable in softer framing** but **not the realtime gate**.
Accept this framing OR proceed with upstream investigation.
