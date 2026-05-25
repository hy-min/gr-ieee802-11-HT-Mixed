#ifndef INCLUDED_IEEE802_11_HT_HEADER_TAGGED_IMPL_H
#define INCLUDED_IEEE802_11_HT_HEADER_TAGGED_IMPL_H

#include <ieee802_11/ht_header_tagged.h>
#include <ieee802_11/signal_field.h>
#include <pmt/pmt.h>

#include <deque>
#include <vector>

namespace gr {
namespace ieee802_11 {

class ht_header_tagged_impl : public ht_header_tagged
{
private:
    int d_rate_field;
    bool d_enable_ht;

    pmt::pmt_t d_len_tag_key;
    pmt::pmt_t d_encoding_tag_key;
    pmt::pmt_t d_packet_len_tag_key;

    signal_field::sptr d_formatter;

    std::deque<std::vector<unsigned char>> d_pending_headers;
    size_t d_header_index;
    int d_pending_encoding;
    int d_pending_mcs;
    bool d_pending_use_ldpc;
    int d_pending_scrambler_seed;
    int d_pending_ldpc_block_length;
    int d_pending_ldpc_n_sym;

    bool make_one_header_from_tags(const std::vector<tag_t>& tags_at_offset,
                                   std::vector<unsigned char>& out_hdr);

public:
    ht_header_tagged_impl(int rate_field,
                          bool enable_ht,
                          const std::string& len_tag_key,
                          const std::string& encoding_tag_key,
                          const std::string& packet_len_tag_key);

    ~ht_header_tagged_impl() override;

    void forecast(int noutput_items,
                  gr_vector_int& ninput_items_required) override;

    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items) override;
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_HT_HEADER_TAGGED_IMPL_H */