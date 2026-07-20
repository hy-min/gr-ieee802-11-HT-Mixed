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
// #define USRP_DEBUG_LOGS
#ifdef USRP_DEBUG_LOGS
#define USRP_LOG(...) do { fprintf(stderr,  __VA_ARGS__); } while(0)
#else
#define USRP_LOG(...) ((void)0)
#endif

#include "utils.h"
#include <gnuradio/io_signature.h>
#include <ieee802_11/sync_short.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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

// Phase 151c: parse IEEE80211_SYNC_SHORT_ADAPTIVE_EMA_ALPHA env var.
// Alpha in [0,1]: 0 = no EMA smoothing (default, preserves original behavior);
// alpha is the weight given to the freshly computed target, (1-alpha) is the
// weight of the previous threshold. Empirically some values (e.g. 0.75) can
// suppress run-to-run jitter in certain chunk partitions, but stability is not
// guaranteed across all flowgraph buffer configurations.
static float parse_adaptive_ema_alpha() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_ADAPTIVE_EMA_ALPHA");
    if (!env || !*env) return 0.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v < 0.0 || v > 1.0) {
        fprintf(stderr, "[SYNC-SHORT-ADAPTIVE] invalid EMA alpha '%s', using 0.0\n", env);
        return 0.0f;
    }
    return static_cast<float>(v);
}

