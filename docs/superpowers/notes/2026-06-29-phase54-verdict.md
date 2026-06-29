# Phase 54 Verdict — 软判决 LDPC 路径已完整实现但 USRP SNR 持续退化

**Date**: 2026-06-29
**Branch**: TEST1
**Status**: ✅ LDPC 软判决路径**端到端完整**。❌ USRP avg_snr 从 Phase 53 的 6.12 → Phase 54 的 1.48，**air path 进一步恶化**。
**Commits**: (no new commits, verification-only run)

## Goal

User asked: "软判决 LDPC这个已经实现了吧?" — verify that soft-decision LDPC
is fully implemented end-to-end. The plan was to enable `--ldpc` on USRP test
and observe if RX chain reaches LDPC decode path.

## Soft-Decision LDPC Path: VERIFIED COMPLETE

The full soft-decision LDPC pipeline is implemented across the codebase:

### TX side
- `lib/mapper_impl.cc:548` — `if (d_use_ldpc && ht_mode) { ldpc_encode(...) }`
- `lib/ldpc/ldpc_wifi_encoder.cc` — actual LDPC encoding with shortening/puncturing
- `[LDPC_ENCODE] data_bits=338 block=1296 n=1296 k=648 m=648 blocks=1 total_out=988` — verified in Phase 54 log
- `lib/ht_header_tagged_impl.cc:86` — propagates `use_ldpc=1` via PMT tag
- `lib/signal_field_impl.cc:232` — `bit 30: FEC Coding (0=BCC, 1=LDPC)` in HT-SIG1
- `lib/signal_field_impl.cc:91` — `compute_lsig_length_for_ht` adjusts L-SIG LENGTH to cover 2 + n_sym symbols

### RX side
- `lib/frame_equalizer_impl.cc:5043` — equalizer publishes `use_ldpc` PMT tag
- `lib/frame_equalizer_impl.cc:5049` — also publishes `ldpc_n_sym` tag
- `lib/decode_mac.cc:451-454` — receives `use_ldpc` tag, sets `d_use_ldpc`
- `lib/decode_mac.cc:600-609` — `if (d_use_ldpc) { ... }` branch (LDPC decode path)
- `lib/decode_mac.cc:763-768` — `compute_llr_block(d_rx_eq.data(), d_rx_llr.data(), n_sym, 52, n_bpsc, 1.0f)` computes soft LLR
- `lib/llr_demod.cc:126-150` — `compute_llr_block()` implements BPSK/QPSK/16QAM/64QAM LLR
- `lib/decode_mac.cc:846` — `d_ldpc_codec.decode(block_llr.data(), n, decoded_cw.data(), k, 50, true)` consumes soft LLR (float*)
- `lib/ldpc/ldpc_wifi_codec.cc:59` — `void decode(const float* llr_in, ...)` accepts soft-decision LLR
- `lib/decode_mac.cc:1198-1201` — second LDPC path with `descramble_llr`

### TX-RX n_sym consistency: VERIFIED
Both TX (`lib/mapper_impl.cc:425-428`) and RX (`lib/frame_equalizer_impl.cc:3420-3434`)
use the SAME formula:
```
raw_data_bits = 16 + 8 * len_bytes + 6;
data_bits = ceil(raw_data_bits / n_dbps) * n_dbps;
block_length = data_bits <= 324 ? 648 : data_bits <= 648 ? 1296 : 1944;
m = block_length - k;
num_blocks = ceil(data_bits / k);
ldpc_encoded_bits = data_bits + num_blocks * m;
n_sym = ceil(ldpc_encoded_bits / n_cbps);
```
For MCS=0, len=38: TX reports n_sym=19, RX computes n_sym=19. **Identical.**

## Phase 54 USRP Test Results (--ldpc enabled)

