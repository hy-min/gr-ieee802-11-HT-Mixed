# L-SIG Parity Check Failure - Debug Findings

**Date:** 2026-05-11
**Branch:** ht-mixed-mode-fcs-fix

## Executive Summary

L-SIG parity check fails ~100% of the time despite HT-SIG boundaries being correct (verified in previous session). The issue is in the channel estimation / FFT positioning chain, NOT in the decoder chain.

## Debugging Session Summary

### What Was Verified as Working
- [x] HT-SIG boundary positions (fix committed in d4d737b)
- [x] Deinterleaver formula `j = 16*(k%3) + k//3`
- [x] CPE (Common Phase Error) correction application
- [x] Viterbi decoder itself

### What Was Found Broken

#### 1. FFT Output Magnitude (Task 1)
**Finding:** L-SIG FFT samples show significant imaginary components (~1.0)

```
bin[0]=1.593230-0.616860i
bin[1]=0.946932-1.028167i
bin[2]=-0.718339-0.004634i
```

**Significance:** For BPSK, points should be on real axis only

#### 2. Channel Estimation Magnitude (Task 2)
**Finding:** Channel estimate d_H has magnitude ~8.3 instead of ~1.0

```
n=0: d_H[6-10] = magnitudes ranging from 6.9 to 10.9
Average RX magnitude: 8.3177
Average EQ magnitude: 1.0795
```

**Root Cause:** FFT/IFFT scaling mismatch
- TX IFFT window: 1/sqrt(52) ≈ 0.125
- RX FFT window: None (rectangular = all 1s)
- FFT gain: N = 64
- Effective ratio: 64/sqrt(52) ≈ 8.88

#### 3. Equalized Symbols Still Wrong (Task 3)
**Finding:** Even after equalization, L-SIG symbols have imaginary components

```
SC0: eq=-1.515+0.780i (should be ~-1+0j for BPSK)
SC1: eq=0.712-0.954i (should be ~+1+0j for BPSK)
```

#### 4. Viterbi Input Completely Wrong (Task 6)
**Finding:** Viterbi input bits are COMPLETELY different from TX

| Signal | Bits (first 12) |
|--------|-----------------|
| TX L-SIG intl48 | `110000111110` |
| RX Viterbi input | `000110101000` |

Not just inverted - completely different bit patterns.

## Root Cause Analysis

The Viterbi input being completely wrong indicates the issue occurs **BEFORE** the decoder chain:

1. **FFT output positioning** - SPLITTER outputs at correct positions (verified)
2. **Channel estimation** - d_H magnitude is 8x too large (FFT/IFFT scaling issue)
3. **Equalization** - Even with correct EQ magnitude, phase is wrong

The channel estimation formula `H = RX / TX` doesn't account for FFT/IFFT scaling factors. This causes the equalized symbols to have wrong phase, which cascades into wrong bits.

## Proposed Fix

**Option A: Fix Channel Estimation Normalization**
In `frame_equalizer_impl.cc`, normalize the channel estimate by dividing by the FFT gain factor (~8.88) or multiply TX reference by same factor.

**Option B: Fix FFT Window Scaling**
Ensure TX IFFT and RX FFT have consistent scaling (e.g., both use 1/N normalization).

## Next Steps

1. Implement Option A or B
2. Rebuild and test
3. Verify d_H magnitude is ~1.0
4. Verify L-SIG parity check passes

## Related Commits

- d4d737b - fix: Correct HT-SIG boundary positions in ht_symbol_splitter
- 5f0ce3c - debug: Add L-SIG FFT sample dump in SPLITTER
- b459a12 - debug: Verify LTF channel estimation
- 42a80c7 - debug: Add L-SIG raw subcarrier dump
- 2978d11 - debug: Add deinterleaver verification script
- 0248976 - debug: Add Viterbi input dump for L-SIG decode