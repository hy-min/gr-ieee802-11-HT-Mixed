# Phase 20 — Per-Subcarrier Phase Tracking on HT-SIG1 (Design)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce HT-SIG1-specific corruption at 5 GHz A:0 by applying per-subcarrier phase correction (estimated from HT-SIG0's 48 data SCs) to HT-SIG1 equalized symbols before viterbi decode, increasing FCS OK count from 1 to ≥5 per 30s.

**Architecture:** Add two env-var gated features inside `decode_htsig_from_rotated` (line 1847 of `lib/frame_equalizer_impl.cc`): (1) `IEEE80211_HT_PER_SC_PHASE_DUMP=1` for diagnostic dump of per-SC phase, (2) `IEEE80211_HT_PER_SC_PHASE_FIX=1` for fix that re-encodes HT-SIG0 per (rot, inv_a, inv_b) trial, estimates 52 per-SC phase values, and applies opposite rotation to HT-SIG1 equalized symbols. Diagnostic-first workflow: measure hypothesis before applying fix.

**Tech Stack:** GNU Radio OOT module (C++), gr-ieee802-11 PHY decoder, USRP X300 + SBX/CBX subdevice A:0 at 5 GHz, Python test drivers.

---

## Background (Phase 19 findings)

After 19 phases, the 5 GHz A:0 chain runs end-to-end (FCS OK = 1 baseline). The new bottleneck: **24/24 HT_SIG_PARSE_FAIL events on legitimate rate=0xD L-SIGs are `crc_fail`** (viterbi converges on garbage 48-bit codeword with valid tail).

Phase 19 analysis (128 events) revealed:
- **HT-SIG0 is STABLE** (4 distinct values across 128 events)
- **HT-SIG1 VARIES** (4 distinct values)
- All 128 frames share the same 8 enc96 patterns = same TX content, RX rotates consistently per (rot, inv_a, inv_b) trial
- inv_a is a clean BPSK polarity flip

**Conclusion**: HT-SIG1-specific corruption, NOT random noise or code bug.

**Phase 19 T7 (per-symbol CPE) REFUTED** H1 (commit 94c50e2): applying common-phase rotation estimated from HT-SIG0's 4 pilots to HT-SIG1 did NOT improve viterbi convergence. The fix changed bit patterns (57 vs 8) but didn't fix the underlying corruption.

**Phase 10 Task 4 (per-symbol CPE on L-SIG) was also REVERTED** (high variance 7.9%→13.6% enc=0, run-to-run noise floor).

Both prior attempts were **per-symbol (common phase)**, not **per-subcarrier (per-SC phase)**. Phase 20 takes the per-SC approach.

---

## Section 1: Architecture

**Core idea**: In `decode_htsig_from_rotated`'s existing 16-candidate brute-force loop, add a **per-subcarrier phase rewind** for each (rot, inv_a, inv_b) trial:

```
Existing loop: try (rot, inv_a, inv_b) → viterbi on enc96 → check CRC
New loop:      try (rot, inv_a, inv_b) → rewind HT-SIG0 → rewind HT-SIG1 → viterbi → check CRC
```

**Key changes**:
1. In `decode_htsig_from_rotated` (line 1847), before viterbi call, add per-SC phase estimation + correction pass
2. Each of the 16 trials independently re-encodes HT-SIG0 + estimates per-SC phase + applies to HT-SIG1
3. Viterbi sees the phase-corrected 96-bit codeword, which should be closer to the true codeword

**Two env-var gated modes**:
- `IEEE80211_HT_PER_SC_PHASE_DUMP=1` → dump per-SC phase to log (diagnostic)
- `IEEE80211_HT_PER_SC_PHASE_FIX=1` → apply per-SC correction to viterbi input (fix)

Both modes can be toggled independently. Diagnostic-first: measure hypothesis, then apply fix.

---

## Section 2: Components

5 new code units, all in `lib/frame_equalizer_impl.cc`:

### 2.1 `estimate_per_sc_phase_from_htsig0()` — static helper

