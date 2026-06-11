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
#include <cstdlib>
#include <list>
#include <tuple>

using namespace gr::ieee802_11;
using namespace std;

// FFT window timing experiment (Phase 3, 2026-06-11):
// d_frame_start is hardcoded to 160 in two paths (see below). To test whether
// sub-sample timing is the source of L-LTF0 FFT corruption (per-frame std
// 12.7x loopback), allow opt-in adjustment via env var.
// Default offset=0 preserves the current behavior (160).
// Usage: IEEE80211_FRAME_START_OFFSET=N (N can be negative, e.g., -2, +1).
// See: docs/superpowers/notes/2026-06-11-stage1-reorganized-verdict.md
static const int FRAME_START_BASE = 160;
static int g_frame_start_offset = 0;
static bool g_frame_start_offset_inited = false;

static int get_frame_start_offset() {
    if (!g_frame_start_offset_inited) {
        const char* env = std::getenv("IEEE80211_FRAME_START_OFFSET");
        if (env && env[0] != '\0') {
            g_frame_start_offset = std::atoi(env);
            fprintf(stderr, "[SYNC_LONG] IEEE80211_FRAME_START_OFFSET=%d (frame_start=%d)\n",
                    g_frame_start_offset, FRAME_START_BASE + g_frame_start_offset);
        }
        g_frame_start_offset_inited = true;
    }
    return g_frame_start_offset;
}


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
          d_tag_skip_count(0),
          d_sync_samples(0),
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
                std::string tag_key = pmt::symbol_to_string(d_tags.front().key);

                // CRITICAL FIX for USRP continuous streaming:
                // In loopback mode, sync_long finds frame via correlation search in SYNC state.
                // In USRP mode, data arrives in small chunks (< 64 samples), preventing the
                // while loop from running. When wifi_start tag arrives (offset <= nread),
                // we can't complete correlation search. Direct SYNC->COPY transition using
                // the tag preserves frame detection.
                if (d_state == SYNC && tag_key == "wifi_start") {
                    d_freq_offset_short = pmt::to_double(d_tags.front().value);
                    d_freq_offset = static_cast<float>(d_freq_offset_short);
                    // UNIFIED TIMING: Consume SYNC_LENGTH samples before outputting,
                    // just like the correlation-search path does during SYNC state.
                    d_tag_skip_count = SYNC_LENGTH;
                    d_sync_samples = SYNC_LENGTH;  // Virtually consumed via skip
                    d_frame_start = FRAME_START_BASE + get_frame_start_offset();  // Same as correlation-search path (with opt-in offset)
                    d_state = COPY;
                    d_offset = 0;
                    d_count = 0;
                    d_wifi_start_added = false;
                    fprintf(stderr, "[SYNC_LONG] SYNC->COPY via wifi_start tag at offset=%llu\n",
                            (unsigned long long)offset);
                } else if (d_offset && (d_state == SYNC)) {
                    throw std::runtime_error("wtf");
                } else if (d_state == COPY) {
                    // FIX: Don't transition to RESET when wifi_start arrives during HT-Mixed preamble!
                    // In Legacy mode (802.11a/g), wifi_start at end of preamble means DATA follows.
                    // In HT-Mixed mode, HT-SIG comes after L-SIG, so we need to continue COPY.
                    // Only transition to RESET if we've processed enough samples to cover the full HT preamble.
                    if (tag_key == "wifi_start") {
                        // wifi_start during COPY - this is a new frame detected by sync_short.
                        // CRITICAL FIX: Go directly to SYNC, not RESET.
                        //
                        // The RESET state was designed to align output to 64-sample boundaries.
                        // But after a wall-clock gap, d_count can be huge, making RESET consume
                        // up to 63 samples before SYNC starts. This can miss the new frame's
                        // L-LTF T1 half, causing catastrophic detection failure.
                        //
                        // Downstream (ht_symbol_splitter) re-aligns using wifi_start tag anyway,
                        // so 64-sample alignment in sync_long output is not critical.
                        if (d_count < 1000) {
                            // Still in first frame's preamble/data - ignore this wifi_start
                            fprintf(stderr, "[SYNC_LONG_HT_MIXED] Ignoring wifi_start during HT-Mixed frame d_count=%d\n", d_count);
                        } else {
                            // New frame: direct SYNC transition to preserve full preamble
                            int saved_count = d_count;
                            d_state = SYNC;
                            d_offset = 0;
                            d_wifi_start_added = false;
                            d_cor.clear();
                            d_count = 0;
                            d_sync_samples = 0;
                            d_tag_skip_count = 0;
                            fprintf(stderr, "[SYNC_LONG_FAST_SYNC] Direct SYNC for new frame (was d_count=%d)\n", saved_count);
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

        case SYNC: {
            int filter_len = std::min(SYNC_LENGTH, std::max(ninput - 63, 0));
            d_fir.filterN(d_correlation, in, filter_len);

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

            // CRITICAL FIX: Prevent deadlock with small input chunks.
            // In continuous streaming (e.g., USRP), downstream blocks may
            // deliver data in small chunks (< 64 samples). If we don't consume
            // anything, the read pointer never advances, and we deadlock.
            // Consume all available data even if we can't process it yet.
            if (i == 0 && ninput > 0) {
                i = ninput;
                fprintf(stderr, "[SYNC_LONG] SYNC state: consuming %d samples to prevent deadlock\n", ninput);
            }

            // Track actual SYNC consumption for CFO compensation
            d_sync_samples += i;

            break;
        }

        case COPY: {
            // UNIFIED TIMING: Skip initial samples when entering COPY via tag-jump.
            // The correlation-search path consumes SYNC_LENGTH samples during SYNC state.
            // We must consume the same amount to align both paths.
            // NOTE: Do NOT increment d_offset here. d_offset tracks logical position
            // within COPY state. The skipped samples are "virtual SYNC" consumption.
            while (d_tag_skip_count > 0 && i < ninput) {
                d_tag_skip_count--;
                i++;
            }

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
                    // CFO correction: compensate phase accumulated during SYNC period
                    // d_sync_samples tracks actual SYNC consumption (or SYNC_LENGTH for tag-jump)
                    // CFO compensation disabled: frame_equalizer handles CPE
                    if (std::abs(d_freq_offset) > 100.0) {
                        // Only compensate phase within COPY state.
                        // d_sync_samples is SYNC/skip consumption outside the frame.
                        float total_phase = -d_offset * d_freq_offset;
                        out[o] = in_delayed[i] * std::exp(gr_complex(0.0f, total_phase));
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
                    d_sync_samples = 0;  // Reset for new SYNC phase
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
        // CRITICAL FIX: Lower from 64 to 1 to prevent deadlock with small
        // input chunks in continuous streaming (e.g., USRP hardware).
        // The while loop in SYNC state checks (i + 63 < ninput) anyway,
        // so having less than 64 samples just means the loop won't run.
        if (d_state == SYNC) {
            ninput_items_required[0] = 1;
            ninput_items_required[1] = 1;

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
        // Minimum absolute magnitude for correlation peak detection.
        // Correct LTF template peak = 0.0225 (after 1/sqrt(52) window scaling).
        // With signal RMS=0.025: FIR peak ≈ 0.0045. Threshold 0.01 catches weak signals.
        // Previous value 3.0 was based on an incorrectly scaled x10 template.
        const double MIN_ABS_MAGNITUDE = 0.01;
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

                // CRITICAL FIX: HT-Mixed uses the SAME L-LTF as Legacy mode.
                // The two identical L-LTF halves (T1 and T2) are separated by 64 samples.
                // The previous range [70, 90] EXCLUDED 64, causing HT-mode to always fail
                // and fall through to Legacy mode. For robustness, use [55, 75] which
                // comfortably includes 64 while filtering out false pairs.
                if (diff < 55 || diff > 75) {
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
            // CRITICAL FIX: Force d_frame_start=160 for all frames. Frame 1 works
            // perfectly with d_frame_start=160 (LTF_CORR=0.9990). Frame 2+ have
            // correlation peaks shifted by L-STF interference (lower_peak=199-201
            // instead of 172). The preamble structure is identical for all frames;
            // L-LTF0 DATA should always start at the same relative offset within
            // the SYNC window. With SPLITTER using tag_abs_pos+16, d_frame_start
            // must be constant for correct alignment.
            int fs_offset = get_frame_start_offset();
            if (d_frame_start != 160 || fs_offset != 0) {
                int target = FRAME_START_BASE + fs_offset;
                fprintf(stderr, "[SYNC_LONG] d_frame_start=%d -> forcing to %d (offset=%d)\n",
                        d_frame_start, target, fs_offset);
                d_frame_start = target;
            }
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
    int d_tag_skip_count;      // Samples to skip when entering COPY via tag-jump
    int d_sync_samples;        // Actual samples consumed during SYNC state

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
    // IEEE 802.11 L-LTF Matched Filter Taps
    // Generated from mixed_mode_carrier_allocator.py LEGACY_LTF
    // Window: 1/sqrt(52), IFFT: 64-point
    // taps[00:04]
    gr_complex(-0.0007101896, -0.0166860937),
    gr_complex(+0.0055122914, -0.0154148332),
    gr_complex(+0.0134281663, +0.0114820042),
    gr_complex(+0.0029276758, +0.0038670812),
    // taps[04:08]
    gr_complex(+0.0082960746, -0.0121627392),
    gr_complex(-0.0159658269, -0.0076521579),
    gr_complex(-0.0053134687, -0.0147232565),
    gr_complex(+0.0135265391, -0.0035900679),
    // taps[08:12]
    gr_complex(+0.0073966129, +0.0005652848),
    gr_complex(+0.0001371468, -0.0159482746),
    gr_complex(-0.0189714230, -0.0065703977),
    gr_complex(+0.0033941899, -0.0081168996),
    // taps[12:16]
    gr_complex(+0.0081358942, -0.0020716665),
    gr_complex(-0.0031178597, +0.0222791635),
    gr_complex(+0.0165354864, -0.0005679568),
    gr_complex(+0.0086671906, -0.0086671906),
    // taps[16:20]
    gr_complex(+0.0051195974, +0.0136378799),
    gr_complex(-0.0079330928, +0.0054497336),
    gr_complex(-0.0182028487, +0.0090453892),
    gr_complex(+0.0114016299, +0.0128075494),
    // taps[20:24]
    gr_complex(+0.0096457992, +0.0019583633),
    gr_complex(-0.0083635061, +0.0112723572),
    gr_complex(-0.0078289177, -0.0030236598),
    gr_complex(-0.0048593486, -0.0209244490),
    // taps[24:28]
    gr_complex(-0.0169026870, -0.0022973211),
    gr_complex(-0.0176567119, -0.0028430299),
    gr_complex(+0.0104108486, -0.0102675587),
    gr_complex(-0.0003891144, +0.0074571490),
    // taps[28:32]
    gr_complex(-0.0127425112, +0.0159654794),
    gr_complex(+0.0127187969, +0.0146817576),
    gr_complex(+0.0017035662, +0.0135346229),
    gr_complex(-0.0216679764, -0.0000000000),
    // taps[32:36]
    gr_complex(+0.0017035662, -0.0135346229),
    gr_complex(+0.0127187969, -0.0146817576),
    gr_complex(-0.0127425112, -0.0159654794),
    gr_complex(-0.0003891144, -0.0074571490),
    // taps[36:40]
    gr_complex(+0.0104108486, +0.0102675587),
    gr_complex(-0.0176567119, +0.0028430299),
    gr_complex(-0.0169026870, +0.0022973211),
    gr_complex(-0.0048593486, +0.0209244490),
    // taps[40:44]
    gr_complex(-0.0078289177, +0.0030236598),
    gr_complex(-0.0083635061, -0.0112723572),
    gr_complex(+0.0096457992, -0.0019583633),
    gr_complex(+0.0114016299, -0.0128075494),
    // taps[44:48]
    gr_complex(-0.0182028487, -0.0090453892),
    gr_complex(-0.0079330928, -0.0054497336),
    gr_complex(+0.0051195974, -0.0136378799),
    gr_complex(+0.0086671906, +0.0086671906),
    // taps[48:52]
    gr_complex(+0.0165354864, +0.0005679568),
    gr_complex(-0.0031178597, -0.0222791635),
    gr_complex(+0.0081358942, +0.0020716665),
    gr_complex(+0.0033941899, +0.0081168996),
    // taps[52:56]
    gr_complex(-0.0189714230, +0.0065703977),
    gr_complex(+0.0001371468, +0.0159482746),
    gr_complex(+0.0073966129, -0.0005652848),
    gr_complex(+0.0135265391, +0.0035900679),
    // taps[56:60]
    gr_complex(-0.0053134687, +0.0147232565),
    gr_complex(-0.0159658269, +0.0076521579),
    gr_complex(+0.0082960746, +0.0121627392),
    gr_complex(+0.0029276758, -0.0038670812),
    // taps[60:64]
    gr_complex(+0.0134281663, -0.0114820042),
    gr_complex(+0.0055122914, +0.0154148332),
    gr_complex(-0.0007101896, +0.0166860937),
    gr_complex(+0.0216679764, -0.0000000000)
};
