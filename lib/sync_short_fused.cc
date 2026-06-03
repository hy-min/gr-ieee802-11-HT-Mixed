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
          d_energy_gate_factor(energy_gate_factor),
          d_noise_est_window(noise_est_window),
          d_alpha(std::exp(-1.0f / static_cast<float>(noise_est_window))),
          d_noise_floor(1e-6f),
          d_delay_idx(0),
          d_ma_cc_idx(0),
          d_ma_ff_idx(0),
          d_sum_cc(0),
          d_sum_ff(0)
    {
        (void)threshold; // threshold is used by downstream sync_short, not this block

        set_tag_propagation_policy(block::TPP_DONT);

        // Initialize ring buffers to zero
        for (int i = 0; i < 16; i++) d_delay_ring[i] = gr_complex(0, 0);
        for (int i = 0; i < 48; i++) d_mult_ring[i] = gr_complex(0, 0);
        for (int i = 0; i < 64; i++) d_mag_sq_ring[i] = 0.0f;
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

        if (d_energy_gate_factor > 0.0f && n > 0) {
            // Energy gating: compute batch mean power
            float batch_power = 0.0f;
            for (int i = 0; i < n; i++) {
                batch_power += std::norm(in[i]);
            }
            batch_power /= static_cast<float>(n);

            // Update noise floor estimate (EMA)
            d_noise_floor = d_alpha * d_noise_floor + (1.0f - d_alpha) * batch_power;
            gate_threshold = d_noise_floor * d_energy_gate_factor;

            gated = (batch_power < gate_threshold);
        }

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
            float cor = (ma_ff > 1e-12f) ? (std::abs(ma_cc) / ma_ff) : 0.0f;

            out0[i] = delayed;
            out1[i] = ma_cc;
            out2[i] = cor;
        }

        consume_each(n);
        return n;
    }

private:
    const float d_energy_gate_factor;
    const int d_noise_est_window;
    const float d_alpha;

    float d_noise_floor;

    // Ring buffers
    gr_complex d_delay_ring[16];
    gr_complex d_mult_ring[48];
    float d_mag_sq_ring[64];

    // Ring indices
    int d_delay_idx;
    int d_ma_cc_idx;
    int d_ma_ff_idx;

    // Running sums
    gr_complex d_sum_cc;
    float d_sum_ff;
};

sync_short_fused::sptr
sync_short_fused::make(double threshold, float energy_gate_factor, int noise_est_window)
{
    return gnuradio::get_initial_sptr(
        new sync_short_fused_impl(threshold, energy_gate_factor, noise_est_window));
}
