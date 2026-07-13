# Phase 145 Verdict: L-SIG NOISE_LIKE Root-Cause Narrowing

**Date:** 2026-07-13  
**Branch:** TEST1  
**Commit:** `9b929ca`  
**Status:** IN PROGRESS — hardware/config matrix exhausted; root cause narrowed to timing alignment / H52 estimation

---

## Goal

Determine whether the USRP FCS_OK blocker is RF path, UHD streaming, equalizer algorithm, or timing alignment; commit test-script fixes found during the sweep.

---

## What Was Tested

### Hardware configurations
- A:0 same-board (cable and antenna)
- B:0 same-board (cable)
- A:0 TX → B:0 RX2 cross-board (cable)

### Frequencies
- 5180, 5250, 5500, 5890 MHz

### Gains
- tx-gain: 0, 10, 20 dB
- rx-gain: 18–31.5 dB

### Software knobs
- CPU governor: powersave → performance
- Network buffers: wmem_max/rmem_max 1 MB → 25 MB
- CPU affinity: taskset --cpu-list 0-1
- Packet interval: 100–2000 ms
- `IEEE80211_FRAME_START_OFFSET`: -6 … +8 around base 174
- Phase 142 Wiener H52, Phase 143 BPSK fallback, L-SIG rotation search

---

## Key Results

| Configuration | Best frames | HT_SIG_CAND | FCS_OK |
|---|---|---|---|
| A:0 cable 5250 tx=0 rx=31.5 performance | 195 | 496 | 0 |
| A:0 cable 5250 tx=0 rx=21 | varies (up to 19) | up to 112 | 0 |
| A:0 antenna 5890 tx=20 rx=31.5 | 7 | 32 | 0 |
| B:0 cable 5250 tx=0 rx=31.5 | 42 | 0–16 | 0 |
| Cross-board A:0→B:0 RX2 | 28 | 16 | 0 |

**No configuration produced FCS_OK ≥ 1.**

---

## Root-Cause Evidence

### 1. L-SIG constellation is NOISE_LIKE on all USRP configurations

`test_eqlsig_constellation_offline.py` verdicts:

```text
A:0 cable 5250 rx=21:
  mean |eq|: 1.140   std |eq|: 0.910   mean margin: 0.166   mean |H|: 0.247
A:0 antenna 5890 rx=31.5:
  mean |eq|: 1.225   std |eq|: 1.002   mean margin: 0.098   mean |H|: 0.692
Clean software loopback:
  mean |eq|: 0.970   std |eq|: 0.212   mean margin: 0.820   mean |H|: 8.746
```

USRP H52 magnitude is **12–35× weaker** than clean loopback.

### 2. Sub-sample timing offset δ is anomalously large

With `IEEE80211_DELTA_PER_SYMBOL_DUMP=1`:

```text
[DELTA_DUMP] counter=4 delta=0.7019 (k/64=45) |H|mean=0.433
```

Clean loopback δ ≈ 0.08. USRP δ ≈ 0.7 samples — **9× larger**.

### 3. L-SIG length never correct

Correct `lsig_len = 45`. Closest observed: 39, 85, 106. Most values are hundreds/thousands.

### 4. TX underflow is not the blocker

Reducing packet interval to 100 ms reduced underflow rate from ~1/s to ~5/s, but L-SIG length remained wrong.

---

## Commits

- `9b929ca` fix(test): Phase 145 capture and B-board subdev fixes
  - `test_usrp_minimal_loopback.py`: remove `blocks.head` from capture path
  - `test_b_board_loopback.py`: fix `set_subdev_spec` order and channel indexing for B:0

---

## Conclusion

The blocker is **upstream of viterbi**: L-SIG equalized constellation is uniformly scattered (NOISE_LIKE) due to either:

1. Incorrect FFT-window timing (`FRAME_START_BASE=174` may no longer be optimal).
2. δ-correction sign/direction error in `frame_equalizer_impl.cc:7458`.
3. CFO/SFO residual contaminating the δ estimate.

Hardware path, cabling, antenna vs cable, daughterboard choice, and buffer tuning do not change the NOISE_LIKE verdict.

---

## Next Steps

1. Add `IEEE80211_TIMING_OFFSET_SIGN_FLIP=1` opt-in flag to test δ-correction direction.
2. Build a reliable single-process TX+RX+capture script for repeatable offline analysis.
3. Re-evaluate `FRAME_START_BASE` once capture/timing variance is reduced.

---

## Related

- Phase 144: `docs/superpowers/notes/2026-07-12-phase144-lsig-stability-verdict.md`
- Phase 142: `docs/superpowers/notes/2026-07-11-phase142-t3-wiener-forward-verdict.md`
- Phase 139: `docs/superpowers/notes/2026-07-10-phase139-architecture-rewrite-verdict.md`
- Phase 112 R1: `docs/superpowers/notes/2026-07-07-phase112-r1-argh-rootcause.md`
