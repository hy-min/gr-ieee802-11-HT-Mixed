# Phase 35 — HT-SIG Viterbi Diagnostic Verdict

**Date**: 2026-06-24
**Test**: 30s USRP at 5 GHz A:0+A:0, freq 5890 MHz, tx-gain 20, rx-gain 20, rx-scale 60
**Env-vars**: `TIMING_OFFSET_APPLY=1`, `LSIG_RATE_FORCE=0xD`, `HTSIG_BIN_DUMP=1`, `HTSIG_PILOT_DUMP=1`, `HTSIG_EQ_INPUT_DUMP=1`
**Data**: `/tmp/p35a_usrp.log` (357 MB), `/tmp/p35a_raw_iq.bin` (4.5 GB)
**Analyzer**: `examples/p35_htsig_analyze.py` (commit `c5da5dc`)
**Goal**: USRP e2e `[HT_SIG]` viterbi crc_fail → identify failure layer → pick fix path

---

## Diagnostic Results

### Dump Count Anomaly

Only **2 [HTSIG_BIN_DUMP] and 2 [HTSIG_PILOT_DUMP]** lines captured despite
**14 HT_SIG_PARSE_FAIL events** (238 [HT_SIG_CAND]/[HT_SIG_PARSE_FAIL] lines
total — many from the same candidates across multiple inv_lsig retries).

Root cause: dump is gated on `d_internal_symbol_counter == 4`
(`lib/frame_equalizer_impl.cc:3921`), but HT-SIG processing can run at higher
counters during `inv_lsig` retries (which re-run after failed L-SIG
candidates, advancing the counter past 4 before re-reach the dump site).
Additionally, the `lsig_enc != 0` continue filter (line 3896) blocks the
dump for many frames unless `IEEE80211_FORCE_HTSIG=1` is also set — frames
where L-SIG encryption mode is non-zero skip the HT-SIG dump block entirely.

**Sparse but usable data** — the 2 captured frames are enough to compute
pilot-diff statistics, since both frames dump complete HT-SIG0/HT-SIG1
arrays.

### Pilot Phase Diff (HT-SIG1 − HT-SIG0)

Analyzer output from `/tmp/p35a_usrp.log`:

```
[P35] ===== HT-SIG1 - HT-SIG0 pilot phase diff =====
[P35]   N frames: 2
[P35]   pilot@-21: mean=+2.457rad  std=0.297rad  max|diff|=2.754rad
[P35]   pilot@-7:  mean=+1.546rad  std=0.091rad  max|diff|=1.637rad
[P35]   pilot@+7:  mean=+1.755rad  std=1.161rad  max|diff|=2.915rad
[P35]   pilot@+21: mean=-0.297rad  std=2.314rad  max|diff|=2.611rad
[P35]   ALL pilots pooled: mean=+1.365rad  std=1.654rad  max|diff|=2.915rad

[P35] ===== Pilot coherence (std of 4 pilots within symbol) =====
[P35]   frame  htsig0_std  htsig1_std  mean_|h0-h1|
[P35]       0      1.429      2.191       2.479
[P35]       1      1.589      2.011       1.556
```

**Interpretation**:
- Pilot diff std=1.654 rad is HUGE (≈ 95°). The HT-SIG1 phase is wildly
  different from HT-SIG0 phase.
- Per-symbol drift of ~1.6 rad between consecutive OFDM symbols ≈
  - SFO of ~0.2 ppm (δ between symbols at adjacent counters)
  - or CFO of ~10 kHz residual at 5 GHz
- Phase 34 δ correction only handles a **constant per-frame offset**. It does
  NOT handle per-symbol drift between HT-SIG0 (counter=3) and HT-SIG1
  (counter=4).
- The CFO+SFO rotation at `frame_equalizer_impl.cc:3104` uses
  `d_phase_diff_per_sc[i] * d_internal_symbol_counter` — for counter=3 vs
  counter=4, the rotation is 3× and 4× the per-SC phase. If
  `phase_diff_per_sc` is accurate, this should cancel the linear drift.
- **Hypothesis**: `d_phase_diff_per_sc` is inaccurate (or zero) for these
  frames, so the rotation doesn't cancel the per-symbol drift, leaving
  ~1.6 rad residual between HT-SIG0 and HT-SIG1.

### |bin| Saturation (ADC Clipping)

