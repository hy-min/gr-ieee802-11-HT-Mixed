# Phase 137 Design: Stable-Null-Aware Masking with Alternative CPE

**Date**: 2026-07-09
**Branch**: TEST1
**Status**: 📝 DESIGN — awaiting implementation
**Author**: gr-ieee802-11 team

## 1. Goal

Reduce HT-SIG viterbi metric on USRP from 13-15 (viterbi free-distance ceiling,
K=7 R=1/2 = 10) toward ≤10 by eliminating the per-symbol CPE contamination caused
by Phase 78b's 5 stable null SCs: **{-21, -13, -7, +7, +21}**.

## 2. Background

Per Phase 78b verdict (`docs/superpowers/notes/2026-07-03-phase78b-offline-analysis-verdict.md`):

| Metric | USRP (5250 MHz) | Synthetic |
|--------|-----------------|-----------|
| Globally null SCs (std_im > 3.0) | **5 stable** | 0 |
| max std_im | **7.784** | 3.647 |
| 5 stable SCs | **{-21, -13, -7, +7, +21}** | rotating |

Per Phase 100 verdict (`docs/superpowers/notes/2026-07-05-phase100-verdict.md`):

> 5 null SCs × 2 OFDM symbols (HT-SIG0 + HT-SIG1) = ~10 random bits in 96
> encoded (viterbi FREE-DISTANCE=10 EXACTLY, +1-3 noise errors pushes metric
> to 11-15, uncorrectable)

The 4 SCs **{-21, -7, +7, +21}** are HT-SIG QBPSK pilots (kScIndex52[48..51]).
They are used by the per-symbol CPE estimator to derive a constant phase rotation
that is applied to HT-SIG1 (line 3626-3646). When pilots are null, the CPE estimate
is biased by std_im=7.8 noise, contaminating every BPSK decision in HT-SIG1.

## 3. Architecture

Three-layer opt-in fix:

| Layer | Env var | Default | Effect |
|-------|---------|---------|--------|
| L1 parser | `IEEE80211_HTSIG_NULL_SCS` | unset | Existing env var extended to accept signed SC values (`-21,-13,-7,7,21`); backward compatible with old format (`12`) |
| L2 CPE | `IEEE80211_HTSIG_NULL_PILOT_MASK=1` | OFF | When ON, CPE estimator skips null pilots (positions 48..51) |
| L3 fallback | (auto with L2) | n/a | If all 4 pilots masked/invalid, estimate CPE from top-N data SCs |

### 3.1 L1 Env Var Parser

`lib/frame_equalizer_impl.cc` line 4816 (existing `IEEE80211_HTSIG_NULL_SCS` block):

- Accept comma/space-separated values
- Each value can be:
  - `0..51` — loop position (old format, e.g., `12` → mask SC at kScIndex52[12] = -13)
  - `-26..+26` — signed SC value (new format, e.g., `-21` → search kScIndex52[] for matching value)
- New format examples:
  - `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21'` → mask all 5 Phase 78b nulls
  - `IEEE80211_HTSIG_NULL_SCS='-21,7'` → mask only pilots at -21 and +7

### 3.2 L2 Pilot CPE Mask

`lib/frame_equalizer_impl.cc` line 3626-3646 (`IEEE80211_HT_PER_SYMBOL_CPE` block):

Add skip condition for masked pilots:

```cpp
if (htsig_null_sc_mask && htsig_null_sc_mask[sc]) {
    continue;  // Phase 137: skip null pilots (Phase 78b stable nulls)
}
```

Gate behind new opt-in `IEEE80211_HTSIG_NULL_PILOT_MASK=1` to preserve baseline.

### 3.3 L3 Data-SC CPE Fallback

If all 4 pilots are masked or invalid (n_pilots == 0):

1. Iterate over 48 data SCs (positions 0..47)
2. Skip null data SCs (respect `htsig_null_sc_mask`)
3. Require |H| ≥ 0.1 (stricter threshold for data SCs)
4. Compute QBPSK reference and accumulate phase residual
5. Use accumulated phase as CPE estimate (same `cpe_rot_b` formula)

This guarantees the CPE estimator always has ≥1 reference even when all
pilots are null.

## 4. Verification Plan

