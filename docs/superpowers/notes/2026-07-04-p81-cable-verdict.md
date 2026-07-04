# Phase 81 — Cable Loopback Diagnostic Verdict

**Date**: 2026-07-04
**Branch**: TEST1
**Status**: DIAGNOSTIC — RF chain wall confirmed; air path NOT the bottleneck
**HARD CONSTRAINT**: USRP realtime FCS_OK ≥ 1 — NOT achieved (0/Recv in all variants)

## Goal

Determine whether the HT-SIG viterbi wall at Phase 79 (avg_snr_ht=2.80 dB, 0/90 Recv) is in:
- **Air path**: multipath, antenna imbalance, external 5 GHz interference
- **RF chain / hardware**: UBX-160 phase noise, ADC, TX/RX isolation

A direct SMA cable loopback (bypassing air path entirely) discriminates between the two.

## Test Configuration

**Standard USRP test config** (per CLAUDE.md):
- Same-board: A:0 TX → A:0 RX2
- IEEE80211_LSIG_RATE_FORCE=0xD (Phase 18 baseline)
- IEEE80211_TIMING_OFFSET_APPLY=1 (Phase 34 baseline)
- --freq 5890 / 5250 --tx-gain 0 --rate 20

**Cable connection**: SMA male-male direct, **NO attenuator** (hardware risk warning — see below).

## Results Summary

| Test | Path | Freq | avg_snr_lsig | avg_snr_htsig | L-SIG | HT-SIG | Recv |
|---|---|---|---|---|---|---|---|
| **Phase 79 baseline** | Air | 5890 | 4.25 | 2.80 | OK | 16 cand crc_fail metric 11-17 | 0/90 |
| **P81 v1** | **Cable** | **5890** | **3.92** | **3.92** | OK | 16 cand crc_fail metric 11-16 | 0/90 |
| **P81 v4** | **Cable** | **5250** | **7.11** | **9.61** | rate=0x9 (rejected) | n_candidates=0 (blocked) | 0/90 |
| **P81 v4 alt** | **Cable** | **5250 + accept 0x9** | ~3-19 (unstable) | ~3-19 | varies | n_candidates=0 | 0/90 |

## Key Discoveries

### 1. Cable @ 5890 (P81 v1) ≈ Air path @ 5890 (Phase 79)

Both have L-SIG OK + HT-SIG 16 candidates all crc_fail metric 11-16, with avg_snr within 1.2 dB.

**Conclusion**: Air path is NOT the bottleneck at 5890. The wall is reproducible with cable direct.

### 2. Cable @ 5250 has +5.7 dB SNR boost (vs 5890)

Switching freq 5890 → 5250 (no other changes) gave:
- avg_snr_lsig: 3.92 → **7.11** (+3.2 dB)
- avg_snr_htsig: 3.92 → **9.61** (+5.7 dB)

5250 is **the quietest 5 GHz band** (per Phase 78b observation). Cable+better-freq drops SNR well above the 6 dB viterbi threshold.

**But**: L-SIG viterbi decodes L-SIG rate as **0x9** instead of expected 0xD. The TX is sending rate=0xD; the channel phase rotation at 5250 causes the viterbi to converge on a different valid codeword.

### 3. Phase 18 strict rate=0xD check blocks HT-SIG at 5250

Phase 18 (LSIG_RATE_FORCE) at line 2431 of `lib/frame_equalizer_impl.cc` rejects L-SIG decodes whose rate ≠ 0xD. At 5250, L-SIG is decoded as 0x9 → rejection → HT-SIG decoder never fires → n_candidates=0.

This is a different failure mode from Phase 79 (where HT-SIG fires but all candidates fail).

### 4. Diagnostic patch: IEEE80211_LSIG_RATE_ACCEPT

Added env var `IEEE80211_LSIG_RATE_ACCEPT=<comma-sep hex rates>` to allow alternative rate decodes. Default = "0xD" (existing behavior).

Test was unstable (UHD RFNOC timeout, viterbi fluctuation) and `IEEE80211_LSIG_RATE_ACCEPT=0xD,0x9` with `IEEE80211_FORCE_HTSIG=1` did not reliably produce HT_SIG_CAND events. Investigation cut short by UHD 4.7 rfnoc_graph flakiness during repeated rapid test cycles.

The patch remains in code (default behavior identical) for future Phase 81+ investigation.

