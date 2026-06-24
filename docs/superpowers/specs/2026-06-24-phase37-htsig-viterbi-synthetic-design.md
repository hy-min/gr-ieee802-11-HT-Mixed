# Phase 37 — HT-SIG Viterbi Synthetic Tolerance Test (Design Spec)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this design task-by-task. Implementation will be detailed in a separate plan.

**Date**: 2026-06-24
**Author**: Phase 37 brainstorming session
**Status**: APPROVED (pending user review of this spec)

---

## Goal

Determine whether the HT-SIG viterbi decoder in `frame_equalizer_impl.cc::decode_htsig_from_rotated()` is the cause of the HT-SIG viterbi failure on USRP, and quantify its tolerance to controlled impairments. This isolates whether the 30+ phase equalizer-level investigation was on the wrong layer, or whether the viterbi itself has a bug that no equalizer fix can mask.

**Success outcome**: A test harness that produces a pass/fail verdict per impairment layer, with a clear go/no-go for "is the viterbi the bug?".

---

## Background

### Why we are here

Phase 36 T4 verdict (`docs/superpowers/notes/2026-06-24-phase36-t4-verdict.md`) REFUTED the per-SC pilot CPE hypothesis:

- Pilot diff std (HT-SIG1 - HT-SIG0) = **1.367 rad** (down from 1.390 rad baseline = 1.7%, within noise)
- Per-pilot std 1.2-1.4 rad roughly constant across 4 SCs `{-21, -7, 7, 21}` — NOT a linear function of SC index
- `b` (slope) coefficient |b| < 0.14 rad/SC, projects to ~1 rad at 52-SC edge (comparable to noise floor)

**9+ equalizer-level investigations REFUTED** in a row (CFO/SFO, timing offset, per-symbol, per-SC, H52 variants, K-sweep, δ correction, etc.). The T4 verdict proposes 4 possibilities, the most likely being **BCC decoder / viterbi bug** that no equalizer fix can mask.

### Phase 33 finding

Phase 33 L-LTF0 14-sample shift fix unblocked L-SIG. Loopback shows **L-SIG viterbi works perfectly** after this fix — this is critical because it means:
- BCC encoding/decoding infrastructure is functional for L-SIG
- The `viterbi_decode_*` primitives work on the 24-bit L-SIG field
- The HT-SIG viterbi is the SAME decoder (rate 1/2, K=7, polynomials `[133, 171]`) but on a 48-bit input (24+24 across two symbols)
- L-SIG has a synthetic test (`test_lsig_viterbi_synthetic.py`) that passes 3/3 — this is the template for Phase 37

### HT-SIG specifics (per IEEE 802.11-2016 Section 18.3.5.3)

- 48 bits total: 24 bits in HT-SIG0 + 24 bits in HT-SIG1
- BCC rate 1/2 encoding: 24 bits → 48 coded bits per symbol
- Interleaver: depth-2 per Table 18-6
- Modulation: BPSK with QBPSK rotation (90° offset from L-SIG)
- Pilots at SCs `{-21, -7, 7, 21}` with polarity sequence `kHtPilotPolarity127[127]`
- QBPSK detection: `E_Q > E_I` after equalization determines 4 possible rotations `{0°, 90°, 180°, -90°}`
- Sign ambiguity: `inv_a, inv_b ∈ {0, 1}` — 16 total candidates (4 rot × 2 inv × 2 inv)

---

## Architecture

### Components

1. **Python test harness** (`examples/test_htsig_viterbi_synthetic.py`, new, ~300-400 lines)
   - Synthesizes HT-SIG bit patterns
   - BCC encodes per IEEE 802.11-2016
   - Interleaves per Table 18-6
   - BPSK + QBPSK modulates
   - Inserts pilots
   - Injects controlled impairments
   - Calls C++ decoder via Python binding
   - Compares decoded bits to expected (BER + CRC)

2. **C++ decoder binding** (existing)
   - `decode_htsig_from_rotated(eq_data, eq_data, H52, inv_a, inv_b, ...)` in `lib/frame_equalizer_impl.cc`
   - Verify this is exposed via the existing Python binding (`swig` or `pybind`)
   - If not exposed, add a thin wrapper to make it accessible

