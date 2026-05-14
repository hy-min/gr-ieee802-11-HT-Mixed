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
//! Guard bands (0-5, 59-63), DC (32), and pilots (bins 11, 25, 39, 53) are 0.
static constexpr gr_complex kLltf64Binned[64] = {
    // bin  0-5: 保护带 -> 0
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    // bin  6: SC -26 -> kLltf48TX[0] = +1
    gr_complex(+1.0f, 0.0f),
    // bin  7: SC -25 -> kLltf48TX[1] = +1
    gr_complex(+1.0f, 0.0f),
    // bin  8: SC -24 -> kLltf48TX[2] = -1
    gr_complex(-1.0f, 0.0f),
    // bin  9: SC -23 -> kLltf48TX[3] = -1
    gr_complex(-1.0f, 0.0f),
    // bin 10: SC -22 -> kLltf48TX[4] = +1
    gr_complex(+1.0f, 0.0f),
    // bin 11: SC -21 (导频) -> 0
    gr_complex(0.0f, 0.0f),
    // bin 12-15: SC -20 to -17 -> kLltf48TX[6-9]
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 16-21: SC -16 to -11 -> kLltf48TX[10-15]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 22-25: SC -10 to -8 -> kLltf48TX[16-18]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 26-27: SC -6 to -5 -> kLltf48TX[19-20]
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 28-31: SC -4 to -1 -> kLltf48TX[21-24]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 32: DC -> 0
    gr_complex(0.0f, 0.0f),
    // bin 33: SC +1 -> kLltf48TX[25]
    gr_complex(+1.0f, 0.0f),
    // bin 34-38: SC +2 to +6 -> kLltf48TX[26-30]
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 39: SC +7 (导频) -> 0
    gr_complex(0.0f, 0.0f),
    // bin 40-47: SC +8 to +15 -> kLltf48TX[31-38]
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 48-51: SC +16 to +19 -> kLltf48TX[39-42]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),
    // bin 52: SC +20 -> kLltf48TX[43]
    gr_complex(+1.0f, 0.0f),
    // bin 53: SC +21 (导频) -> 0
    gr_complex(0.0f, 0.0f),
    // bin 54-58: SC +22 to +26 -> kLltf48TX[43-47]
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
    // bin 59-63: SC +27 to +31 (超出范围) -> 0
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
    gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f), gr_complex(0.0f, 0.0f),
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_CONSTANTS_H */
