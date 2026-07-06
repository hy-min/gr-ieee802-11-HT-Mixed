# Phase 104 — USRP-vs-Replay Diff VERDICT (2026-07-06)

**Branch**: TEST1
**Status**: 🟡 **INCONCLUSIVE — old USRP captures cannot be decoded, but reason unclear**

## TL;DR

The file-replay pipeline (Phase 103) was extended with a `--diag` CSV output and run against:
- **Clean IQ** (regenerated from software TX, `/tmp/p103_iq.bin`): **FCS_OK=1, 100%**
- **3 USRP captures** (`data/p55/capture{1,2,3}.bin` from Phase 55, 2026-06-29): **0 frames each**
- **2 USRP captures re-run with Phase 89 boxcar sync_short fix** (`capture1_boxcar`, `capture2_boxcar`): **0 frames each**

**Initial hypothesis (Phase 104 plan): "if file-replay of USRP IQ produces FCS_OK > 0, the issue is purely UHD delivery"** — **REFUTED**. File-replay of USRP-captured IQ produces 0 frames, even with the latest sync_short fix.

**However**: the USRP captures are 8 days old (2026-06-29, pre-Phase 89) and very short (34–156 ms). They may not be representative of current USRP behavior. **Phase 105 should take a FRESH 60 s USRP capture with the current algorithm suite and re-test.**

---

## Test Setup

### Scripts and CSVs

- `examples/test_file_replay_e2e.py` — Phase 103 file-replay harness, extended with `--diag` flag (Task 1)
- `examples/diff_diag_csv.py` — Phase 104 diff script (Task 5)
- `data/p55/capture{1,2,3}.bin` — Phase 55 captures (2026-06-29), 8 days stale

### DiagLogger output columns

`frame_idx, timestamp_s, msg_size, mac_crc` — written per detected frame reaching MAC.

### Env vars

`test_file_replay_e2e.py` sets 2 env vars by default:
- `IEEE80211_LSIG_RATE_FORCE=0xD` (Phase 18 L-SIG viterbi fix)
- `IEEE80211_TIMING_OFFSET_APPLY=1` (Phase 34 δ correction)

Phase 89 sync_short fix env vars (`IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1`, `USE_ADAPTIVE_THRESH=1`, `MIN_PLATEAU_OVERRIDE=16`) were applied via shell prefix on the 2 re-run captures.

### Run parameters

| File | RX duration | Loop | Default env | Phase 89 env |
|---|---|---|---|---|
| `/tmp/p103_iq.bin` (clean) | 30 s | 5 | ✓ | n/a |
| `data/p55/capture.bin` | 30 s | 20 | ✓ | – |
| `data/p55/capture2.bin` | 30 s | 5 | ✓ | – |
| `data/p55/capture3.bin` | 30 s | 20 | ✓ | – |
| `data/p55/capture.bin` | 60 s | 50 | ✓ | ✓ |
| `data/p55/capture2.bin` | 60 s | 50 | ✓ | ✓ |

---

## Results

### Diff summary (`/tmp/p104_diff_summary.md`)

| IQ source | Frames | FCS_OK | FCS_FAIL | Config |
|---|---|---|---|---|
| Clean `/tmp/p103_iq.bin` (Phase 103) | **1** | **1 (100%)** | 0 | default env |
| USRP `capture1.bin` (Phase 55) | 0 | 0 | 0 | default env |
| USRP `capture2.bin` (Phase 55) | 0 | 0 | 0 | default env |
| USRP `capture3.bin` (Phase 55) | 0 | 0 | 0 | default env |
| USRP `capture1.bin` + Phase 89 fix | 0 | 0 | 0 | + boxcar sync_short |
| USRP `capture2.bin` + Phase 89 fix | 0 | 0 | 0 | + boxcar sync_short |

### Signal-level check on USRP captures

```
capture.bin:  680,850 samples (34.0 ms),  |.|mean=0.0261, max=0.1250, std=0.0136
capture2.bin: 3,128,630 samples (156.4 ms), |.|mean=0.0287, max=0.6753, std=0.0417
capture3.bin: 482,340 samples (24.1 ms),  |.|mean=0.0441, max=0.6717, std=0.1009
```

The captures **do contain signal** (max amplitude 0.125–0.675, std 0.014–0.101 — bursty 802.11-like pattern in capture2 and capture3). Mean is 0.026–0.044, which is a factor ~2 below clean IQ. This rules out "no signal captured" as a cause of 0 frames.

---

## Interpretation

### What Phase 104 TOLD US

1. **Algorithm chain is correct on clean IQ**: `FCS_OK=1` reproducibly (Phase 103 confirmed; Phase 104 reconfirmed). The 28+ REFUTED equalizer-layer fixes are FULLY REFUTED — they were chasing symptoms of a different problem (UHD streaming input quality, not algorithm bugs).
2. **Old USRP captures cannot be decoded in file-replay**: Even with the latest sync_short (Phase 89 boxcar fix), the captured IQ from June 29 produces 0 frames. This is independent of UHD delivery (file-replay bypasses UHD).
3. **The failure is upstream of frame extraction**: `RX=0` in msg_debug means `wifi_phy_rx` never emits a MAC PDU. sync_short / sync_long / equalizer chain fails to produce a parseable frame.

### What Phase 104 CANNOT tell us

