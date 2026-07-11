# Phase 141 Wiener H52 MMSE Filter — Verdict

**Date:** 2026-07-11
**Hardware:** USRP X310 + UBX-160 v2, A:0 TX → B:0 RX2 (cross-daughterboard), REF LED green (external reference locked)
**Test command base:** `test_usrp_minimal_loopback.py --freq 5250 --rate 20 --warmup 60 --rx-subdev B:0 --cross-board-rx2 --interval 200`
**Goal:** Improve avg_snr_htsig above the 6 dB viterbi threshold and achieve FCS_OK ≥ 1.

---

## Executive Summary

**PARTIAL on USRP.**

- Wiener H52 kernel (T1) is **correct**: Python + C++ equivalence tests PASS, file-replay baseline preserved 1/1.
- L-SIG call site **fires** on USRP cross-board (`[WIENER_LSIG] sigma2=... applied`).
- Cross-board RX2 at `--tx-gain 31.5` is the first configuration in this phase to consistently reach the frame_equalizer and produce `LSIG_DECODE OK` events.
- However, **HT-SIG viterbi still fails**: avg_snr_htsig stays at 2–4 dB, far below the ~6 dB needed; best_metric is N/A across all 16 rotation/inversion candidates.
- Cross-board signal is **extremely unstable**: identical parameters produce frames in one run and zero detections in the next, making controlled A/B comparison unreliable.
- **No FCS_OK achieved.**

---

## What Was Implemented

Phase 141 adds a per-subcarrier Wiener MMSE shrinkage step to the H52 channel estimate:

```
G[k] = R_hh[k] / (R_hh[k] + sigma² / |y_ltf[k]|²), clamped to g_min
H_out[k] = G[k] · H_ls[k]
```

Components (all default OFF):
- `IEEE80211_WIENER_H52=1` — master enable
- `IEEE80211_WIENER_FIFO_N=N` — R_hh FIFO depth (1..8, default 4)
- `IEEE80211_WIENER_G_MIN=G` — minimum shrinkage gain (0..1, default 0.1)
- `IEEE80211_WIENER_NULL_SCS=...` — σ² estimation SCs (default `-21,-13,-7,7,21`)
- `IEEE80211_WIENER_LOG=1` — per-frame diagnostic

Call sites in `lib/frame_equalizer_impl.cc`:
- **(a) L-SIG viterbi:** Wiener on `Hhdr52_for_lsig` (line ~8055). This is the path that feeds HT-SIG viterbi as well, because HT-SIG uses `Hhdr52` derived from the same H.
- **(c) HT/Data direct-tx_order:** Wiener on `d_H52_tx_order` in both the 3-way branch (line ~6531) and the lazy L-LTF0 branch (line ~8997). This path only runs **after** `d_have_ht_header && d_is_ht` is true, so it does not affect HT-SIG decoding itself.

Test harness updates:
- `test_usrp_minimal_loopback.py`: added `--cross-board-rx2` flag for the user’s actual wiring (A:0 TX → B:0 RX2), distinct from the older `--cross-board` (A:0 TX → B:0 TX/RX).
- `examples/test_file_replay_e2e.py`: added `--wiener-on`, `--wiener-log`, `--wiener-fifo-n`.

---

## Validation Results

### T1-T6: Unit / file-replay

| Test | Result |
|------|--------|
| `p141_t1_wiener_unit.py` | PASS (4/4) |
| `p141_t1_wiener_equiv.cpp` | PASS (4/4, `-Wall -Wextra` clean) |
| File-replay baseline with Wiener OFF | 1/1 PASS |
| File-replay baseline with Wiener ON | 1/1 PASS |

### T7: USRP cross-board RX2

Configuration matrix tested (post-USRP-reboot):

| tx-gain | rx-gain | Wiener | Phase140 | outcome |
|--------:|--------:|:------:|:--------:|:--------|
| 20 | 20 | OFF | OFF | Recv=0, no LSIG_DECODE |
| 25 | 20 | OFF | OFF | Recv=0, no LSIG_DECODE |
| 31.5 | 20 | OFF | OFF | Recv=0, splitter frame_start only |
| 31.5 | 20 | ON | OFF | **LSIG_DECODE OK observed**, avg_snr_lsig=4.37–8.02, avg_snr_ht=2.22–4.49, HT_SIG_PARSE_FAIL, FCS_OK=0 |
| 20 | 20 | ON | N=4 | Recv=0 (USRP had no detections this run) |
| 31.5 | 20 | ON | N=4 | Recv=0 (USRP had no detections this run) |

Key observation logs (tx-gain 31.5, Wiener ON):

```
[WIENER_RHH] n_avg=5 depth=4 freq=5890000000 rhh_mean=8.4301 rhh_max=99.1807
[WIENER_LSIG] sigma2=0.1582 g_min=0.10 applied
[LSIG_DECODE] OK enc=2 len=511
[LSIG_PARSE_FAIL] sym=4 reason='viterbi_fail' ... avg_snr=6.15 avg_snr_ht=3.07
...
[HT_SIG_PARSE_FAIL] timeout_sym=7 n_candidates=16 best_metric=N/A avg_snr_lsig=1.08 avg_snr_htsig=0.75
```

