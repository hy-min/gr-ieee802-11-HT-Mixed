/*
 * Copyright (C) 2013, 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */
#ifndef INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_IMPL_H
#define INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_IMPL_H

#include <ieee802_11/ht_symbol_splitter.h>
#include <gnuradio/thread/thread.h>
#include <vector>
#include <cstdint>

namespace gr {
namespace ieee802_11 {

class ht_symbol_splitter_impl : public ht_symbol_splitter
{
private:
    int d_fft_size;       // FFT size (typically 64 for 20MHz)
    int d_symbol_size;     // OFDM symbol size (80 for HT-Mixed, 64 for Legacy)
    int d_cp_size;        // Cyclic prefix size (16 for HT-Mixed preamble)
    bool d_ht_mixed;      // true for HT-Mixed mode

    bool d_debug;
    int d_debug_count;

    // Circular buffer for FFT blocks
    std::vector<gr_complex> d_buffer;
    int d_buffer_count;
    bool d_buffer_filled;  // True when buffer filled at non-boundary, waiting for boundary

    // Frame tracking
    int64_t d_frame_start_abs;      // Absolute item index of frame start (from wifi_start tag)
    bool d_frame_start_known;       // Have we seen wifi_start tag?
    int64_t d_items_processed;      // Total items we've processed

    // Symbol counter for debugging - tracks which symbol we're outputting
    int d_internal_symbol_counter;   // 0=L-LTF0, 1=L-LTF1, 2=L-SIG, 3=HT-SIG0, 4=HT-SIG1, etc.

public:
    ht_symbol_splitter_impl(int fft_size, int symbol_size, int cp_size);
    ~ht_symbol_splitter_impl();

    // Block work function
    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items);

    // forecast function - tell scheduler what we need
    void forecast(int noutput_items, gr_vector_int& ninput_items_required);

    // Mode setting
    void set_ht_mixed(bool ht_mixed);
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_IMPL_H */