**Responsibility**: Compute 52 per-SC phase values from HT-SIG0 received vs re-encoded.

**Interface**:
```cpp
// Input: rx_htsig0[52] = received HT-SIG0 equalized symbols
//        expected_htsig0[52] = re-encoded HT-SIG0 expected symbols
// Output: phase_per_sc[52] = per-SC phase (radians)
// Returns: number of valid SCs (excludes SCs with |expected| ≈ 0)
static int estimate_per_sc_phase_from_htsig0(
    const gr_complex* rx_htsig0,
    const gr_complex* expected_htsig0,
    float* phase_per_sc);
```

**Logic**:
```cpp
int valid = 0;
for (int i = 0; i < 52; i++) {
    gr_complex ratio = rx_htsig0[i] / expected_htsig0[i];
    // Skip SCs with near-zero expected (e.g., DC null at i=0,26)
    if (std::abs(expected_htsig0[i]) < 1e-6f) {
        phase_per_sc[i] = 0.0f;
        continue;
    }
    phase_per_sc[i] = std::arg(ratio);
    valid++;
}
return valid;
```

### 2.2 `apply_per_sc_phase_correction()` — static helper

**Responsibility**: Apply per-SC phase rotation to HT-SIG1 equalized symbols (in place).

**Interface**:
```cpp
// Input: rx_htsig1[52] = received HT-SIG1 equalized symbols (in/out, modified in place)
//        phase_per_sc[52] = per-SC phase from HT-SIG0
// Output: rx_htsig1[52] modified in place
// Returns: void
static void apply_per_sc_phase_correction(
    gr_complex* rx_htsig1,
    const float* phase_per_sc);
```

**Logic**:
```cpp
for (int i = 0; i < 52; i++) {
    gr_complex rotation = std::polar(1.0f, -phase_per_sc[i]);
    rx_htsig1[i] *= rotation;
}
```

### 2.3 Diagnostic dump (env-gated)

**Trigger**: `IEEE80211_HT_PER_SC_PHASE_DUMP=1`

**Location**: Inside `decode_htsig_from_rotated` (line 1847), before viterbi call, after each (rot, inv_a, inv_b) trial.

**Output format** (atomic, single-call `snprintf` + `USRP_LOG`):
```
[HT_PER_SC_PHASE] frame=N rot=R inv_a=A inv_b=B valid_sc=V phase=[p0,p1,...,p51]
```

### 2.4 Fix gate (env-gated)

**Trigger**: `IEEE80211_HT_PER_SC_PHASE_FIX=1`

**Location**: Same as 2.3, immediately after diagnostic dump.

**Logic**:
```cpp
if (fix_enabled) {
    // Re-encode HT-SIG0 (depends on inv_a/rotation/inv_b context)
    gr_complex expected_htsig0[52];
    reencode_htsig0(decoded_htsig0_bits, expected_htsig0);
    
    // Estimate per-SC phase
    float phase_per_sc[52];
    estimate_per_sc_phase_from_htsig0(rx_htsig0, expected_htsig0, phase_per_sc);
    
    // Apply to HT-SIG1
    apply_per_sc_phase_correction(rx_htsig1, phase_per_sc);
}
// Then viterbi sees corrected HT-SIG1
```

### 2.5 Test driver: `/tmp/test_p20_per_sc_phase_30s.py`

**Responsibility**: 30s USRP 5 GHz A:0 test, capture dump, validate fix.

**Steps**:
1. Run 1: `IEEE80211_HT_PER_SC_PHASE_DUMP=1` (no fix) → capture dump, analyze
2. Run 2: `IEEE80211_HT_PER_SC_PHASE_FIX=1` (no dump) → measure FCS OK
3. Run 3: Baseline (no env) → measure FCS OK baseline

**Success criteria**:
- Run 2: `FCS OK >= 5` (vs baseline 1)
- Run 2: `HT_SIG_PARSE_FAIL` reduced

---

## Section 3: Data Flow

