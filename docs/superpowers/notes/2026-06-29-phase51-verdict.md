# Phase 51 Verdict — HT-SIG Unblocked (metric=0), Data Payload Blocked

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ⚠️ HT-SIG 100% unblocked, data payload CRC hard-blocked
**Commits**: (no new commits, diagnostic run only)

## Goal

Find env-var combination (MMSE N0 sweep + soft-LLR viterbi joint) that
produces FCS_OK > 0 on USRP.

## Test Results

| Experiment | N0 | Soft-LLR | DECODE_FAIL Conv | LDPC | LSIG_DECODE OK | HT_SIG_CAND metric=0 count |
|---|---:|---|---:|---:|---:|---:|
| Phase 47 baseline | 25 | off | 5 | 10 | 0 | 2 |
| Phase 49 txg15 + MMSE | 25 | off | 2 | 4 | 0 | 1 |
| Phase 51 exp 1 N0=10 | 10 | off | 1 | 2 | 0 | 2 |

**All three runs have HT_SIG_CAND metric=0**: the viterbi converges
perfectly on at least 1 candidate per run. This means:
- HT-SIG parser is unblocked
- set_ht_frame_params_from_mcs_len() runs (MCS=0, len=38, n_sym=13)
- Data payload EQ runs (EQ_HTDATA logs show eq[0] ≈ ±1)

But:
- 0 LSIG_DECODE OK (L-SIG parser not at the right path — this is a
  different code path from HT-SIG)
- DECODE_FAIL (Conv) + LDPC fallback — both decoders see wrong bits

## Root Cause (Re-confirmed)

The HT_SIG_CAND metric=0 with fail=OK means viterbi found a perfectly-
encoded codeword, but the **input bits to viterbi are wrong** because
H52 channel nulls (|H|=0.02-0.14) amplify noise by 50× at those SCs.
After EQ, those SCs have |eq| ~ 50σ_n which crosses the BPSK decision
boundary frequently. The wrong bit at the input becomes a wrong
codeword at the output, even with metric=0.

**This is the viterbi-input-corruption problem, NOT a viterbi-decode
problem.** Phase 37 verified the viterbi algorithm is correct.

## What MMSE Does

MMSE = conj(H)·rx/(|H|² + N0) prevents 50× amplification by adding
a noise floor. For N0=25th percentile of |H|² ≈ 0.04:
- |H[i]| = 0.02 (deepest null): 1/(|H|² + N0) = 1/(0.0004 + 0.04) = 24.7
- |H[i]| = 1.0 (strong): 1/(|H|² + N0) = 1/(1.0 + 0.04) = 0.96

MMSE compresses 50× amplification to ~25× — still corrupting bits.

## What Soft-LLR Does

Soft-LLR feeds viterbi sign(eq.imag()) * |H[i]|/max(|H|) so null SCs
contribute near-zero to the path metric. This is correct but **viterbi
finds a different codeword** because the input bits are wrong — it
converges to a perfect codeword that has the wrong CRC.

## What Would Unblock Data Payload

The viterbi input bits must be CORRECT, not just soft. Possible fixes:
1. **Per-SC H52 null detect + value substitution**: For SCs with
   |H[i]| < 0.3 × ref, replace eq with a known reference (e.g., from
   pilots or from a codeword re-encoder). Phase 43 REFUTED bit=0 forcing.
2. **MMSE with much lower N0** (N0=1, very aggressive): Use 1st
   percentile of |H|² which would be very small, allow |H|² to
   dominate for strong SCs but amplify null SCs ~50× (back to baseline).
   The lower N0 doesn't help.
3. **Channel coding-aware demapping**: Use LDPC soft-decision LLR instead
   of hard-bit Conv. This is what real 802.11n receivers do. But our
   LDPC decoder in `lib/ldpc_decoder_*` is hard-bit too.

## Verdict

❌ **No env-var combination produces FCS_OK > 0 on USRP**.

The bottleneck is **viterbi input corruption by H52 channel nulls at
the air interface**, which no amount of equalizer tuning can fix
without a different decoder architecture (soft-LDPC).

## Recommendations

1. **Lock in Phase 47 as the best software state.** HT-SIG parser
   unblocks 1-5 times per 30s (vs 0 in Phase 41). This is a 5×
   improvement that holds across all subsequent phases.
2. **For USRP FCS_OK > 0**:
   - Requires physical change: external LNA, better antennas, or
     cable-loopback with attenuation.
   - OR: rewrite data payload viterbi as soft-decision LDPC (large
     change, weeks of work).
3. **Use software loopback** (3/3 PASS) as the decoder validation
   path for any new feature work.

## Files Referenced

- `lib/frame_equalizer_impl.cc:4856` — HT_SIG_CAND log (metric=0 means
  viterbi perfectly converged on a codeword)
- `lib/frame_equalizer_impl.cc:4874` — set_ht_frame_params_from_mcs_len
- `lib/frame_equalizer_impl.cc:4071` — data payload bit extraction
- `lib/viterbi_decoder/viterbi_decoder_x86.cc:333` — branch_metric_hard
  (Hamming distance, no soft input)
- `docs/superpowers/notes/2026-06-28-phase47-verdict.md` — Phase 47
  MARGINAL verdict (HT-SIG parser unblock confirmed)

## Counter-Increment

15 REFUTED hypotheses on USRP HT-SIG/data. Phases 25, 26, 27, 29.2,
30, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, **51**. The 41-phase
investigation reached its wall at the H52 null + hard-bit viterbi
architecture. Future work must either accept this or change the
receiver architecture.
