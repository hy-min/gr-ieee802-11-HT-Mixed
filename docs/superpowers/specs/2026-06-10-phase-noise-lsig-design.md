# L-SIG Phase Noise Root Cause Investigation & Fix

**Date**: 2026-06-10
**Author**: Claude Code
**Status**: Approved (pending user review of this spec)
**Branch**: TEST1
**Prior context**: See `[[project_lsig_viterbi_2026_06_10]]` for the kFftNormalize false-positive history and prior diagnostic infrastructure.

---

## 1. Problem Statement

USRP real-time air transmission fails: 30s run produces Sent=31, Recv=0, all frames exit at `sym_idx=12` (HT-SIG timeout) due to L-SIG viterbi failure. Software loopback passes 9/9.

**Current diagnostic data (after kFftNormalize revert, commit `a19ddca`)**:
- mean `|H|`: 9.87
- mean `|rx|`: 18.10 (1.83× the H magnitude — suspicious)
- mean `|eq|`: 1.71 (correct magnitude)
- **mean margin: -0.084** (NEGATIVE — BPSK points OFF the I-axis, phase noise)
- viterbi audit (`[LSIG_VITERBI_AUDIT]` commit `e90e3f5`): 96/96 OK-path runs produced garbage L-SIG fields, deintl48 mean=24.3/48 std=2.1 → RANDOM verdict

**The viterbi decoder itself works**: 1 frame at SNR=53.29 produced valid L-SIG (rate=0x0D, length=0x16A, parity=0). The typical USRP frame's viterbi inputs are noise-like, not signal-with-residual-rotation.

## 2. Goals

1. **Root cause identification**: Pin down which of three candidate hypotheses is the actual cause of L-SIG viterbi failure on USRP.
2. **Fix implementation**: Apply a targeted fix in `lib/frame_equalizer_impl.cc` based on the identified root cause.
3. **USRP end-to-end validation**: Achieve `DECODE_SUCCESS > 0` in a 30s USRP run.

## 3. Non-Goals

1. Do NOT modify `sync_short`, `sync_long`, `ht_symbol_splitter` — all already verified OK.
2. Do NOT redesign the CFO/SFO estimation framework — only incremental changes.
3. Do NOT investigate the `|rx|/|H| = 1.83` ratio anomaly in this plan — separate investigation.
4. Do NOT optimize 64QAM / HT-SIG performance — focus is L-SIG chain.
5. Do NOT recalibrate USRP hardware (gain/timing/freq).

## 4. Architecture

### 4.1 Three-Phase A/B Investigation Framework

```
┌──────────────────────────────────────────────────────────────┐
│ Diagnostic Layer (already in place from prior work)          │
│  [LSIG_EQ_FULL]      52-SC H_mag/rx/eq dump                 │
│  [LSIG_VITERBI_AUDIT] deintl48/eqsym dump                   │
│  [FRAME_DETECT]      ratio_ht/ratio_lsig/E_I/E_Q            │
│  [CFO_EST][SFO_EST]  phase_per_symbol/sfo estimates         │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Phase 1: CFO/SFO 残留补偿 (env: IEEE80211_CFO_SFO_FIX)       │
└──────────────────────────────────────────────────────────────┘
                            ↓ (fail)
┌──────────────────────────────────────────────────────────────┐
│ Phase 2: per-SC H 插值 (env: IEEE80211_H_LTF_AVG)            │
└──────────────────────────────────────────────────────────────┘
                            ↓ (fail)
┌──────────────────────────────────────────────────────────────┐
│ Phase 3: L-SIG CPE 补偿 (env: IEEE80211_LSIG_CPE)            │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Data Flow

```
frame_equalizer (with env flag controlling fix)
    → USRP stdout (atomic snprintf dumps)
    → /tmp/usrp_<phase>_<on/off>.log
    → existing analysis scripts:
         test_eqlsig_constellation_offline.py
         test_viterbi_pathmetric_offline.py
    → decision: DECODE_SUCCESS / mean margin / verdict