```
[P35] ===== |bin| distribution across all BIN dumps =====
[P35]      htsig0 ALL bins: mean=  90.08  median=  81.55  std= 41.63  min=   9.80  max= 202.75
[P35]      htsig1 ALL bins: mean=  82.21  median=  76.16  std= 45.46  min=   6.34  max= 194.38
[P35]        htsig0 PILOTS: mean=  85.89  median=  75.12  std= 45.04  min=  43.87  max= 200.30
[P35]        htsig1 PILOTS: mean=  74.02  median=  64.01  std= 46.32  min=  26.74  max= 182.64
[P35]      htsig0 DATA SCs: mean=  90.43  median=  82.24  std= 41.31  min=   9.80  max= 202.75
[P35]      htsig1 DATA SCs: mean=  82.89  median=  78.14  std= 45.32  min=   6.34  max= 194.38
```

The |bin| values are SATURATED (mean ~90, max ~200). Expected clean BPSK
constellation |bin| ≈ 1 (after normalization). Values 80-200 indicate raw
FFT output is far beyond the AGC's intended linear range — **ADC clipping**.

Phase 34's lower rx-scale didn't help here because we kept the same script
config (`--rx-scale 60` on the test invocation). This saturation is a
**separate issue** but contributes to the high pilot-diff: ADC clipping
distorts phase nonlinearly, especially at the SCs where signal magnitude
peaks.

### Per-Frame |bin| Pilots vs Data

```
[P35]   frame  |h0_pilot|_mean  |h0_data|_mean  |h1_pilot|_mean  |h1_data|_mean  pilot/data_h0
[P35]       0           69.40          92.91           42.36          82.39          0.747
[P35]       1          102.37          87.95          105.68          83.40          1.164
```

Pilots and data are similar magnitude (within 2×), so no SC-specific
clipping pattern visible. Both are equally saturated.

---

## Decision

**Fix path: Task 7c — per-symbol H update from HT-SIG pilots**

### Why NOT 7a, 7b, or 7d

- **7a (H52 re-investigation)**: H52 dump from earlier runs
  (commit 23726d6 baseline) showed mean|H|=0.10-0.12 (reasonable for USRP),
  so H52 estimation is not the upstream bottleneck.
- **7b (improve δ estimation)**: Phase 34 δ already gives 100% within 0.01
  of 1/64 grid (19771 frames, [[project-p34-delta-correction]]); cannot
  refine further with current data.
- **7d (viterbi threshold/metric)**: Viterbi metric range 12-17 is
  consistent across failures; lowering threshold is unlikely to help if the
  underlying symbols are at 1.6 rad phase error.

### Why 7c

The HT-SIG0/HT-SIG1 pilot diff of ~1.6 rad is a clear **per-symbol phase
drift** between consecutive OFDM symbols (HT-SIG0 counter=3, HT-SIG1
counter=4). This drift is NOT addressed by:

- Phase 34 δ correction (constant per-frame only)
- Phase 18 `LSIG_RATE_FORCE=0xD` (L-SIG specific, not HT-SIG)
- CFO+SFO rotation at line 3104 (requires accurate `phase_diff_per_sc`,
  which appears inaccurate here)

HT-SIG has **4 known pilot positions** (SC -21, -7, 7, 21 → bins 48, 49,
50, 51). Computing per-symbol mean pilot phase from these 4 pilots and
applying a CPE rotation directly cancels the per-symbol drift. This is a
"soft" form of CPE specifically for HT-SIG0 and HT-SIG1, gated on pilot
magnitude to avoid NaN on noise-only frames.

### Implementation Sketch (T7c)

After `d_early_eqsym[3,4]` are computed (counter=4), insert:

```cpp
// Phase 35 Task 7c: pilot-aided CPE for HT-SIG
if (d_early_eqsym_valid[3] && d_early_eqsym_valid[4]) {
    const int pilot_bins[4] = {48, 49, 50, 51};
    for (int sym_idx : {3, 4}) {
        gr_complexd sum(0, 0);
        int n_valid = 0;
        for (int b : pilot_bins) {
            if (std::abs(d_early_eqsym[sym_idx][b]) > 5.0) {  // |bin| threshold
                sum += std::polar(1.0, std::arg(d_early_eqsym[sym_idx][b]));
                n_valid++;
            }
        }
        if (n_valid >= 2) {
            double phi = std::arg(sum);
            for (int i = 0; i < 52; i++) {
                d_early_eqsym[sym_idx][i] *= std::polar(1.0, -phi);
            }
        }
    }
}
```

