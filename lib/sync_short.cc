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

// Phase 158: COPY-state smart re-detection ("refractory but not blind").
// Phase 157 confirmed the long COPY episode is a protective refractory period
// against noise-burst re-triggering (gap 0.3 -> 22x re-trigger explosion ->
// false wifi_start bombardment -> sync_long de-aligned). Do NOT shorten COPY
// episodes. Instead, keep the refractory but let a CLEARLY STRONGER real L-STF
// plateau arriving during a false COPY re-trigger a wifi_start tag so
// sync_long re-syncs (sync_long.cc COPY-state wifi_start handler, d_count>=1000
// -> direct SYNC). Master switch default OFF.
static bool parse_copy_redetect_enabled() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT");
    return env && *env && env[0] != '0';
}

// Strength gate: re-detect requires in_cor > FACTOR * effective threshold.
// Noise boxcar ~0.13-0.2; real L-STF boxcar ~1.4-2.3; adaptive floor 0.2.
// 5 x 0.2 = 1.0 sits between. Must be > 1.0.
static float parse_copy_redetect_factor() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT_FACTOR");
    if (!env || !*env) return 5.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v <= 1.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid COPY_REDETECT_FACTOR '%s', using 5.0\n", env);
        return 5.0f;
    }
    return static_cast<float>(v);
}

// Power-EMA gate ceiling: re-detect armed only when the COPY-state power EMA
// (alpha 1/512) is below this. Trap noise ~0.005-0.02; real frame power 3-28.
// This is what distinguishes "L-STF of a NEW frame while trapped" from "L-LTF /
// CP correlation inside a correctly-detected frame" (both have strong corr).
static float parse_copy_redetect_ema_max() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT_EMA_MAX");
    if (!env || !*env) return 0.5f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v <= 0.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid COPY_REDETECT_EMA_MAX '%s', using 0.5\n", env);
        return 0.5f;
    }
    return static_cast<float>(v);
}

// Phase 158 diagnostic: per-COPY-episode stats at episode end (opt-in).
static bool parse_copy_redetect_diag() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_COPY_REDETECT_DIAG");
    return env && *env && env[0] != '0';
}

// Phase 159: trigger-strength margin for plateau counting (opt-in, default 1.0
// = legacy). On-air DIAG (2026-08-04, p159 verdict): trap episodes have
// max_cor 0.26-0.36 (1.3-1.8x the 0.2 floor) while real frames trigger at
// max_cor >= 500 — the 0.4-10 band is empty. A 2.5x margin gate therefore
// rejects ALL noise traps (46% of sync_long's diet; each forces a FAST_SYNC
// restart or HT_MIXED ignore on the NEXT real frame) while losing zero real
// frames. Missed frames (22%) never plateau above the un-margined threshold,
// so they are unaffected.
static float parse_trigger_margin() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_TRIGGER_MARGIN");
    if (!env || !*env) return 1.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v < 1.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid TRIGGER_MARGIN '%s', using 1.0\n", env);
        return 1.0f;
    }
    return static_cast<float>(v);
}

// Phase 162b: absolute max_cor floor for wifi_start emission (opt-in,
// default 0.0 = OFF). On-air DIAG (2026-08-07, 4x 300s runs, 13114 episodes):
// noise detections have episode max_cor < 100; real frames >= 593. The
// [100, 500] band is empty (9/13114). The relative gates (adaptive threshold
// x margin) cannot reject strong noise bursts during storm runs (p90 inflates
// and bursts still cross); an absolute floor at the plateau peak can.
// Mechanism attacked: noise-detection storms (43..1548 per 300s, 36x swing)
// feed sync_long false frame-starts, killing real frames mid-flight (arrival
// anti-correlates with noise-detection count). NOTE: the absolute value is
// config-dependent (rx-scale/rx-gain); 200 suits the standard testbed
// (rx-scale 40, rx-gain 31.5, real-frame max_cor ~600).
static float parse_min_cor_floor() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_MIN_COR_FLOOR");
    if (!env || !*env) return 0.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v < 0.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid MIN_COR_FLOOR '%s', using 0.0\n", env);
        return 0.0f;
    }
    float f = static_cast<float>(v);
    if (f > 0.0f) {
        fprintf(stderr, "[SYNC-SHORT] IEEE80211_SYNC_SHORT_MIN_COR_FLOOR=%.1f "
                "(absolute max_cor floor for wifi_start emission)\n", f);
    }
    return f;
}

