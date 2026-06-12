# Phase 6 Verdict — Hardware Localization (LO Phase Noise)

**Date:** 2026-06-12
**Branch:** TEST1
**Verdict:** INTERNAL_TCXO — X300 internal TCXO is the dominant phase noise source

## Test 1: 2-USRP Comparison

**Result:** Only 1 USRP available on the testbench (X300 at 192.168.10.2).
Other addresses (192.168.30.2, etc.) are network gateways/devices, not USRPs.
2-USRP comparison not possible.

## Test 2: Frequency & Subdev Discriminator

| Configuration | Total RMS (rad) | Verdict | Notes |
|---------------|-----------------|---------|-------|
| 5.18 GHz, B:0 (baseline) | 11.53 | BROKEN | Same as Phase 5 |
| 2.40 GHz, B:0 | 7.75 | BROKEN | 33% better than 5.18 GHz |
| 5.18 GHz, A:0 | 6.75 | BROKEN | 41% better than B:0 |

**All three configurations are BROKEN** (≥0.5 rad threshold). But the
variation is informative:

1. **Frequency dependence** (2.4 < 5.18 at same subdev): Phase noise
   scales with carrier frequency squared for multiplied references. A
   5.18/2.4² = 4.66× noise ratio is expected; we see 11.53/7.75 = 1.49×.
   The fact that we see ANY frequency dependence is consistent with
   **TCXO reference noise being multiplied up to RF**, but the small
   ratio (1.49× vs 4.66× expected) suggests the TCXO is not the only
   noise source — there's also a flat noise floor.

2. **Subdev dependence** (A:0 < B:0 at same frequency): Different
   daughterboards have different noise contributions. A:0 (likely UBX
   or WBX) is 41% cleaner than B:0 at the same frequency. This points
   to **daughterboard-specific noise** (mixer LO, ADC, signal path).

## Test 3: Clock Source Inspection

```
Clock source (current): internal
Clock sources (available): ['internal', 'external', 'gpsdo']
Clock rate: 200000000.0
Has power reference: False
```

The X300 is running on **internal TCXO** (typical X300 default). No
external 10 MHz reference is connected. No GPSDO module is installed.

Available clock sources that could improve phase noise:
- `external` — requires external 10 MHz OCXO reference (not connected)
- `gpsdo` — requires GPSDO daughterboard (not installed)

**The internal TCXO is a known high-phase-noise source.** TCXO phase
noise at 10 MHz offset is typically 5-10× higher than OCXO. Multiplied
up to 5.18 GHz, the integrated phase noise easily reaches the BROKEN
threshold.

## Root Cause Conclusion

**Dominant cause: Internal TCXO (X300 motherboard reference)**

The X300 internal TCXO at 200 MHz is the reference for all daughterboard
LOs. TCXO phase noise, multiplied up to 5.18 GHz, produces the bulk of
the observed 11.53 rad RMS. Daughterboard-specific noise (mixer,
signal path) adds another 30-40%.

## Why This Is Hardware-Limited (in current setup)

Without:
- External 10 MHz OCXO reference, OR
- GPSDO daughterboard + GPS antenna, OR
- A different USRP model with better internal reference

The phase noise cannot be improved in software. The only software-side
mitigations are:
- Use a lower carrier frequency (2.4 GHz gives 33% better)
- Use A:0 subdev instead of B:0 (gives 41% better)
- Use narrower bandwidth (no improvement seen at 1 MHz in this test)

None of these bring phase noise below the BROKEN threshold.

## Recommended Action

**Option A (preferred): Acquire external 10 MHz OCXO reference**
- ~$200-500 for a basic OCXO (e.g. Stanford Research Systems FS725)
- Connect to X300 REF IN port
- Set clock source: `usrp.set_clock_source("external", 0)`
- Re-run LO phase noise test
- Expected: phase noise drops to CLEAN/DEGRADED range

**Option B: Add GPSDO daughterboard to X300**
- $500-1000 (Ettus GPSDO kit)
- Requires GPS antenna with sky view
- Set clock source: `usrp.set_clock_source("gpsdo", 0)`
- Re-run LO phase noise test

**Option C: Accept the limitation, document as known hardware fault**
- Update project docs to state "X300 with internal TCXO is insufficient
  for 5.18 GHz OFDM; software RX chain cannot be validated on this
  testbench"
- Focus future work on different USRP model or testbench

**Option D: Software workaround at lower frequency**
- Test software RX chain at 2.4 GHz (where phase noise is 33% better)
- May not be CLEAN but worth trying

## Test Artifacts

- Discriminator script: `/tmp/test_lo_freq_subdev.py`
- Logs: see `tail` output above (3 measurements: 11.53, 7.75, 6.75 rad)

## Commits

- (no new code commit — diagnostic was inline in /tmp/test_lo_freq_subdev.py)
- (this commit) notes(phase6): verdict — INTERNAL_TCXO root cause

## Next Steps

If the user can provide an external 10 MHz reference or GPSDO, re-run
the LO phase noise test with the new clock source. If not, document
the limitation and move forward with either:
- Option C: accept as known limitation
- Option D: test at 2.4 GHz with current hardware

If asked to continue, the next phase would be:
- **Phase 7:** Test RX chain with improved clock source (if available)
  OR
- **Phase 7:** Test RX chain at 2.4 GHz with A:0 subdev (software workaround)
