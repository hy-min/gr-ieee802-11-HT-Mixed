# gr-ieee802-11 Project Instructions

## Project Goal: USRP End-to-End Validation (HARD CONSTRAINT)

**The goal of this project is USRP realtime end-to-end validation.**
Loopback-only verification is **NOT acceptable** as a final outcome.

When evaluating any proposed solution, investigation, or verdict, the following
hierarchy applies:

1. **Primary success metric**: USRP realtime `FCS_OK ≥ Sent/N` (per-frame
   success ratio on actual air path through X310 + UBX-160 hardware).
2. **Acceptable intermediate evidence**:
   - Software loopback 3/3 PASS (decoder correctness validation only — not
     sufficient alone)
   - Synthetic unit tests / cross-check (algorithm correctness validation
     only — not sufficient alone)
3. **NOT acceptable**:
   - Concluding "BLOCKED" without an upstream-attack plan that targets
     the actual USRP gate
   - Closing an investigation with "use loopback for verification" as the
     final state
   - Treating `d_is_ht=false` (HT-SIG viterbi upstream gate) as a permanent
     architectural barrier — it is a problem to solve, not to accept

## Implications for Phase 60+

- Any verdict ending in BLOCKED must include a concrete Phase 60+
  attack plan that operates **upstream** of the blocker (L-LTF0 path,
  splitter port, RF chain, etc.) — not just "leave the code as opt-in".
- Accepting USRP HT-SIG as "not solvable" is NOT an acceptable project
  outcome. The project's success criterion is USRP realtime FCS_OK.
- Software loopback 3/3 PASS is preserved as a regression check, but
  cannot substitute for USRP verification.

## Project-Specific Conventions

- **make install** must run after every `make` (else Python loads stale .so).
- **GRC ban**: never generate Python from `wifi_phy_hier.grc` (segfaults);
  edit `wifi_phy_hier.py` directly.
- **Thread-safe logging**: `USRP_LOG` is non-atomic. Multi-value dumps use
  `snprintf` + `USRP_LOG("%s", buf)` per commit `e90e3f5`.
- **env vars default OFF**: all new env vars opt-in to preserve baseline.
- **Standard USRP test config** (preserved across all phases):
  `IEEE80211_LSIG_RATE_FORCE=0xD IEEE80211_LLTF_OFFSET_CORRECT=14
  IEEE80211_TIMING_OFFSET_APPLY=1 --freq 5890 --tx-gain 20`
- **Same-board default**: `A:0` TX → `A:0` RX2 (per Phase 53 verdict,
  cross-board is 2.4x weaker).
- **禁止 `--rate 5`** (Phase 58 REFUTED, 48× more overflows than `--rate 20`).

## Reference

- Phase 41 closure: `docs/superpowers/notes/2026-06-28-usrp-final-verdict.md`
- Phase 59 BLOCKED: `docs/superpowers/notes/2026-06-29-phase59-h52-null-interp-verdict.md`
  (call site unreachable due to `d_is_ht` gate; Phase 60 must attack
  upstream).
- 12+ REFUTED equalizer-layer hypotheses documented in MEMORY.md
  `禁止方向` section.

*Last updated: 2026-06-30 (Phase 59 closure, BLOCKED verdict — Phase 60
must target the upstream HT-SIG gate)*