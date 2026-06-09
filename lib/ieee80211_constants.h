/*
 * Copyright (C) 2013 ...
 * (same as upstream)
 *
 * IEEE 802.11n HT-Mixed mode constants for channel estimation.
 *
 * These constants provide unified TX reference signals used by both
 * the frame equalizer and LS channel estimator to compute channel
 * estimates: H = RX / kLltf48TX / kFftNormalize
 *
 * Using the same TX reference in both places ensures consistent H magnitude.
 */

#ifndef INCLUDED_IEEE802_11_CONSTANTS_H
#define INCLUDED_IEEE802_11_CONSTANTS_H

#include <gnuradio/gr_complex.h>
#include <cmath>

namespace gr {
namespace ieee802_11 {

//! FFT normalization factor: 64-bin FFT output scaled to 52 used subcarriers
static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);

//! L-LTF TX BPSK ±1 reference in kHeader48Sc order (subcarriers -26..-1, +1..+26)
//! This is the theoretical TX signal before RF/FFT processing.
static constexpr gr_complex kLltf48TX[48] = {
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc -26 to -20
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -19 to -14
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -13 to  -8
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc  -6 to  -1
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +1 to  +6
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +8 to +13
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +14 to +19
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +20 to +26
};

//! L-LTF TX reference mapped to 64-bin FFT order (bins 0..63).
//! Derived from LEGACY_LTF (mixed_mode_carrier_allocator.py).
//! RX FFT output (shift=True): bins 1-26 = SC +1..+26, bins 38-63 = SC -26..-1.
static constexpr gr_complex kLltf64Binned[64] = {
    gr_complex(+0.0f, 0.0f),   // bin  0: DC
    gr_complex(+1.0f, 0.0f),   // bin  1: SC +1
    gr_complex(-1.0f, 0.0f),   // bin  2: SC +2
    gr_complex(-1.0f, 0.0f),   // bin  3: SC +3
    gr_complex(+1.0f, 0.0f),   // bin  4: SC +4
    gr_complex(+1.0f, 0.0f),   // bin  5: SC +5
    gr_complex(-1.0f, 0.0f),   // bin  6: SC +6
    gr_complex(+1.0f, 0.0f),   // bin  7: SC +7
    gr_complex(-1.0f, 0.0f),   // bin  8: SC +8
    gr_complex(+1.0f, 0.0f),   // bin  9: SC +9
    gr_complex(-1.0f, 0.0f),   // bin 10: SC +10
    gr_complex(-1.0f, 0.0f),   // bin 11: SC +11
    gr_complex(-1.0f, 0.0f),   // bin 12: SC +12
    gr_complex(-1.0f, 0.0f),   // bin 13: SC +13
    gr_complex(-1.0f, 0.0f),   // bin 14: SC +14
    gr_complex(+1.0f, 0.0f),   // bin 15: SC +15
    gr_complex(+1.0f, 0.0f),   // bin 16: SC +16
    gr_complex(-1.0f, 0.0f),   // bin 17: SC +17
    gr_complex(-1.0f, 0.0f),   // bin 18: SC +18
    gr_complex(+1.0f, 0.0f),   // bin 19: SC +19
    gr_complex(-1.0f, 0.0f),   // bin 20: SC +20
    gr_complex(+1.0f, 0.0f),   // bin 21: SC +21
    gr_complex(-1.0f, 0.0f),   // bin 22: SC +22
    gr_complex(+1.0f, 0.0f),   // bin 23: SC +23
    gr_complex(+1.0f, 0.0f),   // bin 24: SC +24
    gr_complex(+1.0f, 0.0f),   // bin 25: SC +25
    gr_complex(+1.0f, 0.0f),   // bin 26: SC +26
    gr_complex(+0.0f, 0.0f),   // bin 27: guard
    gr_complex(+0.0f, 0.0f),   // bin 28: guard
    gr_complex(+0.0f, 0.0f),   // bin 29: guard
    gr_complex(+0.0f, 0.0f),   // bin 30: guard
    gr_complex(+0.0f, 0.0f),   // bin 31: guard
    gr_complex(+0.0f, 0.0f),   // bin 32: DC
    gr_complex(+0.0f, 0.0f),   // bin 33: guard
    gr_complex(+0.0f, 0.0f),   // bin 34: guard
    gr_complex(+0.0f, 0.0f),   // bin 35: guard
    gr_complex(+0.0f, 0.0f),   // bin 36: guard
    gr_complex(+0.0f, 0.0f),   // bin 37: guard
    gr_complex(+1.0f, 0.0f),   // bin 38: SC -26
    gr_complex(+1.0f, 0.0f),   // bin 39: SC -25
    gr_complex(-1.0f, 0.0f),   // bin 40: SC -24
    gr_complex(-1.0f, 0.0f),   // bin 41: SC -23
    gr_complex(+1.0f, 0.0f),   // bin 42: SC -22
    gr_complex(+1.0f, 0.0f),   // bin 43: SC -21
    gr_complex(-1.0f, 0.0f),   // bin 44: SC -20
    gr_complex(+1.0f, 0.0f),   // bin 45: SC -19
    gr_complex(-1.0f, 0.0f),   // bin 46: SC -18
    gr_complex(+1.0f, 0.0f),   // bin 47: SC -17
    gr_complex(+1.0f, 0.0f),   // bin 48: SC -16
    gr_complex(+1.0f, 0.0f),   // bin 49: SC -15
    gr_complex(+1.0f, 0.0f),   // bin 50: SC -14
    gr_complex(+1.0f, 0.0f),   // bin 51: SC -13
    gr_complex(+1.0f, 0.0f),   // bin 52: SC -12
    gr_complex(-1.0f, 0.0f),   // bin 53: SC -11
    gr_complex(-1.0f, 0.0f),   // bin 54: SC -10
    gr_complex(+1.0f, 0.0f),   // bin 55: SC -9
    gr_complex(+1.0f, 0.0f),   // bin 56: SC -8
    gr_complex(-1.0f, 0.0f),   // bin 57: SC -7
    gr_complex(+1.0f, 0.0f),   // bin 58: SC -6
    gr_complex(-1.0f, 0.0f),   // bin 59: SC -5
    gr_complex(+1.0f, 0.0f),   // bin 60: SC -4
    gr_complex(+1.0f, 0.0f),   // bin 61: SC -3
    gr_complex(+1.0f, 0.0f),   // bin 62: SC -2
    gr_complex(+1.0f, 0.0f),   // bin 63: SC -1
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_CONSTANTS_H */
