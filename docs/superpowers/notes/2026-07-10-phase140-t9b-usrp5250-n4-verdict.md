# Phase 140 T9b USRP 5250 MHz N=4 VERDICT — 2026-07-10

## Verdict: **REFUTED** (Recv=0, Phase 140 path never executes)

## Test Setup

- USRP X310 @ 192.168.10.2 serial 323850C (verified ping 0.64ms)
- 5250 MHz same-board cable, A:0 TX → A:0 RX2, --tx-gain 20 --rx-gain 31.5 --rate 20
- 60s warmup + 60s capture, USRP_LOG_LEVEL=DEBUG
- LD_PRELOAD=wrap_rpc2.so (mandatory to bypass gr-uhd 4.9.0.0 rpcmanager::register_booter crash)
- Python 3.8 from /home/hy/conda/envs/gnuradio (load installed ieee802_11 module from /usr/local/lib/python3.8)
- Env: `IEEE80211_PHASE140_ON=4` + `IEEE80211_TIMING_OFFSET_APPLY=1`

## Wall-clock

~3 minutes (60s warmup + 60s capture + ~10s startup overhead + teardown)

## USRP Connection

PASS — config printed correctly (TX subdev A:0, RX antenna RX2, freq=5250, tx-gain=20, rx-gain=31.5)

## Metrics (60s capture)

- **Sent: 600**
- **Recv: 0**
- **FCS_OK=0, FCS_FAIL=0**
- **Success Rate: 0.0%**
- Sync_short detections: 64 (1 strong corr=0.926, 63 noise corr=0.218-0.377)
- Sync_long frame_starts: 28 (HT-mode-plateau SELECTED, score=1.12-1.75)
- Splitter FRAME_START events: **1,358,764** (pathological loop due to HT detection failure)
- **Phase 140 cross-frame FIFO code path never fires** (H52_2WAY=0, N=4 history remained empty)

## Root Cause

1. **L-SIG viterbi FAILS upstream** — `[LSIG_PARSE_FAIL] viterbi_fail sym=4-11 avg_snr=2.72 avg_snr_ht=3.74` on the only frame that reached frame_equalizer. Phase 140's 2-way L-LTF0+L-LTF1 SNR-weighted H52 (default ON since Phase 139) averages, but the gate is L-SIG viterbi decoding, which fails before HT-SIG chain can run.

2. **Frame classified as Legacy, not HT** — `[FRAME_DETECT] Detected Legacy frame (HT-SIG ratio=0.937, L-SIG ratio=0.781)` → `is_ht_frame=0`. Phase 140 cross-frame FIFO call site is gated behind `is_ht_frame` (H52_2WAY history fill requires HT path). Without HT detection, the FIFO never populates, so N=4 averaging is a no-op.

3. **avg_snr=2.72 dB is still far below the ~6 dB viterbi threshold** even with Phase 139's 2-way averaging — confirming that 1.25 rad σ (Phase 139) is the algorithmic floor; further FIFO averaging cannot help at this stage because the FIFO requires successful L-SIG viterbi to fire on previous frames.

## Verdict Criteria Evaluation

- **Strong PASS (FCS_OK ≥ 1)**: NO — Recv=0, FCS_OK=0
- **PARTIAL (Recv > 0 AND best metric < 13)**: NO — Recv=0, no metric reached
- **REFUTED (Recv = 0 OR best metric ≥ 14)**: **YES** — Recv=0

## Layer Status

**Equalizer-layer attack surface EXHAUSTED at FIFO-averaging axis.** Phase 140 was the first attempt at FIFO-averaging the post-L-SIG H52. The architecture requires:
1. Successful L-SIG viterbi → `is_ht_frame=1` → enter HT branch
2. Then the FIFO averaging at HT-SIG chain gates fires

Step 1 fails at avg_snr=2.72 dB (well below viterbi capacity) on this hardware. Per user directive 2026-07-07 "不可能接受现状": new architectures MUST continue, but at a different layer — **the L-SIG viterbi upstream gate itself, NOT the post-L-SIG FIFO**. The 1.77 rad per-SC noise floor (Phase 112 R1) is still the dominant physical limit; FIFO averaging cannot bridge it when the gate is closed.

## Cable Budget

This test consumed 1 cable run. Total cumulative Phase 140+ cable runs: 1 (well within ≤5 budget user-cited from Phase 138/139).

## Next Steps (per user "不可能接受现状")

The 30 dB SMA attenuator install path (HW, $50) reduces noise toward 0.5-0.7 rad — strongest single intervention. Wiener filter or multi-frame averaging AT THE PRE-L-SIG LEVEL (before viterbi) is the algorithmic alternative that could bypass the gate. The current Phase 140 design is conceptually sound but architecturally dependent on L-SIG succeeding.

## Artifacts

- Log: `/tmp/p140_usrp/T9b_p140_N4.log` (650 MB)
- Capture: `/tmp/p140_usrp/T9b_p140_N4.fc32` (68 MB)