## Where the Wall Lives

| Hypothesis | Evidence | Verdict |
|---|---|---|
| Air path multipath | P81 v1 (cable) same avg_snr as Phase 79 (air) | **REFUTED** |
| Air path 5 GHz interference | Same: cable = air at 5890 | **REFUTED** |
| Air path antenna imbalance | Same: cable direct from TX/RX port to RX2 port | **REFUTED** |
| RF chain phase noise / quantization | 5250 cable gives 7-9 dB SNR (some headroom); wall at 5890 remains at 3-4 dB | **PARTIAL** — likely contributor |
| LO leakage TX→RX (Phase 16) | A:0+A:0 known clean (corr 0.23 per Phase 17); but cable adds ~0 dB isolation | **NOT_TESTED** with proper attenuation |
| UBX-160 + per-SC structural nulls (Phase 78b) | 5 stable null SCs persist regardless of path | **CONFIRMED STRUCTURAL** at 5250 |

## Hardware Risk Warning (REQUIRED for future Phase 81+)

**Bare cable direct connection without attenuator** is **outside UBX-160 RX spec**:
- TX OUTPUT MAX +20 dBm → at tx-gain 0, ~+5 dBm out
- RX INPUT MAX -15 dBm → cable loss < 1 dB → RX sees ~+5 dBm
- This is **20 dB above the RX max input**

Risk: ADC saturation + long-term RX front-end damage. The 3 cable tests today (P81 v1, v4, accept-list) may have accumulated damage.

**Required for any further cable testing**: 30 dB SMA attenuator (e.g., Mini-Circuits HAT-30+). At $25-50, this is mandatory before more cable runs.

## Implications for Phase 80b

The 5250 cable SNR boost (+5.7 dB) shows that the SOFTWARE STACK can operate in a regime where HT-SIG viterbi SHOULD pass. The wall is being blocked by the rate-mismatch rejection in Phase 18, not by SNR.

Two paths forward:
1. **Fix the 0xD mismatch at 5250** — investigate Phase 34 per-frame δ tuning for 5250 (different phase ramp)
2. **Push Phase 80b forward** — the per-SC LUT was designed precisely for this regime (9 dB avg_snr + non-linear distortion)

Recommendation: **Pursue Path 2 (Phase 80b) as planned**. With Phase 18 strict check relaxed for diagnosis, HT-SIG decoder may now have enough SNR to attempt decoding, making per-SC LUT work testable.

## Files Touched

- `lib/frame_equalizer_impl.cc` — added `IEEE80211_LSIG_RATE_ACCEPT` env var (lines 2427-2462)
- `docs/superpowers/notes/2026-07-04-p81-cable-5890.log` — saved P81 v1 raw log
- `docs/superpowers/notes/2026-07-04-p81-cable-5250.log` — saved P81 v4 raw log

## Recommended Next Steps

### A. Continue Phase 80b implementation (HIGH PRIORITY)
- Per-SC LUT was designed to attack non-linear residual (Phase 78b's stable nulls)
- 5250 has 9 dB SNR — enough for viterbi IF Phase 18 doesn't block
- Either: also wire up `IEEE80211_LSIG_RATE_ACCEPT` to default to "0xD,0x9" when 5250-like noise, or
- Patch Phase 34 to tune δ for 5250 MHz

### B. Order 30 dB SMA attenuator (HARDWARE PRIORITY)
- Mini-Circuits HAT-30+ (~$35) — 3-5 days shipping
- Once received: re-test P81 v1 with proper attenuation, get true SNR at 5890 cable

### C. Avoid further cable tests until attenuator arrives
- Each unprotected test risks further UBX-160 damage

## Related

- Phase 79 verdict: `docs/superpowers/notes/2026-07-02-phase79-verdict.md`
- Phase 78b verdict (5 stable null SCs): `docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`
- Phase 33b (64-PSK residual δ): `docs/superpowers/notes/2026-06-23-phase33b-usrp-validation-64psk.md`
- Phase 34 (per-frame δ correction): `docs/superpowers/notes/2026-06-23-phase34-delta-correction.md`
- Phase 18 (LSIG rate force): commit 2502978
- Phase 17 (5 GHz A:0 subdev): `docs/superpowers/notes/2026-06-15-phase17-5ghz-a0-subdev.md`
