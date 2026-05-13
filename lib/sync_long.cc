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

                if (d_offset == SYNC_LENGTH) {
                    bool detected = search_frame_start();
                    mylog("LONG: frame start at {} (d_offset was {})", d_frame_start, d_offset);
                    d_offset = 0;
                    d_count = 0;
                    if (detected) {
                        d_state = COPY;
                    } else {
                        // No valid detection - stay in SYNC state, clear correlation for new search
                        d_cor.clear();
                        d_state = SYNC;
                    }

                    break;
                }
            }

            break;

        case COPY: {
            
            while (i < ninput && o < noutput) {

                int rel = d_offset - d_frame_start;

                // Debug: trace d_offset and rel in COPY loop
                if (d_offset < 10 || d_offset == d_frame_start) {
                    fprintf(stderr, "[SYNC_LONG_COPY] d_offset=%d, d_frame_start=%d, rel=%d\n",
                            d_offset, d_frame_start, rel);
                }

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

                // PROBE: L-LTF periodicity check (samples[0]≈samples[64], samples[32]≈samples[96])
                // Also probe key HT-SIG/L-SIG positions
                static int periodicity_probe_count = 0;
                if (periodicity_probe_count < 20 && (rel == 0 || rel == 32 || rel == 64 || rel == 96 || rel == 128 || rel == 144 || rel == 240 || rel == 304 || rel == 320)) {
                    const char* pos_label = "";
                    if (rel == 0) pos_label = "LTF0_START";
                    else if (rel == 32) pos_label = "LTF0_MID";
                    else if (rel == 64) pos_label = "LTF1_START";
                    else if (rel == 96) pos_label = "LTF1_MID";
                    else if (rel == 128) pos_label = "LSIG_CP";
                    else if (rel == 144) pos_label = "LSIG_DATA";
                    else if (rel == 240) pos_label = "HTSIG0_DATA";
                    else if (rel == 304) pos_label = "HTSIG1_CP";
                    else if (rel == 320) pos_label = "HTSIG1_DATA";
                    fprintf(stderr, "[SYNC_LONG_PERIODICITY] d_offset=%d rel=%d out_idx=%d amp=%.4f sample=%.4f%+.4fi [%s]\n",
                            d_offset, rel, o, std::abs(in_delayed[i]), in_delayed[i].real(), in_delayed[i].imag(), pos_label);
                    periodicity_probe_count++;
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
                    // PROBE: Print first 10 output samples to verify sync_long output
                    static int copy_probe_count = 0;
                    if (copy_probe_count < 10) {
                        fprintf(stderr, "[SYNC_LONG_OUT] d_offset=%d out_idx=%d amp=%.6f sample=%.6f%+.6fi\n",
                                d_offset, o, std::abs(out[o]), out[o].real(), out[o].imag());
                        copy_probe_count++;
                    }
                    // PROBE: Print at out_idx=240 (HTSIG0_DATA position)
                    if (o == 240) {
                        fprintf(stderr, "[SYNC_LONG_OUT_IDX240] d_offset=%d out_idx=%d amp=%.6f sample=%.6f%+.6fi in_delayed[i]=%.6f%+.6fi\n",
                                d_offset, o, std::abs(out[o]), out[o].real(), out[o].imag(),
                                std::abs(in_delayed[i]), in_delayed[i].real(), in_delayed[i].imag());
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

    bool search_frame_start()
    {
        bool valid = false;

        // sort list (highest correlation first)
        assert(d_cor.size() == SYNC_LENGTH);
        d_cor.sort(compare_abs);

        // copy list in vector for nicer access
        vector<pair<gr_complex, int>> vec(d_cor.begin(), d_cor.end());
        d_cor.clear();

        // ESSENTIAL DEBUG: d_frame_start detection
        const char* mode = "unknown";

        // Print Top 20 peaks to diagnose plateau effect
        fprintf(stderr, "[SYNC_LONG_DEBUG] Top 20 peaks: ");
        for (int m = 0; m < 20 && m < (int)vec.size(); m++) {
            fprintf(stderr, "%d(%.1f) ", get<1>(vec[m]), abs(get<0>(vec[m])));
        }
        fprintf(stderr, "\n");
        fflush(stderr);

        // Method 1: Plateau-aware L-LTF peak pair detection
        // Problem: The correlation peak can form a "plateau" (wide peak)
        // due to multipath, causing max() to return an index at the edge
        // of the plateau rather than the true peak at ~171.
        // Solution: Find ALL candidate pairs with diff≈80 and select the
        // one with best amplitude balance and position score.
        double top_mag = abs(get<0>(vec[0]));
        fprintf(stderr, "[SYNC_LONG] Top correlation magnitude: %.4f\n", top_mag);

        // Minimum thresholds (keep from previous implementation)
        const double MIN_ABS_MAGNITUDE = 3.0;
        const double MIN_PEAK_RATIO = 0.30;

        // ============================================================
        // HT-mode: Find ALL candidate pairs in diff range [70, 90]
        // ============================================================
        std::vector<std::tuple<int, int, int, double, int>> ht_candidates;  // (i, k, diff, ratio, lower_peak)

        for (int i = 0; i < (int)vec.size() && i < 10; i++) {
            double mag_i = abs(get<0>(vec[i]));
            if (mag_i < MIN_ABS_MAGNITUDE || mag_i < top_mag * MIN_PEAK_RATIO) {
                continue;
            }

            for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
                double mag_k = abs(get<0>(vec[k]));
                int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));

                // Only consider pairs in extended L-LTF period range (70-90, expanded for plateau)
                if (diff < 70 || diff > 90) {
                    continue;
                }

                // Amplitude similarity ratio (both peaks should be similar magnitude)
                double ratio = std::min(mag_i, mag_k) / std::max(mag_i, mag_k);

                int p1 = get<1>(vec[i]);
                int p2 = get<1>(vec[k]);
                int lower_peak = std::min(p1, p2);

                ht_candidates.push_back(std::make_tuple(i, k, diff, ratio, lower_peak));
            }
        }

        // Select best HT-mode candidate: highest amplitude ratio, then closest to expected lower_peak
        int best_ht_i = -1, best_ht_k = -1, best_ht_diff = -1, best_ht_lower_peak = -1;
        double best_ht_score = 0.0;

        for (auto& cand : ht_candidates) {
            int i = std::get<0>(cand);
            int k = std::get<1>(cand);
            int diff = std::get<2>(cand);
            double ratio = std::get<3>(cand);
            int lower_peak = std::get<4>(cand);

            // Score: amplitude ratio (primary) * continuous position score (secondary)
            // Continuous position score: closer to ideal_lower_peak=171 is better
            // Score ranges from ratio*1.0 (lower_peak at edge of range) to ratio*2.0 (exact ideal)
            int ideal_lower_peak = 171;
            double position_score = 1.0 - std::abs(lower_peak - ideal_lower_peak) / 50.0;
            position_score = std::max(0.0, position_score);
            double score = ratio * (1.0 + position_score);

            fprintf(stderr, "[SYNC_LONG] HT Candidate: i=%d(idx=%d,amp=%.2f) k=%d(idx=%d,amp=%.2f) diff=%d ratio=%.2f lower_peak=%d score=%.2f\n",
                    i, get<1>(vec[i]), abs(get<0>(vec[i])),
                    k, get<1>(vec[k]), abs(get<0>(vec[k])),
                    diff, ratio, lower_peak, score);

            if (score > best_ht_score) {
                best_ht_score = score;
                best_ht_i = i;
                best_ht_k = k;
                best_ht_diff = diff;
                best_ht_lower_peak = lower_peak;
            }
        }

        // If we found a valid HT candidate
        if (best_ht_i >= 0 && best_ht_k >= 0) {
            d_frame_start = best_ht_lower_peak + 1;
            mode = "HT-mode-plateau";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] HT-mode-plateau SELECTED: best_i=%d(idx=%d) best_k=%d(idx=%d) best_diff=%d best_lower_peak=%d d_frame_start=%d score=%.2f\n",
                    best_ht_i, get<1>(vec[best_ht_i]), best_ht_k, get<1>(vec[best_ht_k]),
                    best_ht_diff, best_ht_lower_peak, d_frame_start, best_ht_score);
            valid = true;
            return valid;
        }

        // ============================================================
        // Legacy mode: Find ALL candidate pairs in diff range [55, 70]
        // ============================================================
        std::vector<std::tuple<int, int, int, double, int>> legacy_candidates;  // (i, k, diff, ratio, lower_peak)

        for (int i = 0; i < (int)vec.size() && i < 10; i++) {
            double mag_i = abs(get<0>(vec[i]));
            if (mag_i < MIN_ABS_MAGNITUDE || mag_i < top_mag * MIN_PEAK_RATIO) {
                continue;
            }

            for (int k = i + 1; k < (int)vec.size() && k < 20; k++) {
                double mag_k = abs(get<0>(vec[k]));
                int diff = abs(get<1>(vec[i]) - get<1>(vec[k]));

                // Only consider pairs in Legacy L-LTF period range (55-70)
                if (diff < 55 || diff > 70) {
                    continue;
                }

                // Amplitude similarity ratio
                double ratio = std::min(mag_i, mag_k) / std::max(mag_i, mag_k);

                int p1 = get<1>(vec[i]);
                int p2 = get<1>(vec[k]);
                int lower_peak = std::min(p1, p2);

                legacy_candidates.push_back(std::make_tuple(i, k, diff, ratio, lower_peak));
            }
        }

        // Select best Legacy candidate
        int best_leg_i = -1, best_leg_k = -1, best_leg_diff = -1, best_leg_lower_peak = -1;
        double best_leg_score = 0.0;

        for (auto& cand : legacy_candidates) {
            int i = std::get<0>(cand);
            int k = std::get<1>(cand);
            int diff = std::get<2>(cand);
            double ratio = std::get<3>(cand);
            int lower_peak = std::get<4>(cand);

            // Score: amplitude ratio (primary) + position bonus (secondary)
            double position_bonus = 0.0;
            if (lower_peak >= 130 && lower_peak <= 160) {
                position_bonus = 0.5;
            }

            double score = ratio + position_bonus;

            fprintf(stderr, "[SYNC_LONG] Legacy Candidate: i=%d(idx=%d,amp=%.2f) k=%d(idx=%d,amp=%.2f) diff=%d ratio=%.2f lower_peak=%d score=%.2f\n",
                    i, get<1>(vec[i]), abs(get<0>(vec[i])),
                    k, get<1>(vec[k]), abs(get<0>(vec[k])),
                    diff, ratio, lower_peak, score);

            if (score > best_leg_score) {
                best_leg_score = score;
                best_leg_i = i;
                best_leg_k = k;
                best_leg_diff = diff;
                best_leg_lower_peak = lower_peak;
            }
        }

        // If we found a valid Legacy candidate
        if (best_leg_i >= 0 && best_leg_k >= 0) {
            d_frame_start = best_leg_lower_peak + 1;
            mode = "Legacy-mode-plateau";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] Legacy-mode-plateau SELECTED: best_i=%d(idx=%d) best_k=%d(idx=%d) best_diff=%d best_lower_peak=%d d_frame_start=%d score=%.2f\n",
                    best_leg_i, get<1>(vec[best_leg_i]), best_leg_k, get<1>(vec[best_leg_k]),
                    best_leg_diff, best_leg_lower_peak, d_frame_start, best_leg_score);
            valid = true;
            return valid;
        }

        // Method 2: Use the highest correlation peak as frame start
        // ONLY use this if the peak magnitude is above the noise floor
        if (!vec.empty() && top_mag >= MIN_ABS_MAGNITUDE) {
            int peak_pos = get<1>(vec[0]);
            d_frame_start = peak_pos + 1;
            mode = "Method2-peak";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] d_frame_start=%d (%s, peak_pos=%d)\n",
                    d_frame_start, mode, peak_pos);
            valid = true;
            return valid;
        }

        // Fallback: no valid detection - return false
        d_frame_start = SYNC_LENGTH;
        d_freq_offset = 0.0f;
        return valid;
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
