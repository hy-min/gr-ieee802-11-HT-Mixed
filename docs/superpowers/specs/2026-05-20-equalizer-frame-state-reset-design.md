# Equalizer Frame State Reset — Fix HT-SIG Timeout After Takeover

## Problem

When `frame_equalizer` processes multiple back-to-back HT-Mixed frames, a takeover (new `wifi_start` arriving while the previous frame is still in progress) leaves the **equalizer's internal channel estimate `d_H[64]` untouched**. The next frame's L-LTF0 gets equalized using the *previous* frame's `d_H`, producing a corrupted channel estimate. This cascades into:

- HT-SIG0 QBPSK detection failing (`ratio_ht` drops to ~1.4 instead of 1000+)
- HT-SIG CRC decode failure
- Frame being mis-classified as Legacy or timing out
- Multi-frame reception rate stuck at 1/10

The issue is most visible on Frame 2 (immediate takeover after Frame 1's data) and Frames 4–9 (subsequent immediate starts).

## Root Cause

`reset_frame_state()` in `frame_equalizer_impl.cc` resets all frame-level state (`d_sym_idx`, `d_internal_symbol_counter`, `d_early_eqsym`, etc.) but **never resets `d_equalizer->d_H`**. The LS/Comb/LMS/STA equalizers all store `d_H[64]` as a member of `equalizer::base`, and it survives across frames.

## Design

### 1. Add `reset()` to equalizer base class

**File:** `lib/equalizer/base.h`

Add a virtual `reset()` method to the `equalizer::base` interface:

```cpp
class base
{
public:
    virtual ~base(){};
    virtual void equalize(gr_complex* in, int n, gr_complex* symbols,
                          uint8_t* bits, std::shared_ptr<gr::digital::constellation> mod) = 0;
    virtual double get_snr() = 0;
    virtual void reset() {}  // default no-op
    // ...
};
```

### 2. Implement `reset()` in each equalizer

**Files:** `lib/equalizer/ls.cc`, `lib/equalizer/comb.cc`, `lib/equalizer/lms.cc`, `lib/equalizer/sta.cc`

Each implementation clears its `d_H` array (and any related state like SNR):

```cpp
// ls.cc
void ls::reset() {
    std::memset(d_H, 0, sizeof(d_H));
    d_snr = 0.0;
}
```

```cpp
// comb.cc
void comb::reset() {
    std::memset(d_H, 0, sizeof(d_H));
}
```

```cpp
// lms.cc
void lms::reset() {
    std::memset(d_H, 0, sizeof(d_H));
    // clear any LMS-specific state if present
}
```

```cpp
// sta.cc
void sta::reset() {
    std::memset(d_H, 0, sizeof(d_H));
}
```

### 3. Call `d_equalizer->reset()` in `reset_frame_state()`

**File:** `lib/frame_equalizer_impl.cc`

Append one line to the existing `reset_frame_state()`:

```cpp
void frame_equalizer_impl::reset_frame_state(void)
{
    d_frame_bytes = 0;
    d_frame_encoding = 0;
    d_frame_symbols = 0;
    d_frame_mod = 1;
    d_frame_n_bpsc = 1;
    d_frame_n_cbps = 52;
    d_frame_n_dbps = 26;

    d_have_header = false;
    d_have_ht_header = false;
    d_is_ht = false;
    d_sym_idx = 0;
    d_takeover_reject_symbols = 0;
    d_internal_symbol_counter = 0;
    d_first_valid_symbol = -1;

    d_chan_est_mode = 0;
    d_have_lsig = false;
    d_lsig_rel = -1;
    d_hdr_reorder_mode = 0;
    d_hdr_inverted = false;
    d_htsig0_rel = -1;
    d_htsig1_rel = -1;
    d_data_start_rel = kDataStartRel;

    std::memset(d_early_bits, 0, sizeof(d_early_bits));
    std::memset(d_early_bits_valid, 0, sizeof(d_early_bits_valid));
    std::memset(d_early_eqsym, 0, sizeof(d_early_eqsym));
    std::memset(d_early_eqsym_valid, 0, sizeof(d_early_eqsym_valid));

    g_extract_call_count = 0;
    ltf0_ever_saved = false;
    ltf0_saved = false;

    // NEW: prevent previous frame's H estimate from polluting the next frame
    d_equalizer->reset();
}
```

## Verification

1. **Build** the project and copy the shared library to the conda env.
2. **Run** `python test_mcs_end_to_end.py`.
3. **Check** the following log indicators:
   - Frame 2 (and subsequent frames) n=0 `d_H` values should be in the same magnitude/phase ballpark as Frame 1/3 (e.g. ~0.8±0.5i for subcarrier 6, not wildly different like -0.07+1.04i).
   - `[HTSIG_DECODE] crc=PASS` should appear for more than just Frame 1.
   - Multi-frame reception rate should improve from ~1/10.

## Scope & Non-Goals

- **In scope:** Equalizer state reset on frame boundary.
- **Not in scope:** SPLITTER symbol-alignment fixes, takeover logic restructuring, or decode_mac changes. Those are tracked separately if needed.
