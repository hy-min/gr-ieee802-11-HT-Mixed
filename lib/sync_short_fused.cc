/*
 * Copyright (C) 2026
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 */
#include "utils.h"
#include <gnuradio/io_signature.h>
#include <ieee802_11/sync_short_fused.h>

#include <cmath>

using namespace gr::ieee802_11;

class sync_short_fused_impl : public sync_short_fused
{
public:
    sync_short_fused_impl(double threshold,
                          float energy_gate_factor,
                          int noise_est_window)
        : block("sync_short_fused",
                gr::io_signature::make(1, 1, sizeof(gr_complex)),
                gr::io_signature::make3(
                    3, 3, sizeof(gr_complex), sizeof(gr_complex), sizeof(float))),
          d_energy_gate_factor(
              getenv("IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE")
                  ? 0.0f
                  : energy_gate_factor),
          d_use_boxcar(
              getenv("IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR")
                  ? true : false),
          d_boxcar_dump(
              getenv("IEEE80211_SYNC_SHORT_FUSED_BOXCAR_DUMP")
                  ? true : false),
          d_use_schmidl_cox(
              getenv("IEEE80211_SYNC_SHORT_FUSED_SCHMIDL_COX")
                  ? true : false),
          d_schmidl_cox_dump(
              getenv("IEEE80211_SYNC_SHORT_FUSED_SCHMIDL_COX_DUMP")
                  ? true : false),
          d_noise_est_window(noise_est_window),
          d_alpha(std::exp(-1.0f / static_cast<float>(noise_est_window))),
          d_noise_floor(1e-6f),
          d_delay_idx(0),
          d_ma_cc_idx(0),
          d_ma_ff_idx(0),
          d_boxcar_idx(0),
          d_sc_p_idx(0),
          d_sc_r_idx(0),
          d_sum_cc(0),
          d_sum_ff(0),
          d_sum_boxcar(0),
          d_sum_sc_p(gr_complex(0, 0)),
          d_sum_sc_r(0.0f)
    {
        (void)threshold; // threshold is used by downstream sync_short, not this block

        set_tag_propagation_policy(block::TPP_DONT);

        // Initialize ring buffers to zero
        for (int i = 0; i < 16; i++) d_delay_ring[i] = gr_complex(0, 0);
        for (int i = 0; i < 48; i++) d_mult_ring[i] = gr_complex(0, 0);
        for (int i = 0; i < 64; i++) d_mag_sq_ring[i] = 0.0f;
        for (int i = 0; i < 16; i++) d_boxcar_ring[i] = 0.0f;
        // Phase 132: 32-sample rings for Schmidl-Cox two-period correlation
        for (int i = 0; i < 32; i++) d_sc_mult_ring[i] = gr_complex(0, 0);
        for (int i = 0; i < 32; i++) d_sc_pow_ring[i] = 0.0f;
    }

    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items)
    {
        const gr_complex* in = (const gr_complex*)input_items[0];
        gr_complex* out0 = (gr_complex*)output_items[0];
        gr_complex* out1 = (gr_complex*)output_items[1];
        float* out2 = (float*)output_items[2];

        int ninput = ninput_items[0];
        int noutput = noutput_items;
        int n = std::min(ninput, noutput);

        bool gated = false;
        float gate_threshold = 0.0f;
        float batch_power = 0.0f;

        if (d_energy_gate_factor > 0.0f && n > 0) {
            // Energy gating: compute batch mean power
            for (int i = 0; i < n; i++) {
                batch_power += std::norm(in[i]);
            }
            batch_power /= static_cast<float>(n);

            // Update noise floor estimate (EMA)
            d_noise_floor = d_alpha * d_noise_floor + (1.0f - d_alpha) * batch_power;
            gate_threshold = d_noise_floor * d_energy_gate_factor;

            gated = (batch_power < gate_threshold);
        }

        // Phase 88 T2c diagnostic: log batch stats when env var is set
        // Computed AFTER the main loop fills out2[]
        static bool dump_enabled =
            (getenv("IEEE80211_SYNC_SHORT_FUSED_DUMP") != nullptr);
        static int dump_call_count = 0;

        for (int i = 0; i < n; i++) {
            // Step 1: 16-sample delay ring
            gr_complex delayed = d_delay_ring[d_delay_idx];
            d_delay_ring[d_delay_idx] = in[i];
            d_delay_idx = (d_delay_idx + 1) & 0xF; // mod-16 via bitmask

            if (gated) {
                // Energy-gated: output delayed sample, zero correlation
                out0[i] = delayed;
                out1[i] = gr_complex(0, 0);
                out2[i] = 0.0f;
                continue;
            }

            // Step 2: autocorrelation with 16-sample delayed sample
            gr_complex mult = in[i] * std::conj(delayed);

            // Step 3: MA(48) complex correlation (running sum, matches GR moving_average scale=1)
            d_sum_cc += mult;
            d_sum_cc -= d_mult_ring[d_ma_cc_idx];
            d_mult_ring[d_ma_cc_idx] = mult;
            d_ma_cc_idx = (d_ma_cc_idx + 1) % 48;
            gr_complex ma_cc = d_sum_cc;

            // Step 4: MA(64) energy (running sum, matches GR moving_average scale=1)
            float mag_sq = std::norm(in[i]);
            d_sum_ff += mag_sq;
            d_sum_ff -= d_mag_sq_ring[d_ma_ff_idx];
            d_mag_sq_ring[d_ma_ff_idx] = mag_sq;
            d_ma_ff_idx = (d_ma_ff_idx + 1) % 64;
            float ma_ff = d_sum_ff;

            // Step 5: normalized correlation
            // When USE_BOXCAR is set (Phase 89): output raw period-16 autocorr
            // magnitude, then 16-sample boxcar-smooth. This matches Python's
            // |a*conj(b)| + boxcar(16) algorithm and avoids the MA(48)/MA(64)
            // ratio failure mode where noise random walk > coherent L-STF.
            //
            // Phase 132 SCHMIDL_COX: two-period (32-sample) sliding complex sum.
            // Standard Schmidl-Cox algorithm from 802.11n textbook. Produces a
            // 16-sample plateau over the L-STF (one short symbol period) instead
            // of a single peak, making detection more robust to frequency offset
            // and amplitude variation. Output: |P|² / (R² + eps) in [0, 1].
            // L-STF gives ~0.5-0.8, noise gives ~0.0-0.1. User should lower
            // threshold to ~0.3-0.5 when SCHMIDL_COX is enabled.
            float out2_val;
            if (d_use_schmidl_cox) {
                // Update 32-sample complex sum of mult (P)
                d_sum_sc_p += mult;
                d_sum_sc_p -= d_sc_mult_ring[d_sc_p_idx];
                d_sc_mult_ring[d_sc_p_idx] = mult;
                d_sc_p_idx = (d_sc_p_idx + 1) & 0x1F;  // mod-32
                // Update 32-sample sum of |in[i]|^2 (R)
                d_sum_sc_r += mag_sq;
                d_sum_sc_r -= d_sc_pow_ring[d_sc_r_idx];
                d_sc_pow_ring[d_sc_r_idx] = mag_sq;
                d_sc_r_idx = (d_sc_r_idx + 1) & 0x1F;  // mod-32
                // Schmidl-Cox metric: |P|² / R² (standard formula)
                float p_abs_sq = std::norm(d_sum_sc_p);
                float r_sq = d_sum_sc_r * d_sum_sc_r;
                out2_val = p_abs_sq / (r_sq + 1e-9f);  // [0, 1]
            } else if (d_use_boxcar) {
                float ac_raw = std::abs(mult);  // |in[i] * conj(in[i-16])|, real
                d_sum_boxcar += ac_raw;
                d_sum_boxcar -= d_boxcar_ring[d_boxcar_idx];
                d_boxcar_ring[d_boxcar_idx] = ac_raw;
                d_boxcar_idx = (d_boxcar_idx + 1) & 0xF;  // mod-16 via bitmask
                out2_val = d_sum_boxcar;  // raw sum; ~16*sigma for noise, ~16*peak for L-STF
            } else {
                out2_val = (ma_ff > 1e-12f) ? (std::abs(ma_cc) / ma_ff) : 0.0f;
            }

            out0[i] = delayed;
            out1[i] = ma_cc;
            out2[i] = out2_val;
        }

        // Optional boxcar dump (Phase 89 T1)
        if (d_boxcar_dump && dump_call_count < 50) {
            float max_v = 0.0f, min_v = 1e9f;
            for (int i = 0; i < n; i++) {
                if (out2[i] > max_v) max_v = out2[i];
                if (out2[i] < min_v) min_v = out2[i];
            }
            fprintf(stderr, "[SYNC-SHORT-FUSED-BOXCAR] call=%d n=%d batch_power=%.6f "
                    "max_out2=%.4f min_out2=%.4f\n",
                    dump_call_count, n, batch_power, max_v, min_v);
            dump_call_count++;
        }

        if (dump_enabled && dump_call_count < 200) {
            float max_cor = 0.0f;
            int n_above_001 = 0;
            int n_above_01 = 0;
            for (int i = 0; i < n; i++) {
                if (out2[i] > max_cor) max_cor = out2[i];
                if (out2[i] > 0.001f) n_above_001++;
                if (out2[i] > 0.01f) n_above_01++;
            }
            // Phase 88 T2c: log every call so we can find the L-STF region
            fprintf(stderr, "[SYNC-SHORT-FUSED] call=%d n=%d batch_power=%.6f noise_floor=%.6f "
                    "gate_thresh=%.6f gated=%d max_cor=%.4f n>0.001=%d n>0.01=%d\n",
                    dump_call_count, n, batch_power, d_noise_floor,
                    gate_threshold, gated ? 1 : 0, max_cor, n_above_001, n_above_01);
            dump_call_count++;
        } else if (dump_enabled && dump_call_count < 1000000 &&
                   d_noise_floor * d_energy_gate_factor > 0.0f &&
                   batch_power > d_noise_floor * d_energy_gate_factor * 5.0f) {
            // Only log spikes where batch_power > 5x gate threshold (signal region)
            float max_cor = 0.0f;
            for (int i = 0; i < n; i++) {
                if (out2[i] > max_cor) max_cor = out2[i];
            }
            // pos = absolute input sample index (USRP timeline domain) — lets
            // offline analysis align spikes with sync_short detection positions.
            fprintf(stderr, "[SYNC-SHORT-FUSED-SPIKE] call=%d batch_power=%.6f "
                    "noise_floor=%.6f gate_thresh=%.6f max_cor=%.4f pos=%llu\n",
                    dump_call_count, batch_power, d_noise_floor,
                    d_noise_floor * d_energy_gate_factor, max_cor,
                    (unsigned long long)nitems_read(0));
            dump_call_count++;
        }

        consume_each(n);
        return n;
    }

