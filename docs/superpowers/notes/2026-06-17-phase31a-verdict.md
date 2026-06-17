# Phase 31a — L-LTF0 Sample Boundary Diagnostic (2026-06-17)

## Status: BLOCKED — instrumentation never fired

The Phase 31a data collection did **not** produce any L-LTF0 timing records.
The dump instrumentation (Tasks 2/3) was confirmed active in both the
`ht_symbol_splitter` and `frame_equalizer`, but no `[SPLITTER] LTS0` or
`[EQUALIZER] H52 compute` lines were emitted during the 30-second capture
because **no frame ever reached the LTS0 emission site or the H52 compute
site in the equalizer**. Every potential LTS0/HT-SIG FFT was rejected at
the energy gate in the splitter. The Phase 31 timing-offset hypothesis
cannot be evaluated from this run.

## Setup
- 5 GHz A:0+A:0 USRP X310 setup (Phase 17 single-board TDD workaround)
  - `addr=192.168.10.2` (auto-discovery via `uhd_find_devices` returns
    "No UHD Devices Found"; explicit `--args addr=192.168.10.2` works)
  - Subdev: `A:0` for both TX (TX/RX port) and RX (RX2 port)
  - Center freq: 5.89 GHz, rate: 10 MSps, TX gain / RX gain: defaults
- Env-vars (set in `test_lltf_timing_diagnostic.py`):
  - `IEEE80211_LLTF_TIMING_DUMP=1`
  - `IEEE80211_LSIG_RATE_FORCE=0xD`
  - `IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE=1`
- 30-second capture via `examples/test_lltf_timing_diagnostic.py`
  (which shells out to `test_usrp_minimal_loopback.py --duration 30`)
- `/tmp/p31a_raw.log` (546 MB) and `/tmp/p31a_diagnostic.csv` (0 rows) captured

## Instrumentation Summary
- **Task 2** (commit a9347a3): `ht_symbol_splitter` dump at LTS0 emission —
  `[SPLITTER] LTS0 seq=N current_idx=M lts1_expected_rel=K`
  (gated on `g_lltf_timing_dump && symbol_type == 0 && rel_idx == 63`,
  source line 736 of `lib/ht_symbol_splitter_impl.cc`)
- **Task 3** (commit 862321f): `frame_equalizer` dump at H52 compute site —
  `[EQUALIZER] H52 compute nread=A lts0_bin=B lts1_bin=C d_sym_idx=D
  lts0_mag0=E lts0_mag25=F`
  (source line 3861 of `lib/frame_equalizer_impl.cc`)

Both env-var gated init lines **were observed** in the log:
```
[EQUALIZER] IEEE80211_LLTF_TIMING_DUMP=1 (received LTS0/LTS1 indices logged at H52 compute site)
[SPLITTER] IEEE80211_LLTF_TIMING_DUMP=1 (LTS0/LTS1 sample indices will be logged)
```
This confirms the new code path is built and loaded. The dumps simply
never fire because the underlying data flow stalls upstream.

## Baseline (Loopback Verification)
- Splitter current_idx: 63 (LTS0 sample offset within frame window)
- Equalizer lts0_bin: 0 (FFT-block index of LTS0)
- Equalizer lts1_bin: 1 (FFT-block index of LTS1, = lts0_bin + 1)
- avg_snr_lsig: ~30-50 (clean)

(Baseline values are from the loopback pass referenced by Tasks 2/3;
this Phase 31a run did not regenerate the loopback baseline.)

## Results — analyzer output
```
Analyzed 0 frames with dump records

[VERDICT] No data — check env-var gating or run test_lltf_timing_diagnostic.py
```
Exit code 1 (no CSV rows).

## Results — raw log analysis (the real story)
The 30-second capture ran to completion. Frame count statistics from
`/tmp/p31a_raw.log`:

| Metric                                       | Count         |
|----------------------------------------------|---------------|
| `[SPLITTER_FRAME_START]` (frame entries)     | 48,244        |
| `[SPLITTER_ENERGY_DROP]` (FFT rejections)    | 191,256       |
| `[SPLITTER] LTS0` (env-var dump, EXPECTED)   | **0**         |
| `[EQUALIZER] H52 compute` (env-var dump)     | **0**         |
| `[SYNC_LONG] Top correlation magnitude` > 0.01 | **0**       |
| Final test counter (test_usrp_minimal_loopback) | Sent: 31, Recv: 0 |

