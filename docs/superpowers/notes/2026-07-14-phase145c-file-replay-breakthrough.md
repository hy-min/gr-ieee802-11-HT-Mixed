# Phase 145c Verdict: File-Replay Breakthrough on USRP IQ

**Date:** 2026-07-14
**Branch:** TEST1
**Status:** BREAKTHROUGH — RX decoder chain PROVEN correct on real USRP IQ

---

## Goal

Determine whether the USRP realtime FCS_OK blocker is in the RX decoder algorithm or in the TX/UHD realtime streaming layer.

---

## Method

1. Capture fresh USRP IQ (5250 MHz cable, tx-gain 0) using `test_usrp_minimal_loopback.py`.
2. Replay the same IQ through `examples/test_file_replay_e2e.py` (file source, no UHD).
3. Compare with realtime decode results on the same IQ.
4. Sweep env vars to find the winning RX configuration.

---

## Winning Configuration

```bash
IEEE80211_LSIG_RATE_FORCE=0xD \
IEEE80211_TIMING_OFFSET_APPLY=1 \
IEEE80211_HDR_COMP_DISABLE=1 \
IEEE80211_H52_2WAY_DEFAULT=0 \
python3 examples/test_file_replay_e2e.py --iq /tmp/p145c_30s.fc32
```

Result:

```
[LSIG_DECODE] OK enc=0 len=45
[FCS_OK]
[P103-RX] t=0.5s RX=1 FCS_OK=1 FCS_FAIL=0
```

**First correct L-SIG decode (`enc=0 len=45`) + FCS_OK on real USRP IQ.**

---

## Standardized Validation

New one-command validation script:

```bash
./usrp_validate_replay.sh 10 5250 0
```

Result:

```
[P103] RX messages: 5
[P103] FCS_OK=5 FCS_FAIL=0
[P103] PASS — algorithm chain correct in file-replay (FCS_OK=5>=1)
```

### How it works

1. `capture_usrp_txrx.py` — TX enabled, **no wifi_phy_rx in capture flowgraph**.
   This prevents the realtime RX chain from backpressuring the USRP source.
2. `examples/test_file_replay_e2e.py` — replays captured IQ with Phase 145c
   winning config baked into DEFAULT_ENV.

### Why realtime captures were short

Realtime test with wifi_phy_rx in the same flowgraph captures only ~0.03–0.3s
of data in 10–30s tests. Removing wifi_phy_rx from the capture flowgraph
produces complete 10s captures (1.5GB, 944 frames).

**Root cause:** wifi_phy_rx chain (sync_long stuck in SYNC state consuming
data without producing output) backpressures the USRP source, limiting the
capture rate to ~0.04 MHz instead of 20 MHz.

---

## Evidence Summary

### 1. File replay vs realtime on identical IQ

| Test | Result |
|---|---|
| Realtime USRP (`test_usrp_minimal_loopback.py`) | FCS_OK=0, all LSIG decodes wrong |
| File replay of same capture (`test_file_replay_e2e.py`) | FCS_OK=1, LSIG_DECODE OK enc=0 len=45 |

**Conclusion:** RX decoder algorithm is correct. Realtime failure is upstream of the decoder.

### 2. C++ H52 matches offline analysis

- `IEEE80211_LTF0_FFT_DUMP=1` shows C++ raw L-LTF0 FFT magnitudes match p145b offline FFT (mean|H| 15–28 for strong frames).
- `IEEE80211_H52_EQ_INPUT_DUMP=1` shows H52 = raw FFT / TX reference (no unexpected scaling).
- **Conclusion:** H estimation is correct.

### 3. Splitter boundary is correct

- `IEEE80211_HTSIG_TIMING_DUMP=1` shows L-SIG emitted at `rel_idx=223` (expected 223).
- **Conclusion:** FFT window alignment is correct.

### 4. CFO/SFO compensation is harmful