// Phase 157: parse IEEE80211_SYNC_SHORT_GAP_POWER_THRESHOLD env var.
// COPY-state gap-detector power threshold. Default 0.01 (baseline, Phase 155
// showed 0.3 regresses on air). Env-gated for controlled experiments only.
static float parse_gap_power_threshold() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_GAP_POWER_THRESHOLD");
    if (!env || !*env) return 0.01f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v <= 0.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid GAP_POWER_THRESHOLD '%s', using 0.01\n", env);
        return 0.01f;
    }
    return static_cast<float>(v);
}

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
          d_threshold(threshold),
          d_use_adaptive(
              getenv("IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH")
                  ? true : false),
          d_adaptive_dump(
              getenv("IEEE80211_SYNC_SHORT_ADAPTIVE_DUMP")
                  ? true : false),
          d_corr_window_idx(0),
          d_corr_window_filled(0),
          d_adaptive_thresh(threshold),
          d_adaptive_ema_alpha(parse_adaptive_ema_alpha())
    {
        memset(d_corr_window, 0, sizeof(d_corr_window));
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

            // Phase 89 T2: track in_cor for adaptive threshold via median
            for (i = 0; i < ninput; i++) {
                d_corr_window[d_corr_window_idx] = in_cor[i];
                d_corr_window_idx = (d_corr_window_idx + 1) & 0xFFF;  // mod-4096
                if (d_corr_window_filled < 4096) d_corr_window_filled++;
            }

            // Recompute adaptive threshold (percentile_90 * 1.5) every call
            // Phase 92: switch from median*10 to percentile_90*1.5 to be robust
            // against zero contamination (88% zeros + 12% non-zero → median=0).
            // Percentile 90 ignores the zero tail and tracks actual noise level.
            float effective_threshold = d_threshold;
            if (d_use_adaptive) {
                if (d_corr_window_filled >= 4096) {
                    // Phase 147 fix: was `static float sorted_buf[4096]`. The
                    // static made this scratch buffer SHARED across ALL
                    // sync_short instances. A realtime transceiver has TWO
                    // (wifi_phy_hier RX path + RX-only chain) running on
                    // separate GNU Radio threads; their concurrent
                    // memcpy/std::sort raced -> std::sort walked OOB -> SIGSEGV
                    // (the intermittent Heisenbug). Use a stack-private buffer
                    // (fully initialized by the memcpy below) -> thread/instance
                    // safe. Offline replay has one instance so never crashed.
                    float sorted_buf[4096];
                    memcpy(sorted_buf, d_corr_window, sizeof(sorted_buf));
                    // Phase 151b diagnostic: compute a bit-pattern checksum of the
                    // window BEFORE sorting. This lets us determine whether p90
                    // jitter comes from different window content (upstream
                    // chunk-dependent in_cor) or from std::sort order instability.
                    uint64_t win_cksum = 0;
                    for (int ck = 0; ck < 4096; ck++) {
                        uint32_t bits;
                        static_assert(sizeof(bits) == sizeof(sorted_buf[ck]), "float size");
                        memcpy(&bits, &sorted_buf[ck], sizeof(bits));
                        win_cksum ^= (static_cast<uint64_t>(bits) << ((ck % 2) * 32)) ^ ck;
                    }
                    std::sort(sorted_buf, sorted_buf + d_corr_window_filled);
                    int p90_idx = d_corr_window_filled * 9 / 10;
                    float p90 = sorted_buf[p90_idx];
                    // Phase 98 (2026-07-05): floor adaptive_thresh at 0.05 because
                    // Phase 89 T5c energy gate in sync_short_fused force-zeros all
                    // noise samples (out2=0 for gated==1), making 90%+ of the
                    // window zeros. Without a non-zero floor, p90=0 causes
                    // effective_threshold to stick at 0.01, and every noise spike
                    // fires "Frame detected!".
                    //
                    // Phase 99 (2026-07-05): floor raised to 0.2 because Phase 98
                    // cable run showed residual noise spikes at 0.07-0.16 still
                    // trigger "Frame detected!" at 0.05 floor. Real L-STF boxcar
                    // values are ~1.4-2.3 (verified Phase 96-98 cable logs), so
                    // 0.2 floor is below signal but above observed noise.
                    float prev_thresh = d_adaptive_thresh;
                    d_adaptive_thresh = std::max(std::max(p90 * 1.5f, 0.01f), 0.2f);
                    // Phase 151c: optionally smooth the threshold with an EMA to
                    // filter tiny p90 run-to-run jitter caused by chunk-partition
                    // differences. Default alpha=0 preserves original behavior.
                    // alpha is the weight given to the NEW target (0..1); values
                    // close to 0 mean heavy smoothing, values close to 1 mean fast
                    // tracking.
                    if (d_adaptive_ema_alpha > 0.0f) {
                        d_adaptive_thresh = d_adaptive_ema_alpha * d_adaptive_thresh +
                                            (1.0f - d_adaptive_ema_alpha) * prev_thresh;
                    }
                    effective_threshold = d_adaptive_thresh;
                    if (d_adaptive_dump) {
                        fprintf(stderr, "[SYNC-SHORT-ADAPTIVE] filled=%d p90=%.6f "
                                "adaptive_thresh=%.6f win_cksum=%016lx\n",
                                d_corr_window_filled, p90, d_adaptive_thresh,
                                static_cast<unsigned long>(win_cksum));
                    }
                } else {
                    // Phase 89 T5c: startup gate. Until window is fully populated,
                    // we don't know the noise level. Use a pessimistic high
                    // threshold to suppress false positives.
                    effective_threshold = 3.0f;
                    if (d_adaptive_dump && d_corr_window_filled % 500 == 0) {
                        fprintf(stderr, "[SYNC-SHORT-ADAPTIVE-STARTUP] filled=%d/%d "
                                "using high_thresh=%.3f\n",
                                d_corr_window_filled, 4096, effective_threshold);
                    }
                }
            }

            int i2;
            int copy_start = -1;
            for (i2 = 0; i2 < ninput; i2++) {
                if (in_cor[i2] > effective_threshold) {
                    if (d_plateau < MIN_PLATEAU) {
                        d_plateau++;

                    } else {
                        copy_start = i2;
                        d_state = COPY;
                        d_copied = 0;
                        // Phase 110: Set freq_offset to 0 (was arg(in_abs[i2]) / 16).
                        // The ma_cc-based estimate is unreliable for CFO (random in [-π/16, π/16]
                        // range). frame_equalizer handles its own CFO/SFO via L-LTF.
                        d_freq_offset = 0;
                        d_plateau = 0;
                        insert_tag(nitems_written(0), d_freq_offset, nitems_read(0) + i2);
                        dout << "SHORT Frame!" << std::endl;
                        USRP_LOG( "[SYNC-SHORT] Frame detected! i=%d corr=%.3f thresh=%.3f freq_offset=%.6f (will be applied as CFO rotation)\n",
                                     i2, in_cor[i2], effective_threshold, d_freq_offset);
                        break;
                    }
                } else {
                    d_plateau = 0;
                }
            }

            // Phase 151e: instead of returning immediately after detection, continue
            // copying the remainder of this input chunk in COPY state within the same
            // general_work call. This removes the scheduler/chunk boundary at frame
            // start. Consumption is deterministic: exactly the processed region
            // (consume_each(copy_start + o)), independent of chunk partitioning.
            if (copy_start >= 0) {
                int o = 0;
                const int rem = ninput - copy_start;
                const gr_complex* in_rem = in + copy_start;
                float min_cor = 1e9, max_cor = -1e9;
                int max_below = 0;
                // Phase 155 REFUTED: raising this threshold 0.01 -> 0.3
                // REGRESSED USRP batch mean 200 -> 102 (real frames harmed).
                // 0.01 is load-bearing; see p155 verdict before retuning.
                const float POWER_THRESHOLD = parse_gap_power_threshold();
                while (o < rem && o < noutput && d_copied < MAX_SAMPLES) {
                    float power = std::norm(in_rem[o]);
                    bool high_power = (power >= POWER_THRESHOLD);
                    if (high_power) {
                        d_below_threshold = 0;
                    } else {
                        d_below_threshold++;
                        if (d_below_threshold > max_below) max_below = d_below_threshold;
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
                    if (in_cor[copy_start + o] < min_cor) min_cor = in_cor[copy_start + o];
                    if (in_cor[copy_start + o] > max_cor) max_cor = in_cor[copy_start + o];

                    out[o] = in_rem[o];
                    o++;
                    d_copied++;
                }

                if (o > 0) {
                    USRP_LOG( "[SYNC-SHORT] COPY work (SEARCH continuation): consumed=%d min_cor=%.4f max_cor=%.4f max_below=%d threshold=%.3f\n",
                            o, min_cor, max_cor, max_below, d_threshold);
                }

                if (d_copied == MAX_SAMPLES) {
                    d_state = SEARCH;
                }

                // Consume ONLY the processed region (skipped prefix + copied
                // samples). The while loop can stop before rem when output
                // space runs out (ninput > noutput, common in unthrottled
                // loopback with huge chunks), on gap-break, or at MAX_SAMPLES;
                // consuming the whole ninput then would silently drop frame
                // samples never written to the output (destroyed frames,
                // deterministic loopback failure caught in Phase 151e
                // regression). Unprocessed tail stays buffered for the next
                // call (COPY branch continues there) — consumption still
                // equals processed region, so chunk-invariance is preserved.
                consume_each(copy_start + o);
                return o;
            }

            consume_each(i2);
            return 0;
        }

        case COPY: {

            int o = 0;
            float min_cor = 1e9, max_cor = -1e9;
            int max_below = 0;
            // Power threshold for gap detector: noise power ~0.001 (30dB SNR),
            // signal power ~1.0. Use 0.01 as threshold (20dB below signal).
            // Phase 155 REFUTED raising this to 0.3 (USRP batch mean 200 ->
            // 102, real frames harmed); 0.01 is load-bearing — do not retune
            // without a verified model of the regression.
            const float POWER_THRESHOLD = parse_gap_power_threshold();
            while (o < ninput && o < noutput && d_copied < MAX_SAMPLES) {
                float power = std::norm(in[o]);
                bool high_power = (power >= POWER_THRESHOLD);
                // Phase 151d: gap detection should rely on power drop, not on
                // correlation staying above a threshold. During a real frame both
                // power and correlation are high; in the inter-frame gap power
                // drops. Requiring high correlation allows noise spikes to keep
                // resetting the gap counter and traps sync_short in COPY.
                if (high_power) {
                    d_below_threshold = 0;
                } else {
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
    double d_threshold;  // Phase 89 T2: made non-const for adaptive support
    const bool d_log;
    const bool d_debug;
    const unsigned int MIN_PLATEAU;
    // Phase 89 T2: adaptive threshold state
    const bool d_use_adaptive;
    const bool d_adaptive_dump;
    float d_corr_window[4096];  // ring buffer of in_cor samples
    int d_corr_window_idx;
    int d_corr_window_filled;
    float d_adaptive_thresh;
    // Phase 151c: EMA smoothing coefficient for adaptive threshold.
    // 0.0 = no smoothing (default); values near 1.0 = strong smoothing.
    const float d_adaptive_ema_alpha;
};

sync_short::sptr
sync_short::make(double threshold, unsigned int min_plateau, bool log, bool debug)
{
    return gnuradio::get_initial_sptr(
        new sync_short_impl(threshold, min_plateau, log, debug));
}
