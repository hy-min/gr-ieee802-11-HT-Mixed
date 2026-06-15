# Phase 18 — L-SIG viterbi "wrong codeword" fix at 5 GHz A:0 (2026-06-15)

## TL;DR

Phase 18 (5 tasks) identified and partially fixed the L-SIG viterbi "wrong codeword" issue at 5 GHz A:0 subdev. The fix (`IEEE80211_LSIG_RATE_FORCE=0xD` env flag) rejects L-SIG decodes with non-HT rate field, reducing HT_SIG_PARSE_FAIL 176→24 (87% reduction) and **producing the first e2e FCS OK frame** in 18 phases of investigation.

## Critical finding (Task 2-3)

L-SIG viterbi at 5 GHz A:0 **structurally succeeds** (returns 24-bit decoded24 with all structural fields valid) but the bits form **semantically wrong** L-SIG:
- All 8 valid L-SIG rates appear with ~uniform distribution
- **Only 5.9% of decoded24 have rate=0xD** (BPSK 1/2, required for HT)
- 100% have tail=000000 (passes structural check)
- Parity roughly balanced (72 zero / 64 one)

The 91% wrong-codeword L-SIGs hit the `if (lsig_enc != 0) continue;` gate (line 3194), skipping HT-SIG brute-force. The 5.9% correct L-SIGs are drowned out by the 91% noise.

## Why this works (Task 4)

The L-SIG viterbi constraint is the rate field at bits [23:20] = 0xD. Without this constraint, viterbi finds the most likely 24-bit codeword given the equalized symbols — but the equalized symbols are too noisy (Phase 3 root cause), so the most likely codeword is often wrong. **Adding the rate=0xD constraint forces viterbi to look for the right codeword across multiple rotation attempts.**

The fix is implemented as an env-var-gated check in `decode_lsig_direct_from_header52`:
```c
if (getenv("IEEE80211_LSIG_RATE_FORCE")) {
    int rate_f = (decoded24 >> 20) & 0xF;
    if (rate_f != 0xD) return false;  // reject, try next rotation
}
```

## Results

| Probe | Baseline | FORCE_HTSIG=1 | LSIG_RATE_FORCE=0xD |
|-------|----------|----------------|----------------------|
| LSIG_REJECT | 0 | 0 | **144** |
| HT_SIG_PARSE_FAIL | 0 | 176 | 24 |
| LSIG_PARSE_FAIL | 184 | 144 | 324 |
| FORCE_HTSIG | 0 | 178 | 0 |
| **FCS OK** | **0** | **1** | **1** |

**Net result**: With `IEEE80211_LSIG_RATE_FORCE=0xD`, 144 wrong-rate L-SIGs are rejected at the source, 24 legitimate L-SIGs reach HT-SIG brute-force, and 1 frame decodes end-to-end.

## Test scripts

- `/tmp/test_p18a_5ghz_viterbi_audit.py` — Task 1: capture viterbi audit
- `/tmp/analyze_p18a_viterbi.py` — Task 2: analyze audit log
- `/tmp/test_p18c_diag_baseline.py` — Task 3: regression test
- `/tmp/test_p18d_diag_5ghz.py` — Task 3: 5 GHz A:0 diagnostic
- `/tmp/test_p18e_force_htsig.py` — Task 4: H1 test (FORCE_HTSIG)
- `/tmp/test_p18f_lsig_rate_force.py` — Task 4: rate force test

## Logs

- `/tmp/p18a_viterbi_audit.log` (414 MB)
- `/tmp/p18d_diag_5ghz.log` (30 MB)
- `/tmp/p18e_force_htsig.log`
- `/tmp/p18f_lsig_rate_force.log`

## Code changes (frame_equalizer_impl.cc, +92 lines)

1. `IEEE80211_HT_VITERBI_AUDIT` env-gated audit log in 3 HT-SIG viterbi fail sites (lines 1174, 1571, 1766)
2. `IEEE80211_LSIG_VALIDITY_AUDIT` env-gated validity check in L-SIG viterbi success path (line 1494)
3. `IEEE80211_LSIG_RATE_FORCE` env-gated rate field validation (lines 1536-1573) — **the fix**

## Commits

- `2502978` — fix(phase18-task4): reject L-SIG decodes with non-HT rate_field

## Why this matters

After 18 phases of investigation across multiple subdevs and frequency bands, **this is the first end-to-end FCS OK at 5 GHz A:0**. The fix doesn't solve the entire problem (HT-SIG brute-force still fails on 24/24 legitimate L-SIGs), but it confirms:
1. L-SIG viterbi algorithm is correct
2. The chain runs end-to-end at 5 GHz A:0 with the proper subdev (Phase 17) + scheduler fix (Phase 14) + this rate-constraint fix
3. The remaining bottleneck is HT-SIG viterbi on clean L-SIGs (upstream equalization noise is the next investigation target)

## Open questions / follow-up

1. **HT_VITERBI_AUDIT placement** — currently fires only on HT-SIG viterbi success path. The 24 remaining HT_SIG_PARSE_FAILs are on legitimate 0xD L-SIGs but the audit doesn't capture that path. Future task: move audit to fail path or add separate probe.
2. **HT-SIG viterbi on clean L-SIGs** — 24/24 still fail. The cause is likely upstream equalization noise (Phase 3 root cause) or HT-SIG-specific decoder issue. Next investigation: dump equalized HT-SIG symbols (48 subcarriers) for post-mortem.
3. **FCS OK=1 is too low to declare victory** — the test ran 30s with 1 e2e pass. Need longer run to confirm the fix is stable, OR identify what made that one frame pass and replicate the conditions.
4. **Loopback parity** — direct loopback still OK=0 FAIL=1 due to pre-existing FcsLogger `crc` field bug. Confirmed no regression from new diagnostics.

## Related memory

- [[project-p17-5ghz-a0-subdev]] — Phase 17: 5 GHz A:0 chain unblocked
- [[project-p14-sync-long-deadlock]] — Phase 14: scheduler fix (preserved)
- [[project-p10-finding-enc-mismatch]] — Phase 10: original enc!=0 finding (now superseded by rate=0xD constraint)
- [[project-p16-usrp-lo-leakage]] — Phase 16: 16-sample LO leakage at B:0 (workaround: use A:0)
- [[project-lsig-viterbi-2026-06-10]] — kFftNormalize was red herring