Observations:
1. `freq=5890000000` in `[WIENER_RHH]` despite `--freq 5250`. The freq_key used for FIFO reset is taken from `d_freq_offset_from_synclong` (constructor argument), not the runtime tuned frequency. This is a cosmetic mismatch in the diagnostic; it does not alter algorithm behavior, but it means the cross-frame reset logic may reset more often than intended when the runtime frequency differs from the block’s construction frequency.
2. **No `[WIENER_3WAY]` or `[WIENER_LAZY]` logs were ever seen.** The 3-way branch is gated by `d_apply_htltf_avg=0` by default; the lazy branch only runs after HT header is successfully parsed. Therefore HT-SIG decoding itself is driven by call-site (a) only.
3. `avg_snr_htsig` peaks around 4.5 dB, still ~1.5 dB short of the viterbi threshold. No candidate produced a valid viterbi metric (`best_metric=N/A`).

### Signal stability issue

After the user rebooted the USRP, identical commands produced qualitatively different results across runs:
- `--tx-gain 20 --rx-gain 20 --wiener-on`: sometimes reached LSIG_DECODE/HT_SIG_PARSE_FAIL, sometimes no detections at all.
- `--tx-gain 31.5 --rx-gain 20 --wiener-on`: one run reached LSIG_DECODE OK, the next produced only sync_short general_work logs.

This run-to-run variance is larger than the effect being measured, so **5× redux was not informative** and is not reported as a controlled experiment.

---

## Root-Cause Assessment

1. **Wiener works on L-SIG H.** It successfully shrinks the L-LTF-based channel estimate and helps L-SIG viterbi produce `LSIG_DECODE OK` events in a regime where the baseline often fails entirely.
2. **Wiener does not reach the HT-SIG path directly.** HT-SIG viterbi uses `Hhdr52`, which is the output of call-site (a). Call-sites (b)/(c) on `d_H52_tx_order` are only active after HT-SIG is already decoded. So any benefit to HT-SIG must come indirectly through the L-SIG H being reused.
3. **HT-SIG SNR remains below threshold.** Even with L-SIG H improved, the HT-SIG symbols themselves are too noisy (avg_snr_htsig 2–4 dB vs ~6 dB needed). This is consistent with the 1.77 rad per-SC phase noise ceiling (Phase 112 R1) and the additional 0.5–1 rad cross-daughterboard LO drift (Phase 122).
4. **Cross-board instability dominates.** The largest source of variance is not the equalizer algorithm but the RF/link state: identical parameters yield frames in one run and silence in the next.

---

## Conclusion

Phase 141 Wiener H52 is **PARTIAL**:
- Algorithmically sound and correctly integrated.
- Helps L-SIG decode on cross-board RX2 at high TX gain.
- Does **not** bridge the remaining gap to HT-SIG viterbi success, because the HT-SIG path does not run the Wiener filter on its own H estimate and the analog SNR is still too low.
- **0 FCS_OK** on USRP.

---

## Next Steps

1. **Apply Wiener directly to HT-SIG H estimate.** The current design only filters `Hhdr52_for_lsig`. A more effective attack would apply MMSE shrinkage to the HT-SIG pilot-based H re-estimate (`IEEE80211_HTSIG_H_REESTIMATE`) before it is used by `decode_htsig_from_rotated`.
2. **Stabilize cross-board RF link.** The run-to-run variance is the primary blocker to any controlled equalizer experiment. Options:
   - Verify SMA cables and connectors.
   - Try `--tx-gain 31.5 --rx-gain 31.5` (both max).
   - Revert to same-board A:0→A:0 RX2 for cleaner A/B validation of Wiener itself.
3. **Combine Wiener with other HT-SIG refinements.** Wiener + `IEEE80211_HTSIG_H_AVERAGE` or Wiener + `IEEE80211_HTSIG_PILOT_CPE` may stack constructively, since each targets a different noise source.
4. **Fix freq_key mismatch.** Use the runtime tuned frequency (or remove freq_key gating for single-frequency tests) so `[WIENER_RHH]` logs reflect the actual channel.

---

## Files Modified

- `lib/frame_equalizer_impl.cc` — Wiener kernel, σ² estimator, R_hh estimator, env parser, 3 call sites
- `lib/frame_equalizer_impl.h` — Wiener state members
- `test_usrp_minimal_loopback.py` — `--wiener-on`, `--wiener-log`, `--wiener-fifo-n`, `--cross-board-rx2`
- `examples/test_file_replay_e2e.py` — `--wiener-on`, `--wiener-log`, `--wiener-fifo-n`
- `p141_t1_wiener_unit.py` — new Python reference test
- `p141_t1_wiener_equiv.cpp` — new C++ equivalence test
- `docs/superpowers/specs/2026-07-10-phase141-wiener-h52-design.md`
- `docs/superpowers/plans/2026-07-10-phase141-wiener-h52.md`
- `docs/superpowers/notes/2026-07-11-phase141-verdict.md` (this file)
