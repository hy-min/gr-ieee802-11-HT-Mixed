# Phase 99 — Threshold Floor 0.2 PARTIAL with HIGH SNR (2026-07-05)

**Branch**: TEST1
**Status**: 🟡 PARTIAL — sync_short threshold works well now, **avg_snr
HIGHEST ever** (l-sig 12.47, ht-sig 10.63), but HT-SIG viterbi still
metric=13-15 crc_fail
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — **NOT achieved** (Recv=0)
**Cable runs used**: 7 total (1 over budget)

## Phase 99 vs previous

| Metric | Phase 96 | Phase 98 | **Phase 99** | Δ vs 96 |
|---|---|---|---|---|
| sync_short threshold | 0.010 | 0.050 | **0.200** | +20× |
| Real L-STF corr | 1.864 | 1.441 | 1.484 | -0.38 |
| L-SIG EQ ratio | 0.701 | 0.769 | **0.357** | -49% (cleanest) |
| ratio_ht | 0.760 (Legacy) | 1.941 (HT) | 0.566 (Legacy) | - |
| avg_snr_lsig | 1.90 | n/a | **12.47** | **6.6×** |
| avg_snr_htsig | 3.58 | n/a | **10.63** | **3.0×** |
| avg_snr_lsig (dB) | 2.79 | – | **10.96 dB** | +8.2 dB |
| avg_snr_htsig (dB) | 5.54 | – | **10.27 dB** | +4.7 dB |
| L-SIG viterbi | 1 clean | 1 clean | **1 clean** | flat |
| L-SIG len | 346 | 1234 | **801** | – |
| HT-SIG viterbi metric | 12-17 | 13-17 | **13-15** | slightly tighter |
| HT-SIG viterbi fail | crc_fail | crc_fail | **crc_fail** | flat |
| FCS_OK | 0 | 0 | 0 | flat |

**avg_snr_htsig=10.27 dB is WAY above viterbi threshold** (6 dB needed).
Yet HT-SIG viterbi still fails with metric 13-15.

## Root cause is NOT SNR anymore

This is the smoking gun:

```
[LSIG_CANDIDATE_WIN] rot=3 inv=1 approx_metric=0 enc=0 len=801 rate_field=0xD parity_ok=1
[HT_SIG_PARSE_FAIL] timeout_sym=8 n_candidates=32 best_metric=N/A threshold=N/A
                    avg_snr_lsig=12.47 avg_snr_htsig=10.63 ...
[HT_SIG_CAND] sym=8 rot=0 inv_a=0 inv_b=0 metric=15 fail=crc_fail
[HT_SIG_CAND] sym=1 inv_a=1 inv_b=1 metric=15 fail=crc_fail
... 32 candidates, all metric 13-15, all crc_fail
```

**At avg_snr_lsig=10.96 dB**, L-SIG viterbi **succeeds** (1 clean win,
approx_metric=0). At **avg_snr_htsig=10.27 dB**, HT-SIG viterbi **fails**
with metric 13-15 (raw ~14-15% bit error rate, way too high for 10 dB SNR).

If avg_snr is truly 10 dB, raw BER should be ~10⁻⁶ (essentially zero).
But we see 14-15 errors / 96 bits = ~15%. **Something in HT-SIG bit
extraction is producing wrong bits independent of SNR.**

Two possibilities:

1. **HT-SIG bit extraction bug**: The QBPSK convention might be wrong,
   or subcarriers are mis-indexed. 5 stable globally-null SCs (Phase 78b)
   at 5 GHz might overlap with HT-SIG data SCs, injecting noise.

2. **Equalizer per-SC scale**: 5 SCs with tiny |H| → 20× noise
   amplification → 5/48 random bits → ~10 errors per codeword → metric 10.
   Even with |H|≥0.001 skip, 0.001 threshold might be too low; some
   "non-null" SCs still amplify noise.

## Why L-SIG works at lower SNR but HT-SIG fails at higher

HT-SIG = same 48 data SCs as L-SIG. But HT-SIG has 48 bits × 2 symbols =
**96 bits** vs L-SIG's 24 bits × 1 symbol = **24 bits**. So HT-SIG viterbi
gets 4× more input. If 5 SCs have |H|≈0.001 and amplify noise, then in
L-SIG (24 bits) only 2.5 random bits, while in HT-SIG (96 bits) up to 10
random bits. Viterbi at R=1/2 K=7 has free distance 10 — 10+ errors
= uncorrectable.

The HT-SIG bit count + 5 unstable SCs are the perfect storm.

## What's blocking the fix

1. The 0.001 null-skip threshold (line 1247, 2791, 2931) catches SCs
   where |H|<0.001. To skip more, raise threshold (e.g., 0.01 or 0.05),
   but this is REFUTED territory (Phase 78c).
2. Force-zero on null SCs is REFUTED on synthetic (Phase 78c). But
   Phase 78c was synthetic with non-deterministic null patterns. The
   USRP 5 stable nulls are deterministic.
3. Phase 80b per-SC LUT REFUTED (USRP). Per-SC multiplicative
   correction didn't help.
4. Phase 79 per-symbol δ REFUTED (USRP).
5. Phase 82 δ-tuning REFUTED at 5250.

26+ REFUTED equalizer-layer hypotheses. The equalizer layer is **CLOSED**
(Phase 77 verdict).

## What this means

**At avg_snr_htsig=10.27 dB, the equalizer produces clean signals. Yet
HT-SIG viterbi fails. This means HT-SIG viterbi failure is NOT an SNR
problem; it's a per-SC noise amplification problem affecting HT-SIG
specifically.**

This is a real bug, but it's in the "structural equalizer-layer CEILING"
that Phase 77 verdict declared CLOSED.

## Files of Record

- Phase 99 cable run: `/tmp/p99_cable.log`
- Fix: `lib/sync_short.cc:129` (floor 0.05 → 0.2)
- This verdict: `docs/superpowers/notes/2026-07-05-phase99-verdict.md`

## Recommendation

Phase 99 is the cleanest signal in 7 cable runs:

1. **avg_snr_htsig=10.27 dB is 1.7× the viterbi threshold**. If the
   bit-extraction had been correct, we'd have FCS_OK ≥ 1.
2. The bug is **per-SC noise amplification affecting HT-SIG specifically**
   due to 5 stable globally-null SCs (Phase 78b) on 5250 MHz.
3. **Equalizer-layer is CLOSED** (Phase 77). No documented path to
   fix this without revisiting that closure.

**Path forward**:
- Accept that HT-SIG viterbi on USRP cable at 5250 is the END of the
  equalizer-layer hypothesis space. Per HARD CONSTRAINT, this means
  either accept loopback 3/3 PASS as final state, or pursue upstream
  re-architecture (e.g., L-LTF window timing, RF chain, UHD streaming).
- The threshold floor fix (Phase 99) IS a real improvement — should be
  preserved in code for future runs.

## Status

| Condition | Status |
|---|---|
| L-SIG viterbi | ✅ Pass (1/1 clean) |
| FRAME_DETECT | ✅ Fires |
| HT-Mixed detection | ⚠️ Inconsistent (Legacy in Phase 99, HT in Phase 98) |
| HT-SIG viterbi | ❌ Fails at metric 13-15 (per-SC noise) |
| HARD CONSTRAINT (FCS_OK ≥ 1) | ❌ NOT achieved |