3. **Pattern source**: `examples/test_lsig_viterbi_synthetic.py` — read first, mirror its structure (test case format, BER reporting, regression markers)

### Test layers (per the brainstorming session decision)

| Layer | Impairment | Range | Purpose |
|---|---|---|---|
| **Layer 1: Clean** | None | n/a | Verify decoder is correct on ideal input. Failure = decoder bug. |
| **Layer 2: +CFO** | Carrier freq offset | 0, 100, 500, 1000, 5000 Hz | Test phase drift tolerance. USRP CFO expected < 1 kHz. |
| **Layer 3: +AWGN** | Additive white Gaussian noise | 20, 15, 12, 9, 6 dB SNR | Test noise tolerance. USRP avg_snr_lsig ~12-15 dB. |
| **Layer 4 (optional): +SFO** | Sampling freq offset | 0, 10, 50, 100 ppm | Test SFO drift across symbols. Skip if budget tight. |

### Test cases per layer (3 cases per the L-SIG pattern)

- **Case A**: `len=100, MCS=0, SGI=0` (typical, no aggregation)
- **Case B**: `len=1000, MCS=7, SGI=1, agg=1` (max rate, SGI, aggregation)
- **Case C**: `len=10, MCS=0, LDPC=1` (LDPC flag boundary, minimum length)

---

## Data flow

For each test case:

1. **Synthesize HT-SIG bits** (48 total = 24+24):
   - Build 24-bit HT-SIG0 from case parameters (length, MCS, CBW 20/40, SGI, LDPC, etc.)
   - Build 24-bit HT-SIG1 (continuation + CRC)

2. **BCC encode** (rate 1/2, K=7, polynomials `[133, 171]`):
   - 24 input bits → 48 output coded bits per OFDM symbol
   - Total: 96 coded bits across HT-SIG0 + HT-SIG1

3. **Interleave** per IEEE 802.11n Table 18-6:
   - Depth-2 interleave, two permutations

4. **Modulate to complex symbols**:
   - Map coded bits: 0 → +1, 1 → -1 (BPSK)
   - QBPSK rotation: 90° offset (multiply by `j`)
   - Result: 48 complex symbols per OFDM symbol

5. **Insert pilots** at SCs `{-21, -7, 7, 21}` (bins 48-51 in 52-order):
   - Pilot polarity from `kHtPilotPolarity127[symbol_index % 127]`
   - 48 data + 4 pilot = 52 SCs

6. **Impairment injection** (per test layer):
   - **Layer 1**: skip
   - **Layer 2 +CFO**: 64-bin IFFT → multiply by `exp(j·2π·f_cfo·t/n)` → 64-bin FFT
   - **Layer 3 +AWGN**: add complex Gaussian noise after FFT, with given SNR
   - **Layer 4 +SFO** (if done): fractional resample to simulate SFO

7. **Slice** to 48 data SCs (drop pilots) for the C++ decoder

8. **Call C++ decoder** for all 16 candidates (4 rot × 2 inv × 2 inv):
   - `decode_htsig_from_rotated(eq_data, eq_data, H52, inv_a, inv_b, parsed_len, parsed_mcs, ...)`
   - Return: `crc_ok` bool + decoded bit pattern + parsed fields

9. **Compare** decoded bits to expected → compute BER and CRC.

---

## Components (file-by-file)

### New: `examples/test_htsig_viterbi_synthetic.py`