```

### 4.3 Regression Protection

- Software loopback (`examples/test_direct_loopback.py`, expect 9/9) is the minimum gate after any code change
- USRP runs use default parameters (`./test_usrp_minimal_loopback.py --duration 30`)
- Any regression → immediate `git revert` and abort

## 5. Phase 1: CFO/SFO 残留补偿

### 5.1 Phase 1a: Diagnostic Dump (no fix logic change)

**File**: `lib/frame_equalizer_impl.cc`, after the existing `[LSIG_EQ_FULL]` dump (after L2449)

**New env flag check** (in `frame_equalizer_impl` constructor, alongside existing `IEEE80211_H_LLTF1` check at L1714):

```cpp
// New env flag check, alongside existing d_use_lltf1_for_h
const char* env_phase_residual = std::getenv("IEEE80211_PHASE_RESIDUAL");
d_log_phase_residual = (env_phase_residual && env_phase_residual[0] == '1');
if (d_log_phase_residual) {
    std::cout << "[FRAME_EQ] Phase residual dump ENABLED via env\n";
}
```

**New dump** (in `general_work()`, after the existing `[LSIG_EQ_FULL]` dump at L2449, only when `d_log_phase_residual` is true):

```cpp
if (d_log_phase_residual) {
    // Compute mean and std of arg(eq_lsig[i]) over 48 data SC
    double sum_arg = 0.0, sum_arg2 = 0.0;
    int cnt = 0;
    for (int i = 0; i < 48; i++) {
        float a = std::arg(eq_lsig[i]);
        sum_arg += a;
        sum_arg2 += (double)a * a;
        cnt++;
    }
    double mean_phase = (cnt > 0) ? sum_arg / cnt : 0.0;
    double var_phase = (cnt > 0) ? (sum_arg2 / cnt - mean_phase * mean_phase) : 0.0;
    double std_phase = (var_phase > 0) ? std::sqrt(var_phase) : 0.0;

    char phase_dump[1024];
    int n = snprintf(phase_dump, sizeof(phase_dump),
                     "[PHASE_RESIDUAL] counter=%d eq_phase=",
                     d_internal_symbol_counter);
    for (int i = 0; i < 48 && n < (int)sizeof(phase_dump) - 16; i++) {
        int w = snprintf(phase_dump+n, sizeof(phase_dump)-n, "%.3f,",
                         std::arg(eq_lsig[i]));
        if (w < 0) break;
        n += w;
    }
    n += snprintf(phase_dump+n, sizeof(phase_dump)-n,
                  " mean=%.3f std=%.3f\n", mean_phase, std_phase);
    USRP_LOG("%s", phase_dump);
}
```

### 5.2 Phase 1a: Analysis Script

**New file**: `examples/test_phase_residual_offline.py`

Parses `[PHASE_RESIDUAL]` log lines, computes per-frame statistics:
- `mean_phase`: mean of `arg(eq_lsig[i])` over 48 data SC
- `std_phase`: std of `arg(eq_lsig[i])`
- `histogram`: distribution over [-π, +π]

Reports verdict per frame and aggregate:
- `CLEAN_MODEL`: mean_phase ∈ [-0.1, 0.1] AND std_phase < 0.3 → model OK
- `COMMON_PHASE_ERROR`: mean_phase ∉ [-0.1, 0.1] but std_phase < 0.3 → CPE fix needed
- `MODEL_INCOMPLETE`: std_phase > 0.5 → model missing something
- `NOISE_LIKE`: histogram uniform → equalized signal is noise

### 5.3 Phase 1b: Fix (if Phase 1a shows MODEL_INCOMPLETE or COMMON_PHASE_ERROR)

**Approach B: Direct per-subcarrier phase measurement**

Modify the header compensation at L2196-2206 in `general_work()`:

```cpp
// In general_work(), alongside the existing d_phase_diff_valid block:
// d_enable_cfo_sfo_fix is a class member set from env flag in constructor

