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
// USRP debug log control - uncomment to enable verbose logs
#define USRP_DEBUG_LOGS
#ifdef USRP_DEBUG_LOGS
#define USRP_LOG(...) do { fprintf(stderr,  __VA_ARGS__); } while(0)
#else
#define USRP_LOG(...) ((void)0)
#endif

#include "utils.h"
#include <gnuradio/io_signature.h>
#include <ieee802_11/sync_short.h>

#include <iostream>

using namespace gr::ieee802_11;

// HT-Mixed mode: L-SIG(80) + HT-SIG(160) + HT-STF(80) + HT-LTF(160+) = ~480+ samples after L-STF
// CRITICAL FIX: MIN_GAP was 1200, shorter than typical HT-Mixed frames (~1800+ samples).
// This caused false re-detections within the same frame, resetting d_copied and
// preventing proper detection of the next frame.
//
// Fix strategy:
// 1. Remove the re-detection in COPY (don't emit tags within a frame).
// 2. Add a gap detector: when correlation drops below threshold for GAP_THRESHOLD
//    consecutive samples, transition to SEARCH. This handles inter-frame gaps.
//    The threshold (500) is larger than L-LTF (160) but smaller than typical gaps.
// 3. In SEARCH, detect the next frame normally.
//
// For OFDM data symbols, auto-correlation spikes from the CP occur every 80 samples,
// preventing gap detector from firing during valid frame data.
static const int GAP_THRESHOLD = 500;
static const int MAX_SAMPLES = 5400 * 80;

class sync_short_impl : public sync_short
{

public:
    sync_short_impl(double threshold, unsigned int min_plateau, bool log, bool debug)
        : block("sync_short",
                gr::io_signature::make3(
                    3, 3, sizeof(gr_complex), sizeof(gr_complex), sizeof(float)),
                gr::io_signature::make(1, 1, sizeof(gr_complex))),
          d_log(log),
          d_debug(debug),
          d_state(SEARCH),
          d_plateau(0),
          d_freq_offset(0),
          d_copied(0),
          d_below_threshold(0),
          MIN_PLATEAU(min_plateau),
          d_threshold(threshold)
    {

        set_tag_propagation_policy(block::TPP_DONT);
    }

    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items)
    {

        USRP_LOG( "[SYNC-SHORT] general_work called: noutput=%d ninput=%d threshold=%.3f state=%d\n",
                     noutput_items, ninput_items[0], d_threshold, d_state);

        const gr_complex* in = (const gr_complex*)input_items[0];
        const gr_complex* in_abs = (const gr_complex*)input_items[1];
        const float* in_cor = (const float*)input_items[2];
        gr_complex* out = (gr_complex*)output_items[0];

        int noutput = noutput_items;
        int ninput =
            std::min(std::min(ninput_items[0], ninput_items[1]), ninput_items[2]);

        // dout << "SHORT noutput : " << noutput << " ninput: " << ninput_items[0] <<
        // std::endl;

        switch (d_state) {

        case SEARCH: {
            int i;

            for (i = 0; i < ninput; i++) {
                if (in_cor[i] > d_threshold) {
                    if (d_plateau < MIN_PLATEAU) {
                        d_plateau++;

                    } else {
                        d_state = COPY;
                        d_copied = 0;
                        d_freq_offset = arg(in_abs[i]) / 16;
                        d_plateau = 0;
                        insert_tag(nitems_written(0), d_freq_offset, nitems_read(0) + i);
                        dout << "SHORT Frame!" << std::endl;
                        USRP_LOG( "[SYNC-SHORT] Frame detected! i=%d corr=%.3f freq_offset=%.6f (will be applied as CFO rotation)\n",
                                     i, in_cor[i], d_freq_offset);
                        break;
                    }
                } else {
                    d_plateau = 0;
                }
            }

            consume_each(i);
            return 0;
        }

        case COPY: {

            int o = 0;
            float min_cor = 1e9, max_cor = -1e9;
            int max_below = 0;
            // Power threshold for gap detector: noise power ~0.001 (30dB SNR),
            // signal power ~1.0. Use 0.01 as threshold (20dB below signal).
            const float POWER_THRESHOLD = 0.01f;
            while (o < ninput && o < noutput && d_copied < MAX_SAMPLES) {
                float power = std::norm(in[o]);
                bool high_correlation = (in_cor[o] > d_threshold);
                bool high_power = (power >= POWER_THRESHOLD);
                // CRITICAL FIX: Only consider it a valid signal spike if BOTH
                // correlation AND power are high. During noise-only gaps, the
                // normalized correlation can spike artificially when instantaneous
                // noise power is low (division by small number). Requiring high
                // power prevents false gap-counter resets.
                if (high_correlation && high_power) {
                    if (d_plateau < MIN_PLATEAU) {
                        d_plateau++;
                    } else {
                        // Sustained correlation above threshold with real signal power.
                        // Reset gap detector.
                        d_below_threshold = 0;
                    }
                } else {
                    d_plateau = 0;
                    d_below_threshold++;
                    if (d_below_threshold > max_below) max_below = d_below_threshold;
                    // Gap detector: if signal stays weak for GAP_THRESHOLD consecutive
                    // samples, the frame has ended. Transition to SEARCH.
                    if (d_below_threshold >= GAP_THRESHOLD) {
                        d_state = SEARCH;
                        d_below_threshold = 0;
                        d_copied = 0;
                        d_plateau = 0;
                        USRP_LOG( "[SYNC-SHORT] Gap detected after %d samples (power=%.4f), transitioning to SEARCH\n",
                                o, power);
                        break;
                    }
                }
                if (in_cor[o] < min_cor) min_cor = in_cor[o];
                if (in_cor[o] > max_cor) max_cor = in_cor[o];

                out[o] = in[o];  // CFO compensation disabled - no real CFO in simulation
                o++;
                d_copied++;
            }

            if (o > 0) {
                USRP_LOG( "[SYNC-SHORT] COPY work: consumed=%d min_cor=%.4f max_cor=%.4f max_below=%d threshold=%.3f\n",
                        o, min_cor, max_cor, max_below, d_threshold);
            }

            if (d_copied == MAX_SAMPLES) {
                d_state = SEARCH;
            }

            dout << "SHORT copied " << o << std::endl;

            consume_each(o);
            return o;
        }
        }

        throw std::runtime_error("sync short: unknown state");
        return 0;
    }

    void insert_tag(uint64_t item, double freq_offset, uint64_t input_item)
    {
        mylog("frame start at in: {} out: {}", item, input_item);

        const pmt::pmt_t key = pmt::string_to_symbol("wifi_start");
        const pmt::pmt_t value = pmt::from_double(freq_offset);
        const pmt::pmt_t srcid = pmt::string_to_symbol(name());
        add_item_tag(0, item, key, value, srcid);
    }

private:
    enum { SEARCH, COPY } d_state;
    int d_copied;
    int d_plateau;
    int d_below_threshold;
    float d_freq_offset;
    const double d_threshold;
    const bool d_log;
    const bool d_debug;
    const unsigned int MIN_PLATEAU;
};

sync_short::sptr
sync_short::make(double threshold, unsigned int min_plateau, bool log, bool debug)
{
    return gnuradio::get_initial_sptr(
        new sync_short_impl(threshold, min_plateau, log, debug));
}
