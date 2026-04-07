#ifndef INCLUDED_IEEE802_11_EQUALIZER_BASE_H
#define INCLUDED_IEEE802_11_EQUALIZER_BASE_H

#include <gnuradio/digital/constellation.h>
#include <gnuradio/gr_complex.h>
#include <vector>
#include <memory>
#include <cstdint>

namespace gr {
namespace ieee802_11 {
namespace equalizer {

class base
{
public:
    virtual ~base(){};
    virtual void equalize(gr_complex* in,
                          int n,
                          gr_complex* symbols,
                          uint8_t* bits,
                          std::shared_ptr<gr::digital::constellation> mod) = 0;
    virtual double get_snr() = 0;

    static const gr_complex POLARITY[127];

    std::vector<gr_complex> get_csi();

protected:
    static const gr_complex LONG[64];

    gr_complex d_H[64];
};

} // namespace equalizer
} /* namespace ieee802_11 */
} /* namespace gr */

#endif /* INCLUDED_IEEE802_11_EQUALIZER_BASE_H */