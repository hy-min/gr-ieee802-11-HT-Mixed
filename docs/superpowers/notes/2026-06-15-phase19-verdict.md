# Phase 19 — HT-SIG viterbi "structural fail" investigation (5 GHz A:0) (2026-06-15)

## TL;DR

Phase 19 (8 tasks) investigated the 24/24 HT_SIG_PARSE_FAIL events on legitimate rate=0xD L-SIGs at 5 GHz A:0 (the remaining bottleneck after Phase 18's LSIG_RATE_FORCE=0xD fix). The active decoder `decode_htsig_from_rotated` had no structural-failure audit, leaving the 24/24 events unattributed. Phase 19 added the diagnostics (Task 1.5 commit f0faf7c), captured the data (Task 3), analyzed it (Tasks 4, 6), and applied a per-symbol CPE fix (Task 7, commit 94c50e2). **The fix did not improve viterbi convergence** — H1 (CFO/SFO common-phase drift) was REFUTED. The HT-SIG1 corruption is more complex than a single global phase rotation. The fix is kept as a diagnostic-only knob.

## Critical findings (Tasks 1.5, 4, 6)

**Task 1.5**: The original plan scoped diagnostics to `decode_htsig_candidate` (line 1120), but the active code path at `general_work:3469` calls `decode_htsig_from_rotated` (line 1847). After adding the diagnostics to the active decoder, 128 HT_STRUCT_AUDIT events fired in 30s.

**Task 4**: All 128 structural failures are `crc_fail` (100%). Viterbi converges on a 48-bit codeword with valid tail, but the 8-bit CRC doesn't match. The dec48 bit-density is 15-22 (constrained, not uniform random), with 6 distinct mcs values and 5 distinct rsv patterns. Viterbi is finding a local minimum (similar codeword neighborhood) rather than the true codeword. Bit-error rate on CRC: mean 4.5/8 (HIGH noise).

**Task 6**: 8 distinct enc96 patterns out of 128 events. **HT-SIG0 is STABLE** (4 distinct values) but **HT-SIG1 VARIES** (4 distinct values). All 128 frames share the same 8 patterns = same TX content, RX rotates consistently per (inv_a, inv_b) trial. inv_a is a clean BPSK polarity flip (4/4 patterns have bit-inverse in inv_a=1 set). This points to **HT-SIG1-specific corruption**, NOT random noise or a code bug.

## Why Task 7's per-symbol CPE fix didn't work (H1 REFUTED)

The fix estimated residual phase from HT-SIG0's 4 pilots (SCs -21, -7, +7, +21 at indices 48-51 in the 52-element layout) and applied opposite rotation to HT-SIG1's symbols before bit extraction.

- 56 HT_SIG_PARSE_FAIL (more than baseline 24)
- 1 FCS OK (unchanged)
- 57 distinct enc96 patterns (was 8) — fix changes bits but doesn't fix the underlying corruption
- All 56 parse fails cluster at last_rot=3, last_inv_a=1, last_inv_b=1

**Conclusion**: The HT-SIG1 corruption is **not** a single common-phase rotation between HT-SIG0 and HT-SIG1. It's more complex — likely per-subcarrier phase noise, equalization quality, or sub-sample timing.

## Test results

| Probe | Phase 18f | Phase 19 T1.5 | Phase 19 T7 |
|-------|-----------|---------------|-------------|
| HT_STRUCT_AUDIT | 0 | 128 | 896 |
| HTSIG_INPUT_DUMP | 0 | 128 | 128 |
| HT_SIG_PARSE_FAIL | 24 | 8 | 56 |
| FCS OK | 1 | 1 | 1 |
| All failures | n/a | crc_fail 100% | crc_fail 100% |

**No improvement on the 1 FCS OK baseline.**

## Test scripts and logs

- `/tmp/test_p19a_struct_audit.py` — Task 3: capture diagnostic
- `/tmp/test_p19c_const_loopback.py` — Task 6: loopback test driver
- `/tmp/test_p19g_fix_validation.py` — Task 7: fix validation
- `/tmp/p19a_struct_audit.log` — Task 3 output (402 MB)
- `/tmp/p19c_const_loopback.log` — Task 6 output
- `/tmp/p19g_fix_validation.log` — Task 7 output
- `/tmp/analyze_p19_struct_audit.py` — Task 4 analyzer
- `/tmp/analyze_p19_constellation.py` — Task 6 analyzer
- `/tmp/p19a_analysis.json` — Task 4 output
- `/tmp/p19_constellation_analysis.json` — Task 6 output

## Code changes (frame_equalizer_impl.cc)

1. `IEEE80211_HT_STRUCT_AUDIT` (commit f0faf7c) — env-gated structural failure audit at 7 sites in `decode_htsig_from_rotated` (the ACTIVE decoder)
2. `IEEE80211_HTSIG_INPUT_DUMP` (commit f0faf7c) — env-gated dump of 96-bit enc96 before viterbi call
3. `IEEE80211_HT_PER_SYMBOL_CPE=1` (commit 94c50e2) — env-gated per-symbol CPE fix. **Did not work**, kept as diagnostic-only knob.

## Commits

- `f61ef16` — diag(phase19-task1): original (in wrong function, not on active path)
- `b6c86ac` — diag(phase19-task2): HTSIG_INPUT_DUMP (also in wrong function)
- `f0faf7c` — diag(phase19-task1.5): moved diagnostics to active decoder
- `94c50e2` — fix(phase19-task7): per-symbol CPE fix (did not work)

## Why this matters

After 19 phases, **the 5 GHz A:0 chain still has FCS OK = 1** (no improvement from Phase 18's baseline). The HT-SIG viterbi path is the new bottleneck, with all 24+ parse failures being `crc_fail` (viterbi converges on garbage 48-bit codeword with valid viterbi-metric but invalid CRC).

The per-symbol CPE fix did not work, but the diagnostic infrastructure (HT_STRUCT_AUDIT, HTSIG_INPUT_DUMP) is now permanently in place for future investigation. The HT-SIG1-specific corruption (HT-SIG0 stable, HT-SIG1 varies) is a NEW finding that points to:
- Per-subcarrier phase tracking (per-SC CPE)
- Sub-sample timing offset between HT-SIG0 and HT-SIG1
- Equalization quality (Phase 3 root cause: L-LTF0 FFT per-frame std=12.7)

## Open questions / Phase 20+ direction

1. **Per-subcarrier phase tracking on HT-SIG1**: Try applying per-subcarrier phase rotation (not common phase) using HT-SIG0's pilots as reference. This was tried on L-SIG in Phase 10 Task 4 (reverted, high variance 7.9%→13.6%) but might be different for HT-SIG.
2. **Sub-sample timing offset**: Try applying a fractional-sample timing offset to HT-SIG1 specifically.
3. **Equalization improvement**: The original Phase 3 root cause (per-frame L-LTF0 FFT std=12.7) still affects the equalized symbols. Improving equalization quality would benefit both L-SIG and HT-SIG.
4. **Per-symbol CFO/SFO correction**: Apply CFO/SFO correction to HT-SIG1 specifically (HT-SIG1 is one OFDM symbol later than HT-SIG0).
5. **HT-SIG-specific decoder changes**: The 8 enc96 patterns suggest the decoder is trying 4 rotations × 2 inv_a × 2 inv_b = 16 candidates. Reduce to 4 rotations × 1 inv_a × 1 inv_b = 4 candidates if inv_b is also a clean polarity flip (similar to inv_a).

## Related memory

- [[project-p18-lsig-viterbi-analysis]] — Phase 18: L-SIG rate=0xD fix
- [[project-p17-5ghz-a0-subdev]] — Phase 17: 5 GHz A:0 subdev isolation
- [[project-p14-sync-long-deadlock]] — Phase 14: scheduler fix
- [[project-p10-finding-enc-mismatch]] — Phase 10: original enc!=0 finding
- [[project-p16-usrp-lo-leakage]] — Phase 16: 16-sample LO leakage
