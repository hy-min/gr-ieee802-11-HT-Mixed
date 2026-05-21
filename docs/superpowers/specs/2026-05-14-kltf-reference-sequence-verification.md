# Verify kLltf48TX Reference Sequence

**Date:** 2026-05-14
**Status:** Design Approved
**Branch:** fcs-backup-apply

## Problem Statement

L-SIG parity check fails with ~62.5% bit error rate. The THREE_STEP_TRACE reveals:
- RX = +162°, H = -78.6°, RX/H = -119° (should be ~0° or 180° for BPSK)

The user discovered that when H phase is subtracted, the result is actually PERFECT BPSK at SC=-18:
- LTF1: RX=+101.4° - H(-78.6°) = 180° ✓
- L-SIG: RX=-82.1° - H(-78.6°) = -3.5° ≈ 0° ✓

However, H phase varies wildly across subcarriers:
- SC7 (i=7, SC=-18): H phase = -78.6°
- SC14 (i=14, SC=-11): H phase = +113.9°
- SC21 (i=21, SC=-3): H phase = -32.2°

This non-linear H phase suggests a **Reference Sequence Bin Shift** - the kLltf48TX array may have incorrect indexing.

## Root Cause Hypothesis

When computing H = RX / txRef, if txRef[i] doesn't match the actual subcarrier's reference value (off by 1 index), H will have wrong phase/magnitude.

## Verification Design

Add debug probe in `estimate_header_channel_from_lltf52()` to print kLltf48TX values before channel estimation.

### Modified File

- `lib/frame_equalizer_impl.cc` — Add reference sequence probe in `estimate_header_channel_from_lltf52()`

### Implementation

Insert at line ~619 (before the `for (int i = 0; i < 48; i++)` loop):

```cpp
// DEBUG: Print kLltf48TX reference sequence for verification
// IEEE 802.11n标准 L-LTF 序列（部分）
fprintf(stderr, "\n[KLTX_REF_CHECK] kLltf48TX[i] for i=0..11:\n");
const char* expected_kltx = "+1,+1,-1,-1,+1,-1,+1,-1,+1,+1,+1,+1";  // 标准值
fprintf(stderr, "  Expected (IEEE 802.11n): %s\n", expected_kltx);
fprintf(stderr, "  Actual kLltf48TX:  ");
for (int i = 0; i < 12; i++) {
    fprintf(stderr, "%+.0f ", kLltf48TX[i].real());
}
fprintf(stderr, "\n");

// Also print kHeader48Sc[i] to show which SC each index corresponds to
fprintf(stderr, "  kHeader48Sc:     ");
for (int i = 0; i < 12; i++) {
    fprintf(stderr, "%+3d ", kHeader48Sc[i]);
}
fprintf(stderr, "\n");
fflush(stderr);
```

### Expected Output

```
[KLTX_REF_CHECK] kLltf48TX[i] for i=0..11:
  Expected (IEEE 802.11n): +1,+1,-1,-1,+1,-1,+1,-1,+1,+1,+1,+1
  Actual kLltf48TX:  +1 +1 -1 -1 +1 -1 +1 -1 +1 +1 +1 +1
  kHeader48Sc:      -26 -25 -24 -23 -22 -20 -19 -18 -17 -16 -15 -14
```

### Success Criteria

1. kLltf48TX values match IEEE 802.11n standard exactly
2. If mismatch found, fix kLltf48TX array in `lib/ieee80211_constants.h`
3. If match, investigate other causes (FFT window alignment, symbol ordering, etc.)

## Related Debug Output

The following existing debug traces will be used for correlation:
- `[THREE_STEP_TRACE]` - rx, H, rx/H phase at SC7
- `[H_PHASE_CHECK]` - H phase across multiple subcarriers
- `[CHAN_EST_FULL]` - All 52 H values from LTF0 vs LTF1