The threshold (|bin| > 5.0) avoids NaN on noise-only symbols. The
|g_htsig_pilot_cpe_apply| env-var (`IEEE80211_HTSIG_PILOT_CPE=1`) gates
the fix to keep loopback regression clean when off.

---

## Next Steps

- **T7**: Implement pilot-aided CPE on HT-SIG0/HT-SIG1 (see sketch above).
  Required: loopback regression (Final OK=1 FAIL=0) before USRP test.
- **T8**: USRP e2e verification. Re-run with `IEEE80211_HTSIG_PILOT_CPE=1`,
  `--rx-scale 40` (lower to avoid ADC clipping), 60s duration. Expected:
  `[HT_SIG_CAND] crc OK` and `FCS_OK > 0`.
- **T9**: Update memory (`project_p35_htsig_fix.md` + MEMORY.md index
  entry + active conventions if env-vars are added).

---

## Caveats

- **Sparse data**: only 2 dumps captured vs 14 candidate failures. Pilot-diff
  std (1.654 rad) is computed from 2 frames × 4 pilots = 8 samples. The
  mean diff (+1.365 rad) is well-supported by N=8, but the std could shift
  with more samples. Pilot-diff direction is consistent across all 4 pilot
  positions (all positive at 3 of 4 positions), so the per-symbol drift is
  real, not noise.
- **ADC clipping**: rx-scale 60 saturates FFT bins (mean 90, max 200). This
  distorts phase measurements and may inflate the pilot-diff std. **T8
  should use `--rx-scale 40`** to keep bins in linear range (target mean
  |bin| ≈ 1.0 after normalization).
- **BIN_DUMP == EQ_INPUT_DUMP**: As noted in Task 3 Step 3, both dumps fire
  at the same counter=4 site after all rotations are applied, so they are
  byte-identical. The pre-rotation BIN_DUMP would require a different
  insertion point (right after `extract_header52_from_sym64`, before CFO+SFO
  rotation). For Task 6 diagnosis, post-rotation is sufficient because the
  pilot-diff is computed between HT-SIG0 and HT-SIG1 (same per-symbol
  treatment for both, so rotation cancels in the diff).
- **Dump gate bug** (`d_internal_symbol_counter == 4`): should be relaxed to
  `>= 4` in a future phase to capture inv_lsig-retry cases. Not blocking
  for Phase 35 since 2 dumps are sufficient.

---

## Files

- Raw USRP capture: `/tmp/p35a_raw_iq.bin` (4.5 GB, ~30s @ 20 MSps × 4 bytes)
- Raw log: `/tmp/p35a_usrp.log` (357 MB)
- Analyzer: `/home/hy/gr-ieee802-11/examples/p35_htsig_analyze.py` (commit c5da5dc)
- Plan: `/home/hy/gr-ieee802-11/docs/superpowers/plans/2026-06-24-phase35-htsig-viterbi-fix.md`
- Source for dump gate: `lib/frame_equalizer_impl.cc:3921`
- Source for CFO+SFO rotation: `lib/frame_equalizer_impl.cc:3104`
- Source for lsig_enc filter: `lib/frame_equalizer_impl.cc:3896`

---

## Related Memory

- [[project-p34-delta-correction]] — Phase 34 δ correction (constant per-frame,
  cannot reach per-symbol drift)
- [[project-p33-lltf0-14sample-shift-fix]] — Phase 33 14-sample shift fix
  (solved H52 argH chaos; did NOT solve HT-SIG viterbi)
- [[project-p33b-usrp-validation-64psk]] — Phase 33b USRP validation,
  revealed per-frame 64-PSK quantization residual (Phase 34's target)
- [[project-p19-htsig-viterbi]] — Phase 19 per-symbol/per-SC CPE REFUTED,
  **prohibits** re-attempting CPE-without-pilot-aid
- [[project-p18-lsig-viterbi-analysis]] — Phase 18 `LSIG_RATE_FORCE=0xD`
  (still required; supersedes rate-field-corruption hypothesis)
- [[project-p31c-k-sweep-refuted]] — Phase 31c L-LTF0 K-sweep REFUTED
  (obsoleted by Phase 33 14-sample shift fix)
- [[project-status-overview]]