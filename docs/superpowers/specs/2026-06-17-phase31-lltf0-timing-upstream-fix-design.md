# Phase 31 — L-LTF0 Timing Upstream Fix Design

**Date**: 2026-06-17
**Status**: DESIGN (awaiting user review)
**Author**: 🧙🏾‍♂️ Professor Synapse
**Phase goal**: Unblock USRP e2e verification (≥1 FCS OK repeatable)
**Upstream context**: Phase 30 verdict — 8 equalizer-level hypotheses REFUTED, root cause is H52 estimation global failure on 40% of USRP frames caused by L-LTF0 FFT window timing sensitivity in the e2e chain.

## 1. Background

### 1.1 Phase 30 Final Verdict

30-phase investigation concluded:

| Component | Status |
|-----------|--------|
| Hardware (X310 + UBX-160) | ✅ OK (LO locked, DC=2e-6, TCXO 0.6 ppb) |
| Decoder logic | ✅ CORRECT (software loopback 3/3 PASS) |
| Offline USRP analysis | ✅ 40/48 matches (16.7% BER) |
| **E2E USRP** | **❌ BLOCKED at L-SIG viterbi** |
| **H52 estimation in e2e** | **❌ 40% global failure** |

### 1.2 Root Cause (Phase 30.1)

Controlled null-injection experiment showed:
- Single null SC in software loopback → 36/52 SCs have |H| < 0.5
- avg_snr_lsig: 3303.47 (matches USRP 2317-3031 pathological range)
- Conclusion: H52 estimator is highly sensitive to FFT window position
- Likely upstream cause: L-LTF0 FFT window extraction in the e2e chain
- 40% of USRP frames have this pathology, 40% are clean, 20% borderline

### 1.3 Why E2E Fails Despite Offline Success

- **Offline (Phase 28.3)**: Manually aligned capture, manually extracted LTS0/LTS1, manually equalized → 32.3 dB L-SIG SNR, 40/48 matches
- **E2E (Phase 28.4)**: Uses sync_short_fused → sync_long → ht_symbol_splitter → frame_equalizer → 0/16 FCS OK

The difference is the upstream L-LTF0 symbol timing alignment in the e2e chain. When the FFT window lands 1-2 samples off for some frames, the H52 estimator corrupts across many SCs.

### 1.4 Eight REFUTED Equalizer-Level Hypotheses

1. CFO dominant (Phase 24)
2. Static L-SIG timing offset (Phase 25.1)
3. CFO frequency sweep (Phase 25.2)
4. SFO / linear phase ramp (Phase 25.4)
5. Decision-directed phase tracking (Phase 26.1)
6. H52 estimation variants (Phase 27.1)
7. viterbi input scaling (Phase 29.2)
8. Per-SC SNR drop (Phase 30)

**Equalizer-level investigation is exhausted.** Phase 31 must move upstream.

## 2. Goals

### 2.1 Primary Goal

**Unblock USRP e2e verification**: ≥1 FCS OK repeatable across 3 consecutive 10-second runs at 5 GHz A:0+A:0 setup.

### 2.2 Out of Scope

- ❌ Equalizer-level changes (8 REFUTED hypotheses exhausted)
- ❌ Decoder logic changes (software loopback 3/3 PASS confirms correctness)
- ❌ Hardware changes (Phase 28.1 confirmed X310 + UBX-160 healthy)
- ❌ Sync_long algorithm changes (Phase 14 fix is stable, must not regress)
- ❌ Channel coding-aware equalization (Phase 30 listed as future work, not Phase 31)

## 3. Architecture: Three-Phase Investigation

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 31a: DIAGNOSTIC (instrument only, no fix)              │
│                                                              │
│ 1. In sync_long: dump d_frame_start sample index            │
│ 2. In ht_symbol_splitter: dump actual LTS0/LTS1 sample index │
│ 3. In frame_equalizer: dump received LTS0/LTS1 sample index  │
│ 4. Env-var gate: IEEE80211_LLTF_TIMING_DUMP=1               │
│ 5. Collect 100 frames, verify Phase 30 hypothesis            │
└──────────────────────────────────────────────────────────────┘
                              ↓
                   Hypothesis confirmed? ───┬─── NO → REFUTED
                              │                  Write verdict, close Phase 31
                              ↓ YES