- Top-level structure mirrors `test_lsig_viterbi_synthetic.py`
- Functions:
  - `synth_htsig_bits(case: str) -> tuple[np.ndarray, np.ndarray]` — 24+24 bits
  - `bcc_encode(bits: np.ndarray) -> np.ndarray` — K=7 rate 1/2
  - `htsig_interleave(coded: np.ndarray) -> np.ndarray` — Table 18-6
  - `bpsk_qbpsk_modulate(coded: np.ndarray) -> np.ndarray` — 48 complex symbols
  - `insert_pilots(symbols: np.ndarray, sym_idx: int) -> np.ndarray` — 52 SC array
  - `apply_cfo(symbols: np.ndarray, cfo_hz: float) -> np.ndarray` — time-domain rotation
  - `apply_awgn(symbols: np.ndarray, snr_db: float) -> np.ndarray` — complex Gaussian
  - `call_cpp_decoder(eq_data: np.ndarray, h52: np.ndarray) -> tuple[bool, np.ndarray]`
  - `run_test_case(case: str, layer: str) -> dict` — runs one case through one layer
  - `main()` — runs all 3 cases through all enabled layers
- Test output format: `3/3 PASS` per layer, like L-SIG test
- Uses NumPy; no PyTorch/TensorFlow dependencies

### Possibly modified: `lib/frame_equalizer_impl.cc` or binding

- Verify that `decode_htsig_from_rotated` is reachable from Python
- If wrapped behind the `frame_equalizer` block constructor, may need a thin standalone function
- Mirror what `test_lsig_viterbi_synthetic.py` does for L-SIG viterbi access

### Modified: `memory/MEMORY.md`

- Add new test command: `python examples/test_htsig_viterbi_synthetic.py`
- Add note: "HT-SIG viterbi synthetic test (3/3 PASS) confirms decoder correctness independent of USRP equalizer"

### New: `docs/superpowers/notes/2026-06-24-phase37-verdict.md`

- Verdict on which layer the failure lives in
- One of: (a) decoder bug → fix path; (b) decoder fine, equalizer issue → revert; (c) decoder fine, equalizer fine, impairment beyond tolerance → new direction

### New: `memory/project_p37_htsig_viterbi_synthetic.md`

- Standard memory file with goal, root cause, implementation, verification, files, test commands, related memory

---

## Success criteria

| Layer | Pass criterion | Failure indicates |
|---|---|---|
| **Layer 1 (clean)** | 3/3 cases CRC OK, BER = 0 | Decoder has a bug |
| **Layer 2 (+CFO)** | 3/3 cases CRC OK at CFO ≤ 1 kHz | Viterbi too sensitive to phase drift |
| **Layer 3 (+AWGN)** | 3/3 cases CRC OK at SNR ≥ 10 dB | Decoder doesn't use soft info or has metric bug |
| **Layer 4 (+SFO)** *(if done)* | 3/3 cases CRC OK at SFO ≤ 50 ppm | Viterbi too sensitive to per-symbol drift |

**Verdict flow**:
- Layer 1 FAILS → fix decoder (Phase 38 candidate)
- All layers PASS → equalizer is the bottleneck; revert to upstream investigation
- Layer 1 passes, Layer 2 or 3 fails at expected USRP levels → tolerance is the issue; new direction needed

---

## Error handling & fallback

| Outcome | Action |
|---|---|
| Layer 1 fails (CRC not OK on clean input) | Diagnose decoder: sign error in metric, polarity wrong, interleaver index off-by-one, metric threshold too strict, CRC mask wrong. Fix in `lib/frame_equalizer_impl.cc` or viterbi implementation file. |
| Layer 2 fails at CFO ≥ 1 kHz | Viterbi tolerates typical USRP CFO. If failing at < 1 kHz, viterbi needs phase tracking pre-input. |
| Layer 3 fails at SNR ≥ 10 dB | Viterbi not using soft info; OR metric threshold too high. Investigate metric computation. |
| All layers pass | Decoder is correct. **Equalizer is the bottleneck** (Phase 36 conclusion stands). Next: investigate what impairment the equalizer is NOT removing that viterbi needs. |

---

## Testing & regression

- **New test command** (regression): `python examples/test_htsig_viterbi_synthetic.py` — must print `3/3 PASS` per layer.
- Run after any C++ viterbi change to confirm no regression.
- **Loopback regression** must still pass: `python examples/test_direct_loopback.py` → `Final: OK=1 FAIL=0`.
- The new test should be runnable in <30 seconds (no USRP needed).

---