// At counter == kLSigRel (i.e., 2): measure actual phase advance
if (d_enable_cfo_sfo_fix && d_internal_symbol_counter == kLSigRel &&
    d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLSigRel]) {
    for (int i = 0; i < 48; i++) {  // data SC only
        gr_complex prod = d_early_eqsym[kLSigRel][i] *
                          std::conj(d_early_eqsym[kLltf0Rel][i]);
        d_measured_phase_per_sc[i] = std::arg(prod);
    }
    USRP_LOG("[DIRECT_PHASE] measured phase[0]=%.3f phase[26]=%.3f\n",
             d_measured_phase_per_sc[0], d_measured_phase_per_sc[26]);
}

// Replace model-based compensation with measurement-based
if (d_enable_cfo_sfo_fix && d_internal_symbol_counter >= kLSigRel &&
    d_early_eqsym_valid[kLltf0Rel]) {
    for (int i = 0; i < 52; i++) {
        // For L-SIG (counter=2), use measured directly
        // For HT-SIG (counter=3,4), scale linearly: measured * (counter/2)
        float scale = (float)d_internal_symbol_counter / (float)kLSigRel;
        float total_phase = d_measured_phase_per_sc[i] * scale;
        gr_complex rot = std::exp(gr_complex(0.0f, -total_phase));
        d_early_eqsym[d_internal_symbol_counter][i] *= rot;
    }
} else if (d_phase_diff_valid && d_internal_symbol_counter >= kLSigRel) {
    // Original model-based path (unchanged)
    for (int i = 0; i < 52; i++) {
        float total_phase = d_phase_diff_per_sc[i] * d_internal_symbol_counter;
        gr_complex rot = std::exp(gr_complex(0.0f, -total_phase));
        d_early_eqsym[d_internal_symbol_counter][i] *= rot;
    }
}
```

### 5.4 Phase 1: A/B Test Design

| Run | Command | Duration | Purpose |
|-----|---------|----------|---------|
| Baseline (off) | `unset IEEE80211_CFO_SFO_FIX; ./test_usrp_minimal_loopback.py --duration 30` | 30s | Reproduce current Sent=31 Recv=0 |
| Phase 1a (diag) | `IEEE80211_PHASE_RESIDUAL=1 ./test_usrp_minimal_loopback.py --duration 30` | 30s | Confirm model residual |
| Phase 1b (on) | `IEEE80211_CFO_SFO_FIX=1 ./test_usrp_minimal_loopback.py --duration 30` | 30s | Test direct measurement fix |

### 5.5 Phase 1: Decision Gate

| Outcome | Action |
|---------|--------|
| `DECODE_SUCCESS > 0` on 1b | Lock in fix, write summary, end plan |
| `mean_phase ∈ [-0.1, 0.1]` and `std_phase < 0.3` on 1a but `DECODE_SUCCESS = 0` | Model OK; deeper issue. Investigate H estimation → Phase 2 |
| `std_phase > 0.5` on 1a | Model incomplete. Implement Phase 1b (direct measurement) and re-test |
| Software loopback fails on any change | Revert immediately, abort Phase 1 |

### 5.6 Phase 1: File Changes

| File | Change |
|------|--------|
| `lib/frame_equalizer_impl.cc` | +60 lines (env flag checks in constructor at L1714, `[PHASE_RESIDUAL]` dump in general_work, direct measurement path) |
| `lib/frame_equalizer_impl.h` | +3 lines (member `d_measured_phase_per_sc[48]`, member `d_log_phase_residual`, member `d_enable_cfo_sfo_fix`) |
| `examples/test_phase_residual_offline.py` (new) | ~80 lines (parse log, compute statistics, classify verdict) |

Env flag members must be added to the `frame_equalizer_impl` class declaration in the `.h` file. Env flag checks go in the constructor (alongside the existing `IEEE80211_H_LLTF1` check at L1714-1718), not in `general_work()`.

### 5.7 Phase 1: Risks

| Risk | Mitigation |
|------|------------|
| Direct measurement misinterprets data symbols as phase | Only enable via env flag, offline analysis can confirm |
| L-LTF1/0 symmetry broken when scaling | Only data SC, only 48 of 52; pilots left unchanged |
| USRP 30s run variance gives false negative | Run 2× per config, take stable result |
| `IEEE80211_PHASE_RESIDUAL` log noise pollutes default logs | Default OFF, only when env var set |

## 6. Phase 2: per-SC H 插值 (Fallback)

### 6.1 Trigger

Phase 1 fails the decision gate AND `std_phase < 0.5` (model OK but other issue) OR direct measurement also fails.

### 6.2 Fix: Average L-LTF0 and L-LTF1 for H

```cpp
// New env flag: IEEE80211_H_LTF_AVG=1
const char* env_h_ltf_avg = std::getenv("IEEE80211_H_LTF_AVG");
bool d_enable_h_ltf_avg = (env_h_ltf_avg && env_h_ltf_avg[0] == '1');