**Input data** (passed from `general_work` to `decode_htsig_from_rotated`):
- `rx52_a[52]`: HT-SIG0 equalized symbols (frequency domain, after H52 division)
- `rx52_b[52]`: HT-SIG1 equalized symbols (frequency domain, after H52 division)
- `H52[52]`: Channel estimate (frequency domain)
- State: `d_htsig0_rel`, `d_htsig1_rel`, etc.

**New data flow** (per (rot, inv_a, inv_b) trial):

```
┌──────────────────────────────────────────────────────┐
│ Step 1: Build candidate enc96 (existing code)        │
│   bits_a[48] = HT-SIG0 bits (from rx52_a)            │
│   bits_b[48] = HT-SIG1 bits (from rx52_b)            │
│   enc96[96] = bits_a || bits_b                       │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Step 2 (NEW, fix only): Per-SC phase correction     │
│   expected_htsig0[52] = re_encode(bits_a, H52)       │
│   phase[i] = arg(rx52_a[i] / expected_htsig0[i])     │
│   rx52_b_corrected[i] = rx52_b[i] * exp(-j*phase[i]) │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Step 3 (NEW, fix only): Re-decide HT-SIG1 bits      │
│   bits_b_corrected[48] = QPSK_decide(rx52_b_corrected)│
│   (QBPSK rotation applied based on inv_a)            │
│   enc96_corrected[96] = bits_a || bits_b_corrected   │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Step 4: viterbi on enc96_corrected (existing code)   │
│   returns decoded48[48]                              │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Step 5: CRC check (existing code)                   │
│   if CRC pass → return decoded48                     │
│   else → try next (rot, inv_a, inv_b)                │
└──────────────────────────────────────────────────────┘
```

**Key points**:
- **Re-encode HT-SIG0**: With (rot, inv_a) assumption, convert decoded bits to expected frequency-domain symbols. Apply H52: `expected_freq[i] = H52[i] * tx_symbol[i]`.
- **Per-SC phase**: 52 values, but i=0 (DC) and i=26 are null SCs, skip.
- **Apply to HT-SIG1**: In-place rotation across 52 SCs.
- **Re-decide HT-SIG1 bits**: QPSK demap (QBPSK rotation determined by inv_a).
- **env-gate**: Step 2+3 wrapped in `if (getenv("IEEE80211_HT_PER_SC_PHASE_FIX"))`, doesn't affect baseline.

---

## Section 4: Error Handling

### 4.1 Wrong (rot, inv_a, inv_b) trial

**Scenario**: Current trial is incorrect, re-encoded HT-SIG0 ≠ received HT-SIG0.
**Risk**: per-SC phase estimates are noise, applying to HT-SIG1 makes it worse.
**Mitigation**: Existing 16-candidate loop handles this — wrong-trial typically CRC fails, loop continues to next trial. Fix only applies to current trial, doesn't pollute other trials.

### 4.2 Zero division in phase estimation

**Scenario**: `expected_htsig0[i] ≈ 0` (e.g., DC null at i=0, i=26).
**Risk**: `arg(0/0) = NaN`, `polar(1, NaN) = NaN`, propagates to viterbi.
**Mitigation**:
```cpp
if (std::abs(expected_htsig0[i]) < 1e-6f) {
    phase_per_sc[i] = 0.0f;  // No correction for null SCs
    continue;
}
```

### 4.3 Pilot SCs (i=48..51) handling

**Scenario**: 4 pilot SCs in HT-SIG have known polarity from POLARITY[] table.
**Risk**: If re-encode is incorrect, pilot-based phase estimates are biased.
**Mitigation**:
- Re-encode correctly applies pilot polarity (from `POLARITY[symbol_index % 127]`)
- Pilot SCs in re-encoded array should match received magnitude
- Treated as 4 high-SNR reference points (consistent with L-SIG pilot phase tracking)

### 4.4 Fix makes things worse

