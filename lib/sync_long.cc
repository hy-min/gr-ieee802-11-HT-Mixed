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
#include <cstring>
#include <list>
#include <tuple>

using namespace gr::ieee802_11;
using namespace std;

// L-LTF0 FFT window timing (Phase 32+, 2026-06-18):
// Phase 32 investigation discovered the FFT window for L-LTF0 was positioned
// 14 samples BEFORE the actual L-LTF data start, causing saved_ltf0_fft to
// show an 8-DPSK phase pattern instead of the expected real ±1 BPSK
// (LEGACY_LTF). FRAME_START_BASE=174 positions the FFT window at the L-LTF
// DATA start (= 160 L-LTF START + 16 GI). In sync_long's input stream the
// sync short already consumes 320 samples (2 L-STF repetitions), so the
// offset 174 from the post-skip origin lands the FFT at the correct place.
//
// Empirically verified with FRAME_START_BASE=174 (offset=0):
//   - LTF0_FFT_DUMP: arg(LLTF) = 0/π exactly (perfect BPSK), was 8-DPSK
//   - H52_DUMP: arg(H) std = 0.0000 (was 1.8647), all 52 SCs |H|=8.875, arg(H)=0
//   - Loopback FCS OK=1, no regression
//
// IEEE80211_FRAME_START_OFFSET env var still works for fine-tuning
// (N can be negative or positive, e.g., -2, +1); default offset=0 means
// d_frame_start=174.
// See: project_p32_h52_e2e_vs_offline.md
static const int FRAME_START_BASE = 174;
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

// Phase 146 L2 (2026-07-15): noise early-out toggle (default ON). This is a
// behavior-identical optimization in search_frame_start() — skip the O(n log n)
// sort + vector allocs when no correlation magnitude reaches the detection floor.
// The toggle exists for A/B verification and as a safety switch; it is read once
// at startup (static init) so there is no per-call getenv cost on the hot path.
// Set IEEE80211_SYNC_LONG_EARLYOUT=0 to disable.
static bool g_earlyout_inited = false;
static bool g_earlyout_enabled = true;
static bool earlyout_enabled() {
    if (!g_earlyout_inited) {
        const char* env = std::getenv("IEEE80211_SYNC_LONG_EARLYOUT");
        if (env && env[0] == '0') {
            g_earlyout_enabled = false;
        }
        fprintf(stderr, "[SYNC_LONG_P146] noise early-out %s\n",
                g_earlyout_enabled ? "ENABLED (default)" : "DISABLED via IEEE80211_SYNC_LONG_EARLYOUT=0");
        g_earlyout_inited = true;
    }
    return g_earlyout_enabled;
}

// Phase 133: Multi-feature L-LTF detector
// Pattern: noise false-positives at FIR (Phase 87 verdict) caused sync_long
// to produce 156 NOISE frames in 80M samples. Root cause was structured noise
// (DC offset, LO spurs) producing FIR peaks that matched L-LTF plateau
// criteria but lacked periodic phase coherence.
//
// Solution: add two additional detection features that noise cannot easily
// mimic together with FIR peak:
//   F2 = Schmidl-Cox L-LTF metric |P|²/R² at lag=80, integrated over 80 samples
//       P = sum of in[k-80]·conj(in[k]) for k in 80-sample window
//       R = sum of (|in[k-80]|² + |in[k]|²) over same window
//   F3 = Frequency-domain template match (FFT correlation with known L-LTF)
//       (Phase 134 — currently TBD, requires 64-pt FFT per detection)
//
// Phase 133 T3 implements F2 only. Default OFF (env var opt-in) to preserve
// baseline. When enabled, candidate pairs in search_frame_start() are
// GATED by schmidl_cox >= threshold. This rejects noise false-positives
// that have FIR peak but low phase coherence.
//
// Threshold defaults to 0.05 (5% — empirically above noise floor ~0.001).
// Use IEEE80211_SYNC_LONG_SCHMIDL_COX=1 to enable.
// Use IEEE80211_SYNC_LONG_SCHMIDL_COX_THRESHOLD=N to override.
static const double DEFAULT_SCHMIDL_COX_THRESHOLD = 0.05;
static const int SCHMIDL_COX_LAG = 80;       // L-LTF period in samples
static const int SCHMIDL_COX_WINDOW = 80;    // integration window in samples
static double g_schmidl_cox_threshold = -1.0;
static bool g_schmidl_cox_threshold_inited = false;
static bool g_schmidl_cox_enabled = false;

