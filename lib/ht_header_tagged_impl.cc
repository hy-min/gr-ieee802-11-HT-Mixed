#include "ht_header_tagged_impl.h"

#include <gnuradio/io_signature.h>
#include <pmt/pmt.h>

#include <algorithm>
#include <cstring>

namespace gr {
namespace ieee802_11 {

ht_header_tagged::sptr
ht_header_tagged::make(int rate_field,
                       bool enable_ht,
                       const std::string& len_tag_key,
                       const std::string& encoding_tag_key,
                       const std::string& packet_len_tag_key)
{
    return gnuradio::make_block_sptr<ht_header_tagged_impl>(
        rate_field, enable_ht, len_tag_key, encoding_tag_key, packet_len_tag_key);
}

ht_header_tagged_impl::ht_header_tagged_impl(int rate_field,
                                             bool enable_ht,
                                             const std::string& len_tag_key,
                                             const std::string& encoding_tag_key,
                                             const std::string& packet_len_tag_key)
    : gr::block("ht_header_tagged",
                gr::io_signature::make(1, 1, sizeof(unsigned char)),
                gr::io_signature::make(1, 1, sizeof(unsigned char))),
      d_rate_field(rate_field),
      d_enable_ht(enable_ht),
      d_len_tag_key(pmt::intern(len_tag_key)),
      d_encoding_tag_key(pmt::intern(encoding_tag_key)),
      d_packet_len_tag_key(pmt::intern(packet_len_tag_key)),
      d_formatter(signal_field::make()),
      d_header_index(0),
      d_pending_encoding(-1),
      d_pending_mcs(-1)
{
    set_tag_propagation_policy(TPP_DONT);
    set_output_multiple(144);
}

ht_header_tagged_impl::~ht_header_tagged_impl() {}

void ht_header_tagged_impl::forecast(int noutput_items,
                                     gr_vector_int& ninput_items_required)
{
    (void)noutput_items;
    ninput_items_required[0] = 1;
}

bool ht_header_tagged_impl::make_one_header_from_tags(
    const std::vector<tag_t>& tags_at_offset,
    std::vector<unsigned char>& out_hdr)
{
    std::fprintf(stderr, "[HT_HEADER_TAGGED] called with %zu tags\n", tags_at_offset.size());

    long psdu_len = -1;
    d_pending_encoding = -1;
    d_pending_mcs = -1;

    for (const auto& t : tags_at_offset) {
        std::fprintf(stderr, "[HT_HEADER_TAGGED] tag: key=%s\n", pmt::symbol_to_string(t.key).c_str());
        if (pmt::eq(t.key, d_len_tag_key) && pmt::is_integer(t.value)) {
            psdu_len = pmt::to_long(t.value);
            std::fprintf(stderr, "[HT_HEADER_TAGGED] found psdu_len=%ld\n", psdu_len);
        } else if (pmt::eq(t.key, d_encoding_tag_key) && pmt::is_integer(t.value)) {
            d_pending_encoding = pmt::to_long(t.value);
            d_pending_mcs = d_pending_encoding;
            std::fprintf(stderr, "[HT_HEADER_TAGGED] found encoding=%d\n", d_pending_encoding);
        } else if (pmt::eq(t.key, pmt::mp("mcs")) && pmt::is_integer(t.value)) {
            d_pending_mcs = pmt::to_long(t.value);
            std::fprintf(stderr, "[HT_HEADER_TAGGED] found mcs=%d\n", d_pending_mcs);
        }
    }

    if (psdu_len < 0) {
        std::fprintf(stderr, "[HT_HEADER_TAGGED] no psdu_len, returning false\n");
        return false;
    }

    std::fprintf(stderr, "[HT_HEADER_TAGGED] psdu_len=%ld encoding=%d mcs=%d\n",
                 psdu_len, d_pending_encoding, d_pending_mcs);

    out_hdr.assign(144, 0);

    // Build a modified tag list that includes d_pending_encoding
    // since header_formatter extracts encoding from tags, not from the packet_len param
    std::vector<tag_t> tags_for_formatter = tags_at_offset;
    if (d_pending_encoding >= 0) {
        tag_t enc_tag;
        enc_tag.key = pmt::mp("encoding");
        enc_tag.value = pmt::from_long(d_pending_encoding);
        enc_tag.offset = 0;
        tags_for_formatter.push_back(enc_tag);
        std::fprintf(stderr, "[HT_HEADER_TAGGED] added encoding tag with value %d\n", d_pending_encoding);
    }

    // Use ieee802_11::signal_field's header_formatter
    bool result = d_formatter->header_formatter(psdu_len, out_hdr.data(), tags_for_formatter);
    std::fprintf(stderr, "[HT_HEADER_TAGGED] header_formatter returned %d\n", result);
    return result;
}

int ht_header_tagged_impl::general_work(int noutput_items,
                                        gr_vector_int& ninput_items,
                                        gr_vector_const_void_star& input_items,
                                        gr_vector_void_star& output_items)
{
    (void)input_items;

    auto* out = static_cast<unsigned char*>(output_items[0]);
    int produced = 0;

    if (ninput_items[0] > 0) {
        std::vector<tag_t> tags;
        get_tags_in_window(tags, 0, 0, ninput_items[0]);

        for (const auto& t : tags) {
            if (!pmt::eq(t.key, d_len_tag_key)) {
                continue;
            }

            const uint64_t abs_off = t.offset;
            const uint64_t base = nitems_read(0);
            if (abs_off < base) {
                continue;
            }

            const uint64_t rel = abs_off - base;

            std::vector<tag_t> tags_at_offset;
            get_tags_in_window(tags_at_offset, 0, rel, rel + 1);

            std::vector<unsigned char> hdr;
            if (make_one_header_from_tags(tags_at_offset, hdr)) {
                d_pending_headers.push_back(std::move(hdr));
            }
        }

        // This wrapper only observes input tags, does not pass through payload bytes.
        consume(0, ninput_items[0]);
    }

    while (produced < noutput_items && !d_pending_headers.empty()) {
        auto& hdr = d_pending_headers.front();

        if (d_header_index == 0) {
            add_item_tag(0,
                         nitems_written(0) + produced,
                         d_packet_len_tag_key,
                         pmt::from_long(144));

            // Propagate mcs and encoding tags so frame_equalizer can identify HT frames
            if (d_pending_mcs >= 0) {
                add_item_tag(0,
                             nitems_written(0) + produced,
                             pmt::mp("mcs"),
                             pmt::from_long(d_pending_mcs));
            }
            if (d_pending_encoding >= 0) {
                add_item_tag(0,
                             nitems_written(0) + produced,
                             pmt::mp("encoding"),
                             pmt::from_long(d_pending_encoding));
            }
        }

        const int remaining = static_cast<int>(hdr.size() - d_header_index);
        const int ncopy = std::min(noutput_items - produced, remaining);

        std::memcpy(out + produced, hdr.data() + d_header_index, ncopy);

        produced += ncopy;
        d_header_index += ncopy;

        if (d_header_index >= hdr.size()) {
            d_pending_headers.pop_front();
            d_header_index = 0;
            d_pending_encoding = -1;
            d_pending_mcs = -1;
        }
    }

    return produced;
}

} // namespace ieee802_11
} // namespace gr