**Scenario**: per-SC phase hypothesis is wrong, correction amplifies noise in wrong direction.
**Risk**: HT_SIG_PARSE_FAIL increases, FCS OK decreases.
**Mitigation**:
- env-var gated, default OFF — doesn't affect baseline
- Diagnostic-first: measure before applying
- Prior `IEEE80211_HT_PER_SYMBOL_CPE` (commit 94c50e2) is NO-OP example, same diagnostic pattern applicable
- If fix fails, `git revert` to remove

### 4.5 HT-SIG0 bits uncertain

**Scenario**: When crc_fail occurs in HT-SIG0 portion (not just HT-SIG1).
**Risk**: re-encoded HT-SIG0 is garbage, phase estimates are garbage.
**Mitigation**:
- 16-candidate loop tries all (rot, inv_a, inv_b), at least 1 trial has correct HT-SIG0
- Even if current trial's HT-SIG0 is garbage, 16 trials include correct one
- Iteration cost: ~52 phase estimates × 16 trials = 832 ops/frame, negligible

### 4.6 Diagnostic output format

**Convention**: Use atomic single-call pattern (Phase 19 learning).
```cpp
if (getenv("IEEE80211_HT_PER_SC_PHASE_DUMP")) {
    char buf[1024];
    int n = snprintf(buf, sizeof(buf),
                     "[HT_PER_SC_PHASE] frame=%d rot=%d inv_a=%d inv_b=%d "
                     "valid_sc=%d phase=[", frame_idx, rot, inv_a, inv_b, valid);
    for (int i = 0; i < 52 && n < (int)sizeof(buf); i++) {
        n += snprintf(buf + n, sizeof(buf) - n, "%.3f,", phase_per_sc[i]);
    }
    snprintf(buf + n, sizeof(buf) - n, "]\n");
    USRP_LOG("%s", buf);  // single call → atomic
}
```

---

## Section 5: Testing Strategy

### 5.1 Unit tests (Synthetic)

**Purpose**: Verify per-SC phase estimation + correction in isolation.

**Test script**: `/tmp/test_p20_per_sc_phase_synthetic.py`

**Test cases**:
1. **Test 1: Identity phase** (no error) — phase estimation returns 0, correction is no-op.
2. **Test 2: Uniform phase** (constant per all SCs) — phase estimation recovers it, correction removes common rotation.
3. **Test 3: Per-SC random phase** (σ=0.1 rad) — phase estimation recovers approximately.
4. **Test 4: Per-SC structured phase** (linear in SC index) — phase estimation recovers.
5. **Test 5: Null SC handling** (i=0, i=26) — phase = 0, no NaN/Inf.

**Expected**: 5/5 PASS.

### 5.2 Integration tests (Loopback)

**Purpose**: Verify software loopback has no regression.

**Test script**: `examples/test_direct_loopback.py` (existing).

**Test matrix**:
| Env | Expected | Notes |
|-----|----------|-------|
| (none) | 9/9 baseline | Reference baseline |
| `IEEE80211_HT_PER_SC_PHASE_DUMP=1` | 9/9 | dump only, doesn't affect |
| `IEEE80211_HT_PER_SC_PHASE_FIX=1` | 9/9 or 10/10 | fix may no-op on clean math |
| `IEEE80211_LSIG_RATE_FORCE=0xD` | 1 frame (Phase 18 baseline) | regression test |

**Note**: Phase 19-discovered loopback regression (`sync_short_fused` energy gate) may block these tests. If so, report and skip — does not affect USRP test.

### 5.3 USRP 5 GHz A:0 e2e test

**Purpose**: Verify hypothesis + measure improvement on real hardware.

**Test script**: `/tmp/test_p20_usrp_per_sc_phase_30s.py`

**Test flow**:
1. **Run 1: Diagnostic only** (env=`IEEE80211_HT_PER_SC_PHASE_DUMP=1`, 30s)
   - Collect 30s of per-SC phase dump
   - Analyze: is per-SC phase structured (e.g., linear) or random?
   - Verify hypothesis: if phase across SCs is structured, fix may work; if random noise, fix won't help.