// In estimate_header_channel_from_lltf52, replace lltf0 with avg(lltf0, lltf1)
if (d_enable_h_ltf_avg) {
    gr_complex lltf_avg[52];
    for (int i = 0; i < 52; i++) {
        if (d_ltf_compensated_valid[0] && d_ltf_compensated_valid[1]) {
            lltf_avg[i] = 0.5f * (d_ltf_compensated[0][i] + d_ltf_compensated[1][i]);
        } else {
            lltf_avg[i] = d_ltf_compensated_valid[0] ? d_ltf_compensated[0][i]
                                                     : d_early_eqsym[kLltf0Rel][i];
        }
    }
    // Use lltf_avg instead of lltf0_52 in the existing function
    estimate_header_channel_from_lltf52(lltf_avg, lltf_avg, H52);
}
```

### 6.3 A/B Test & Decision Gate

Same as Phase 1, with `IEEE80211_H_LTF_AVG` env flag.

### 6.4 File Changes

`lib/frame_equalizer_impl.cc` +30 lines (env flag + averaging logic).

### 6.5 Risk

Low — only changes H estimation source, not the compensation logic.

## 7. Phase 3: L-SIG CPE 补偿 (Fallback)

### 7.1 Trigger

Phase 2 fails the decision gate.

### 7.2 Fix: Common Phase Error Compensation

```cpp
// New env flag: IEEE80211_LSIG_CPE=1
const char* env_lsig_cpe = std::getenv("IEEE80211_LSIG_CPE");
bool d_enable_lsig_cpe = (env_lsig_cpe && env_lsig_cpe[0] == '1');