// Phase 163: confirm gate (opt-in, default OFF). On-air paired measurement
// (2026-08-07, 805 episodes): trigger-point correlation OVERLAPS noise (real
// p5=27.4/p50=37.4 vs noise max 23.3; ramp ratio p50=17.6x), so NO trigger-
// point gate can work (the 162b failure). Post-ramp (episode) peak separates
// cleanly: real ~600 vs noise <=40, band [100,500] empty. At the plateau
// trigger, this gate peeks in_cor over [i2, i2+K) (read-only lookahead within
// the current chunk); a full window with peak < floor is rejected (no tag, no
// COPY — the noise episode is consumed as SEARCH, never reaching sync_long).
// A window extending past the chunk end defaults to CONFIRM (never drop a
// possibly-real frame on an edge). Confirmed frames forward byte-identically
// to the OFF path (no buffer, no tag shift). NOTE (2026-08-10): NOT CONFIRMED
// on USRP realtime — N=8 ABAB DS -3.4 (p=0.032), garbage L-SIG +12; the edge
// default-confirm leaks sustained storm bursts straddling chunk boundaries.
// Kept opt-in default OFF. See 2026-08-10-phase163 verdict.
static float parse_confirm_floor() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_CONFIRM_FLOOR");
    if (!env || !*env) return 0.0f;
    char* end = nullptr;
    double v = std::strtod(env, &end);
    if (end == env || v < 0.0) {
        fprintf(stderr, "[SYNC-SHORT] invalid CONFIRM_FLOOR '%s', using 0.0\n", env);
        return 0.0f;
    }
    float f = static_cast<float>(v);
    if (f > 0.0f) {
        fprintf(stderr, "[SYNC-SHORT] IEEE80211_SYNC_SHORT_CONFIRM_FLOOR=%.1f "
                "(post-ramp confirm gate for wifi_start emission)\n", f);
    }
    return f;
}