┌──────────────────────────────────────────────────────────────┐
│ Phase 31b: TARGETED FIX (env-var-gated)                      │
│                                                              │
│ 1. In ht_symbol_splitter: add fixed offset correction       │
│ 2. Env-var: IEEE80211_LLTF_OFFSET_CORRECT=<int>             │
│ 3. Default = 0 (matches current behavior)                   │
│ 4. Test K = -2, -1, +1, +2 (within Phase 30 hypothesis)     │
│ 5. Do not modify sync_long or frame_equalizer unless 31a    │
│    proves the offset originates there                       │
└──────────────────────────────────────────────────────────────┘
                              ↓
                e2e unblocked? ───┬─── YES → Phase 31c
                                │
                                ↓ NO
                  REFUTED fix → Write verdict, close
                                │ (Consider Phase 32 fallback)
                                ↓
┌──────────────────────────────────────────────────────────────┐
│ Phase 31c: VERIFICATION (live USRP e2e)                      │
│                                                              │
│ 1. 5 GHz A:0+A:0 setup (Phase 17 workaround)               │
│ 2. Run test_usrp_minimal_loopback.py --duration 10          │
│ 3. Repeat 3 times, confirm ≥1 FCS OK repeatable             │
│ 4. Record best K value in verdict note                     │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 Key Design Choices

- **Hypothesis-driven three-stage flow**: If 31a fails, do not enter 31b; clearly mark REFUTED
- **Single primary modification point**: Default = `ht_symbol_splitter`; pivot only if 31a proves otherwise
- **Env-var pattern**: Matches `LSIG_RATE_FORCE=0xD` / `HT_STRUCT_AUDIT=1` / `HTSIG_INPUT_DUMP=1` project convention
- **sync_long protected**: Do not modify unless 31a proves offset originates there (avoids breaking Phase 14 fix)

## 4. Components

### 4.1 Three Instrument Points

**C1: `lib/sync_long.cc` (Phase 31a)**
- **Location**: When `frame_start` tag is emitted
- **Add**: `snprintf + USRP_LOG` dump of `d_frame_start` sample index
- **Env-var gate**: `IEEE80211_LLTF_TIMING_DUMP=1`
- **Format**: Single `USRP_LOG("%s", buf)` call (project convention, see commit e90e3f5)
- **Modifies business logic**: NO (read-only observation)

**C2: `lib/ht_symbol_splitter_impl.cc` (Phase 31a + 31b)**
- **Location**: When LTS0/LTS1 are output as blocks
- **31a add**: Dump actual LTS0/LTS1 sample index (`nread_so_far - block_size`)
- **31b add**: Apply `IEEE80211_LLTF_OFFSET_CORRECT` env-var offset before LTS0 extraction
- **31b fix mechanism**: `actual_index = nread_so_far - block_size + K` (K = -2..+2, default 0)
- **Modifies business logic**: 31a NO, 31b YES (env-var-gated, default 0 = no change)

**C3: `lib/frame_equalizer_impl.cc` (Phase 31a only)**
- **Location**: When H52 is computed from received LTS0/LTS1
- **Add**: Dump received LTS0/LTS1 sample index (from tag or counter)
- **Modifies business logic**: NO (observation only — 31b will not modify this block by default)

### 4.2 Key Boundaries

- `sync_long` business logic untouched (Phase 14 fix stable since 2026-06-15)
- `ht_symbol_splitter` is the **primary modification point** for 31b fix
- `frame_equalizer` is **observation-only** in 31a, not modified in 31b unless 31a proves offset originates there
- New test script: `examples/test_lltf_timing_diagnostic.py` (runs 5 GHz A:0, collects 100 frames)

### 4.3 Out-of-Scope Components

- ❌ No rewrite of `sync_long` algorithm
- ❌ No CP-correlation auto-timing
- ❌ No viterbi / decoder changes
- ❌ No `frame_equalizer` H52 algorithm changes

## 5. Data Flow

### 5.1 Phase 31a Diagnostic Data Flow

