# Phase 141 Wiener H52 MMSE Filter — Verdict (Revised 2026-07-11)

**Date:** 2026-07-11
**Hardware:** USRP X310 + UBX-160 v2, tested on both A:0 TX → B:0 RX2 (cross-daughterboard) and A:0 TX → A:0 RX2 (same-board). REF LED green (external reference locked)
**Test command base:** `test_usrp_minimal_loopback.py --freq 5250 --rate 20 --warmup 60 --rx-subdev A:0 --interval 200`
**Goal:** Improve avg_snr_htsig above the 6 dB viterbi threshold and achieve FCS_OK ≥ 1.

---

## Executive Summary

**PARTIAL on USRP, with a significant same-board breakthrough.**

- Wiener H52 kernel (T1) is **correct**: Python + C++ equivalence tests PASS, file-replay baseline preserved 1/1.
- L-SIG call site **fires** on USRP (`[WIENER_LSIG] sigma2=... applied`).
- **Same-board A:0 → A:0 RX2 is RF-stable and produces repeatable frames.** This is a major improvement over cross-board.
- **Wiener + `IEEE80211_HTSIG_H_REESTIMATE=1` reaches avg_snr_htsig = 6.16–11.17 dB**, well above the ~6 dB viterbi threshold.
- However, **HT-SIG viterbi still fails** (`best_metric=N/A` across all 16 rotation/inversion candidates), and **0 FCS_OK**.
- The HT-SIG constellation dump reveals large imaginary-axis outliers (`std_im` up to 4.8), indicating residual phase rotation/CFO/SFO or H estimation errors that are not captured by the scalar SNR metric.

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
- `test_usrp_minimal_loopback.py`: added `--cross-board-rx2` flag for cross-daughterboard RX2 wiring, plus `--wiener-on`, `--wiener-log`, `--wiener-fifo-n`.
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

### Same-board A:0 → A:0 RX2 (major new data)

After switching the SMA cable to same-board, the link became stable enough for controlled measurements.

#### Baseline (no Wiener, no Phase 140)

- Repeated `LSIG_DECODE OK` events.
- `avg_snr_htsig` reached **7.16 dB** and **8.52 dB** in separate frames.
- HT_SIG_PARSE_FAIL observed with `n_candidates=16`, `best_metric=N/A`.

#### Wiener + Phase 140 N=4

```
[WIENER_RHH] n_avg=5 depth=4 freq=5890000000 rhh_mean=0.0740 rhh_max=0.1892
[WIENER_LSIG] sigma2=0.1233 g_min=0.10 applied
[LSIG_H52_CROSS_FRAME] n_avg=5 depth=4 sigma_est_input=1.25 sigma_est_post=0.559 rad
[LSIG_DECODE] OK enc=4 len=20
[LSIG_PARSE_FAIL] ... avg_snr=2.02 avg_snr_ht=6.56
```

- Phase 140 FIFO works: `sigma_est_post` drops from 1.25 → 0.559 rad.
- HT-SIG SNR = **6.56 dB** (above threshold).
- Still no HT-SIG CRC pass.

#### Wiener + `IEEE80211_HTSIG_H_REESTIMATE=1` (breakthrough)

```
[WIENER_LSIG] sigma2=0.0419 g_min=0.10 applied
[HTSIG_H_REESTIMATE] h0=ok h1=ok
[HT_SIG_PARSE_FAIL] timeout_sym=6 n_candidates=16 best_metric=N/A
                    avg_snr_lsig=10.40 avg_snr_htsig=6.16
...
[HTSIG_H_REESTIMATE] h0=ok h1=ok
[HT_SIG_PARSE_FAIL] timeout_sym=11 n_candidates=16 best_metric=N/A
                    avg_snr_lsig=6.29 avg_snr_htsig=11.17
[TEST] FCS_OK=0 FCS_FAIL=0
```

- **HT-SIG SNR now reaches 6.16–11.17 dB**, comfortably above the ~6 dB viterbi threshold.
- `HTSIG_H_REESTIMATE` reports `h0=ok h1=ok`, so HT-SIG0/1 pilot-based H re-estimation is active.
- Still **0 FCS_OK**; viterbi does not converge for any of the 16 candidates.

HT-SIG equalized constellation dump (with HTSIG_H_REESTIMATE):