Sample SPLITTER_ENERGY_DROP pattern (frame 2):
```
[SPLITTER_ENERGY_DROP] rel=63  energy=0.49 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=143 energy=0.48 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=223 energy=0.51 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=303 energy=0.52 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=383 energy=0.52 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=463 energy=0.54 frame=2 in_frame=1
[SPLITTER_ENERGY_DROP] rel=543 energy=0.50 frame=2 in_frame=1
```
Energy is 0.47–0.54 across all 7 expected L-LTF/L-SIG/HT-SIG/HT-STF/HT-LTF
positions; threshold in source (line 689) is `2.0`. Every symbol is
rejected.

`sync_long` top correlation magnitudes seen in the run:
```
[SYNC_LONG] Top correlation magnitude: 0.0038
[SYNC_LONG] Top correlation magnitude: 0.0036
```
These are the only two correlation values observed — both **< 0.004**,
indicating the long-preamble correlation is essentially noise. The
expected L-LTF correlation peak for a real 802.11n frame is ≫ 0.1.

The TX chain (modulator + UHD sink) is running (31 frames generated),
but the air/RX path delivers no usable signal back into the splitter.

## Verdict
**BLOCKED — no actionable timing data**.

The Phase 31 timing-offset hypothesis ("L-LTF0 sample boundary is off
by 1–2 samples on USRP e2e frames") **cannot be confirmed or refuted**
from this run, because the RX chain never produces a frame whose FFT
buffer carries real energy. The instrumentation we built to test the
hypothesis is correctly placed and correctly gated, but it sits
downstream of a broken air/RX path. Fixing the timing offset (Task 7)
will not unblock Phase 30 verification if no frame ever reaches the
LTS0 emission site.

### Why this is NOT a regression
- The instrumentation code is correct and confirmed active
  (init lines printed at startup).
- The `0 records` outcome reflects the RX chain state, not a script
  regex mismatch. Sample patterns from raw log:
  - SPLITTER: 48,244 frame entries, but **zero** LTS0 emissions
  - EQUALIZER: env-var init printed, but **zero** H52 compute sites reached
  - The expected dump lines (`[SPLITTER] LTS0 seq=…`, `[EQUALIZER] H52
    compute nread=…`) are **absent**, not malformed.
- The script's regex patterns were sanity-checked against
  `lib/ht_symbol_splitter_impl.cc:739` and
  `lib/frame_equalizer_impl.cc:3861`; the patterns match the source
  exactly. The data simply isn't there.

### Why this matters for Phase 31 / Phase 30
Phase 30's verdict was that "L-LTF0 timing fix is the recommended
upstream intervention". This Phase 31a run shows that **timing is
several layers downstream of the actual failure**:
1. sync_long correlation magnitude ≪ 0.01 (expected > 0.1)
2. splitter energy at expected LTS0 position: 0.5 (threshold 2.0)
3. H52 never computed (no equalizer input)

Fixing L-LTF0 timing (Task 7) addresses step 3, but step 1 must be
fixed first or the data never gets there.

## Recommendations
1. **Do not proceed to Task 7** (env-var-gated offset correction) yet.
   There is no point adding a timing correction to a splitter that
   receives noise-only FFTs.
2. **Phase 31b**: investigate why sync_long correlation is < 0.01 on
   USRP e2e. Candidate causes (in priority order):
   - USRP antenna/RX path broken or disconnected (physical)
   - TX/RX frequency mismatch (e.g. 5.89 GHz vs 2.4 GHz subdev)
   - UHD sink underflow (`usrp_sink :error: 1 underflows occurred` in
     the log) starving the air of samples
   - RX2 path on A:0 is damaged / mis-cabled (per Phase 16 B:0 issue)
3. **Phase 31c** (after 31b): re-run `test_lltf_timing_diagnostic.py`
   and re-evaluate the timing-offset hypothesis once frames reach the
   splitter with energy > 2.0.
4. **Re-confirm software loopback** at the start of 31b: 3/3 pass is
   the decoder-validation anchor; if it regresses, the splitter /
   equalizer instrumentation is suspect, not the air path.

## Files
- `/tmp/p31a_raw.log` (546 MB raw USRP capture; kept for post-hoc
  analysis of sync_long magnitudes and energy values)
- `/tmp/p31a_diagnostic.csv` (header only, 0 rows)
- Source confirmed:
  - `/home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc:736-744` (LTS0 dump)
  - `/home/hy/gr-ieee802-11/lib/frame_equalizer_impl.cc:3861` (H52 dump)
  - `/home/hy/gr-ieee802-11/lib/ht_symbol_splitter_impl.cc:689-694` (energy gate, threshold 2.0)

## Related Memory
- [[project-p30-usrp-verdict]] — Phase 30 parent context (recommended
  L-LTF0 timing fix; this Phase 31a shows that fix needs to be preceded
  by an air-path investigation)
- [[project-p23-usrp-verification]] — earlier USRP verification
  attempts (X310 + UBX-160 confirmed at frame-detect layer)
- [[project-status-overview]]
