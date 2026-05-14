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
                    // PROBE: Print at out_idx=416 (HT-SIG0 DATA position)
                    if (o == 416) {
                        fprintf(stderr, "[SYNC_LONG_OUT_IDX416] d_offset=%d out_idx=%d amp=%.6f sample=%.6f%+.6fi in_delayed[i]=%.6f%+.6fi\n",
                                d_offset, o, std::abs(out[o]), out[o].real(), out[o].imag(),
                                std::abs(in_delayed[i]), in_delayed[i].real(), in_delayed[i].imag());
                    }
                    // PROBE: Print when o is near 416 (within 10) to see if we approach but don't reach 416
                    if (o >= 410 && o <= 420 && copy_probe_count < 20) {
                        fprintf(stderr, "[SYNC_LONG_OUT_NEAR416] d_offset=%d out_idx=%d amp=%.6f\n",
                                d_offset, o, std::abs(out[o]));
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

        // PROBE: Track production per call
        static int sync_work_call = 0;
        fprintf(stderr, "[SYNC_LONG_PRODUCE] call=%d produced=%d consumed_port0=%d d_state=%d d_count=%d d_offset=%d\n",
                sync_work_call++, o, i, d_state, d_count, d_offset);

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
    // IEEE 802.11 L-LTF Matched Filter Taps (Generated from LEGACY_LTF)
    // DO NOT EDIT - Auto-generated by generate_long_template.py
    // taps[00:08]
    gr_complex(+0.0308713858, -0.0670629010),
    gr_complex(+0.1030094598, -0.1428268797),
    gr_complex(+0.1087762968, +0.0223013448),
    gr_complex(-0.0205306403, -0.0258788236),
    gr_complex(+0.0044452341, -0.0661801930),
    gr_complex(-0.0991408676, -0.0338874045),
    gr_complex(-0.0433545346, -0.1278796048),
    gr_complex(+0.0800206304, -0.0396177173),
    // taps[08:16]
    gr_complex(-0.0144625917, -0.0073763831),
    gr_complex(-0.0515716094, -0.0101299484),
    gr_complex(-0.0597511687, +0.0253875217),
    gr_complex(+0.0747704429, -0.0439151053),
    gr_complex(+0.1780478973, -0.0137442406),
    gr_complex(+0.0205824564, -0.0176933193),
    gr_complex(-0.0489916473, -0.0530281488),
    gr_complex(+0.0312500000, +0.0468750000),
    // taps[16:24]
    gr_complex(+0.0752034570, +0.1267071400),
    gr_complex(+0.0058640282, +0.0620152810),
    gr_complex(-0.0612473232, -0.0494203491),
    gr_complex(-0.0526733560, -0.0088738446),
    gr_complex(-0.0336702322, +0.1088929785),
    gr_complex(-0.0443802117, +0.1444954157),
    gr_complex(-0.0661135639, +0.0237101148),
    gr_complex(+0.0137293696, -0.0708677173),
    // taps[24:32]
    gr_complex(-0.0118307106, -0.0207448100),
    gr_complex(-0.0883781804, -0.1109562142),
    gr_complex(+0.0275924409, -0.1132866246),
    gr_complex(-0.0015664466, +0.0716624371),
    gr_complex(-0.0758047972, +0.0694016955),
    gr_complex(+0.0290149246, +0.0801875468),
    gr_complex(-0.0097101422, +0.1521090324),
    gr_complex(-0.1093750000, -0.0000000000),
    // taps[32:40]
    gr_complex(-0.0097101422, -0.1521090324),
    gr_complex(+0.0290149246, -0.0801875468),
    gr_complex(-0.0758047972, -0.0694016955),
    gr_complex(-0.0015664466, -0.0716624371),
    gr_complex(+0.0275924409, +0.1132866246),
    gr_complex(-0.0883781804, +0.1109562142),
    gr_complex(-0.0118307106, +0.0207448100),
    gr_complex(+0.0137293696, +0.0708677173),
    // taps[40:48]
    gr_complex(-0.0661135639, -0.0237101148),
    gr_complex(-0.0443802117, -0.1444954157),
    gr_complex(-0.0336702322, -0.1088929785),
    gr_complex(-0.0526733560, +0.0088738446),
    gr_complex(-0.0612473232, +0.0494203491),
    gr_complex(+0.0058640282, -0.0620152810),
    gr_complex(+0.0752034570, -0.1267071400),
    gr_complex(+0.0312500000, -0.0468750000),
    // taps[48:56]
    gr_complex(-0.0489916473, +0.0530281488),
    gr_complex(+0.0205824564, +0.0176933193),
    gr_complex(+0.1780478973, +0.0137442406),
    gr_complex(+0.0747704429, +0.0439151053),
    gr_complex(-0.0597511687, -0.0253875217),
    gr_complex(-0.0515716094, +0.0101299484),
    gr_complex(-0.0144625917, +0.0073763831),
    gr_complex(+0.0800206304, +0.0396177173),
    // taps[56:64]
    gr_complex(-0.0433545346, +0.1278796048),
    gr_complex(-0.0991408676, +0.0338874045),
    gr_complex(+0.0044452341, +0.0661801930),
    gr_complex(-0.0205306403, +0.0258788236),
    gr_complex(+0.1087762968, -0.0223013448),
    gr_complex(+0.1030094598, +0.1428268797),
    gr_complex(+0.0308713858, +0.0670629010),
    gr_complex(+0.1093750000, -0.0000000000)
};
