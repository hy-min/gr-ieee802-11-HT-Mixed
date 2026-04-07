// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef INCLUDED_IEEE802_11_INSERT_HT_TRAINING_IMPL_H
#define INCLUDED_IEEE802_11_INSERT_HT_TRAINING_IMPL_H

#include <ieee802_11/insert_ht_training.h>
#include <pmt/pmt.h>

#include <gnuradio/gr_complex.h>
#include <cstdint>
#include <string>
#include <vector>

namespace gr {
namespace ieee802_11 {

/*!
 * Insert HT-STF + HT-LTF into the OFDM symbol stream (freq-domain, vlen=64).
 *
 * Assumptions (most common config):
 *  - 20 MHz, FFT=64, DC unused
 *  - insertion point: after L-SIG + 2xHT-SIG  => symbol index 3
 *  - inserted training: 1x HT-STF + 1x HT-LTF => N_TRAIN=2
 *
 * NOTE:
 *  - This block is a "symbol-domain" tagged-stream manipulator:
 *    1 item == 1 OFDM symbol (64 complex bins).
 *  - It rewrites the length tag (tag_key) by adding N_TRAIN.
 *  - Other tags are forwarded at their corresponding output symbol.
 */
class insert_ht_training_impl : public insert_ht_training
{
public:
    explicit insert_ht_training_impl(const std::string& tag_key);
    ~insert_ht_training_impl() override = default;

    void forecast(int noutput_items, gr_vector_int& ninput_items_required) override;

    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items) override;

private:
    static constexpr int VLEN = 64;

    // HT-Mixed: insert after L-SIG (1) + HT-SIG (2) => index 3
    static constexpr int INSERT_AT_SYM = 3;

    // Insert: HT-STF + HT-LTF
    static constexpr int N_TRAIN = 2;

    enum state_t { ST_IDLE = 0, ST_IN_PKT = 1 };

    bool is_used_bin(int bin) const;
    void build_training_symbols();

    // Forward all tags at abs_in_item to abs_out_item, except length tag.
    void forward_tags_at_input_item(uint64_t abs_in_item, uint64_t abs_out_item);

private:
    // length tag key (packet_len in symbols)
    const std::string d_tag_key;
    const pmt::pmt_t d_tag_key_pmt;

    state_t d_state;

    int d_in_pkt_remaining;     // remaining input symbols in this packet
    int d_sym_idx_in_pkt;       // current input symbol index within packet [0..len-1]
    bool d_inserted;            // whether HT training already inserted for current packet
    uint64_t d_in_pkt_len;      // original length tag value

    gr_complex d_ht_stf_64[VLEN];
    gr_complex d_ht_ltf_64[VLEN];
};

} // namespace ieee802_11
} // namespace gr

#endif // INCLUDED_IEEE802_11_INSERT_HT_TRAINING_IMPL_H