| Test | Configuration | Success Criterion |
|------|---------------|-------------------|
| T1 (baseline regression) | File-replay `--loop 10`, no Phase 137 env vars | 1/1 FCS_OK, loopback preserved |
| T2 (file-replay validation) | `IEEE80211_HTSIG_NULL_SCS='-21,-13,-7,7,21' IEEE80211_HTSIG_NULL_PILOT_MASK=1` | 1/1 PASS, log shows "masked 5 SCs" |
| T3 (partial pilot mask) | `IEEE80211_HTSIG_NULL_SCS='-21,7' IEEE80211_HTSIG_NULL_PILOT_MASK=1` | 1/1 PASS, log shows "masked 2 SCs" (sanity check on data-SC fallback with 2 valid pilots) |
| T4 (USRP) | 5250 MHz cable, 60s warmup + 30s test, full Phase 137 env | HT_SIG_CAND events ≥ 1, metric distribution shift (e.g., median 14→12) |
| T5 (USRP multi-run) | Same config × 3-5 runs | At least 1 run with metric ≤ 10 (viterbi CRC recovery threshold) |

## 5. Failure Modes & Fallback

| Failure | Mitigation |
|---------|-----------|
| Data-SC CPE too noisy → metric worse than baseline | Disable L3 fallback; require ≥2 valid pilots (skip if n_pilots < 2) |
| New env var format parsing breaks old format | Backward-compat tested in T1 (old format `12` still works) |
| USRP metric does not drop → wrong root cause | Re-analyze with Phase 100 root cause check (1.77 rad noise vs null pilots) |
| Cable run count > 5 | Defer further runs until 30 dB attenuator available |

## 6. Out of Scope (YAGNI)

- ❌ No L-SIG path changes (Phase 78b null SCs only corrupt HT-SIG CPE)
- ❌ No viterbi decoder rewrite (existing `viterbi_decode_133_171_soft` is sufficient)
- ❌ No soft LLR formula change (Phase 129 v2 already deployed)
- ❌ No non-QBPSK path changes (HT-SIG uses QBPSK only)
- ❌ No cross-frame H tracking changes (Phase 123 still in place)

## 7. Implementation Commits (planned)

1. `feat(p137): IEEE80211_HTSIG_NULL_SCS accepts signed SC values (-26..+26)`
2. `feat(p137): IEEE80211_HTSIG_NULL_PILOT_MASK=1 opt-in skips null pilots in CPE`
3. `feat(p137): fallback to data-SC CPE when all pilots masked`
4. `docs(p137): T1-T3 file-replay verification verdict`
5. `docs(p137): T4-T5 USRP 5250 validation + final verdict`

## 8. Files Touched

- `lib/frame_equalizer_impl.cc` — 3 edits (line 3626-3646 CPE, line 4816-4840 env parser, line ~4860 new gate flag)
- `docs/superpowers/notes/2026-07-09-phase137-stable-null-mask-verdict.md` — verdict (after T5)
- `CLAUDE.md` — Phase 137 env vars (after T5)
- `~/.claude/projects/-home-hy-gr-ieee802-11/memory/project_p137_stable_null_mask.md` — memory entry

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| CPE fallback noise worse than baseline | Medium | High | L3 fallback gated behind L2; if T4 metric worse, disable L3 in commit 3 |
| USRP metric unchanged (wrong root cause) | Medium | Medium | Document in verdict, return to Phase 100 alternatives (30 dB attenuator, LDPC) |
| Cable run count exhausted (5/5) | High | Medium | Defer USRP until attenuator arrives; file-replay only |

## 10. Success Criteria (HARD CONSTRAINT alignment)

**Phase 137 success** = at least 1 USRP run with HT-SIG viterbi metric ≤ 10.
This enables the viterbi CRC check to potentially pass and produce FCS_OK.

**Architectural success** = the fix does not regress any existing pass case
(loopback 3/3, file-replay 1/1, baseline USRP no false positives).

**Per project CLAUDE.md "USRP realtime FCS_OK is the absolute goal"** — Phase 137
is one more attempt in the multi-attack sequence; closure is not acceptable if
this attack is REFUTED. Phase 138+ continues with new architectures per the
"Equalizer layer is NOT closed" directive.