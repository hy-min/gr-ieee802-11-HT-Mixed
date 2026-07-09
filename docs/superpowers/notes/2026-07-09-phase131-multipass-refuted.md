# Phase 131: Multi-Pass H52+δ Refinement (2026-07-09)

**Branch**: TEST1
**Date**: 2026-07-09
**Status**: 🔴 **REFUTED on synthetic** — multi-pass iteration HURTS by 2pp
(single-pass 23/50 vs multi-pass 21/50 at σ=1.0 rad; 0/50 vs 0/50 at σ=1.5+).

## TL;DR

T1 simulation (`test_p131_multipass.py:test_multipass_sweep`) tested
single-pass vs 3-iteration multi-pass decoder on USRP-like channel.

| sigma (rad) | single-pass | multi-pass (3 iter) | gain |
|-------------|-------------|---------------------|------|
| 1.0         | 23/50       | 21/50               | **-2pp** |
| 1.5         | 0/50        | 0/50                | 0 |
| 1.77        | 0/50        | 0/50                | 0 |
| 2.0         | 0/50        | 0/50                | 0 |

**Why multi-pass hurts**:
1. δ estimation from current best candidate's expected constellation is
   biased by viterbi errors (candidate may be wrong)
2. Pre-rotation by wrong δ INTRODUCES noise, doesn't reduce it
3. The 1.77 rad per-SC phase noise is INDEPENDENT per SC; averaging across
   iterations doesn't reduce it

## Three Decoder-Internal Attacks REFUTED Consecutively

| Phase | Attack                         | σ=1.0 rad | σ=1.77 rad |
|-------|--------------------------------|-----------|------------|
| 129 v2 | proper LLR formula           | +12pp     | +1pp (stat) |
| 130   | per-SC null zeroing           | -1pp      | -1pp       |
| 131   | multi-pass H52+δ refinement   | -2pp      | 0          |

**Architectural conclusion**: Decoder-internal attacks CANNOT bridge the
1.77 rad per-SC phase noise ceiling. The noise is from USRP analog chain
(LO/RF), not decoder-fixable.

## Phase 131 Algorithm Details

The multi-pass decoder:
1. **Pass 1**: standard soft viterbi (Phase 129 v2)
2. **δ estimation**: linear regression of `arg(eq)` vs `sc_index` for HT-SIG0/1
3. **Pre-rotation**: `eq *= exp(-j·2π·sc·δ/64)` for next pass
4. **Pass 2-3**: re-decode with rotated eq
5. Pick best metric across all iterations

The δ estimation uses the EQ after soft viterbi — but the bits themselves
are noisy at 1.77 rad, so the δ estimate is also noisy. Pre-rotation by
noisy δ adds noise instead of removing it.

## What's Next? (Per Architectural Re-Evaluation)

Per CLAUDE.md "30+ REFUTED + user directive 不可能接受现状" + Phase 112 R1
1.77 rad ceiling confirmed:

**Decoder-internal path is EXHAUSTED.** Cannot bridge analog noise.

User-excluded options:
- LDPC decoder (spec violation) ❌
- External ref clock ❌

Remaining viable directions:
1. **Schmidl-Cox sync_short** (Phase 102 Option E): algorithm rewrite, not
   parameter tune. ~200 lines of C++. May help on real-time USRP cable (Phase
   87 verdict identified sync_short as upstream blocker).
2. **Same-board USRP test** (Phase 53 verdict: 2.4x stronger signal). USRP
   hardware offline — can't test.
3. **UHD streaming stability fix** (Phase 55 territory): hardware-level.

Per user "尽可能给出更多的解决方案" + "逐个实现 + USRP 验证":
- **Next step**: Phase 132 (Schmidl-Cox sync_short) — fresh algorithm,
  not parameter tuning. File-replay validation possible since sync_short
  works on file-replay (Phase 89 verdict: 24 detections at corr=1.95-20876).

## Files

- Verdict: `docs/superpowers/notes/2026-07-09-phase131-multipass-refuted.md`
- Simulation: `examples/test_p131_multipass.py`
- T1 verdict: `docs/superpowers/notes/2026-07-09-phase129-t1-llr-synthetic.md`
- T2 verdict (C++ implementation): `docs/superpowers/notes/2026-07-09-phase129-t2-cpp-verdict.md`
- Phase 130 verdict (REFUTED): `docs/superpowers/notes/2026-07-09-phase130-null-zeroing-refuted.md`
- Phase 112 R1 root cause (1.77 rad ceiling): `docs/superpowers/notes/2026-07-07-phase112-r1-argh-rootcause.md`