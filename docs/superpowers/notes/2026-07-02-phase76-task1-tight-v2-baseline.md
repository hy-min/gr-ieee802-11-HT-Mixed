# Phase 76 Task 1 — Tight_v2 Baseline USRP Capture

**Date**: 2026-07-02
**Branch**: TEST1

## Setup

- USRP X310 + UBX-160 (serial 323850C @ 192.168.10.2), A:0 TX → A:0 RX2 same-board TDD
- Frequency: 5890 MHz, --rate 20 MHz, --tx-gain 20, --rx-gain 20
- --warmup 30, --duration 60
- All 6 tight_v2 env vars explicitly exported:
  - `IEEE80211_H52_NULL_INTERP=1`
  - `IEEE80211_H52_NULL_THRESH=0.03`
  - `IEEE80211_H52_INTERP_RADIUS=5`
  - `IEEE80211_HTSIG_PILOT_CPE=1`
  - `IEEE80211_LSIG_RATE_FORCE=0xD`
  - `IEEE80211_TIMING_OFFSET_APPLY=1`
- Capture: `/tmp/p76_tight_v2_freq_5890.bin` (74 MB, 9.29M samples, 0.465s @ 20 MHz)
- Replay: `--loop 5`, log at `/tmp/p76_tight_v2_freq_5890.log` (110 MB)
- Captive USRP test: Sent=90, Recv=0, FCS_OK=0, FAIL=0

## Tight_v2 propagation VERIFIED

Log preamble confirms all env vars propagated correctly:
- `[FRAME_EQ] IEEE80211_HTSIG_PILOT_CPE=1 (HT-SIG pilot-aided CPE ENABLED)`
- `[FRAME_EQ] IEEE80211_TIMING_OFFSET_APPLY=1 (δ estimation+correction ENABLED)`
- `[FRAME_EQ] IEEE80211_H52_NULL_INTERP=1 (H52 null interp ENABLED, thresh=0.03, radius=5, dump=OFF)`

**THRESH=0.03 (NOT 0.15) and RADIUS=5 (NOT 2) confirmed**. Phase 75 T2 redo was definitely run with
C++ defaults; this Task 1 capture uses the true tight_v2 config.

## Results

| Metric | Count |
|---|---|
| `HT_SIG_CAND` | **0** |
| `LSIG_DECODE OK` (total) | **10** |
| `LSIG_DECODE OK` unique encodings | **enc=5 (×5), enc=7 (×5)** |
| `LSIG_DECODE OK` with enc=0 (HT mode) | **0** |
| `LSIG_PARSE_FAIL reason='viterbi_fail'` | **45** (= 9 fails × 5 loops; sym=4..11) |
| `H60_NULL_PER_FRAME` dumps | **45** (9 per loop × 5 loops) |
| `n_nulls` distribution | **1/52 only** (single value, all dumps) |
| `is_ht` in H60 dumps | **0** (all dumps) |
| `avg_snr_lsig` on parse_fail | **2.74 dB** (constant) |
| `avg_snr_ht` on parse_fail | **6.66 dB** (constant) |
| Per-loop FCS_OK | **0** for all 5 loops |

## Analysis

### What worked
- **H52 null detection works**: tight_v2 fires correctly, n_nulls=1/52 across all 45 dumps.
- **H52 pre-clean is reaching Hhdr52**: H60_NULL_PER_FRAME dump fires 9 times/loop (one per DATA symbol).
- **Mapping is non-HT by default**: TX mapper log shows `[MAPPER] set_use_ldpc called: false old=false`.
  The TX path produced non-HT (legacy) frames; 5 valid L-SIG decodes per loop are all enc=5/7 (legacy
  CCK/OFDM), not enc=0 (HT-mode duplicate L-SIG).

### What didn't work
- **HT-SIG chain still does not fire**: HT_SIG_CAND=0 in entire log.
- **L-SIG viterbi failure at avg_snr=2.74 dB**: Below the 6 dB viterbi threshold by 3.3 dB.
  Constant across all 9 symbols × 5 loops (single capture file replayed 5×).
- **No enc=0 frames reach L-SIG decode**: The 10 successful decodes are all enc=5/7 (legacy).
  Because HT-SIG chain only fires for enc=0 (HT-mode L-SIG), HT chain is structurally unreachable.

### Interpretation relative to Phase 75

| Capture | Loop | HT_CAND | L-SIG encodings | Tight_v2 THRESH/RADIUS |
|---|---|---|---|---|
| p75_control | 5 | 80 | 5/6/0/2 | 0.03/5 ✓ |
| p75_freq_5500 | 5 | 0 | 0 | 0.15/2 ✗ (defaults!) |
| p75_freq_5180 | 5 | 0 | 4 | 0.15/2 ✗ |
| p75_freq_5890 | 5 | 0 | 2/3/4/5 | 0.15/2 ✗ |
| **p76_tight_v2_5890 (THIS)** | **5** | **0** | **5/7 only** | **0.03/5 ✓ (correct!)** |

This Task 1 capture is the FIRST proper tight_v2 USRP baseline at 5890. Result: **tight_v2 at 5890
does NOT produce HT_CAND > 0**. The Phase 75 verdict's hypothesis that 5890 was simply misconfigured
(using defaults) is REFUTED — even with proper tight_v2 applied, 5890 produces no enc=0 frames.

### Why no enc=0 frames?

The TX mapper always emits legacy (non-HT) frames in the current wifi_phy_hier chain. enc=5/7 are
the legacy rates; enc=0 (HT-mode) requires the mapper to emit an HT-format frame (which would
duplicate L-SIG with enc=0). Looking at the prior control capture (`p75_control` with HT_CAND=80
and encodings 5/6/0/2), enc=0 appeared there but not at any of the 5180/5500/5890 captures.

Hypothesis for Phase 76 T2: the TX mapper's `encoding` field may vary by capture run, or there is a
TX-side state that influences whether HT-mode is emitted. The control capture was at default args
while the freq sweep used explicit `--freq` — perhaps the wifi_phy_hier TX chain checks frequency
and downgrades to legacy. **T2 must trace the TX encoder tag flow**.

## Concerns

1. **Tight_v2 working, channel unchanged**: This rules out (a) env var propagation issue.
   The remaining possibilities are (b) the TX encoder never emits enc=0 in freq-sweep captures, or
   (c) some upstream state in the RX chain filters enc=0 before HT_SIG chain runs.
2. **avg_snr_lsig=2.74 dB is a hard limit**: viterbi threshold is 6 dB. Per-symbol CPE / Phase 73
   H52 pre-clean does not raise the per-symbol decision SNR enough. This is the same Phase 75 finding.
3. **Capture file is 74 MB, much less than Phase 74 v2's 8.7 GB**: UHD streaming instability continues
   to starve the capture, but the captured IQ still contains valid frames (sync_short fires, L-SIG EQ
   fires, H60 dump fires). File size is not the limiter here.

## Next steps

**T2: Investigate TX encoder tag flow.** Trace how `encoding` field flows from `wifi_phy_hier.py`
TX mapper to emitted frame, comparing control capture (enc=0 present) vs freq-sweep captures
(enc=0 absent). If encoder is hard-coded to legacy for freq-sweep runs, T3 adds `IEEE80211_FORCE_TX_ENCODING=0`
env var to override.

**T3: Add IEEE80211_FORCE_TX_ENCODING env var** to mapper (if T2 finds no env-var-controlled switch).
**T4: Test forced enc=0 with USRP air path** — if forcing enc=0 yields HT_CAND > 0, the bottleneck
is the TX-side encoder, not the RX-side H52 chain.