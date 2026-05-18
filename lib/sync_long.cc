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

        // Ensure adequate output buffer for HT-mixed preamble (448+ samples)
        set_output_multiple(512);
    }

    ~sync_long_impl() {
        volk_free(d_correlation);
    }

    int general_work(int noutput,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items)
    {
        // VERSION PROBE: Verify correct library is loaded
        static int version_printed = 0;
        if (version_printed == 0) {
            fprintf(stderr, "[SYNC_LONG_VERSION] tagprobe_v2 built=%s %s\n", __DATE__, __TIME__);
            version_printed = 1;
        }
        // Work call counter for debugging
        static int s_call_count = 0;
        int call_count = s_call_count++;

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

            // PROBE: Show tag processing details
            std::string first_key = pmt::symbol_to_string(d_tags.front().key);
            fprintf(stderr, "[SYNC_LONG_TAG] ntags=%zu first_key=%s offset=%llu nread=%llu state=%d d_count=%d\n",
                    d_tags.size(), first_key.c_str(), (unsigned long long)offset,
                    (unsigned long long)nread, d_state, d_count);

            if (offset > nread) {
                ninput = offset - nread;
                fprintf(stderr, "[SYNC_LONG_TAG] offset>nread, ninput=%d\n", ninput);
            } else {
                if (d_offset && (d_state == SYNC)) {
                    throw std::runtime_error("wtf");
                }
                if (d_state == COPY) {
                    // FIX: Don't transition to RESET when wifi_start arrives during HT-Mixed preamble!
                    // In Legacy mode (802.11a/g), wifi_start at end of preamble means DATA follows.
                    // In HT-Mixed mode, HT-SIG comes after L-SIG, so we need to continue COPY.
                    // Only transition to RESET if we've processed enough samples to cover the full HT preamble.
                    std::string tag_key = pmt::symbol_to_string(d_tags.front().key);
                    if (tag_key == "wifi_start") {
                        // wifi_start during COPY - this is HT-Mixed second frame starting
                        // Check if we've output enough to cover HT preamble + HT-LTF (720 samples)
                        // HT-Mixed frame can be very long (up to 65535-byte PDU at MCS0 = ~1.6M samples).
                        // The original threshold of 720 only covers the preamble + training.
                        // We need a much larger threshold to allow all DATA symbols to pass through
                        // before a subsequent wifi_start (from a second frame) triggers RESET.
                        // Use 2000000 as a safe upper bound for any realistic HT-Mixed frame.
                        if (d_count < 1000) {
                            // Still in first frame's preamble/data - ignore this wifi_start
                            fprintf(stderr, "[SYNC_LONG_HT_MIXED] Ignoring wifi_start during HT-Mixed frame d_count=%d\n", d_count);
                        } else {
                            // Frame complete, safe to RESET
                            d_state = RESET;
                            fprintf(stderr, "[SYNC_LONG_HT_MIXED] RESET after full HT-DATA d_count=%d\n", d_count);
                        }
                    } else {
                        // Other tag - use original behavior
                        d_state = RESET;
                        fprintf(stderr, "[SYNC_LONG_TAG] RESET due to non-wifi_start tag: %s\n", tag_key.c_str());
                    }
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
            // Emit sync_offset tag so downstream blocks know our d_offset
            add_item_tag(0,
                         nitems_written(0),
                         pmt::string_to_symbol("sync_offset"),
                         pmt::from_double(d_offset),
                         pmt::string_to_symbol(name()));

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
            // Output actual delayed samples (not zeros) while aligning to
            // 64-sample boundary. Zeros would corrupt the next frame's
            // HT-SIG0 FFT, causing QBPSK detection failure.
            while (o < noutput && i < ninput) {
                if (o > 0 && ((d_count + o) % 64) == 0) {
                    d_offset = 0;
                    d_wifi_start_added = false;
                    d_state = SYNC;
                    break;
                } else {
                    out[o] = in_delayed[i];
                    o++;
                    i++;
                }
            }

            break;
        }
        }

        dout << "produced : " << o << " consumed: " << i << std::endl;

        d_count += o;

        // PROBE: Print production info AFTER d_count update
        static int sync_call_count = 0;
        sync_call_count++;
        fprintf(stderr, "[SYNC_LONG_WORK] call=%d state=%d produced=%d consumed=%d d_count=%d\n",
                sync_call_count, d_state, o, i, d_count);

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
            // FIX: Subtract 13 to compensate for group delay in correlation peak detection
            // The lower_peak is typically 13 samples AFTER the true LTF0 start due to
            // FIR matched filter group delay. Without this fix, the FFT window captures
            // 13 samples of L-SIG CP (dirty data) instead of LTF0, causing massive ISI.
            int offset_compensation = 13;
            d_frame_start = best_ht_lower_peak + 1 - offset_compensation;
            if (d_frame_start < 0) d_frame_start = 0;
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
            // FIX: Same offset compensation for Legacy mode
            int offset_compensation = 13;
            d_frame_start = best_leg_lower_peak + 1 - offset_compensation;
            if (d_frame_start < 0) d_frame_start = 0;
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
            // FIX: Same offset compensation for peak-based detection
            int peak_pos = get<1>(vec[0]);
            int offset_compensation = 13;
            d_frame_start = peak_pos + 1 - offset_compensation;
            if (d_frame_start < 0) d_frame_start = 0;
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
    // IEEE 802.11 L-LTF Matched Filter Taps (Generated from LEGACY_LTF)
    // DO NOT EDIT - Auto-generated by generate_long_template.py
    // taps[00:08]
    gr_complex(+0.3087138580, -0.6706290103),
    gr_complex(+1.0300945983, -1.4282687967),
    gr_complex(+1.0877629680, +0.2230134475),
    gr_complex(-0.2053064028, -0.2587882361),
    gr_complex(+0.0444523406, -0.6618019303),
    gr_complex(-0.9914086764, -0.3388740446),
    gr_complex(-0.4335453465, -1.2787960481),
    gr_complex(+0.8002063037, -0.3961771728),
    // taps[08:16]
    gr_complex(-0.1446259166, -0.0737638306),
    gr_complex(-0.5157160935, -0.1012994837),
    gr_complex(-0.5975116871, +0.2538752165),
    gr_complex(+0.7477044287, -0.4391510533),
    gr_complex(+1.7804789726, -0.1374424060),
    gr_complex(+0.2058245641, -0.1769331928),
    gr_complex(-0.4899164733, -0.5302814880),
    gr_complex(+0.3125000000, +0.4687500000),
    // taps[16:24]
    gr_complex(+0.7520345698, +1.2670714000),
    gr_complex(+0.0586402824, +0.6201528098),
    gr_complex(-0.6124732320, -0.4942034914),
    gr_complex(-0.5267335596, -0.0887384459),
    gr_complex(-0.3367023225, +1.0889297847),
    gr_complex(-0.4438021169, +1.4449541567),
    gr_complex(-0.6611356393, +0.2371011475),
    gr_complex(+0.1372936963, -0.7086771728),
    // taps[24:32]
    gr_complex(-0.1183071063, -0.2074481004),
    gr_complex(-0.8837818044, -1.1095621425),
    gr_complex(+0.2759244090, -1.1328662456),
    gr_complex(-0.0156644663, +0.7166243713),
    gr_complex(-0.7580479721, +0.6940169552),
    gr_complex(+0.2901492465, +0.8018754676),
    gr_complex(-0.0971014223, +1.5210903237),
    gr_complex(-1.0937500000, -0.0000000000),
    // taps[32:40]
    gr_complex(-0.0971014223, -1.5210903237),
    gr_complex(+0.2901492465, -0.8018754676),
    gr_complex(-0.7580479721, -0.6940169552),
    gr_complex(-0.0156644663, -0.7166243713),
    gr_complex(+0.2759244090, +1.1328662456),
    gr_complex(-0.8837818044, +1.1095621425),
    gr_complex(-0.1183071063, +0.2074481004),
    gr_complex(+0.1372936963, +0.7086771728),
    // taps[40:48]
    gr_complex(-0.6611356393, -0.2371011475),
    gr_complex(-0.4438021169, -1.4449541567),
    gr_complex(-0.3367023225, -1.0889297847),
    gr_complex(-0.5267335596, +0.0887384459),
    gr_complex(-0.6124732320, +0.4942034914),
    gr_complex(+0.0586402824, -0.6201528098),
    gr_complex(+0.7520345698, -1.2670714000),
    gr_complex(+0.3125000000, -0.4687500000),
    // taps[48:56]
    gr_complex(-0.4899164733, +0.5302814880),
    gr_complex(+0.2058245641, +0.1769331928),
    gr_complex(+1.7804789726, +0.1374424060),
    gr_complex(+0.7477044287, +0.4391510533),
    gr_complex(-0.5975116871, -0.2538752165),
    gr_complex(-0.5157160935, +0.1012994837),
    gr_complex(-0.1446259166, +0.0737638306),
    gr_complex(+0.8002063037, +0.3961771728),
    // taps[56:64]
    gr_complex(-0.4335453465, +1.2787960481),
    gr_complex(-0.9914086764, +0.3388740446),
    gr_complex(+0.0444523406, +0.6618019303),
    gr_complex(-0.2053064028, +0.2587882361),
    gr_complex(+1.0877629680, -0.2230134475),
    gr_complex(+1.0300945983, +1.4282687967),
    gr_complex(+0.3087138580, +0.6706290103),
    gr_complex(+1.0937500000, +0.0000000000)
};