```
[USRP source @ 5 GHz A:0+A:0]
    │ (sample stream)
    ↓
[sync_short_fused]
    │ sync_offset=1158 (Phase 29.1 OK)
    │ → emits coarse frame_detect tag
    ↓
[sync_long]  ◀── C1: dump d_frame_start sample index
    │ → emits frame_start tag (sample index N)
    │ → LTF cross-correlation → emits LTF start tag
    ↓
[ht_symbol_splitter]  ◀── C2: dump actual LTS0/LTS1 sample indices
    │ → cuts stream into 80-sample OFDM symbols
    │ → emits wifi_start tags at symbol boundaries
    │ → LTS0 at sample M (= N + 16 if +16 sample offset, per Phase 28.2)
    │ → LTS1 at sample M + 80
    ↓
[fft_vxx] (FFT, 64-bin)
    │ → 64 frequency bins per symbol
    ↓
[frame_equalizer]  ◀── C3: dump received LTS0/LTS1 sample indices
    │ → reads LTS0/LTS1 from FFT output
    │ → H52 = (LTS0_freq + LTS1_freq) / 2
    │ → equalize L-SIG using H52
    ↓
[viterbi → decode_mac → FCS OK?]
```

### 5.2 Phase 31a Key Observations

- LTS0 sample index propagation: does it remain consistent from `sync_long` output to `frame_equalizer` input?
- `LTS0 - frame_start` difference: is it = 16 (Phase 28.2 offline-validated) or different in e2e?
- Per-frame variance vs constant offset: is the pathology due to drift or systematic shift?
- Correlation: do `avg_snr_lsig` pathological frames (2317-3031) correspond to LTS0-offset frames?

### 5.3 Phase 31b Fix Data Flow (assuming 31a confirms constant offset)

```
[ht_symbol_splitter] reads IEEE80211_LLTF_OFFSET_CORRECT=K
    │ LTS0 extraction: actual_index = nread_so_far - block_size + K
    │ (K = -2..+2, default 0 = current behavior)
    ↓
[remaining chain unchanged]
```

### 5.4 Data Storage

| File | Content | Used By |
|------|---------|---------|
| `/tmp/p31a_diagnostic.csv` | 100 frames × {sync_long_idx, splitter_lts0_idx, splitter_lts1_idx, eq_lts0_idx, eq_lts1_idx, avg_snr_lsig, lsig_ok} | 31a analysis |
| `p31a_analyze.py` | Statistics script: offset distribution + pathology correlation | 31a verdict |
| `/tmp/p31b_fix_<K>.json` | Per-K-value run statistics (BER, FCS OK, avg_snr) | 31b sweep |
| `docs/superpowers/notes/2026-06-17-phase31a-verdict.md` | 31a verdict (hypothesis confirmed or REFUTED) | All |
| `docs/superpowers/notes/2026-06-17-phase31b-verdict.md` | 31b verdict (best K or REFUTED) | All |
| `docs/superpowers/notes/2026-06-17-phase31c-verdict.md` | 31c verdict (≥1 FCS OK confirmed or BLOCKED) | All |

## 6. Error Handling

### 6.1 Phase 31a Failure Modes

**EF1: Hypothesis REFUTED (offset does not exist or is not constant)**
- **Detection**: 31a collects 100 frames; if `lts0_eq_idx - lts0_splitter_idx` std > 4 samples (2x the 1-2 hypothesis), hypothesis is REFUTED
- **Action**: Write `phase31a-verdict-REFUTED.md`, close Phase 31 topic
- **Fallback**: User decides whether to pivot to Phase 32 (per-frame variance adaptive correction)

**EF2: Hypothesis confirmed but per-frame variance is significant**
- **Detection**: 31a shows offset = 1 sample but std = 2-3 samples
- **Action**: 31b cannot use fixed offset; document need for per-frame correlation
- **Fallback**: Report "adaptive correction needed" and leave to Phase 32

**EF3: Instrument code introduces regression**
- **Detection**: Baseline (env-var off) compared against Phase 28.3 offline 16.7% BER; if regression > 5%, dump code is at fault
- **Action**: Switch to post-hoc dump (log all, filter on env-var) to avoid affecting timing

### 6.2 Phase 31b Failure Modes

**EF4: Fix ineffective (FCS OK still 0)**
- **Detection**: Test K = -2, -1, +1, +2 all fail
- **Action**: Write `phase31b-verdict-REFUTED.md`, document test matrix
- **Fallback**: Consider sync_long modification (violates single-modification-point principle; requires Phase 14 fix re-validation)