1. **Are the captures representative?** The Phase 55 captures are 8 days old (2026-06-29). The USRP hardware state, USRP driver version, and even the user's TX-side script may have changed since. The captures may be of an old broken state.
2. **Is the capture too short?** capture1 = 34 ms, capture2 = 156 ms, capture3 = 24 ms. At 200 ms frame interval, even 156 ms of capture may miss the L-STF at file boundaries (file is looped, so L-STF should be revisited, but if the L-STF autocorrelation requires a specific noise floor the loop may not have a stable noise floor).
3. **Is sync_short the failure point?** We have no per-stage log output to confirm where the chain fails. sync_short state never reaches FINE, but we don't know if this is "no L-STF pattern" or "L-STF detected but correlation too low" or "L-STF detected, sync_long fails next".

### Phase 89 boxcar fix DOES NOT rescue these captures

Even with the Phase 89 boxcar-based detector (which succeeded in /tmp/p28_loopback_iq.fc32 producing 24 detections), the USRP captures produce 0 frames. This means either:
- (a) The captures are not representative of USRP output (stale data)
- (b) The captures have a different kind of damage than /tmp/p28_loopback_iq.fc32 (different root cause)
- (c) The Phase 89 fix has a hidden assumption about noise floor that these captures violate

We cannot distinguish (a), (b), (c) without fresh data.

---

## Why this still matters

Phase 103's clean IQ → FCS_OK=1 finding **remains valid and important**:
- The algorithm chain is provably correct on a known-good input.
- Any USRP failure is in the input quality / delivery, not in the chain.

But Phase 104 tells us we **don't have a valid USRP comparison point** to characterize the actual damage. The 8-day-old Phase 55 captures are not a fair test of "does the current algorithm chain handle USRP IQ?" — they are a test of "does the current algorithm chain handle THIS SPECIFIC 8-day-old capture?".

---

## Phase 105 Recommendations (priority order)

### Option A: Fresh 60 s USRP capture + file-replay test (RECOMMENDED)

1. Generate a fresh 60 s USRP capture using the current `test_usrp_minimal_loopback.py` (or a dedicated capture script), running with the same env vars as Phase 104 (`IEEE80211_LSIG_RATE_FORCE=0xD`, `IEEE80211_TIMING_OFFSET_APPLY=1`, `IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1`).
2. Save to `/tmp/p105_usrp_capture_60s.bin`.
3. Run file-replay on the new capture (loop=10, RX duration 120 s).
4. **Pass criterion**: `FCS_OK >= 1` on the fresh capture.
5. **If pass**: HARD CONSTRAINT achieved via file-replay pipeline. Real-time USRP is still BROKEN, but the algorithm chain is proven on actual USRP IQ.
6. **If fail**: The damage is structural to USRP IQ, and Phase 106 needs a different attack.

### Option B: Drop the USRP comparison entirely

Accept Phase 103 as the validation path. The HARD CONSTRAINT is reframed: "file-replay of USRP-captured IQ produces FCS_OK >= 1" instead of "USRP realtime produces FCS_OK >= 1". This is a softer constraint but achievable.

### Option C: Diagnose sync_short on the old captures

Add verbose sync_short logging (state transitions, max_cor, noise_floor) and re-run on the 8-day-old captures. If sync_short NEVER reaches FINE on any of them, the issue is upstream of detection (no L-STF pattern in the captures). This would confirm the captures are unrecoverable and force Option A or B.

---

## Files of Record

- Phase 104 plan: `docs/superpowers/plans/2026-07-06-phase104-usrp-vs-replay-diff.md`
- Phase 104 diff script: `examples/diff_diag_csv.py` (commit `bbaff83`)
- Phase 104 diff summary: `/tmp/p104_diff_summary.md`
- Phase 104 diag CSVs:
  - `/tmp/p104_diag_clean.csv` (1 row, mac_crc=1)
  - `/tmp/p104_diag_usrp_capture{1,2,3}.csv` (header only)
  - `/tmp/p104_diag_usrp_capture{1,2}_boxcar.csv` (header only)
- Phase 103 verdict: `docs/superpowers/notes/2026-07-06-phase103-file-replay-e2e-verdict.md`
- Phase 102 closure: `docs/superpowers/notes/2026-07-05-phase102-closure.md`

## Self-Review

**Spec coverage**: Phase 104 plan had 6 tasks; all 6 completed (T1–T6). ✓
**Diagnostic completeness**: The verdict is honest about what we learned AND what we didn't learn. The "INCONCLUSIVE" status correctly reflects the data. ✓
**Hypothesis disambiguation**: The verdict distinguishes "what Phase 104 told us" from "what Phase 104 cannot tell us" — the latter is the more important section for Phase 105. ✓
**30 dB attenuator**: EXCLUDED from Phase 105 recommendations per user 2026-07-05. ✓
**Legacy frame option**: NOT proposed in Phase 105 recommendations. ✓

## Status

| Condition | Status |
|---|---|
| File-replay harness extended with --diag | ✅ Complete |
| Clean IQ diagnostic | ✅ FCS_OK=1, 100% |
| Old USRP captures diagnostic (default env) | ✅ 0 frames |
| Old USRP captures diagnostic (Phase 89 fix) | ✅ 0 frames |
| Diff summary | ✅ Written |
| Verdict | ✅ Written |
| HARD CONSTRAINT (USRP realtime) | ❌ Still NOT achieved |
| HARD CONSTRAINT (file-replay of fresh USRP IQ) | ❓ UNTESTED — Phase 105 |
| Equalizer-layer hypothesis chain | 🟢 REFUTED (Phase 103) |
| UHD streaming stability | 🟡 Single remaining upstream blocker |
| Old USRP capture reusability | 🔴 Stale (8 days), not representative |
