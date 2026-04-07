#ifndef INCLUDED_IEEE802_11_FRAME_EQUALIZER_H
#define INCLUDED_IEEE802_11_FRAME_EQUALIZER_H

#include <gnuradio/block.h>
#include <ieee802_11/api.h>
#include <ieee802_11/equalizer.h>

namespace gr {
namespace ieee802_11 {

class IEEE802_11_API frame_equalizer : virtual public gr::block
{
public:
    typedef std::shared_ptr<frame_equalizer> sptr;

    static sptr make(Equalizer algo,
                     double freq,
                     double bw,
                     bool log = false,
                     bool debug = false);

    virtual void set_algorithm(Equalizer algo) = 0;
    virtual void set_bandwidth(double bw) = 0;
    virtual void set_frequency(double freq) = 0;

    // HT-SIG 等额外 header OFDM symbols 数（HT-SIG=2）
    virtual void set_extra_header_symbols(int n) = 0;
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_FRAME_EQUALIZER_H */
