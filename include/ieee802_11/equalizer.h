#ifndef INCLUDED_IEEE802_11_EQUALIZER_H
#define INCLUDED_IEEE802_11_EQUALIZER_H

#include <ieee802_11/api.h>

namespace gr {
namespace ieee802_11 {

// Equalizer algorithm selector (public API)
enum Equalizer {
    COMB = 0,
    LS   = 1,
    LMS  = 2,
    STA  = 3
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_EQUALIZER_H */
