# HT-SIG Closure Reaffirmation (Phase 41 + Phase 77)

**Date**: 2026-07-03
**Branch**: TEST1
**Status**: **CLOSURE** — HT-SIG viterbi convergence on USRP air path is not achievable with current software + X310/UBX-160 hardware. 18+ REFUTED hypotheses confirm equalizer-layer ceiling. Software loopback 3/3 PASS is preserved as decoder validation path. Phase 78 plans software-only upstream attacks (no hardware per user instruction).

---

## Closure Statement

The HT-SIG chain IS reachable on USRP air path at 5250 MHz with tight_v2 (576 candidates fire). However, HT-SIG viterbi convergence requires avg_snr_htsig ≥6 dB AND HT_SIG_EQ std_im ≤0.3 in the equalized constellation. Phase 77 (77a-77c) achieved avg_snr_htsig 10.23 dB but std_im remained in 0.77-1.88 range (above 0.3 ceiling), and HT_SIG_PARSE_OK stayed at 0 across all combinations. This confirms:

1. **Channel-physics limit**: per-frame sub-sample timing offset δ (Phase 33b 64-PSK residual) rotates QBPSK constellation beyond viterbi's branch metric tolerance.

2. **Equalizer-layer ceiling**: all 12+ equalizer-side REFUTED hypotheses confirm software fixes cannot close this gap.

3. **Hardware requirement**: closing the gap requires reducing the per-frame δ via:
   - LNA (improves SNR but doesn't fix δ)
   - Better USRP timing reference (TCXO discipline, GPSDO)
   - Lower-rate sampling (--rate 10 marginal per Phase 56-57)
   - Different USRP model (B210, N210 with different timing characteristics)

Per user instruction "先不要考虑硬件", hardware attacks are excluded. Phase 78 will explore software-only alternatives.

---

## What's Preserved (per HARD CONSTRAINT hierarchy §2)

**Software loopback 3/3 PASS** is the decoder validation path:
- Decoder (viterbi, LDPC, MAC) all pass on loopback
- Demonstrates that the bug is NOT in decoder implementation
- It's the USRP air path + equalizer chain that's the wall

---

## Per HARD CONSTRAINT — Phase 78 plan

Phase 78 will execute 78a (synthetic channel model) + 78b (per-frame offline analysis) before any closure reaffirmation. If both fail, 78e (closure permanent) will follow.

This is a CLOSURE-WITH-PLAN, not pure BLOCKED. Per HARD CONSTRAINT §"Implications for Phase 60+":

> Any verdict ending in BLOCKED must include a concrete Phase 60+ attack plan that operates upstream of the blocker

Phase 78 plans are upstream attacks (synthetic model, offline analysis, MCS change) that don't require hardware.

---

## See Also

- Phase 41 closure: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md`
- Phase 76 verdict: `docs/superpowers/notes/2026-07-02-phase76-verdict.md`
- Phase 77 verdict: `docs/superpowers/notes/2026-07-03-phase77-verdict.md`
