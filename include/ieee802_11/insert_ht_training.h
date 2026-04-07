/* -*- c++ -*- */
/* SPDX-License-Identifier: GPL-3.0-or-later */

#ifndef INCLUDED_IEEE802_11_INSERT_HT_TRAINING_H
#define INCLUDED_IEEE802_11_INSERT_HT_TRAINING_H

#include <gnuradio/block.h>
#include <ieee802_11/api.h>

#include <memory>
#include <string>

namespace gr {
namespace ieee802_11 {

/*! \brief Insert HT-STF and HT-LTF (HT-Mixed, 20 MHz, 1 spatial stream).
 *
 * Input/Output: each item is one 64-subcarrier OFDM symbol in the *frequency*
 * domain (vector length 64, itemsize = 64 * sizeof(gr_complex)).
 */
class IEEE802_11_API insert_ht_training : public gr::block
{
public:
    using sptr = std::shared_ptr<insert_ht_training>;

    /*! \param len_tag_key Length tag key (typically "packet_len"). */
    static sptr make(const std::string& len_tag_key = "packet_len");

protected:
    insert_ht_training(const std::string& name,
                       gr::io_signature::sptr input_signature,
                       gr::io_signature::sptr output_signature);
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_INSERT_HT_TRAINING_H */
