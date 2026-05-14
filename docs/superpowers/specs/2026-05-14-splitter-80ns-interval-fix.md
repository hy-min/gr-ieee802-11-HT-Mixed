# SPLITTER 80ns Interval Fix - Alignment of Preamble Symbols

**Date:** 2026-05-14
**Status:** Design Approved
**Branch:** fcs-backup-apply

## Problem Statement

L-SIG parity check fails with ~62.5% bit error rate. Previous debugging found:
- H phase shows linear slope of ~28.125°/bin across subcarriers
- This corresponds to a **5-sample absolute timing offset** from USRP hardware or sync_long
- But the **relative interval** between preamble symbols was BROKEN

### Root Cause Analysis

**Absolute Offset (5 samples):** The ~28.125°/bin phase slope corresponds to 5 samples of delay from USRP hardware or sync_long. This is OK - it affects all symbols equally.

**Relative Offset (OFF-BY-ONE ERROR):** The critical issue is that symbol intervals were NOT 80 samples:
- LTF1 → L-SIG interval was **79** (not 80)
- This 1-sample relative error causes L-SIG and L-LTF to have DIFFERENT phase slopes after equalization
- When rx / H is computed, the residual slope destroys BPSK decoding

### Key Discovery

The user's mathematical analysis proved:
1. H phase is NOT random - it's a perfect linear slope (~28.125°/bin)
2. The slope = 5 sample timing offset (Δt = 28.125/360 * 64 ≈ 5)
3. If all symbols share the same 5-sample offset, rx/H will cancel perfectly
4. But the 1-sample interval error breaks the cancellation

## Solution Design

**Principle:** "Absolute offset doesn't matter, relative interval is everything"

Fix SPLITTER boundaries to ensure ALL preamble symbols are separated by exactly 80 samples.

### Symbol Boundaries (Fixed)

| Symbol | DATA Start | DATA End | C++ Boundary | Interval |
|--------|------------|----------|-------------|----------|
| L-LTF0 | 0 | 63 | `rel_idx < 64` | (base) |
| L-LTF1 | 80 | 143 | `rel_idx < 144` | 80 ✓ |
| L-SIG | 160 | 223 | `rel_idx < 224` | 80 ✓ |
| HT-SIG0 | 240 | 303 | `rel_idx < 304` | 80 ✓ |
| HT-SIG1 | 320 | 383 | `rel_idx < 384` | 80 ✓ |

### The Off-by-One Error

**Previous (BROKEN):**
```cpp
} else if (rel_idx < 224) {
    should_buffer = (rel_idx >= 159);  // BUG: includes rel_idx=159 (CP sample)
    // This creates 65-point window: 159-223 = 65 samples!
```

**Fixed:**
```cpp
} else if (rel_idx < 224) {
    should_buffer = (rel_idx >= 160);  // FIXED: starts at 160, 64 samples
```

## Files Modified

- `lib/ht_symbol_splitter_impl.cc:405` — Fix L-SIG boundary condition

## Implementation

Change line 405 from:
```cpp
should_buffer = (rel_idx >= 159);
```

To:
```cpp
should_buffer = (rel_idx >= 160);
```

## Expected Result

After fix:
- All preamble symbols will have strictly 80-sample intervals
- H phase slope will be consistent across all symbols
- rx / H will cancel the phase slope perfectly
- L-SIG parity check should pass
- HT-SIG should decode correctly