**EF5: Fix introduces new pathology**
- **Detection**: Software loopback 3/3 PASS should remain unchanged
- **Action**: If loopback regresses, revert and consider smaller K range
- **Fallback**: Reduce K range to {-1, 0, +1}

**EF6: USRP setup not reproducible**
- **Detection**: Phase 28 baseline (40/48 matches) does not reproduce
- **Action**: Re-run hardware characterization (similar to Phase 28.1)
- **Fallback**: Abort Phase 31, defer to environment-stable retest

### 6.3 Phase 31c Failure Modes

**EF7: ≥1 FCS OK not repeatable**
- **Detection**: First run yields 1 FCS OK, but 3 repeats yield 0
- **Action**: Mark as fluke in verdict note, do not claim unblock
- **Fallback**: Continue debugging or accept BLOCKED status pending Phase 32

## 7. Testing

### 7.1 Test Suite: Three Layers

**T1: Unit Tests (existing, no regression allowed)**
- `test_h_estimation_synthetic.py` — 5/5 must still pass
- `test_lsig_viterbi_synthetic.py` — 3/3 must still pass
- `test_h_estimation_lltf1_synthetic.py` — no regression
- `test_viterbi_pathmetric_offline.py` — no regression
- **Purpose**: 31a instrument code + 31b offset correction must not break synthetic tests

**T2: Software Loopback Regression (existing, must remain 3/3)**
- `test_direct_loopback.py` with `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`
- **Purpose**: `ht_symbol_splitter` modification must not break loopback path

**T3: New E2E USRP Test (Phase 31 goal)**
- `test_usrp_minimal_loopback.py --duration 10` × 3 runs
- **Pass criteria**: ≥1 FCS OK repeatable across 3 runs
- **Setup**: 5 GHz A:0+A:0 (Phase 17 workaround)
- **Env-vars active**:
  - `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`
  - `IEEE80211_LSIG_RATE_FORCE=0xD` (Phase 18)
  - `IEEE80211_LLTF_OFFSET_CORRECT=<K>` (Phase 31b, K = 0..2)
  - `IEEE80211_LLTF_TIMING_DUMP=1` (Phase 31a)

### 7.2 Test Data Storage

| File | Content | Used By |
|------|---------|---------|
| `/tmp/p31_baseline_loopback.log` | Pre-Phase-31 loopback regression baseline | 31b regression check |
| `/tmp/p31c_run_<N>_log.json` | Per-run T3 statistics (FCS OK, BER, avg_snr) | 31c verdict |

### 7.3 Pass/Fail Criteria

| Test | When | Pass Standard |
|------|------|---------------|
| T1 unit tests | After 31b code change | All pass |
| T2 loopback 3/3 | After 31b code change | 3/3 PASS |
| T3 e2e USRP | 31c verification | ≥1 FCS OK × 3 runs |

### 7.4 Test Execution Order

1. **31a code complete** → Run T1 + T2 (regression check on instrument code)
2. **31a data collection** → 100 frames, analyze, write verdict
3. **31b fix code complete** → Run T1 + T2 (regression check on fix code)
4. **31b sweep** → Test K = -2, -1, 0, +1, +2; find best K
5. **31c verification** → Run T3 3 times with best K; write final verdict

## 8. File Structure

### 8.1 New Files

| Path | Purpose |
|------|---------|
| `examples/test_lltf_timing_diagnostic.py` | 31a test script (collect 100 frames) |
| `p31a_analyze.py` | 31a offset distribution + pathology correlation analysis |
| `p31b_sweep_k.py` | 31b K-value sweep automation |
| `docs/superpowers/specs/2026-06-17-phase31-lltf0-timing-upstream-fix-design.md` | This design doc |
| `docs/superpowers/notes/2026-06-17-phase31a-verdict.md` | 31a verdict (CONFIRMED / REFUTED) |
| `docs/superpowers/notes/2026-06-17-phase31b-verdict.md` | 31b verdict (best K or REFUTED) |
| `docs/superpowers/notes/2026-06-17-phase31c-verdict.md` | 31c verdict (≥1 FCS OK or BLOCKED) |

### 8.2 Modified Files