private:
    const float d_energy_gate_factor;
    const bool d_use_boxcar;
    const bool d_boxcar_dump;
    const bool d_use_schmidl_cox;   // Phase 132
    const bool d_schmidl_cox_dump;  // Phase 132
    const int d_noise_est_window;
    const float d_alpha;

    float d_noise_floor;

    // Ring buffers
    gr_complex d_delay_ring[16];
    gr_complex d_mult_ring[48];
    float d_mag_sq_ring[64];
    float d_boxcar_ring[16];  // Phase 89: 16-sample boxcar over raw autocorr
    // Phase 132: Schmidl-Cox 32-sample rings
    gr_complex d_sc_mult_ring[32];  // sum of in[i]*conj(in[i-16]) over 32 samples
    float d_sc_pow_ring[32];        // sum of |in[i]|^2 over 32 samples

    // Ring indices
    int d_delay_idx;
    int d_ma_cc_idx;
    int d_ma_ff_idx;
    int d_boxcar_idx;
    int d_sc_p_idx;  // Phase 132
    int d_sc_r_idx;  // Phase 132

    // Running sums
    gr_complex d_sum_cc;
    float d_sum_ff;
    float d_sum_boxcar;
    gr_complex d_sum_sc_p;  // Phase 132: 32-sample complex sum of mult
    float d_sum_sc_r;       // Phase 132: 32-sample sum of |in[i]|^2
};

sync_short_fused::sptr
sync_short_fused::make(double threshold, float energy_gate_factor, int noise_est_window)
{
    return gnuradio::get_initial_sptr(
        new sync_short_fused_impl(threshold, energy_gate_factor, noise_est_window));
}
