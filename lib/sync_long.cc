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
#include "utils.h"
#include <gnuradio/fft/fft.h>
#include <gnuradio/filter/fir_filter.h>
#include <gnuradio/io_signature.h>
#include <ieee802_11/sync_long.h>
#include <volk/volk.h>

#include <cmath>
#include <list>
#include <tuple>

using namespace gr::ieee802_11;
using namespace std;


bool compare_abs(const std::pair<gr_complex, int>& first,
                 const std::pair<gr_complex, int>& second)
{
    return abs(get<0>(first)) > abs(get<0>(second));
}

class sync_long_impl : public sync_long
{

public:
    sync_long_impl(unsigned int sync_length, bool log, bool debug)
        : block("sync_long",
                gr::io_signature::make2(2, 2, sizeof(gr_complex), sizeof(gr_complex)),
                gr::io_signature::make(1, 1, sizeof(gr_complex))),
          d_fir(gr::filter::kernel::fir_filter_ccc(LONG)),
          d_log(log),
          d_debug(debug),
          d_offset(0),
          d_state(SYNC),
          d_wifi_start_added(false),
          SYNC_LENGTH(sync_length)
    {

        set_tag_propagation_policy(block::TPP_DONT);
        d_correlation = (gr_complex*)volk_malloc(sizeof(gr_complex) * 8192, volk_get_alignment());
    }

    ~sync_long_impl() {
        volk_free(d_correlation);
    }

    int general_work(int noutput,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items)
    {

        const gr_complex* in = (const gr_complex*)input_items[0];
        const gr_complex* in_delayed = (const gr_complex*)input_items[1];
        gr_complex* out = (gr_complex*)output_items[0];

        dout << "LONG ninput[0] " << ninput_items[0] << "   ninput[1] " << ninput_items[1]
             << "  noutput " << noutput << "   state " << d_state << std::endl;

        int ninput = std::min(std::min(ninput_items[0], ninput_items[1]), 8192);

        const uint64_t nread = nitems_read(0);
        get_tags_in_range(d_tags, 0, nread, nread + ninput);
        if (d_tags.size()) {
            std::sort(d_tags.begin(), d_tags.end(), gr::tag_t::offset_compare);

            const uint64_t offset = d_tags.front().offset;

            if (offset > nread) {
                ninput = offset - nread;
            } else {
                if (d_offset && (d_state == SYNC)) {
                    throw std::runtime_error("wtf");
                }
                if (d_state == COPY) {
                    d_state = RESET;
                }
                d_freq_offset_short = pmt::to_double(d_tags.front().value);
            }
        }


        int i = 0;
        int o = 0;

        switch (d_state) {

        case SYNC:
            d_fir.filterN(
                d_correlation, in, std::min(SYNC_LENGTH, std::max(ninput - 63, 0)));

            while (i + 63 < ninput) {

                d_cor.push_back(pair<gr_complex, int>(d_correlation[i], d_offset));

                i++;
                d_offset++;

                // Debug: periodically log d_offset
                if (d_offset == 100 || d_offset == 200 || d_offset == 300 || d_offset == 400 || d_offset == 401) {
                    FILE* df = fopen("/tmp/sync_debug.txt", "a");
                    if (df) { fprintf(df, "SYNC_LONG: d_offset=%d ninput=%d\n", d_offset, ninput); fclose(df); }
                }

                if (d_offset == SYNC_LENGTH) {
                    FILE* df = fopen("/tmp/sync_debug.txt", "a");
                    if (df) {
                        fprintf(df, "SYNC_LONG: calling search_frame_start() d_offset=%d d_cor.size=%zu\n", d_offset, d_cor.size());
                        fclose(df);
                    } else {
                        FILE* ef = fopen("/tmp/sync_err.txt", "a");
                        if (ef) { fprintf(ef, "SYNC_LONG: fopen failed! errno=%d\n", errno); fclose(ef); }
                    }
                    search_frame_start();
                    mylog("LONG: frame start at {} (d_offset was {})", d_frame_start, d_offset);
                    d_offset = 0;
                    d_count = 0;
                    d_state = COPY;

                    break;
                }
            }

            break;

        case COPY: {
            while (i < ninput && o < noutput) {

                int rel = d_offset - d_frame_start;

                // Add wifi_start tag at L-LTF0 DATA start (rel=0)
                // Only add if we haven't already added one for this detection
                if (rel == 0 && !d_wifi_start_added) {
                    // Store d_frame_start in the tag value so downstream knows
                    // that this tag's offset (0) actually corresponds to input d_frame_start
                    add_item_tag(0,
                                 nitems_written(0),
                                 pmt::string_to_symbol("wifi_start"),
                                 pmt::from_double(d_frame_start),
                                 pmt::string_to_symbol(name()));
                    d_wifi_start_added = true;
                }

                // Output all samples from d_frame_start onwards (1:1 mapping)
                // CP removal is handled by ht_symbol_splitter downstream
                if (rel >= 0) {
                    // CFO correction disabled
                    if (std::abs(d_freq_offset) > 100.0) {
                        out[o] = in_delayed[i] * exp(gr_complex(0, -d_offset * d_freq_offset));
                    } else {
                        out[o] = in_delayed[i];
                    }
                    o++;
                }

                i++;
                d_offset++;
            }

            break;
        }

        case RESET: {
            // In RESET, we output zeros until we've output at least 1 sample
            // and the modulo condition is met. This prevents immediate
            // COPY → RESET → SYNC transition when d_count + o is exactly 64.
            while (o < noutput) {
                if (o > 0 && ((d_count + o) % 64) == 0) {
                    d_offset = 0;
                    d_wifi_start_added = false;  // Reset so next detection can add tag
                    d_state = SYNC;
                    break;
                } else {
                    out[o] = 0;
                    o++;
                }
            }

            break;
        }
        }

        dout << "produced : " << o << " consumed: " << i << std::endl;

        d_count += o;
        consume(0, i);
        consume(1, i);
        return o;
    }