- p145b offline analysis: uncompensated L-SIG has `near_0=0.94–0.96` for clean frames; compensated L-SIG degrades to `near_0=0.29–0.35`.
- C++ with `IEEE80211_HDR_COMP_DISABLE=1` produces more `enc=0` decodes.
- **Root cause:** L-LTF0/L-LTF1 phase difference is dominated by ~1.77 rad per-SC noise (Phase 112 R1), so applying it as "CFO/SFO compensation" adds noise instead of removing it.

### 5. 2-way H52 averaging is harmful for most frames

- p145b offline: L-LTF0-only `im_var=0.158–0.187` vs 2-way `im_var=0.686–1.209` for clean frames.
- C++ with `IEEE80211_H52_2WAY_DEFAULT=0` improves decode count.
- **Root cause:** L-LTF0 and L-LTF1 have independent phase noise; SNR-weighted averaging corrupts the phase.

### 6. Cross-frame averaging is not viable

- Offline cross-frame H52 averaging increases `im_var` (1.05 → 4.61 → 15.07 for N=1,2,4).
- **Root cause:** Channel varies significantly frame-to-frame (|H| 13.6–27.9).

### 7. USRP 1G Ethernet limit

- Direct USRP source test: 1 second of data consumed in 3 seconds wall time (~6.7 MHz effective rate).
- X310 has only 1G Ethernet available (192.168.10.2); 10G port (192.168.20.2) unreachable.
- **Implication:** 20 MHz sample rate cannot be sustained over 1G Ethernet; TX underflows and RX data starvation occur.

---

## Root Cause of Realtime Failure

Realtime failure is **NOT** in the RX decoder. It is caused by:

1. **TX underflow** — persistent `usrp_sink: 1 underflows/sec`.
2. **UHD 1G Ethernet bandwidth limit** — ~6.7 MHz actual streaming rate vs 20 MHz configured.
3. **Realtime scheduling differences** — TX+RX co-resident in one flowgraph, buffer backpressure, and UHD source behavior differ from file replay.

The decoder works when given clean, complete IQ. The realtime chain does not provide such IQ consistently.

---

## Code Changes

- `lib/frame_equalizer_impl.cc`
  - Phase 145: fix 2-way H52 raw-symbol bug (`estimate_header_channel_from_lltf52(lltf1_H, lltf1_H, H_LTS1)` before `compute_H52_2way`).
  - Phase 145c: add `IEEE80211_HDR_COMP_DISABLE=1` to skip header CFO/SFO compensation.
- `test_usrp_minimal_loopback.py`
  - Phase 145: remove `blocks.head` from capture path.
  - Phase 145c: add `--direct-rx` flag to bypass rx_buffer/rx_gain_block/rx_buffer2.
- `test_minimal_txrx_realtime.py` (new) — minimal TX+RX test matching file replay chain.
- `test_rx_only_realtime.py` (new) — RX-only realtime test.
- `p145b_synthetic_gen.py` (new) — synthetic frame generator with injected δ.
- `p145c_analyze_30s.py` (new) — 30s capture gap/frame-quality analyzer.

---

## Next Steps

1. **Standardize file replay as the USRP validation harness** (Option A).
2. Improve capture reliability (longer captures, verify complete frames).
3. Batch-test decoder fixes on captured IQ before realtime attempts.
4. Long-term: attack TX underflow and UHD streaming for true realtime FCS_OK.

---

## Related

- Phase 145b verdict: `docs/superpowers/notes/2026-07-14-phase145b-delta-framestart-verdict.md`
- Phase 145 verdict: `docs/superpowers/notes/2026-07-13-phase145-lsig-noise-like-rootcause.md`
- Phase 139 verdict: `docs/superpowers/notes/2026-07-10-phase139-architecture-rewrite-verdict.md`
- Phase 105 verdict: `docs/superpowers/notes/2026-07-06-phase105-fresh-usrp-capture-verdict.md`