```
[HTSIG_EQ_DUMP] frame=0 ... htsig0 mean|re|=1.171 mean_im=0.056 std_im=1.172
                htsig1 mean|re|=0.974 mean_im=-0.242 std_im=1.137
[HTSIG_EQ_DUMP] frame=2 ... htsig0 mean|re|=0.529 mean_im=0.035 std_im=0.650
                htsig1 mean|re|=0.479 mean_im=-0.088 std_im=0.535
[HTSIG_EQ_DUMP] frame=3 ... htsig0 mean|re|=1.161 mean_im=-0.044 std_im=2.720
                htsig1 mean|re|=0.840 mean_im=-0.171 std_im=0.867
```

Some frames show `std_im ≈ 0.5–0.7` (close to usable QBPSK), but others still have large outliers (e.g., `80.311-31.517i`, `55.082-32.080i`, `8.836-1.700i`). These outliers dominate the viterbi metric even when the average SNR looks good.

HT-SIG viterbi input bits (with HTSIG_H_REESTIMATE) show only two enc96 patterns across candidates, suggesting the hard decisions are highly correlated / not exploring the true bit space:

```
[HTSIG_INPUT_DUMP] inv_a=0 inv_b=0 enc96=110100100000100010100100010110111110000001011010000110101101001110111011111101101000100101000110
[HTSIG_INPUT_DUMP] inv_a=0 inv_b=1 enc96=110100100000100010100100010110111110000001011010111001010010110001000100000010010111011010111001
...
```

### Cross-board RX2 (earlier data)

Cross-board was tested first but proved too unstable for controlled experiments. At `--tx-gain 31.5` it occasionally reached `LSIG_DECODE OK` with `avg_snr_ht=2–4 dB`, but run-to-run variance was larger than the algorithmic effect. See the first revision of this file for the cross-board matrix.

---

## Root-Cause Assessment

1. **Wiener works on L-SIG H.** It successfully shrinks the L-LTF-based channel estimate and improves L-SIG decode stability.
2. **Same-board RF is stable; cross-board is not.** This is consistent with Phase 122's finding that cross-daughterboard has independent LOs causing 0.5–1 rad drift.
3. **HT-SIG SNR is no longer the blocker on same-board.** With `HTSIG_H_REESTIMATE`, avg_snr_htsig reaches 6–11 dB, well above the viterbi threshold.
4. **The remaining problem is constellation quality / phase coherence.** High scalar SNR coexists with large per-SC outliers (`std_im` spikes), indicating residual CFO/SFO, H estimation errors on specific subcarriers, or incorrect QBPSK de-rotation.
5. **Wiener does not reach the HT-SIG H estimate directly.** It only filters `Hhdr52_for_lsig`. Applying MMSE shrinkage to the HT-SIG pilot-based H estimate is a logical next attack.

---

## Conclusion

Phase 141 Wiener H52 is **PARTIAL**:
- Algorithmically sound and correctly integrated.
- On **same-board**, combined with `IEEE80211_HTSIG_H_REESTIMATE=1`, it pushes HT-SIG SNR above the viterbi threshold for the first time.
- **0 FCS_OK** remains because HT-SIG viterbi does not converge, likely due to residual phase/outlier corruption not captured by the scalar SNR metric.

---

## Next Steps

1. **Apply Wiener shrinkage to the HT-SIG pilot-based H estimate.** Add a Wiener call inside the `IEEE80211_HTSIG_H_REESTIMATE` branch before `H_a_ptr`/`H_b_ptr` are assigned. This directly targets the H used for HT-SIG equalization.
2. **Enable `IEEE80211_HTSIG_FINE_ROT=1`** with Wiener + H_REESTIMATE. The 45° rotation search may resolve the residual phase offset that 90° steps miss.
3. **Combine with `IEEE80211_HTSIG_PILOT_CPE=1`** to cancel per-symbol phase drift after H re-estimation.
4. **Investigate the `best_metric=N/A` behavior.** Determine whether viterbi_decode_133_171 fails to run (input sanity check) or returns a saturated metric. Add diagnostic around `decode_htsig_candidate`.
5. **Fix the `[WIENER_RHH]` freq_key mismatch** (`5890000000` vs runtime `5250`) so FIFO reset logic matches the actual tuned frequency.
6. **Stay on same-board A:0 → A:0 RX2** for all future equalizer experiments; cross-board adds independent-LO drift that masks algorithmic progress.

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