| Path | Change | Phase |
|------|--------|-------|
| `lib/sync_long.cc` | Add env-var-gated `USRP_LOG` of `d_frame_start` sample index | 31a |
| `lib/ht_symbol_splitter_impl.cc` | Add env-var-gated dump of LTS0/LTS1 sample index | 31a |
| `lib/ht_symbol_splitter_impl.cc` | Add env-var-gated fixed offset correction (`IEEE80211_LLTF_OFFSET_CORRECT`) | 31b |
| `lib/frame_equalizer_impl.cc` | Add env-var-gated dump of received LTS0/LTS1 sample index | 31a |

### 8.3 Active Conventions (Preserved)

- Env-var gates: `IEEE80211_*` prefix, =1 enables (matches `LSIG_RATE_FORCE=0xD`, `HT_STRUCT_AUDIT=1`, etc.)
- Multi-value dumps: single `snprintf + USRP_LOG("%s", buf)` (commit e90e3f5)
- Test scripts: `examples/test_*.py` (matches `test_direct_loopback.py`, `test_usrp_minimal_loopback.py`)
- `make install` required after every `make` (project rule)
- Loopback regression env-vars: `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`

## 9. Timeline Estimate

| Phase | Estimated Effort | Calendar Estimate |
|-------|------------------|-------------------|
| 31a (instrument + collect 100 frames + analyze) | 2-3 hours | Same day |
| 31b (fix code + K sweep) | 1-2 hours | Same day |
| 31c (3x e2e verification) | 1 hour | Same day |
| Verdict notes | 30 min | Same day |
| **Total** | **5-7 hours** | **1 day** |

## 10. Fallback Plan

If Phase 31 is REFUTED at any stage:

| Failure | Pivot |
|---------|-------|
| 31a: hypothesis REFUTED (no constant offset) | Phase 32: per-frame variance adaptive correction (CP-correlation or robust H52 with median) |
| 31b: fix ineffective | Phase 32: modify `sync_long` (re-validate Phase 14 fix) |
| 31b: fix introduces regression | Phase 32: per-frame dynamic offset (not env-var fixed) |
| 31c: not repeatable | Phase 32: environment stability investigation (USRP timing, host scheduling) |
| All 31 phases REFUTED | **Accept BLOCKED status**: USRP e2e verification remains blocked, software loopback remains working decoder verification path |

## 11. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Phase 31a verdict | Hypothesis confirmed OR cleanly REFUTED | Verdict note written |
| Phase 31b verdict | Best K identified OR cleanly REFUTED | Verdict note + test matrix |
| Phase 31c verdict | ≥1 FCS OK repeatable OR cleanly BLOCKED | 3× T3 runs |
| T1 regression | All unit tests pass | 31b regression run |
| T2 regression | Software loopback 3/3 PASS | 31b regression run |
| Documentation | 3 verdict notes + 1 design spec committed | Git log |

## 12. Related Memory

- [[project-p30-usrp-verdict]] — Phase 30 final verdict (parent context)
- [[project-p28-hw-characterization]] — Phase 28.1 hardware OK
- [[project-p28-breakthrough]] — Phase 28 fresh capture analysis
- [[project-p27-h52-quality]] — Phase 27 H52 REFUTED (last equalizer-level)
- [[project-p23-usrp-verification]] — Phase 23+24 original USRP blocker
- [[project-p18-lsig-viterbi-analysis]] — Phase 18 LSIG_RATE_FORCE
- [[project-p17-5ghz-a0-subdev]] — Phase 17 5 GHz A:0 subdev workaround
- [[project-p14-sync-long-deadlock]] — Phase 14 sync_long fix (must not regress)
- [[project-status-overview]] — overall project status

## 13. Anti-Patterns Avoided

- ❌ **No more equalizer-level changes**: 8 REFUTED, exhausted
- ❌ **No sync_long algorithm rewrite**: Phase 14 fix stable, must protect
- ❌ **No CP-correlation complexity in Phase 31**: reserved for Phase 32 if needed
- ❌ **No env-var explosion**: 2 new env-vars (TIMING_DUMP, OFFSET_CORRECT), not 10
- ❌ **No measurement methodology changes**: reuse Phase 28 protocol
- ❌ **No "fix all pathology" overreach**: targeted fix on confirmed root cause only
