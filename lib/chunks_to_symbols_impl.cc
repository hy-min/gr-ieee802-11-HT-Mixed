/*
 * Copyright (C) 2013 Bastian Bloessl
 *
 * GPLv3+
 */
#include "chunks_to_symbols_impl.h"
#include "utils.h"

#include <gnuradio/io_signature.h>
#include <cassert>
#include <stdexcept>
#include <vector>

using namespace gr::ieee802_11;

chunks_to_symbols::sptr chunks_to_symbols::make()
{
    return gnuradio::get_initial_sptr(new chunks_to_symbols_impl());
}

chunks_to_symbols_impl::chunks_to_symbols_impl()
    : tagged_stream_block("chunks_to_symbols",
                          io_signature::make(1, 1, sizeof(char)),
                          io_signature::make(1, 1, sizeof(gr_complex)),
                          "packet_len")
{
    d_bpsk = constellation_bpsk::make();
    d_qpsk = constellation_qpsk::make();
    d_16qam = constellation_16qam::make();
    d_64qam = constellation_64qam::make();

    d_mapping = d_bpsk;
}

chunks_to_symbols_impl::~chunks_to_symbols_impl() {}

int chunks_to_symbols_impl::work(int noutput_items,
                                 gr_vector_int& ninput_items,
                                 gr_vector_const_void_star& input_items,
                                 gr_vector_void_star& output_items)
{
    (void)noutput_items;

    const unsigned char* in = (const unsigned char*)input_items[0];
    gr_complex* out = (gr_complex*)output_items[0];

    // packet_len for this tagged stream item
    const int pkt_len = ninput_items[0];

    // Try to read encoding tag (payload encoding). Header mapping is forced to BPSK anyway.
    Encoding encoding = BPSK_1_2;
    {
        std::vector<tag_t> tags;
        get_tags_in_range(tags,
                          0,
                          nitems_read(0),
                          nitems_read(0) + ninput_items[0],
                          pmt::mp("encoding"));
        if (!tags.empty()) {
            encoding = (Encoding)pmt::to_long(tags[0].value);
        }
    }

    const bool is_header = (pkt_len == 48 || pkt_len == 144);

    if (is_header) {
        // L-SIG and HT-SIG are always BPSK mapped
        d_mapping = d_bpsk;
    } else {
        // DATA mapping uses payload encoding
        switch (encoding) {
        case BPSK_1_2:
        case BPSK_3_4:
            d_mapping = d_bpsk;
            break;
        case QPSK_1_2:
        case QPSK_3_4:
            d_mapping = d_qpsk;
            break;
        case QAM16_1_2:
        case QAM16_3_4:
            d_mapping = d_16qam;
            break;
        case QAM64_2_3:
        case QAM64_3_4:
            d_mapping = d_64qam;
            break;
        default:
            throw std::invalid_argument("wrong encoding");
        }
    }

    for (int i = 0; i < pkt_len; i++) {
        d_mapping->map_to_points(in[i], out + i);

        // For HT header (144 bits): bits[0..47]=L-SIG on I axis, bits[48..143]=HT-SIG on Q axis.
        if (pkt_len == 144 && i >= 48) {
            out[i] *= gr_complex(0.0f, 1.0f); // +90 deg rotation: (±1,0)->(0,±1)
        }
    }

    return pkt_len;
}