## Out of scope (Phase 37 will NOT do)

- HT-DATA viterbi testing (defer to Phase 38+)
- Soft-decision LLR implementation (defer unless Layer 3 reveals need)
- Equalizer changes (defer to Phase 38 if synthetic shows decoder is fine)
- HT-SIG viterbi bug fixes (defer to Phase 38 if diagnostic shows need)

---

## Files affected

| File | Action | Lines (est.) |
|---|---|---|
| `examples/test_htsig_viterbi_synthetic.py` | CREATE | 300-400 |
| `lib/frame_equalizer_impl.cc` or binding | MAYBE_MODIFY | 0-30 (if binding needs adjusting) |
| `docs/superpowers/notes/2026-06-24-phase37-verdict.md` | CREATE | 50-100 |
| `memory/project_p37_htsig_viterbi_synthetic.md` | CREATE | 80-120 |
| `MEMORY.md` | MODIFY | +3-5 (test command, finding) |

Total expected code volume: ~500 lines new, 0-30 lines binding tweak (if needed).

---

## Implementation order (preview, will be detailed in writing-plans)

1. **T1**: Read `test_lsig_viterbi_synthetic.py` to mirror conventions. Verify Python binding exposes `decode_htsig_from_rotated` (or equivalent). Add binding wrapper if needed.
2. **T2**: Implement Layer 1 (clean) of `test_htsig_viterbi_synthetic.py`. Must pass 3/3.
3. **T3**: Add Layer 2 (+CFO). Sweep CFO values, report CRC OK rate.
4. **T4**: Add Layer 3 (+AWGN). Sweep SNR values, report CRC OK rate.
5. **T5** *(optional)*: Add Layer 4 (+SFO).
6. **T6**: USRP validation — if all synthetic layers pass, re-run USRP test to see if behavior matches synthetic boundary.
7. **T7**: Write verdict + memory files.

---

## Risk assessment

| Risk | Mitigation |
|---|---|
| Python binding doesn't expose decoder | Add thin C++ wrapper, mirror how L-SIG test accesses viterbi |
| `decode_htsig_from_rotated` has subtle dependencies on H52 / rotation context | Pass a trivial H52 (all ones), apply rotation in test harness before calling |
| Layer 1 fails with a hard-to-diagnose bug | Add a "decoded bits dump" mode to the harness for comparison with expected |
| USRP validation (T6) doesn't match synthetic | Document the gap; the gap itself is informative (e.g., USRP has additional impairment not modeled) |
| Test takes too long | Run with small impairment sweep (3 CFO values, 3 SNR values); should complete in <10s |

---

## Definition of done

- [ ] `examples/test_htsig_viterbi_synthetic.py` exists, runs in <30s
- [ ] Layer 1 (clean): 3/3 cases CRC OK, BER = 0
- [ ] Layer 2 (+CFO): 3/3 cases CRC OK at CFO ≤ 1 kHz
- [ ] Layer 3 (+AWGN): 3/3 cases CRC OK at SNR ≥ 10 dB
- [ ] Layer 4 (+SFO): either DONE or DEFERRED with note
- [ ] Verdict file written to `docs/superpowers/notes/2026-06-24-phase37-verdict.md`
- [ ] Memory file written to `memory/project_p37_htsig_viterbi_synthetic.md`
- [ ] `MEMORY.md` updated with new test command and finding
- [ ] Loopback regression still passes

---

## Related memory

- [[project-p36-persc-fit-refuted]] — Phase 36 wall (per-SC fit REFUTED)
- [[project-p35-htsig-fix]] — Phase 35 partial (per-symbol MEAN REFUTED)
- [[project-p34-delta-correction]] — Phase 34 success (L-SIG unblocked)
- [[project-p33-lltf0-14sample-shift-fix]] — Phase 33 L-LTF0 root cause
- [[project-p19-htsig-viterbi]] — Phase 19 HT-SIG bottleneck (target of Phase 37)
- [[project-p18-lsig-viterbi-analysis]] — Phase 18 L-SIG unblock (synthetic test pattern source)