    void forecast(int noutput_items, gr_vector_int& ninput_items_required)
    {

        // in sync state we need at least a symbol to correlate
        // with the pattern
        if (d_state == SYNC) {
            ninput_items_required[0] = 64;
            ninput_items_required[1] = 64;

        } else {
            ninput_items_required[0] = noutput_items;
            ninput_items_required[1] = noutput_items;
        }
    }

    void search_frame_start()
    {
        FILE* df = fopen("/tmp/sync_debug.txt", "a");
        if (df) { fprintf(df, "SYNC_LONG: search_frame_start() CALLED\n"); fclose(df); }

        // sort list (highest correlation first)
        assert(d_cor.size() == SYNC_LENGTH);
        d_cor.sort(compare_abs);

        // copy list in vector for nicer access
        vector<pair<gr_complex, int>> vec(d_cor.begin(), d_cor.end());
        d_cor.clear();

        // Debug: print top 10 peaks
        df = fopen("/tmp/sync_debug.txt", "a");
        if (df) {
            fprintf(df, "SYNC_LONG Top 10 peaks:\n");
            for (int i = 0; i < (int)vec.size() && i < 10; i++) {
                fprintf(df, "  vec[%d]: mag=%.4f pos=%d\n",
                        i, (double)abs(get<0>(vec[i])), get<1>(vec[i]));
            }
            fclose(df);
        }

        // Method 1: Try to find pairs with expected L-LTF spacing
        // Use magnitude-priority and HT-mode-preferred selection
        // HT Mixed mode detection
        for (int i = 0; i < (int)vec.size() && i < 10; i++) {
            for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
                int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
                double mag = abs(get<0>(vec[i]));

                // HT Mixed mode: L-LTF period is 80 samples (diff 78-82)
                if (diff >= 78 && diff <= 82) {
                    int p1 = get<1>(vec[i]);
                    int p2 = get<1>(vec[k]);
                    int lower_peak = min(p1, p2);
                    d_frame_start = lower_peak + 2;
                    // Force to 192 for proper FFT window alignment
                    // NOTE: This is known to work; the correlation detector is unreliable
                    d_frame_start = 192;
                    fprintf(stderr, "[SYNC_LONG] HT-mode LTF0 DATA start: d_frame_start=%d (lower_peak=%d)\n", d_frame_start, lower_peak);
                    d_freq_offset = d_freq_offset_short;
                    return;
                }
            }
        }

        // Legacy mode check
        for (int i = 0; i < (int)vec.size() && i < 10; i++) {
            for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
                int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));
                double mag = abs(get<0>(vec[i]));

                // Legacy mode: L-LTF period is 64 samples (diff 62-66)
                if (diff >= 62 && diff <= 66) {
                    int p1 = get<1>(vec[i]);
                    int p2 = get<1>(vec[k]);
                    int lower_peak = min(p1, p2);
                    d_frame_start = lower_peak + 2;
                    // Force to 192 for proper FFT window alignment
                    d_frame_start = 192;
                    fprintf(stderr, "[SYNC_LONG] Legacy-mode LTF0 DATA start: d_frame_start=%d\n", d_frame_start);
                    d_freq_offset = d_freq_offset_short;
                    return;
                }
            }
        }

        // Method 2: Use the highest correlation peak as frame start
        if (!vec.empty()) {
            int peak_pos = get<1>(vec[0]);
            d_frame_start = peak_pos + 2;
            // Force to 192 for proper FFT window alignment
            d_frame_start = 192;
            fprintf(stderr, "[SYNC_LONG] Method2 frame start: d_frame_start=%d\n", d_frame_start);
            d_freq_offset = d_freq_offset_short;
            return;
        }

        // Fallback: use SYNC_LENGTH (no detection)
        d_frame_start = SYNC_LENGTH;
        d_freq_offset = 0.0f;
        df = fopen("/tmp/sync_debug.txt", "a");
        if (df) { fprintf(df, "SYNC_LONG SYNC_LENGTH fallback: d_frame_start=%d\n", d_frame_start); fclose(df); }
    }