2. **Run 2: Fix enabled** (env=`IEEE80211_HT_PER_SC_PHASE_FIX=1`, 30s)
   - Measure: FCS OK count, HT_SIG_PARSE_FAIL count
   - Expected: FCS OK ≥ 5 (vs baseline 1), HT_SIG_PARSE_FAIL doesn't increase.

3. **Run 3: Baseline** (no env, 30s)
   - Measure baseline FCS OK
   - Expected: FCS OK ≈ 1 (Phase 18 baseline).

4. **Run 4: Both env vars** (dump + fix, 30s)
   - Both dump and fix enabled, used for debugging.

### 5.4 Success Criteria

| Metric | Baseline | Target | Verify |
|--------|----------|--------|--------|
| FCS OK count / 30s | 1 | ≥ 5 | Run 2 vs Run 3 |
| HT_SIG_PARSE_FAIL / 30s | 24+ | Reduce 50% | Run 2 vs Run 3 |
| Software loopback | 9/9 | 9/9 | No regression |
| Per-SC phase variance | n/a | < 0.5 rad² | Run 1 diagnostic |
| Per-SC phase structure | n/a | Linear (r > 0.5) or random | Run 1 analyzer |

### 5.5 Diagnostic analysis script

**Purpose**: Analyze Run 1 dump data.

**Test script**: `/tmp/analyze_p20_per_sc_phase.py`

**Output**:
- Per-SC mean phase (across frames)
- Per-SC std phase (variance)
- Pearson correlation: phase vs SC index (linearity test)
- Per-frame mean |phase| (drift across symbols)
- Histogram of |phase| values

---

## Files Modified

- `lib/frame_equalizer_impl.cc`: Add 2 static helpers + 2 env-var gated blocks inside `decode_htsig_from_rotated`. ~150 lines of new code.
- No header changes (helpers are static, env-var gates don't need new state).
- No new files in `lib/` (helpers are static).

## Files Created (test artifacts only)

- `/tmp/test_p20_per_sc_phase_synthetic.py` (synthetic unit tests)
- `/tmp/test_p20_usrp_per_sc_phase_30s.py` (USRP e2e test driver)
- `/tmp/analyze_p20_per_sc_phase.py` (diagnostic analyzer)
- `/tmp/p20_per_sc_phase_30s.log` (test output, ~30 MB)

## Compatibility / Non-Goals

- **NO** changes to viterbi algorithm or branch metric
- **NO** changes to L-SIG decoding path
- **NO** changes to comb equalizer
- **NO** changes to existing env-var gates
- **NO** changes to H52 estimation
- **NO** cross-OFDM-symbol phase model (per-symbol only)

## Risks

1. **Hypothesis is wrong**: per-SC phase doesn't actually correct the corruption. Mitigation: diagnostic-first, can revert.
2. **High variance per SC**: 1 sample per SC per symbol may be too noisy. Mitigation: average across multiple (rot, inv_a, inv_b) trials.
3. **H52 quality**: If H52 itself is wrong, re-encode is wrong, phase estimates are wrong. Mitigation: same as Phase 3 root cause.
4. **Loopback regression from Phase 19**: May prevent synthetic testing.

## Open Questions

- Should we apply correction to HT-SIG0 too? (No — HT-SIG0 is the reference, modifying it is contradictory.)
- Should we use the 4 pilots as additional anchors? (Yes, already in re-encode.)
- Should we re-estimate phase after HT-SIG1 decode? (No — adds complexity, marginal benefit.)

## Related Memory

- [[project_p19_htsig_viterbi]] — Phase 19: HT-SIG0 stable / HT-SIG1 varies finding
- [[project_p18_lsig_viterbi_analysis]] — Phase 18: LSIG_RATE_FORCE=0xD fix
- [[project_p10_task4_cpe]] — Phase 10 Task 4: per-symbol CPE REVERTED (high variance)
- [[project_p14_sync_long_deadlock]] — Phase 14: scheduler fix (enables USRP test)
- [[project_p17_5ghz_a0_subdev]] — Phase 17: 5 GHz A:0 subdev isolation
