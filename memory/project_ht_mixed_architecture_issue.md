# HT Mixed Mode - Architectural Issue: FFT Block vs HT-SIG Symbol Mismatch

> Updated: 2026-04-06

## Root Cause Identified

**HT-Mixed HT-SIG symbols are 80 samples (16 CP + 64 data), but FFT blocks are 64 samples. This architectural mismatch causes HT-SIG to span 2 FFT blocks per symbol.**

### HT-Mixed Preamble Structure (20MHz)

According to 802.11n and the code's comments:
```
L-LTF:  d_offset 160-319 (160 samples, CP + 2×64 data)
L-SIG:  d_offset 320-399 (80 samples, 16 CP + 64 data)
HT-SIG: d_offset 400-559 (160 samples, 2×(16 CP + 64 data))
HT-STF: d_offset 560-639 (80 samples)
HT-LTF: d_offset 640-799+ (160+ samples)
```

### The FFT Alignment Problem

With d_frame_start=176 and wifi_start at output 192 (64-byte aligned):

**FFT mapping:**
- FFT[0]: output 0-63, d_offset 192-255 (L-LTF data)
- FFT[1]: output 64-127, d_offset 256-319 (L-LTF data / L-SIG)
- FFT[2]: output 128-191, d_offset 320-383 (L-SIG / HT-SIG1 CP)
- FFT[3]: output 192-255, d_offset 384-447 (HT-SIG1 data / HT-SIG2)

**HT-SIG1 data (d_offset 400-447)** is split:
- HT-SIG1 data tail (d_offset 400-431, 32 samples) → FFT[2] tail
- HT-SIG1 data head (d_offset 432-447, 16 samples) → FFT[3] head

**HT-SIG2 data (d_offset 480-527)** similarly spans FFT[3] and FFT[4].

### Current Code Assumes Single FFT Block Per Symbol

The frame_equalizer extraction code assumes:
- HT-SIG0 at FFT output 3
- HT-SIG1 at FFT output 4

But HT-SIG symbols span 2 FFT blocks, so this mapping is incorrect.

### Evidence

EXTRACT_HT_SIG output shows:
```
rel=3 (HT-SIG0): 0.000+0.000i 0.000+0.000i 0.000+0.000i 0.000+0.000i
```

This confirms HT-SIG0 extraction fails because the FFT output doesn't contain HT-SIG0 data at the expected position.

## Possible Solutions

1. **Modify frame_equalizer to reconstruct HT-SIG from multiple FFT outputs**
   - Requires buffering and combining HT-SIG fragments
   - Complex but maintains compatibility

2. **Use 128-sample FFT instead of 64-sample**
   - Would require significant changes to fft_vxx block usage
   - Not practical for quick fix

3. **Accept HT-Mixed limitation in current implementation**
   - Document that HT-Mixed mode has known issues with this architecture

## Related Files

| File | Issue |
|------|-------|
| `lib/frame_equalizer_impl.cc` | Assumes single FFT block per HT-SIG symbol |
| `lib/sync_long.cc` | wifi_start alignment doesn't account for 80-sample HT-SIG |

## Status

**CONFIRMED**: Architectural mismatch between FFT block size (64) and HT-Mixed symbol size (80) causes HT-SIG decoding to fail.

This is NOT a simple bug - it requires architectural changes to properly support HT-Mixed mode with 64-sample FFT blocks.