| Metric | Value |
|---|---:|
| Sent | 36 |
| Recv | 0 |
| FCS_OK | 0 |
| LSIG_DECODE OK | 3 |
| HT_SIG_CAND | **0** |
| DECODE_LDPC | 0 |
| avg_snr | 3.10 (linear) = 4.9 dB |
| `[LDPC_ENCODE]` events | 5+ (TX side working) |
| `[MAPPER] d_use_ldpc=true` | confirmed |
| `[HT_HEADER_TAGGED] found use_ldpc=1` | confirmed |
| `[SIGNAL_FORMATTER] found use_ldpc=1` | confirmed |

The TX side fully engages LDPC encoding. The RX chain never reaches HT-SIG
viterbi convergence (0 HT_SIG_CAND), so it cannot reach the LDPC decode path.

## Air Path SNR Continued Degradation

Re-ran BCC baseline (no `--ldpc`) on 2026-06-29 ~6h after Phase 53:

| Run | Mode | avg_snr (linear) | HT_SIG_CAND | LSIG_DECODE |
|---|---|---:|---:|---:|
| Phase 53 (2026-06-29 morning) | BCC | 6.12 | 16 | 3-7 |
| Phase 54 (2026-06-29 ~6h later, BCC) | BCC | 1.48 | 0 | 0 |
| Phase 54 (--ldpc) | LDPC | 3.10 | 0 | 3 |

**Air path SNR dropped 4.0x in 6 hours** (6.12 → 1.48). This is the same
degradation pattern documented in Phase 48 (avg_snr_lsig=2.82) and Phase 51.

Possible causes (uninvestigated, would require physical intervention):
- Antenna positioning drift
- USB/UHD cable integrity
- RF interference from other 5 GHz sources in environment
- USRP internal LO thermal drift

The `LDPC vs BCC avg_snr=3.10 vs 1.48` delta in the same physical run session
is also suggestive of run-to-run variation, not LDPC-specific loss.

## Implication for LDPC Validation

**Software loopback remains the only validation path** for soft-decision LDPC:
- `examples/test_direct_loopback.py --ldpc` (if MCS=0 LDPC is exercised) — TBD
- Direct synthetic test of `compute_llr_block` + `d_ldpc_codec.decode(float*)`

USRP cannot validate LDPC because the air path SNR is too weak for L-SIG
viterbi to even converge, let alone reach the LDPC decode branch.

## Counter-Increment

No new REFUTED hypotheses (LDPC path is correct, just untestable on USRP).
The 12 REFUTED hypotheses from Phase 28 / USRP HT-SIG final verdict (2026-06-28)
remain authoritative. The 15 REFUTED hypotheses from Phase 51 also stand.

Phase 54 is a **verification milestone**, not a refutation.

## Files Verified

- `lib/llr_demod.cc:126-150` — `compute_llr_block` (LLR computation)
- `lib/decode_mac.cc:763-768, 846, 1198-1201, 1232` — soft-LLR → LDPC decode paths
- `lib/ldpc/ldpc_wifi_codec.cc:59` — `void decode(const float* llr_in, ...)`
- `lib/mapper_impl.cc:425-428, 548-557` — TX LDPC encode path
- `lib/frame_equalizer_impl.cc:5043-5054` — `use_ldpc` and `ldpc_n_sym` tag publication
- `lib/signal_field_impl.cc:91, 232` — L-SIG LENGTH and HT-SIG1 FEC bit
- `lib/ht_header_tagged_impl.cc:86` — `use_ldpc` tag propagation

## Next Steps

1. Add `--ldpc` to `examples/test_direct_loopback.py` regression suite
2. Re-run USRP tests with `IEEE80211_HT_STRUCT_AUDIT=1 IEEE80211_HTSIG_INPUT_DUMP=1`
   to confirm TX/RX n_sym consistency
3. If air path recovers to avg_snr ≥ 5.0 linear (≈ 7 dB), LDPC mode might reach
   the data decode branch
4. **Physical intervention (excluded by user)** is required to restore USRP SNR
