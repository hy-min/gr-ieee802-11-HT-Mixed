# Phase 130: Per-SC LLR Zeroing for Null SCs (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on synthetic** — null SC LLR zeroing HURTS by 1pp
(soft+null: 26/50 vs soft-only: 27/50 at σ=1.0 rad; 0/50 vs 1/50 at σ=1.77 rad).
Random nulls (synthetic) are different from USRP stable nulls; C++ already
supports explicit masking via `IEEE80211_HTSIG_NULL_SCS`.

## TL;DR

T1 simulation (`test_p129_soft_llr_viterbi.py:test_soft_plus_null_zeroing`)
tested soft LLR + per-SC LLR zeroing on USRP-like channel with random nulls.

| sigma (rad) | soft-only | soft+null-zero | gain |
|-------------|-----------|----------------|------|
| 1.0         | 27/50     | 26/50          | **-1pp** |
| 1.5         | 0/50      | 0/50           | 0 |
| 1.77        | 1/50      | 0/50           | **-1pp** |
| 2.0         | 0/50      | 0/50           | 0 |

**Null zeroing hurts** because:
1. Soft LLR already DOWN-WEIGHTS low-|H|² SCs naturally (LLR ∝ |H|²/σ²)
2. Explicit zeroing REMOVES the small but positive information those SCs might have
3. Random nulls (synthetic) ≠ Phase 78b STABLE nulls (real USRP)

## C++ Implementation Already Exists

The Phase 102 + Phase 129 T2 paths already support null SC masking:
- `IEEE80211_HTSIG_NULL_SCS='12'` (CSV of HT-SIG data loop positions 0..47)
- For Phase 78b's stable nulls {-21, -13, -7, +7, +21}, only **-13** is in the
  data loop at position 12. The other 4 are PILOTS at kScIndex52[48..51].

So for real USRP data, user can set:
```
IEEE80211_HTSIG_NULL_SCS=12
```

This zeros LLR at position 12 (one SC) for both Phase 44 and Phase 129 v2 paths.

## Why Synthetic Differs from Real USRP

- **Synthetic channel**: 5-10 RANDOM nulls per frame (different positions each frame)
- **Real USRP (Phase 78b)**: 5 STABLE globally-null SCs at fixed positions

Random nulls means the LLR formula naturally averages over many frames and
captures which positions are consistently null. Explicit zeroing on RANDOM nulls
loses information from frames where those SCs happened to be OK.

Stable nulls are at the same SC every frame — the LLR formula CANNOT average
across them, so they appear as high-|H|² but always-noise. Explicit zeroing
would help in this case.

## Phase 130 Synthetic REFUTED → Try Phase 131 (multi-pass)

Per CLAUDE.md "30+ REFUTED" + user "不可能接受现状" directive:
- Don't repeat Phase 130 with different synthetic parameters — the architectural
  conclusion is clear: random nulls ≠ stable nulls, and explicit zeroing
  degrades soft LLR's natural weighting.
- Move to Phase 131 (multi-pass H52+δ refinement): use top-K viterbi candidates
  as pseudo-training to refine H52 and δ, iterate 2-3 times. This is the next
  decoder-internal attack with potential gain.

## Files

- Verdict: `docs/superpowers/notes/2026-07-09-phase130-null-zeroing-refuted.md`
- Simulation: `examples/test_p129_soft_llr_viterbi.py:test_soft_plus_null_zeroing` (line 403)
- T1 verdict: `docs/superpowers/notes/2026-07-09-phase129-t1-llr-synthetic.md`
- T2 verdict (C++ implementation): `docs/superpowers/notes/2026-07-09-phase129-t2-cpp-verdict.md`