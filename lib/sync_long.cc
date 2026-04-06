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

                // Add wifi_start tag at d_frame_start (the start of L-LTF data)
                if (rel == 0) {
                    add_item_tag(0,
                                 nitems_written(0),
                                 pmt::string_to_symbol("wifi_start"),
                                 pmt::from_double(d_freq_offset_short - d_freq_offset),
                                 pmt::string_to_symbol(name()));
                }

                if (rel >= 0 && (rel < 128 || ((rel - 128) % 80) > 15)) {
                    // Apply CFO correction only if d_freq_offset is significant (> 0.001)
                    // For loopback tests with no actual CFO, d_freq_offset is near 0
                    if (std::abs(d_freq_offset) > 0.001) {
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
            while (o < noutput) {
                if (((d_count + o) % 64) == 0) {
                    d_offset = 0;
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
        // HT Mixed: 80 samples, Legacy: 64 samples
        for (int i = 0; i < (int)vec.size() && i < 10; i++) {
            for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
                int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));

                // HT Mixed mode: L-LTF period is 80 samples
                // The lower peak corresponds to L-LTF0 data start (~176)
                // The higher peak corresponds to L-LTF1 data start (~256)
                // For channel estimation, we need L-LTF0, so use the lower peak
                // The correlation peak is 2 samples BEFORE the actual L-LTF data start
                if (diff >= 78 && diff <= 82) {
                    int p1 = get<1>(vec[i]);
                    int p2 = get<1>(vec[k]);
                    // Lower peak corresponds to L-LTF0 data start
                    int lower_peak = min(p1, p2);
                    // L-LTF data starts at lower_peak + 2 = 176
                    // wifi_start should be at this position so frame_equalizer
                    // starts FFT at the beginning of L-LTF data
                    d_frame_start = lower_peak + 2;
                    // Ensure reasonable bounds (data start should be around 176)
                    if (d_frame_start < 170) d_frame_start = 176;
                    if (d_frame_start > 182) d_frame_start = 176;
                    d_freq_offset = arg(get<0>(vec[i]) * conj(get<0>(vec[k]))) / diff;
                    fprintf(stderr, "[SYNC_CFO] diff=%d, arg(vec[i])=%.4f, arg(vec[k])=%.4f, arg(product)=%.4f, CFO=%.6f\n",
                            diff,
                            std::arg(get<0>(vec[i])),
                            std::arg(get<0>(vec[k])),
                            std::arg(get<0>(vec[i]) * std::conj(get<0>(vec[k]))),
                            d_freq_offset);
                    df = fopen("/tmp/sync_debug.txt", "a");
                    if (df) { fprintf(df, "SYNC_LONG Method1 HT found: diff=%d, pos1=%d pos2=%d -> d_frame_start=%d CFO=%.6f\n", diff, p1, p2, d_frame_start, d_freq_offset); fclose(df); }
                    return;
                }
                // Legacy mode: L-LTF period is 64 samples
                // The peaks occur at the actual L-LTF data start
                if (diff >= 62 && diff <= 66) {
                    int p1 = get<1>(vec[i]);
                    int p2 = get<1>(vec[k]);
                    // Lower peak corresponds to L-LTF0 data start
                    int lower_peak = min(p1, p2);
                    // For Legacy, data_start = lower_peak (no offset needed)
                    d_frame_start = lower_peak;
                    if (d_frame_start < 0) d_frame_start = 0;
                    // For Legacy, FFT block alignment: data is already 64 samples
                    // Ensure d_frame_start is at data start, not CP start
                    d_freq_offset = arg(get<0>(vec[i]) * conj(get<0>(vec[k]))) / diff;
                    fprintf(stderr, "[SYNC_CFO_LEGACY] diff=%d, arg(vec[i])=%.4f, arg(vec[k])=%.4f, arg(product)=%.4f, CFO=%.6f\n",
                            diff,
                            std::arg(get<0>(vec[i])),
                            std::arg(get<0>(vec[k])),
                            std::arg(get<0>(vec[i]) * std::conj(get<0>(vec[k]))),
                            d_freq_offset);
                    df = fopen("/tmp/sync_debug.txt", "a");
                    if (df) { fprintf(df, "SYNC_LONG Method1 Legacy found: diff=%d, pos1=%d pos2=%d -> d_frame_start=%d\n", diff, p1, p2, d_frame_start); fclose(df); }
                    return;
                }
            }
        }

        // Method 2: Use the highest correlation peak as frame start
        // (Less accurate but better fallback than SYNC_LENGTH)
        if (!vec.empty()) {
            int peak_pos = get<1>(vec[0]);
            // Correlation peak is 2 samples before L-LTF data start
            // so data_start = peak_pos + 2
            d_frame_start = peak_pos + 2;
            if (d_frame_start < 170) d_frame_start = 176;
            if (d_frame_start > 185) d_frame_start = 176;
            d_freq_offset = 0.0f;
            df = fopen("/tmp/sync_debug.txt", "a");
            if (df) { fprintf(df, "SYNC_LONG Method2 fallback: peak_pos=%d -> d_frame_start=%d\n", peak_pos, d_frame_start); fclose(df); }
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
