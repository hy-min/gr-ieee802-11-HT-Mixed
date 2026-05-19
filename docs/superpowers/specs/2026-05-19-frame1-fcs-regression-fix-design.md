# Frame 1 FCS Regression Fix Design

## Date
2026-05-19

## Background

After the STARVE boundary fix and multi-frame frame1 correction, Frame 1 FCS was previously passing (2/10 frames in 10s test). A subsequent uncommitted change to the equalizer's takeover condition introduced a regression: Frame 1 is now cut short at 7/13 data symbols, decode_mac never receives enough data to verify FCS, and the 2s test shows 0/2 received messages.

## Problem Statement

Frame 1 FCS fails because:

1. **Equalizer takeover too aggressive**: `d_sym_idx >= d_data_start_rel` allows Frame 2 to takeover as soon as Frame 1 emits its first data symbol. Frame 1 is truncated to ~7/13 symbols, insufficient for MAC header + payload + FCS.
2. **SPLITTER multi-frame tag misalignment**: `d_frame_start_abs = tag_abs_pos + 176` hardcodes Frame 1's offset for all frames, causing Frame 2's wifi_start tag to arrive 1-2 FFTs too early in the equalizer.

## Design

### Approach

Two-pronged fix:

1. **Equalizer safety boundary**: Delay takeover until Frame 1 is near completion (`d_sym_idx >= end_rel - 1`). This guarantees Frame 1 emits enough symbols for FCS regardless of SPLITTER tag timing.
2. **SPLITTER root cause**: Restore per-frame `d_frame_start_abs` computation using the frame-specific `d_frame_start` and `sync_offset`.

### Equalizer Fix

**File**: `lib/frame_equalizer_impl.cc`

**Change**: Modify takeover condition in `general_work()`.

```cpp
// OLD (regression):
} else if (d_sym_idx >= d_data_start_rel) {
    allow_takeover = true;
}

// NEW (safety boundary):
} else {
    const int end_rel = d_data_start_rel + d_frame_symbols - 1;
    if (d_sym_idx >= end_rel - 1) {  // Allow takeover 1 symbol before end
        allow_takeover = true;
    }
}
```

**Rationale**:
- `d_frame_symbols` is known after HT-SIG decode (which happens at `d_sym_idx < d_data_start_rel`).
- By the time we reach the data phase, `d_frame_symbols` is always valid.
- `margin = 1` means Frame 1 emits `d_frame_symbols - 1` data symbols. For a 10-byte payload at BPSK 1/2, this is 12/13 symbols, sufficient for MAC header (24B) + payload (10B) + FCS (4B) = 38B = 304 bits, which requires ~12 symbols.
- If `d_frame_symbols` is somehow unknown, takeover is denied, preventing corruption.

### SPLITTER Fix

**File**: `lib/ht_symbol_splitter_impl.cc`

**Change**: In the multi-frame `wifi_start` handler, replace hardcoded `+176` with per-frame computation.

```cpp
// OLD:
d_frame_start_abs = (int64_t)tag_abs_pos + 176;

// NEW:
// sync_for_this_wifi_start is already looked up above
d_frame_start_abs = (int64_t)tag_abs_pos + d_frame_start + 16 - sync_for_this_wifi_start;
```

**Rationale**:
- `d_frame_start` is the wifi_start tag value from sync_long for THIS frame.
- `sync_for_this_wifi_start` is the sync_offset recorded when sync_long placed the tag.
- `+16` accounts for the CP length that SPLITTER discards before L-LTF0 DATA.
- For Frame 1: `0 + 160 + 16 - 0 = 176` (same as before).
- For Frame 2: `1664 + 267 + 16 - 0 = 1947` (was 1840, a 107-sample offset that caused ~1.7 FFT early arrival).

### Fallback Strategy

If the SPLITTER formula fix causes other issues in multi-frame testing, the equalizer's `end_rel - 1` condition alone ensures:
- Single-frame: Frame 1 completes fully, FCS passes.
- Multi-frame: Frame 1 completes fully; Frame 2 is delayed until Frame 1 ends. Throughput may be slightly lower but correctness is guaranteed.

## Verification Plan

1. **2s MCS0 test**: Expect 2/2 received messages (was 0/2).
2. **10s test**: Expect Frame 1 emit 12/13+ data symbols, FCS pass.
3. **Log check**: `[EQ_FRAME_TAKEOVER]` must trigger at `d_sym_idx >= end_rel - 1`, not in early data phase.
4. **SPLITTER log**: `[SPLITTER_TAG_DEFERRED]` should show Frame 2 tag at a later equalizer offset (~25+ vs current 18).

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| SPLITTER formula causes late tag, Frame 2 preamble missed | Equalizer safety boundary alone handles this; Frame 2 simply starts after Frame 1 ends |
| `d_frame_symbols` unknown at takeover decision | Impossible path: HT-SIG decode sets `d_frame_symbols` before data phase; if HT-SIG fails, `d_have_ht_header` is false and takeover is allowed anyway |
| margin=1 still truncates frames with minimum-length payload | 10B payload at MCS0 needs 12 symbols; margin=1 gives 12/13. Even 1B payload needs ~10 symbols, still sufficient. Frame must have at least MAC header (24B) + FCS (4B) = 28B = 224 bits = ~9 symbols at BPSK 1/2. With d_frame_symbols typically >= 9, margin=1 is safe. |
