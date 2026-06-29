# Phase 55 — Raw IQ Capture + Offline Replay SNR Stability Validation

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: Spec (user-approved)
**Goal**: Diagnose root cause of USRP avg_snr 8x drift (Phase 31b 12.91 → Phase 53 6.12 → Phase 54 1.48 in 6h) by separating UHD streaming timing from air path physics.

## Why

Phase 31b baseline measured avg_snr_lsig = 12.91 (19.5 dB) on same-board A:0/A:0 (RX2) at 5890 MHz with tx-gain 20. Subsequent runs degraded:

| Run | Date | avg_snr_lsig (linear) | dB |
|---|---|---:|---:|
| Phase 31b | 2026-06-17 | 12.91 | 19.5 |
| Phase 48 | 2026-06-29 morning | 2.82 | 4.5 |
| Phase 53 | 2026-06-29 midday | 6.12 | 7.9 |
| Phase 54 BCC | 2026-06-29 afternoon | 1.48 | 2.7 |

Same physical configuration, same USRP, same test code. **8.7x drift in 12 days, 4x drift in 6 hours**.

## Hypothesis

The drift could be:
1. **Air path physics** — environment (interference, temperature, antenna position)
2. **UHD streaming** — CPU scheduler pressure causing overflow, sync_long deadline miss, or L-LTF timing errors
3. **GNU Radio scheduling** — set_min_output_buffer under-sized causing back-pressure

The classic method to separate these is to **capture raw IQ once, then replay offline multiple times**. If offline SNR is stable, the issue is UHD/GR timing. If offline SNR also drifts, the issue is air path.

## Approach

### Step 1: Capture raw IQ

Run `test_usrp_minimal_loopback.py --capture /tmp/p55_capture.bin --duration 35` with standard env vars:

```bash
unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so \
PYTHONPATH=./build/python/bindings:./python:./examples \
IEEE80211_LSIG_RATE_FORCE=0xD \
IEEE80211_LLTF_OFFSET_CORRECT=4 \
IEEE80211_TIMING_OFFSET_APPLY=1 \
IEEE80211_MMSE_EQUALIZE=1 \
IEEE80211_MMSE_N0_PERCENTILE=25 \
timeout 50 /home/hy/conda/envs/gnuradio/bin/python \
test_usrp_minimal_loopback.py --freq 5890 --tx-gain 20 --rx-scale 45 --duration 35 \
--capture /tmp/p55_capture.bin
```

Output: ~560 MB binary (35s × 20M samples/s × 8 bytes = 5.6e9 bytes ... but test_usrp_minimal_loopback.py:175 caps at `args.duration * args.rate * 1e6` = 35 × 20e6 = 700e6 samples × 8 bytes = 5.6 GB).

**Concern**: 5.6 GB on /tmp may fill disk. Mitigation: reduce `--duration 10` → ~1.6 GB, still plenty for 10 frames.

### Step 2: Write offline SNR replay script

New file: `examples/p55_offline_snr.py`

Reuses `analyze_h52_offline.py` infrastructure:
- `read_fc32(path)` — read IQ file
- `find_frame_starts(samples)` — sync_short correlation
- `estimate_h52_frame(samples, frame_start)` — H52 from L-LTF0+L-LTF1
- Compute `|eq[i]|² = |safe_div(L_SIG[i], H52[i])|²` for each subcarrier
- Output `avg_snr_lsig = mean(|eq[i]|²)` per frame
- Print per-frame avg_snr_lsig list

This mirrors `lib/frame_equalizer_impl.cc:4455-4471` algorithm.

### Step 3: Run offline replay 3 times

```bash
for i in 1 2 3; do
  python examples/p55_offline_snr.py /tmp/p55_capture.bin > /tmp/p55_offline_$i.log 2>&1
done
```

### Step 4: Compare realtime vs offline SNR

| Source | SNR measurement |
|---|---|
| Realtime (Phase 54) | avg_snr = 1.48 (linear) |
| Offline replay 1 | TBD |
| Offline replay 2 | TBD |
| Offline replay 3 | TBD |

Offline SNR std/mean ratio tells us the stability.

## Decision Logic

- **Offline std/mean < 0.10** → air path issue (real environment). Software cannot fix without physical intervention.
- **Offline std/mean ≥ 0.10** → UHD/GR timing issue. Look at scheduler, overflow, sync_long behavior.

## Success Criteria

1. Capture file exists at /tmp/p55_capture.bin (>1 GB)
2. `examples/p55_offline_snr.py` runs without error and produces per-frame avg_snr_lsig list
3. 3 offline runs complete
4. Std/mean ratio computed and used to determine root cause category
5. Verdict written to `docs/superpowers/notes/2026-06-29-phase55-verdict.md`

## Files

- **Modify**: none (no C++ change)
- **Create**: `examples/p55_offline_snr.py` (~150 lines)
- **Read**: existing `examples/analyze_h52_offline.py` and `examples/p34_delta_offline.py`
- **Write**: `docs/superpowers/notes/2026-06-29-phase55-verdict.md`

## Out of Scope

- No C++ modifications
- No env-var additions
- No regression test changes (separate task)
- No physical intervention (excluded by user)

## Risk

- /tmp disk fill: use `--duration 10` instead of 35 if needed
- Phase 54 SNR=1.48 was already so low that frames may not be detectable offline. Mitigation: also check sync_short correlation peaks even when |H52| is small.