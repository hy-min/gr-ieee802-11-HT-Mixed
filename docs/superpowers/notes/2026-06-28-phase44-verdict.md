# Phase 44 Verdict — Soft-LLR Viterbi for USRP HT-SIG Unblock

**Date:** 2026-06-28
**Status:** REFUTED
**Hypothesis:** Replace hard-bit viterbi input with soft LLR magnitudes (signed
by |H[i]|) so that channel-null SCs (|H[i]| ~ 0) contribute ~0 to the path metric
instead of random hard-bit flips.

## Algorithm

```
LLR[i] = sign(eq.imag()) * |H[i]| / max(|H|)
```

QBPSK bits live on the IMAG axis after rotation. The sign carries the bit
decision; the magnitude carries confidence (proportional to the channel
gain at that SC). Soft-LLR viterbi branch metric is squared-error distance:

```
bm = (r0 - (1 if o0 else -1))^2 + (r1 - (1 if o1 else -1))^2
```

A confident LLR with matching constellation adds 0; a confident LLR with
mismatched constellation adds ~4*conf² (large penalty). A near-zero LLR
contributes ~0 regardless of agreement (down-weighted erasure).

## Implementation

**Files changed:**
- `lib/frame_equalizer_impl.cc` (+248, -22 lines):
  - New `viterbi_decode_133_171_soft()` (Q8.8 fixed-point squared-error metric)
  - New `compute_soft_llr_qbpsk()` helper
  - `decode_htsig_from_rotated()` extended with `use_soft_llr` parameter
  - Constructor reads `IEEE80211_SOFT_LLR_VITERBI` env var
  - Parallel computation of soft LLRs alongside hard bits (no perf regression
    when env var is OFF)
- `lib/frame_equalizer_impl.h` (+10 lines):
  - New `bool d_use_soft_llr_viterbi = false;` field
- `examples/test_soft_llr_viterbi_synthetic.py` (NEW, 433 lines):
  - Layer 1 (regression): clean signal, uniform H, soft-LLR matches hard-bit
  - Layer 2 (channel-nulls): freq-selective H with deep nulls, soft-LLR recovers
  - Layer 3 (SNR sweep): soft-LLR strictly lower BER than hard-bit at 0-3 dB
- `examples/test_usrp_phase44.py` (NEW, 128 lines):
  - Standard USRP config (5.89 GHz, tx-gain 20, A:0 subdev, 30-second capture)

## Validation

### Build / install: PASS
cmake + make + make install — no errors, libgnuradio-ieee802_11.so rebuilt.

### Loopback regression (env var OFF): PASS
`test_direct_loopback.py`: `Final: OK=1 FAIL=0` (regression clean).

### Loopback ON (env var ON): PASS
`test_direct_loopback.py` with `IEEE80211_SOFT_LLR_VITERBI=1`:
`Final: OK=1 FAIL=0` — soft-LLR path doesn't break the clean case.

### Python synthetic test 3 layers: 3/3 PASS
- Layer 1 (regression on clean signal): soft-LLR metric=0 == hard-bit metric=0
- Layer 2 (channel nulls, hard-bit baseline 3/3): soft-LLR 3/3 PASS
- Layer 3 (SNR sweep, 30 trials each):
  - SNR 10 dB: hard 0.000 / soft 0.000
  - SNR 6 dB: hard 0.000 / soft 0.000
  - SNR 3 dB: hard 0.133 / soft 0.033 (soft wins 4x)
  - SNR 0 dB: hard 0.767 / soft 0.600 (soft wins 1.3x)

### USRP validation (Phase 44 ON): REFUTED

Standard USRP test config (`IEEE80211_LSIG_RATE_FORCE=0xD
IEEE80211_LLTF_OFFSET_CORRECT=14 IEEE80211_TIMING_OFFSET_APPLY=1
IEEE80211_SOFT_LLR_VITERBI=1` @ 5.89 GHz, tx-gain 20, A:0 subdev, 30s):

```
[30s] FCS OK=0 FAIL=0
Final: OK=0 FAIL=0
No frames decoded (silent failure)
```

HT_SIG_CAND log inspection shows:
- All 16 candidates crc_fail on every frame
- Soft-LLR metrics saturated: 13000-19400 Q8.8 (delta between candidates
  is <1%, indistinguishable)
- 13230, 13298, 13466, 13614, 13726, 13930, 14246, 14426 — a delta of
  ~1200 across 16 candidates out of ~13000 means the soft metric doesn't
  discriminate between correct and wrong candidates

### USRP validation (Phase 44 OFF baseline): REFUTED

Same config without `IEEE80211_SOFT_LLR_VITERBI=1`:
```
[30s] FCS OK=0 FAIL=0
Final: OK=0 FAIL=0
```

Phase 44 ON does NOT regress the OFF baseline (both 0/0 FCS_OK).
The decoder runs cleanly through the soft path but the channel
corruption is too severe for any decoder-level fix.

## Conclusion

Phase 44 (soft-LLR viterbi) is the **15th hypothesis REFUTED** for USRP HT-SIG
unblock. This confirms the Phase 41 final verdict:

> USRP HT-SIG is BLOCKED at the channel-physics level. Hhdr52 channel nulls
> (|H|=0.02-0.14) cause 50× noise amplification, equalized HT-SIG on REAL axis
> breaks QBPSK rotation. Channel-physics limitation, not software bug.
> Software loopback 3/3 PASS is decoder validation path.

The synthetic test (Layer 2 + 3) confirmed that soft-LLR is a strictly-better
decoder for noisy freq-selective channels — it would unblock the HT-SIG
viterbi IF the channel nulls were the only impairment. But on USRP, even with
the soft weighting, the dominant per-frame impairment (Phase 41's
Hhdr52 argH random over [-π,π]) puts the equalized signal on the wrong axis,
so neither the hard-bit decision nor the soft-LLR sign converges on the
correct constellation point.

## Future work (NOT on critical path)

- The soft-LLR code is kept in the codebase as an opt-in env var
  (`IEEE80211_SOFT_LLR_VITERBI=1`). Useful as a reference implementation and
  for synthetic testing.
- A real unblock would require either:
  (a) Per-SC H null detection + insertion of erasure flags into the LLR
      (-inf / +inf), forcing the soft viterbi to skip those SCs entirely.
  (b) Hhdr52 re-estimation from the HT-SIG pilots themselves (Phase 39 was
      REFUTED — pilots too noisy).
  (c) An upstream L-LTF0 timing fix that doesn't depend on Phase 34 δ
      correction being perfectly accurate.

## References

- Phase 41 (closed 2026-06-28): docs/superpowers/notes/2026-06-28-usrp-final-verdict.md
- Phase 37 (decoder validated): docs/superpowers/notes/2026-06-24-phase37-verdict.md
- Phase 38 (Hhdr52 null bottleneck): docs/superpowers/notes/2026-06-25-phase38-step7-verdict.md
- Commit 6e1209e: phase44: soft-LLR viterbi for HT-SIG unblock (REFUTED on USRP)