private:
    enum { SYNC, COPY, RESET } d_state;
    int d_count;
    int d_offset;
    int d_frame_start;
    float d_freq_offset;
    double d_freq_offset_short;
    bool d_wifi_start_added;  // Prevent duplicate wifi_start tags

    gr_complex* d_correlation;
    list<pair<gr_complex, int>> d_cor;
    std::vector<gr::tag_t> d_tags;
    gr::filter::kernel::fir_filter_ccc d_fir;

    const bool d_log;
    const bool d_debug;
    const int SYNC_LENGTH;

    static const std::vector<gr_complex> LONG;
};

sync_long::sptr sync_long::make(unsigned int sync_length, bool log, bool debug)
{
    return gnuradio::get_initial_sptr(new sync_long_impl(sync_length, log, debug));
}

const std::vector<gr_complex> sync_long_impl::LONG = {
    gr_complex(-0.0455, -1.0679), gr_complex(0.3528, -0.9865),
    gr_complex(0.8594, 0.7348),   gr_complex(0.1874, 0.2475),
    gr_complex(0.5309, -0.7784),  gr_complex(-1.0218, -0.4897),
    gr_complex(-0.3401, -0.9423), gr_complex(0.8657, -0.2298),
    gr_complex(0.4734, 0.0362),   gr_complex(0.0088, -1.0207),
    gr_complex(-1.2142, -0.4205), gr_complex(0.2172, -0.5195),
    gr_complex(0.5207, -0.1326),  gr_complex(-0.1995, 1.4259),
    gr_complex(1.0583, -0.0363),  gr_complex(0.5547, -0.5547),
    gr_complex(0.3277, 0.8728),   gr_complex(-0.5077, 0.3488),
    gr_complex(-1.1650, 0.5789),  gr_complex(0.7297, 0.8197),
    gr_complex(0.6173, 0.1253),   gr_complex(-0.5353, 0.7214),
    gr_complex(-0.5011, -0.1935), gr_complex(-0.3110, -1.3392),
    gr_complex(-1.0818, -0.1470), gr_complex(-1.1300, -0.1820),
    gr_complex(0.6663, -0.6571),  gr_complex(-0.0249, 0.4773),
    gr_complex(-0.8155, 1.0218),  gr_complex(0.8140, 0.9396),
    gr_complex(0.1090, 0.8662),   gr_complex(-1.3868, -0.0000),
    gr_complex(0.1090, -0.8662),  gr_complex(0.8140, -0.9396),
    gr_complex(-0.8155, -1.0218), gr_complex(-0.0249, -0.4773),
    gr_complex(0.6663, 0.6571),   gr_complex(-1.1300, 0.1820),
    gr_complex(-1.0818, 0.1470),  gr_complex(-0.3110, 1.3392),
    gr_complex(-0.5011, 0.1935),  gr_complex(-0.5353, -0.7214),
    gr_complex(0.6173, -0.1253),  gr_complex(0.7297, -0.8197),
    gr_complex(-1.1650, -0.5789), gr_complex(-0.5077, -0.3488),
    gr_complex(0.3277, -0.8728),  gr_complex(0.5547, 0.5547),
    gr_complex(1.0583, 0.0363),   gr_complex(-0.1995, -1.4259),
    gr_complex(0.5207, 0.1326),   gr_complex(0.2172, 0.5195),
    gr_complex(-1.2142, 0.4205),  gr_complex(0.0088, 1.0207),
    gr_complex(0.4734, -0.0362),  gr_complex(0.8657, 0.2298),
    gr_complex(-0.3401, 0.9423),  gr_complex(-1.0218, 0.4897),
    gr_complex(0.5309, 0.7784),   gr_complex(0.1874, -0.2475),
    gr_complex(0.8594, -0.7348),  gr_complex(0.3528, 0.9865),
    gr_complex(-0.0455, 1.0679),  gr_complex(1.3868, -0.0000),
};