static int parse_confirm_k() {
    const char* env = getenv("IEEE80211_SYNC_SHORT_CONFIRM_K");
    if (!env || !*env) return 48;
    int v = atoi(env);
    if (v < 16 || v > 64) {
        fprintf(stderr, "[SYNC-SHORT] invalid CONFIRM_K '%s', using 48\n", env);
        return 48;
    }
    return v;
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
          d_adaptive_ema_alpha(parse_adaptive_ema_alpha()),
          d_copy_redetect(parse_copy_redetect_enabled()),
          d_copy_redetect_factor(parse_copy_redetect_factor()),
          d_copy_redetect_ema_max(parse_copy_redetect_ema_max()),
          d_copy_redetect_diag(parse_copy_redetect_diag()),
          d_trigger_margin(parse_trigger_margin()),
          d_min_cor_floor(parse_min_cor_floor()),
          d_confirm_floor(parse_confirm_floor()),
          d_confirm_k(parse_confirm_k())
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

            // Phase 160: the adaptive window is now TRAILING — it is filled
            // AFTER the scan (below), only with samples that were actually
            // scanned. Previously the whole current chunk was loaded BEFORE
            // scanning (look-ahead), so a strong frame's own ~2000-sample
            // correlation region (boxcar ~646, >10% of the 4096 window)
            // pushed p90 to the frame's own level and the threshold
            // (p90*1.5 ~ 969) killed the frame's detection — the ~28%
            // realtime miss rate on frames with perfect L-STF strength
            // (missed 646.1 == detected 646.4 offline evidence).
            (void)i;

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
                // Phase 159: trigger-strength margin — noise traps only cross
                // the bare threshold (1.3-1.8x floor), real frames cross by
                // ~2500x; margin in the empty band kills traps, keeps frames.
                if (in_cor[i2] > effective_threshold * d_trigger_margin) {
                    if (in_cor[i2] > d_plateau_max_cor) {
                        d_plateau_max_cor = in_cor[i2];
                    }
                    if (d_plateau < MIN_PLATEAU) {
                        d_plateau++;

                    } else {
                        // Phase 162b: absolute max_cor floor — reject weak
                        // detections (noise bursts) BEFORE they reach
                        // sync_long. Real frames peak ~600, noise <~100.
                        if (d_min_cor_floor > 0.0f &&
                            d_plateau_max_cor < d_min_cor_floor) {
                            if (d_debug) {
                                fprintf(stderr, "[SYNC-SHORT] weak detection rejected: "
                                        "plateau_max_cor=%.3f < floor=%.1f\n",
                                        d_plateau_max_cor, d_min_cor_floor);
                            }
                            d_plateau = 0;
                            d_plateau_max_cor = 0.0f;
                            continue;  // keep scanning; do NOT emit a tag
                        }
                        if (d_confirm_floor > 0.0f) {
                            // Phase 163: confirm gate — peek the post-ramp
                            // correlation over the confirm window [i2, i2+K)
                            // (lookahead within the current chunk, read-only).
                            // Real frames ramp to ~600; noise stays <=40.
                            //   peak < floor (full window seen) -> reject: no
                            //     tag, no COPY; keep scanning (noise episode is
                            //     consumed as SEARCH = dropped from the stream).
                            //   window past chunk end -> default CONFIRM (rare;
                            //     never drop a possibly-real frame on an edge).
                            float peak = d_plateau_max_cor;
                            const int avail = ninput - i2;
                            const int win = std::min(d_confirm_k, avail);
                            for (int w = 0; w < win; w++) {
                                if (in_cor[i2 + w] > peak) peak = in_cor[i2 + w];
                            }
                            if (peak < d_confirm_floor && avail >= d_confirm_k) {
                                if (d_copy_redetect_diag) {
                                    fprintf(stderr, "[P163] episode REJECTED peak=%.1f < floor=%.1f\n",
                                            peak, d_confirm_floor);
                                }
                                d_plateau = 0;
                                d_plateau_max_cor = 0.0f;
                                continue;  // keep scanning; no tag, no COPY
                            }
                            if (d_copy_redetect_diag) {
                                fprintf(stderr, "[P163] episode CONFIRMED peak=%.1f (floor=%.1f)\n",
                                        peak, d_confirm_floor);
                            }
                        }
                        copy_start = i2;
                        d_state = COPY;
                        d_copied = 0;
                        // Phase 110: Set freq_offset to 0 (was arg(in_abs[i2]) / 16).
                        // The ma_cc-based estimate is unreliable for CFO (random in [-π/16, π/16]
                        // range). frame_equalizer handles its own CFO/SFO via L-LTF.
                        d_freq_offset = 0;
                        // Phase 163: paired trigger-vs-episode DIAG. The 162b
                        // post-mortem showed the episode-max separation band
                        // does NOT transfer to the trigger point (correlation
                        // still ramping). Log the plateau-peak at emission so
                        // the trigger-point distribution can be measured and
                        // joined with episode_end max_cor via `start`.
                        if (d_copy_redetect_diag) {
                            fprintf(stderr, "[P158-DIAG] trigger start=%llu trigger_cor=%.4f\n",
                                    (unsigned long long)(nitems_read(0) + i2), d_plateau_max_cor);
                        }
                        d_plateau = 0;
                        d_plateau_max_cor = 0.0f;
                        // Phase 158: arm re-detection for the new COPY episode.
                        d_redetect_plateau = 0;
                        d_redetect_cooldown = false;
                        d_redetect_seen_drop = false;
                        // Phase 158 diag: reset per-episode accumulators.
                        d_episode_len = 0; d_episode_max_cor = 0.0f;
                        d_episode_max_plateau = 0; d_episode_cur_plateau = 0;
                        d_episode_start = nitems_read(0) + i2;
                        d_state = COPY;
                        insert_tag(nitems_written(0), d_freq_offset, nitems_read(0) + i2);
                        dout << "SHORT Frame!" << std::endl;
                        USRP_LOG( "[SYNC-SHORT] Frame detected! i=%d corr=%.3f thresh=%.3f freq_offset=%.6f (will be applied as CFO rotation)\n",
                                     i2, in_cor[i2], effective_threshold, d_freq_offset);
                        break;
                    }
                } else {
                    d_plateau = 0;
                    d_plateau_max_cor = 0.0f;
                }
            }

            // Phase 160: trailing-window fill — only the SCANNED prefix enters
            // the adaptive window ([0, i2) here; if a detection fired, the
            // frame body is copied, not scanned, so it never poisons p90).
            {
                const int scanned = (copy_start >= 0) ? copy_start : ninput;
                for (int w = 0; w < scanned; w++) {
                    d_corr_window[d_corr_window_idx] = in_cor[w];
                    d_corr_window_idx = (d_corr_window_idx + 1) & 0xFFF;  // mod-4096
                    if (d_corr_window_filled < 4096) d_corr_window_filled++;
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
                            if (d_copy_redetect_diag) {
                                fprintf(stderr,
                                    "[P158-DIAG] episode_end start=%llu len=%d max_cor=%.4f strong=%.4f "
                                    "ema=%.4f max_plateau=%d cause=%s\n",
                                    (unsigned long long)d_episode_start,
                            d_episode_len, d_episode_max_cor,
                                    std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
                                        d_copy_redetect_factor,
                                    d_copy_power_ema, d_episode_max_plateau, "gap");
                                d_episode_len = 0; d_episode_max_cor = 0.0f;
                                d_episode_max_plateau = 0; d_episode_cur_plateau = 0;
                            }
                            USRP_LOG( "[SYNC-SHORT] Gap detected after %d samples (power=%.4f), transitioning to SEARCH\n",
                                    o, power);
                            break;
                        }
                    }
                    if (in_cor[copy_start + o] < min_cor) min_cor = in_cor[copy_start + o];
                    if (in_cor[copy_start + o] > max_cor) max_cor = in_cor[copy_start + o];

                    // Phase 158
                    copy_redetect_step(power, in_cor[copy_start + o],
                                       nitems_written(0) + o,
                                       nitems_read(0) + copy_start + o);

                    out[o] = in_rem[o];
                    o++;
                    d_copied++;
                }

                if (o > 0) {
                    USRP_LOG( "[SYNC-SHORT] COPY work (SEARCH continuation): consumed=%d min_cor=%.4f max_cor=%.4f max_below=%d threshold=%.3f\n",
                            o, min_cor, max_cor, max_below, d_threshold);
                }

                if (d_copied == MAX_SAMPLES) {
                    if (d_copy_redetect_diag) {
                        fprintf(stderr,
                            "[P158-DIAG] episode_end start=%llu len=%d max_cor=%.4f strong=%.4f "
                            "ema=%.4f max_plateau=%d cause=%s\n",
                            (unsigned long long)d_episode_start,
                            d_episode_len, d_episode_max_cor,
                            std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
                                d_copy_redetect_factor,
                            d_copy_power_ema, d_episode_max_plateau, "max_samples");
                        d_episode_len = 0; d_episode_max_cor = 0.0f;
                        d_episode_max_plateau = 0; d_episode_cur_plateau = 0;
                    }
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
                        if (d_copy_redetect_diag) {
                            fprintf(stderr,
                                "[P158-DIAG] episode_end start=%llu len=%d max_cor=%.4f strong=%.4f "
                                "ema=%.4f max_plateau=%d cause=%s\n",
                                (unsigned long long)d_episode_start,
                            d_episode_len, d_episode_max_cor,
                                std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
                                    d_copy_redetect_factor,
                                d_copy_power_ema, d_episode_max_plateau, "gap");
                            d_episode_len = 0; d_episode_max_cor = 0.0f;
                            d_episode_max_plateau = 0; d_episode_cur_plateau = 0;
                        }
                        USRP_LOG( "[SYNC-SHORT] Gap detected after %d samples (power=%.4f), transitioning to SEARCH\n",
                                o, power);
                        break;
                    }
                }
                if (in_cor[o] < min_cor) min_cor = in_cor[o];
                if (in_cor[o] > max_cor) max_cor = in_cor[o];

                // Phase 158
                copy_redetect_step(power, in_cor[o],
                                   nitems_written(0) + o, nitems_read(0) + o);

                out[o] = in[o];  // CFO compensation disabled - no real CFO in simulation
                o++;
                d_copied++;
            }

            if (o > 0) {
                USRP_LOG( "[SYNC-SHORT] COPY work: consumed=%d min_cor=%.4f max_cor=%.4f max_below=%d threshold=%.3f\n",
                        o, min_cor, max_cor, max_below, d_threshold);
            }

            if (d_copied == MAX_SAMPLES) {
                if (d_copy_redetect_diag) {
                    fprintf(stderr,
                        "[P158-DIAG] episode_end start=%llu len=%d max_cor=%.4f strong=%.4f "
                        "ema=%.4f max_plateau=%d cause=%s\n",
                        d_episode_len, d_episode_max_cor,
                        std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
                            d_copy_redetect_factor,
                        d_copy_power_ema, d_episode_max_plateau, "max_samples");
                    d_episode_len = 0; d_episode_max_cor = 0.0f;
                    d_episode_max_plateau = 0; d_episode_cur_plateau = 0;
                }
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

    // Phase 158: COPY-state smart re-detection step ("refractory but not blind").
    // Called once per copied sample from both COPY loops. See verdict:
    // docs/superpowers/notes/2026-07-19-phase157-refractory-model-verdict.md
    // out_idx/in_idx are the ABSOLUTE stream positions of this sample for tagging.
    inline void copy_redetect_step(float power, float cor, uint64_t out_idx, uint64_t in_idx)
    {
        if (d_copy_redetect_diag) {
            const float strong_thresh_d =
                std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
                d_copy_redetect_factor;
            d_episode_len++;
            if (cor > d_episode_max_cor) d_episode_max_cor = cor;
            if (cor > strong_thresh_d) {
                d_episode_cur_plateau++;
                if (d_episode_cur_plateau > d_episode_max_plateau)
                    d_episode_max_plateau = d_episode_cur_plateau;
            } else {
                d_episode_cur_plateau = 0;
            }
        }
        if (d_copy_redetect || d_copy_redetect_diag) {
            d_copy_power_ema += (power - d_copy_power_ema) * (1.0f / 512.0f);
        }
        if (!d_copy_redetect) return;
        if (d_redetect_cooldown && d_copy_power_ema >= d_copy_redetect_ema_max) {
            d_redetect_cooldown = false;
        }
        const float strong_thresh =
            std::max(d_adaptive_thresh, static_cast<float>(d_threshold)) *
            d_copy_redetect_factor;
        if (cor <= strong_thresh) {
            d_redetect_seen_drop = true;
            d_redetect_plateau = 0;
            return;
        }
        if (!d_redetect_seen_drop || d_redetect_cooldown ||
            d_copy_power_ema >= d_copy_redetect_ema_max) {
            d_redetect_plateau = 0;
            return;
        }
        if (d_redetect_plateau < static_cast<int>(MIN_PLATEAU)) {
            d_redetect_plateau++;
            return;
        }
        // FIRE: clearly stronger sustained L-STF plateau inside a false COPY.
        // Re-tag frame start here; sync_long's COPY-state wifi_start handler
        // (d_count >= 1000) re-syncs from this point. Episode NOT shortened.
        insert_tag(out_idx, 0.0, in_idx);
        d_copied = 0;  // new frame gets the full MAX_SAMPLES budget
        d_below_threshold = 0;
        d_plateau = 0;
        d_redetect_plateau = 0;
        d_redetect_cooldown = true;
        char p158buf[192];
        snprintf(p158buf, sizeof(p158buf),
                 "[SYNC-SHORT-P158] COPY re-detect: out=%llu corr=%.4f "
                 "strong_thresh=%.4f ema=%.4f\n",
                 (unsigned long long)out_idx, cor, strong_thresh, d_copy_power_ema);
        USRP_LOG("%s", p158buf);
        fprintf(stderr, "%s", p158buf);
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
    // Phase 158: COPY-state smart re-detection state (opt-in).
    const bool d_copy_redetect;
    const float d_copy_redetect_factor;
    const float d_copy_redetect_ema_max;
    const bool d_copy_redetect_diag;
    const float d_trigger_margin;   // Phase 159: plateau gate = margin x threshold
    // Phase 162b: absolute max_cor floor for wifi_start emission (0 = OFF)
    const float d_min_cor_floor;
    float d_plateau_max_cor = 0.0f; // peak in_cor over the current plateau run
    // Phase 163: buffered confirm gate (opt-in). d_confirm_floor = 0 -> OFF.
    const float d_confirm_floor;
    const int d_confirm_k;
    // Episode diagnostic accumulators (active when d_copy_redetect_diag).
    int d_episode_len = 0;          // samples copied this COPY episode
    float d_episode_max_cor = 0.0f; // max in_cor this episode
    int d_episode_max_plateau = 0;  // max consecutive samples with cor > strong gate
    int d_episode_cur_plateau = 0;
    uint64_t d_episode_start = 0;   // absolute input position of episode trigger
    float d_copy_power_ema = 0.0f;      // EMA (alpha 1/512) of COPY-state sample power
    int d_redetect_plateau = 0;         // consecutive above-strong-threshold samples
    bool d_redetect_cooldown = false;   // set after a re-detect fires; clears when EMA >= EMA_MAX
    bool d_redetect_seen_drop = false;  // corr dropped below strong gate at least once since COPY entry
};

sync_short::sptr
sync_short::make(double threshold, unsigned int min_plateau, bool log, bool debug)
{
    return gnuradio::get_initial_sptr(
        new sync_short_impl(threshold, min_plateau, log, debug));
}