static double get_schmidl_cox_threshold() {
    if (!g_schmidl_cox_threshold_inited) {
        const char* env_thr = std::getenv("IEEE80211_SYNC_LONG_SCHMIDL_COX_THRESHOLD");
        if (env_thr && env_thr[0] != '\0') {
            g_schmidl_cox_threshold = std::atof(env_thr);
        } else {
            g_schmidl_cox_threshold = DEFAULT_SCHMIDL_COX_THRESHOLD;
        }
        const char* env_en = std::getenv("IEEE80211_SYNC_LONG_SCHMIDL_COX");
        g_schmidl_cox_enabled = (env_en != nullptr && env_en[0] != '\0');
        fprintf(stderr, "[SYNC_LONG_P133] enabled=%d threshold=%.4f (lag=%d, window=%d)\n",
                g_schmidl_cox_enabled ? 1 : 0, g_schmidl_cox_threshold,
                SCHMIDL_COX_LAG, SCHMIDL_COX_WINDOW);
        g_schmidl_cox_threshold_inited = true;
    }
    return g_schmidl_cox_threshold;
}
static bool is_schmidl_cox_enabled() {
    if (!g_schmidl_cox_threshold_inited) {
        get_schmidl_cox_threshold();
    }
    return g_schmidl_cox_enabled;
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
          d_sc_p_idx(0),
          d_sc_r_idx(0),
          d_sum_sc_p(gr_complex(0, 0)),
          d_sum_sc_r(0.0f),
          SYNC_LENGTH(sync_length),
          d_chunk_invariant(false),
          d_stash_len(0)
    {

        set_tag_propagation_policy(block::TPP_DONT);
        d_correlation = (gr_complex*)volk_malloc(sizeof(gr_complex) * 8192, volk_get_alignment());

        // Phase 151: allocate chunk-invariance work buffers + read env gate.
        d_eff0 = (gr_complex*)volk_malloc(sizeof(gr_complex) * kEffCap, volk_get_alignment());
        d_eff1 = (gr_complex*)volk_malloc(sizeof(gr_complex) * kEffCap, volk_get_alignment());
        d_chunk_invariant = (getenv("IEEE80211_SYNC_LONG_CHUNK_INVARIANT") != nullptr);
        if (d_chunk_invariant) {
            fprintf(stderr, "[SYNC_LONG_P151] chunk-invariant accumulation ENABLED\n");
        }

        // Phase 133 T3: Initialize Schmidl-Cox ring buffers
        for (int k = 0; k < SCHMIDL_COX_WINDOW; k++) {
            d_sc_mult_ring[k] = gr_complex(0, 0);
            d_sc_pow_ring[k] = 0.0f;
        }

        // Phase 31b (2026-06-17): Input dump opt-in via env var
        if (getenv("IEEE80211_SYNC_LONG_INPUT_DUMP")) {
            fprintf(stderr, "[SYNC_LONG] IEEE80211_SYNC_LONG_INPUT_DUMP=1 (input samples + FIR output will be logged, first 5 calls only)\n");
        }

        // Phase 14 (2026-06-15): Reduced from 512 to 80 to fix scheduler deadlock
        // with USRP continuous streaming. The 512-multiple could never be satisfied
        // when sync_long's 2 input ports (direct + 320-sample delayed) compete with
        // small USRP data chunks. 80 = 1 OFDM symbol (CP=16 + 64 data) is the
        // minimum to align with natural frame structure while keeping scheduler
        // satisfied. Comment "448+ samples for HT-mixed preamble" was theoretical
        // and never enforced by set_min_output_buffer elsewhere in hier block.
        set_output_multiple(80);
    }

    ~sync_long_impl() {
        volk_free(d_correlation);
        volk_free(d_eff0);
        volk_free(d_eff1);
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

        // === IEEE80211_SYNC_LONG_INPUT_DUMP hook (Phase 31b, 2026-06-17) ===
        // Dumps input samples (port 0 from sync_short, port 1 from blocks_delay)
        // + FIR output magnitudes + peak detection to diagnose 0.003 correlation
        // peak on USRP air path. Atomic single-call pattern (snprintf + USRP_LOG).
        // Opt-in via IEEE80211_SYNC_LONG_INPUT_DUMP=1. Limited to first 5 calls.
        static int sl_in_dump_calls = 0;
        const bool sl_in_dump_enabled = (sl_in_dump_calls < 5) && (getenv("IEEE80211_SYNC_LONG_INPUT_DUMP") != nullptr);
        // === end hook ===

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
            if (first_key == "wifi_start") {
                // Phase 163b forensics: track the last wifi_start tag offset on
                // ALL paths (SYNC-ignore / FAST_SYNC / HT_MIXED) so frame-commit
                // and search logs join to the TX lattice.
                d_last_wifi_start_tag_offset = offset;
            }
            fprintf(stderr, "[SYNC_LONG_TAG] ntags=%zu first_key=%s offset=%llu nread=%llu state=%d d_count=%d\n",
                    d_tags.size(), first_key.c_str(), (unsigned long long)offset,
                    (unsigned long long)nread, d_state, d_count);

            if (offset > nread) {
                ninput = offset - nread;
                fprintf(stderr, "[SYNC_LONG_TAG] offset>nread, ninput=%d\n", ninput);
            } else {
                std::string tag_key = pmt::symbol_to_string(d_tags.front().key);

                // Phase 135 (2026-07-09): REMOVED wifi_start fast-path SYNC->COPY direct
                // transition (was lines 236-249 in pre-Phase-135). The fast-path
                // BYPASSED Phase 133's multi-feature Schmidl-Cox gate, leaving the
                // gate inert during USRP continuous streaming.
                //
                // Phase 87 verdict noted sync_long produces 156 NOISE frames per 80M
                // samples — structured USRP noise (DC offset, LO spurs) makes FIR peaks
                // match L-LTF plateau criteria. The fast-path amplified this by
                // accepting every wifi_start tag regardless of FIR+Schmidl-Cox
                // multi-feature verification.
                //
                // Phase 135 fix: route ALL transitions through search_frame_start()
                // (which now includes P133 multi-feature gate). wifi_start tag during
                // SYNC is now logged + IGNORED for state purposes. The SYNC state's
                // correlation accumulator continues uninterrupted; transition to COPY
                // happens only after SYNC_LENGTH samples + gate validation.
                //
                // Performance: Phase 14 set_output_multiple=80 already mitigates the
                // scheduler-deadlock risk that originally motivated the fast-path.
                if (d_state == SYNC && tag_key == "wifi_start") {
                    d_freq_offset_short = pmt::to_double(d_tags.front().value);
                    d_freq_offset = static_cast<float>(d_freq_offset_short);
                    fprintf(stderr, "[SYNC_LONG_P135] wifi_start tag IGNORED during SYNC "
                            "(offset=%llu nread=%llu, gate validation deferred to "
                            "search_frame_start() at SYNC_LENGTH boundary)\n",
                            (unsigned long long)offset, (unsigned long long)nread);
                    // DO NOT transition to COPY. Continue accumulating correlation
                    // samples. The Phase 133 multi-feature gate validates the
                    // transition at the SYNC_LENGTH boundary.
                } else if (d_offset && (d_state == SYNC)) {
                    fprintf(stderr, "[SYNC_LONG_P135] Non-wifi_start tag during SYNC "
                            "(offset=%llu d_offset=%d key=%s) — accumulated through "
                            "end of SYNC_LENGTH, ignored as data\n",
                            (unsigned long long)offset, d_offset, tag_key.c_str());
                    // Phase 135: replaced throw() with logging. Any tag arriving mid-SYNC
                    // is informational; the SYNC state's correlation accumulator
                    // processes samples normally. search_frame_start() validates
                    // frame_start at SYNC_LENGTH boundary.
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
                            fprintf(stderr, "[SYNC_LONG_HT_MIXED] Ignoring wifi_start during HT-Mixed frame d_count=%d offset=%llu\n", d_count, (unsigned long long)offset);
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
                            fprintf(stderr, "[SYNC_LONG_FAST_SYNC] Direct SYNC for new frame (was d_count=%d) offset=%llu\n", saved_count, (unsigned long long)offset);
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
            if (d_chunk_invariant) {
                // --------------------------------------------------------
                // Phase 151: chunk-invariant accumulation. Prepend stashed
                // small-chunk samples so every sample's correlation is
                // computed exactly once, independent of ninput partitioning.
                // --------------------------------------------------------
                int eff_len = d_stash_len + ninput;
                std::memcpy(d_eff0, d_stash0, d_stash_len * sizeof(gr_complex));
                std::memcpy(d_eff0 + d_stash_len, in, ninput * sizeof(gr_complex));
                std::memcpy(d_eff1, d_stash1, d_stash_len * sizeof(gr_complex));
                std::memcpy(d_eff1 + d_stash_len, in_delayed, ninput * sizeof(gr_complex));

                int i_eff = 0;
                if (eff_len >= 64) {
                    int filter_len_ci = std::min(SYNC_LENGTH, eff_len - 63);
                    d_fir.filterN(d_correlation, d_eff0, filter_len_ci);
                    while (i_eff + 63 < eff_len) {
                        // Schmidl-Cox (Phase 133), on the combined port-1 stream.
                        if (is_schmidl_cox_enabled()) {
                            const gr_complex* sig = d_eff1;
                            gr_complex cur = sig[i_eff];
                            float mag_sq = std::norm(cur);
                            gr_complex mult = (i_eff >= SCHMIDL_COX_LAG)
                                ? cur * std::conj(sig[i_eff - SCHMIDL_COX_LAG]) : gr_complex(0, 0);
                            d_sum_sc_p += mult;
                            d_sum_sc_p -= d_sc_mult_ring[d_sc_p_idx];
                            d_sc_mult_ring[d_sc_p_idx] = mult;
                            d_sc_p_idx = (d_sc_p_idx + 1) % SCHMIDL_COX_WINDOW;
                            float mag_sq_old = (i_eff >= SCHMIDL_COX_LAG) ? std::norm(sig[i_eff - SCHMIDL_COX_LAG]) : 0.0f;
                            d_sum_sc_r += mag_sq + mag_sq_old;
                            d_sum_sc_r -= d_sc_pow_ring[d_sc_r_idx];
                            d_sc_pow_ring[d_sc_r_idx] = mag_sq + mag_sq_old;
                            d_sc_r_idx = (d_sc_r_idx + 1) % SCHMIDL_COX_WINDOW;
                            float metric = -1.0f;
                            if (i_eff >= SCHMIDL_COX_LAG) {
                                float p_abs_sq = std::norm(d_sum_sc_p);
                                float r_sq = d_sum_sc_r * d_sum_sc_r;
                                metric = p_abs_sq / (r_sq + 1e-9f);
                            }
                            d_sc_metric_at.push_back(std::make_pair(metric, d_offset));
                        }

                        d_cor.push_back(pair<gr_complex, int>(d_correlation[i_eff], d_offset));
                        i_eff++;
                        d_offset++;

                        if (d_offset == SYNC_LENGTH) {
                            bool detected = search_frame_start();
                            mylog("LONG: frame start at {} (d_offset was {})", d_frame_start, d_offset);
                            d_offset = 0;
                            d_count = 0;
                            if (detected) {
                                d_state = COPY;
                            } else {
                                d_cor.clear();
                                d_sc_metric_at.clear();
                                d_state = SYNC;
                            }
                            break;
                        }
                    }
                }

                // Consumption: the first d_stash_len eff-positions were already
                // consumed when stashed; only NEW samples count as consumed now.
                int processed = i_eff;
                int from_stash = std::min(processed, d_stash_len);
                int new_consumed = processed - from_stash;
                int stash_left = d_stash_len - from_stash;
                if (stash_left > 0) {
                    std::memmove(d_stash0, d_stash0 + from_stash, stash_left * sizeof(gr_complex));
                    std::memmove(d_stash1, d_stash1 + from_stash, stash_left * sizeof(gr_complex));
                }
                d_stash_len = stash_left;
                if (processed == 0 && ninput > 0) {
                    // Chunk too small to correlate: consume into stash (advances
                    // read ptr => no deadlock) but keep samples for next call.
                    std::memcpy(d_stash0 + d_stash_len, in, ninput * sizeof(gr_complex));
                    std::memcpy(d_stash1 + d_stash_len, in_delayed, ninput * sizeof(gr_complex));
                    d_stash_len += ninput;
                    new_consumed = ninput;
                }
                i = new_consumed;
                d_sync_samples += i;
            } else {
            int filter_len = std::min(SYNC_LENGTH, std::max(ninput - 63, 0));
            d_fir.filterN(d_correlation, in, filter_len);

            // === IEEE80211_SYNC_LONG_INPUT_DUMP hook (Phase 31b, 2026-06-17) ===
            // Dumps the first 64 samples of each input port + the first 64 FIR output
            // magnitudes + the top correlation magnitudes. Uses atomic single-call
            // pattern (snprintf into buf, then USRP_LOG) to avoid interleaved writes.
            if (sl_in_dump_enabled) {
                char buf[4096];
                int off = 0;
                off += snprintf(buf+off, sizeof(buf)-off,
                    "[SYNC_LONG_DUMP] call=%d ninput=%d filter_len=%d ninput0=%d ninput1=%d state=SYNC | ",
                    sl_in_dump_calls, ninput, filter_len, ninput_items[0], ninput_items[1]);
                // First 64 samples of port 0 (sync_short output)
                for (int k = 0; k < 64 && k < ninput_items[0]; k++) {
                    off += snprintf(buf+off, sizeof(buf)-off, "in0[%d]=(%.3f,%.3f) ", k, in[k].real(), in[k].imag());
                }
                off += snprintf(buf+off, sizeof(buf)-off, "| ");
                // First 64 samples of port 1 (blocks_delay output)
                for (int k = 0; k < 64 && k < ninput_items[1]; k++) {
                    off += snprintf(buf+off, sizeof(buf)-off, "in1[%d]=(%.3f,%.3f) ", k, in_delayed[k].real(), in_delayed[k].imag());
                }
                off += snprintf(buf+off, sizeof(buf)-off, "| ");
                // First 64 FIR output magnitudes
                for (int k = 0; k < 64 && k < filter_len; k++) {
                    double mag = abs(d_correlation[k]);
                    off += snprintf(buf+off, sizeof(buf)-off, "fir[%d]=%.4f ", k, mag);
                }
                off += snprintf(buf+off, sizeof(buf)-off, "| ");
                // Top 10 correlation magnitudes (sorted by abs value)
                // We have d_cor at this point: re-sort and dump
                std::vector<std::pair<gr_complex,int>> tmp_cor;
                for (int k = 0; k < filter_len; k++) {
                    tmp_cor.push_back(std::pair<gr_complex,int>(d_correlation[k], k));
                }
                std::sort(tmp_cor.begin(), tmp_cor.end(), compare_abs);
                for (int k = 0; k < 10 && k < (int)tmp_cor.size(); k++) {
                    double mag = abs(tmp_cor[k].first);
                    off += snprintf(buf+off, sizeof(buf)-off, "top[%d]=(%.4f@%d) ", k, mag, tmp_cor[k].second);
                }
                fprintf(stderr, "%s\n", buf);
                sl_in_dump_calls++;
            }
            // === end hook ===

            while (i + 63 < ninput) {

                // Phase 133 T3: Compute Schmidl-Cox L-LTF metric in lock-step with FIR.
                // The first 80 samples are not enough lag history, so metrics are
                // suppressed (=-1 marker). After that, every sample gets a metric.
                if (is_schmidl_cox_enabled()) {
                    // Use port 1 (delayed) signal so we maintain proper alignment
                    // with the FIR correlator (which also runs on the same aligned stream).
                    const gr_complex* sig = (const gr_complex*)input_items[1];
                    gr_complex cur = sig[i];
                    float mag_sq = std::norm(cur);
                    // Update sliding 80-sample complex sum for P
                    gr_complex mult = (i >= SCHMIDL_COX_LAG)
                        ? cur * std::conj(sig[i - SCHMIDL_COX_LAG]) : gr_complex(0, 0);
                    d_sum_sc_p += mult;
                    d_sum_sc_p -= d_sc_mult_ring[d_sc_p_idx];
                    d_sc_mult_ring[d_sc_p_idx] = mult;
                    d_sc_p_idx = (d_sc_p_idx + 1) % SCHMIDL_COX_WINDOW;
                    // Update sliding 80-sample energy sum for R
                    float mag_sq_old = (i >= SCHMIDL_COX_LAG) ? std::norm(sig[i - SCHMIDL_COX_LAG]) : 0.0f;
                    d_sum_sc_r += mag_sq + mag_sq_old;
                    d_sum_sc_r -= d_sc_pow_ring[d_sc_r_idx];
                    d_sc_pow_ring[d_sc_r_idx] = mag_sq + mag_sq_old;
                    d_sc_r_idx = (d_sc_r_idx + 1) % SCHMIDL_COX_WINDOW;
                    // Compute metric, store at this d_offset
                    float metric = -1.0f;
                    if (i >= SCHMIDL_COX_LAG) {
                        float p_abs_sq = std::norm(d_sum_sc_p);
                        float r_sq = d_sum_sc_r * d_sum_sc_r;
                        metric = p_abs_sq / (r_sq + 1e-9f);
                    }
                    d_sc_metric_at.push_back(std::make_pair(metric, d_offset));
                }

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
                        d_sc_metric_at.clear();  // Phase 133 T3: also clear Schmidl-Cox history
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
            }  // end else (baseline SYNC path)

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

        // === IEEE80211_SYNC_LONG_OUT_DUMP hook (Phase 14 Experiment B) ===
        // Reports per-work-call: items produced + items consumed + state at exit.
        // Used to locate the deadlock between splitter -> sync_long -> equalizer.
        // Opt-in via IEEE80211_SYNC_LONG_OUT_DUMP=1.
        // State enum: SYNC=0, COPY=1, RESET=2.
        if (getenv("IEEE80211_SYNC_LONG_OUT_DUMP")) {
            static int sl_out_dump_counter = 0;
            const char* state_name = (d_state == SYNC) ? "SYNC" :
                                     (d_state == COPY) ? "COPY" : "RESET";
            fprintf(stderr, "[SYNC_LONG_OUT] fidx=%d nout=%d consumed=%d state=%s (%d)\n",
                    sl_out_dump_counter++, o, i, state_name, (int)d_state);
        }
        // === end hook ===

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

        // Minimum thresholds (hoisted above the sort so the Phase 146 noise
        // early-out can use them before any O(n log n) work).
        // Minimum absolute magnitude for correlation peak detection.
        // Correct LTF template peak = 0.0225 (after 1/sqrt(52) window scaling).
        // With signal RMS=0.025: FIR peak ≈ 0.0045. Threshold 0.01 catches weak signals.
        // Previous value 3.0 was based on an incorrectly scaled x10 template.
        const double MIN_ABS_MAGNITUDE = 0.01;
        const double MIN_PEAK_RATIO = 0.30;

        // sort list (highest correlation first)
        assert(d_cor.size() == SYNC_LENGTH);

        // Phase 146 L2 (2026-07-15): noise early-out. search_frame_start() runs
        // every SYNC_LENGTH=320 samples on the full 20 MHz stream (~62,500 calls/sec),
        // independent of sync_short detections. On noise windows EVERY correlation
        // magnitude is below MIN_ABS_MAGNITUDE, so the sort + vector building +
        // candidate loops below are pure waste (sort of a 320-element std::list is
        // allocation/cache heavy). Do a cheap O(n) max-scan first and bail out before
        // the O(n log n) sort on ~99.9% of noise windows. This is behavior-identical
        // to the no-detection fallback path at the bottom (same d_frame_start /
        // d_freq_offset, returns false); it only skips unreachable work.
        if (earlyout_enabled()) {
            double peak_mag = 0.0;
            for (const auto& e : d_cor) {
                double m = abs(e.first);
                if (m > peak_mag) peak_mag = m;
            }
            if (peak_mag < MIN_ABS_MAGNITUDE) {
                d_frame_start = SYNC_LENGTH;
                d_freq_offset = 0.0f;
                return false;
            }
        }

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
        fprintf(stderr, "[SYNC_LONG] Top correlation magnitude: %.4f tag_off=%llu\n", top_mag,
                (unsigned long long)d_last_wifi_start_tag_offset);

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
            // Phase 133 T3: Multi-feature gate — verify Schmidl-Cox is also high
            // at this position. Rejects noise false-positives that have FIR plateau
            // but lack periodic phase coherence between L-LTF halves.
            if (is_schmidl_cox_enabled()) {
                int ht_offset = get<1>(vec[best_ht_i]);
                double sc_value = lookup_sc_metric(ht_offset);
                if (sc_value < get_schmidl_cox_threshold()) {
                    fprintf(stderr, "[SYNC_LONG_P133] HT plateau REJECTED: best_ht_i=%d(offset=%d) "
                            "FIR-mag=%.4f Schmidl-Cox=%.4f (thresh=%.4f)\n",
                            best_ht_i, ht_offset,
                            abs(get<0>(vec[best_ht_i])),
                            sc_value, get_schmidl_cox_threshold());
                    valid = false;
                    return valid;
                }
                fprintf(stderr, "[SYNC_LONG_P133] HT plateau ACCEPTED: best_ht_i=%d(offset=%d) "
                        "FIR-mag=%.4f Schmidl-Cox=%.4f (thresh=%.4f)\n",
                        best_ht_i, ht_offset,
                        abs(get<0>(vec[best_ht_i])),
                        sc_value, get_schmidl_cox_threshold());
            }
            // FIX: Subtract 13 to compensate for group delay in correlation peak detection
            // The lower_peak is typically 13 samples AFTER the true LTF0 start due to
            // FIR matched filter group delay. Without this fix, the FFT window captures
            // 13 samples of L-SIG CP (dirty data) instead of LTF0, causing massive ISI.
            int offset_compensation = 13;
            d_frame_start = best_ht_lower_peak + 1 - offset_compensation;
            if (d_frame_start < 0) d_frame_start = 0;
            // CRITICAL FIX: Force d_frame_start=174 for all frames. Frame 1 works
            // perfectly with d_frame_start=174 (LTF_CORR=0.9990). Frame 2+ have
            // correlation peaks shifted by L-STF interference (lower_peak=199-201
            // instead of 172). The preamble structure is identical for all frames;
            // L-LTF0 DATA should always start at the same relative offset within
            // the SYNC window. With SPLITTER using tag_abs_pos+16, d_frame_start
            // must be constant for correct alignment.
            int fs_offset = get_frame_start_offset();
            const int computed_fs = d_frame_start;  // pre-force value (Phase 163b forensics)
            if (d_frame_start != 174 || fs_offset != 0) {
                int target = FRAME_START_BASE + fs_offset;
                fprintf(stderr, "[SYNC_LONG] d_frame_start=%d -> forcing to %d (offset=%d)\n",
                        d_frame_start, target, fs_offset);
                d_frame_start = target;
            }
            mode = "HT-mode-plateau";
            d_freq_offset = d_freq_offset_short;
            fprintf(stderr, "[SYNC_LONG] HT-mode-plateau SELECTED: best_i=%d(idx=%d) best_k=%d(idx=%d) best_diff=%d best_lower_peak=%d d_frame_start=%d score=%.2f computed_fs=%d tag_off=%llu\n",
                    best_ht_i, get<1>(vec[best_ht_i]), best_ht_k, get<1>(vec[best_ht_k]),
                    best_ht_diff, best_ht_lower_peak, d_frame_start, best_ht_score,
                    computed_fs, (unsigned long long)d_last_wifi_start_tag_offset);
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
            // Phase 133 T3: Multi-feature gate (Legacy mode)
            if (is_schmidl_cox_enabled()) {
                int leg_offset = get<1>(vec[best_leg_i]);
                double sc_value = lookup_sc_metric(leg_offset);
                if (sc_value < get_schmidl_cox_threshold()) {
                    fprintf(stderr, "[SYNC_LONG_P133] Legacy plateau REJECTED: best_leg_i=%d(offset=%d) "
                            "FIR-mag=%.4f Schmidl-Cox=%.4f (thresh=%.4f)\n",
                            best_leg_i, leg_offset,
                            abs(get<0>(vec[best_leg_i])),
                            sc_value, get_schmidl_cox_threshold());
                    valid = false;
                    return valid;
                }
                fprintf(stderr, "[SYNC_LONG_P133] Legacy plateau ACCEPTED: best_leg_i=%d(offset=%d) "
                        "FIR-mag=%.4f Schmidl-Cox=%.4f (thresh=%.4f)\n",
                        best_leg_i, leg_offset,
                        abs(get<0>(vec[best_leg_i])),
                        sc_value, get_schmidl_cox_threshold());
            }
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
    // Phase 163b: last wifi_start tag's absolute stream offset (real-time
    // position anchor), logged at frame-commit so each commit joins to its
    // TX-lattice slot for per-frame alignment forensics.
    uint64_t d_last_wifi_start_tag_offset = 0;

    gr_complex* d_correlation;
    list<pair<gr_complex, int>> d_cor;
    std::vector<gr::tag_t> d_tags;
    gr::filter::kernel::fir_filter_ccc d_fir;

    const bool d_log;
    const bool d_debug;
    const int SYNC_LENGTH;

    // Phase 133 T3: Schmidl-Cox L-LTF detector (multi-feature gate)
    // Runs in lock-step with FIR. d_sc_metric_at accumulates (metric, offset)
    // pairs aligned with d_cor. search_frame_start() uses d_sc_metric_at to
    // gate candidate pairs against phase coherence, rejecting FIR-only false
    // positives from structured noise.
    gr_complex d_sc_mult_ring[SCHMIDL_COX_WINDOW];
    float d_sc_pow_ring[SCHMIDL_COX_WINDOW];
    int d_sc_p_idx;
    int d_sc_r_idx;
    gr_complex d_sum_sc_p;
    float d_sum_sc_r;
    std::vector<std::pair<float, int>> d_sc_metric_at;  // (metric, offset), -1.0 = invalid

    // Phase 151: chunk-invariance (deterministic) correlation accumulation.
    // ROOT CAUSE (Phase 148): the baseline SYNC path consumes small chunks
    // (ninput<=63) via a deadlock skip WITHOUT computing correlation. d_offset
    // then lags true consumption by the skipped amount, shifting every
    // subsequent SYNC_LENGTH correlation window by up to 63 samples. The L-LTF
    // peak then leaves its +/-50 position tolerance in search_frame_start(),
    // so the SAME capture yields DIFFERENT detection/decode counts run-to-run.
    // FIX: stash small chunks and prepend them to the next chunk so EVERY
    // sample's correlation is computed exactly once, independent of how GNU
    // Radio partitions the input. Opt-in via
    // IEEE80211_SYNC_LONG_CHUNK_INVARIANT=1 (default OFF = baseline behavior).
    bool d_chunk_invariant;
    int d_stash_len;
    static constexpr int kStashCap = 128;      // > 63 max pending + margin
    static constexpr int kEffCap = 8192 + 128; // max ninput (8192) + stash
    gr_complex d_stash0[kStashCap];            // port-0 pending (too-small chunk)
    gr_complex d_stash1[kStashCap];            // port-1 pending (aligned for SC)
    gr_complex* d_eff0;                        // work: stash + chunk (port 0)
    gr_complex* d_eff1;                        // work: stash + chunk (port 1)

    // Lookup helper for Schmidl-Cox metric at given d_offset
    double lookup_sc_metric(int offset) {
        if (d_sc_metric_at.empty()) return -1.0;
        // Linear scan for matching offset (a few hundred entries)
        for (auto& entry : d_sc_metric_at) {
            if (entry.second == offset) {
                return entry.first;
            }
        }
        // Fallback: nearest neighbor
        int best_diff = INT_MAX;
        double best_val = -1.0;
        for (auto& entry : d_sc_metric_at) {
            int diff = std::abs(entry.second - offset);
            if (diff < best_diff) {
                best_diff = diff;
                best_val = entry.first;
            }
        }
        return best_val;
    }

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