// In general_work(), at the start of ht_parse_condition block (around L2545):
if (d_enable_lsig_cpe) {
    gr_complex Hhdr52_cpe[52];
    estimate_header_channel_from_lltf52(lltf_for_H2, lltf_for_H2, Hhdr52_cpe);
    
    gr_complex cpe_sum(0.0f, 0.0f);
    int cpe_cnt = 0;
    for (int i = 0; i < 48; i++) {
        if (std::abs(Hhdr52_cpe[i]) > 0.001f) {
            gr_complex eq = safe_div(d_early_eqsym[kLSigRel][i], Hhdr52_cpe[i]);
            cpe_sum += eq;
            cpe_cnt++;
        }
    }
    if (cpe_cnt > 0) {
        float cpe_angle = std::arg(cpe_sum);
        gr_complex cpe_rot = std::exp(gr_complex(0.0f, -cpe_angle));
        for (int i = 0; i < 52; i++) {
            d_early_eqsym[kLSigRel][i] *= cpe_rot;
        }
        USRP_LOG("[CPE_COMP] angle=%.4f rad cnt=%d\n", cpe_angle, cpe_cnt);
    }
}
```

### 7.3 A/B Test & Decision Gate

Same as Phase 1, with `IEEE80211_LSIG_CPE` env flag.

### 7.4 File Changes

`lib/frame_equalizer_impl.cc` +30 lines (env flag + CPE estimate + rotation).

### 7.5 Risk

Medium — CPE estimate relies on BPSK decision. L-SIG is BPSK so assumption is valid, but iteration count is 1 (no feedback loop).

## 8. Testing Strategy (Cross-Phase)

| Layer | Command | Expected | When |
|-------|---------|----------|------|
| Software loopback | `examples/test_direct_loopback.py` | 9/9 | After every code change |
| Synthetic H estimation | `examples/test_h_estimation_synthetic.py` | 5/5 | When changing H (Phase 1b, 2) |
| Synthetic L-SIG viterbi | `examples/test_lsig_viterbi_synthetic.py` | 3/3 | When changing viterbi input (Phase 3) |
| USRP 30s live | `examples/test_usrp_minimal_loopback.py --duration 30` | DECODE_SUCCESS > 0 | At every Phase decision gate |

## 9. Risk Register (Overall)

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| 1 | Phase 1a misclassification (model residual looks OK but viterbi still fails) | Medium | Wrong root cause attribution | Cross-check with mean_phase, std_phase, and existing margin |
| 2 | Direct measurement breaks L-LTF1/0 symmetry | Medium | Software loopback fails | env flag only, default OFF, instantly reversible |
| 3 | CPE estimate creates circular dependency with BPSK decision | Low | Noisy viterbi input | Single-shot CPE, no iteration |
| 4 | USRP 30s run variance (Sent=30, Recv=5-10 is noise) | High | False positive/negative | Run 2× per config, take stable result |
| 5 | All three phases fail | Medium | Plan invalid | Write new plan investigating `|rx|/|H| = 1.83` or FFT window timing |
| 6 | Fix introduces new regression | Low | Blocking | `git revert` + software loopback is baseline |

## 10. Out of Scope

- USRP recalibration (gain, timing, frequency)
- Modifying sync_short / sync_long / ht_symbol_splitter
- CFO/SFO estimation framework rewrite
- `|rx|/|H| = 1.83` ratio anomaly investigation
- Phase noise spectrum analysis (requires SDR tooling)
- 64QAM / HT-SIG performance optimization
- Phase 4 (combined Phase 2+3) — only triggered explicitly if 1-3 all fail

## 11. Decision Path

```
Run Phase 1a (diagnostic)
    │
    ├─ model OK (mean_phase ∈ [-0.1, 0.1], std_phase < 0.3)
    │     → Run Phase 2 directly (skip Phase 1b)
    │
    └─ model incomplete (std_phase > 0.5 or mean_phase ∉ [-0.1, 0.1])
          → Run Phase 1b (direct measurement)
                │
                ├─ DECODE_SUCCESS > 0 → END (Phase 1 fix)
                │
                └─ fail → Run Phase 2
                            │
                            ├─ DECODE_SUCCESS > 0 → END (Phase 2 fix)
                            │
                            └─ fail → Run Phase 3
                                        │
                                        ├─ DECODE_SUCCESS > 0 → END (Phase 3 fix)
                                        │
                                        └─ fail → Write new plan
```

## 12. Open Questions

1. **Data symbol phase in direct measurement**: When measuring `arg(lsig * conj(lltf0))` per subcarrier, the result includes the BPSK data phase. The 48-SC averaging should dilute this, but is the assumption robust? **Resolution**: Phase 1a diagnostic first, see the actual std_phase.

2. **CPE after equalization**: The CPE estimate at Phase 3 uses the equalized L-SIG. But if equalization already failed (Phase 1-2 fail), the CPE is on garbage. **Resolution**: Run Phase 1+2 first; if both fail, Phase 3 is unlikely to help; this is why we have the "all fail" exit.

3. **Phase 4 combination**: Should we run all three fixes together if each individually fails? **Decision deferred**: Not in this plan; only if user explicitly requests.

## 13. Plan Reference

This spec will be transformed into an implementation plan at:
`docs/superpowers/plans/2026-06-10-phase-noise-lsig.md`

Created by the writing-plans skill after this spec is approved.

---

**Why this design**: The previous fix attempt (kFftNormalize at `e52ee13`) was driven by magnitude matching (|eq|=10 ≈ kFftNormalize) without verifying it reached the viterbi decoder. Code review caught this. This new design:
- **Tests hypotheses systematically** with strict decision gates
- **Uses env flags** for instant ON/OFF A/B testing
- **Maintains software loopback regression** as the floor
- **Has a clear exit** for the "all phases fail" case
- **All diagnostic infrastructure** is already in place from prior work

**How to apply**: Start with Phase 1a diagnostic. If the model is correct, skip to Phase 2. If wrong, run Phase 1b direct measurement. Each phase has a clear gate, so failure modes are bounded.
