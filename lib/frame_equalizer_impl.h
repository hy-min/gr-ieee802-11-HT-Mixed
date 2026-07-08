#ifndef INCLUDED_IEEE802_11_FRAME_EQUALIZER_IMPL_H
#define INCLUDED_IEEE802_11_FRAME_EQUALIZER_IMPL_H

#include <gnuradio/ieee802_11/frame_equalizer.h>
#include <gnuradio/digital/constellation.h>
#include <gnuradio/gr_complex.h>
#include <pmt/pmt.h>

#include "equalizer/base.h"
#include "equalizer/comb.h"
#include "equalizer/ls.h"
#include "equalizer/lms.h"
#include "equalizer/sta.h"

#include <cstdint>
#include <memory>
#include <vector>

namespace gr {
namespace ieee802_11 {

class frame_equalizer_impl : public frame_equalizer
{
private:
    std::shared_ptr<equalizer::base> d_equalizer;

    std::shared_ptr<gr::digital::constellation> d_bpsk;
    std::shared_ptr<gr::digital::constellation> d_qpsk;
    std::shared_ptr<gr::digital::constellation> d_16qam;
    std::shared_ptr<gr::digital::constellation> d_64qam;

    int d_current_symbol;
    int d_copied;

    bool d_debug;
    bool d_log;

    double d_freq_offset_from_synclong;
    int d_bw;
    int d_chan_est_mode;
    bool d_enable_soft_output;
    // Use L-LTF1 (counter=1) instead of L-LTF0 (counter=0) for H estimation.
    // 4us time gap to L-SIG instead of 8us. Hypothesis: reduces residual phase
    // when the channel rotates between the two LTF symbols.
    // Default OFF (use L-LTF0, current behavior). Set to true via env var
    // IEEE80211_H_LLTF1=1.
    bool d_use_lltf1_for_h;
    // TODO(2026-06-10 lltf1-experiment): remove this flag if USRP Task 4
    // shows no improvement over L-LTF0. See plan:
    // docs/superpowers/plans/2026-06-10-lltf1-h-estimation-experiment.md

    // Phase residual diagnostic: when true, dumps arg(eq_lsig[i]) for all
    // 48 data subcarriers per frame to help diagnose L-SIG viterbi failure
    // on USRP. Default OFF. Enable via env var IEEE80211_PHASE_RESIDUAL=1.
    // See spec: docs/superpowers/specs/2026-06-10-phase-noise-lsig-design.md
    bool d_log_phase_residual;

    // H52 diagnostic: when true, dumps |H52[i]| and arg(H52[i]) for all 52
    // subcarriers per frame to help diagnose whether H estimation is the
    // root cause of L-SIG viterbi failure on USRP. Default OFF. Enable via
    // env var IEEE80211_H52_DUMP=1.
    // See spec: docs/superpowers/specs/2026-06-10-h52-diagnosis-design.md
    bool d_log_h52;

    // LTF0 FFT diagnostic: when true, dumps saved_ltf0_fft[64] per frame at
    // frame_equalizer entry (extracted from sym64 at L-LTF0 RX). Used in H
    // chain traceback (Phase 3 Stage 1, REORGANIZED) to determine if L-LTF0
    // FFT is corrupted at the equalizer input. Default OFF. Enable via
    // env var IEEE80211_LTF0_FFT_DUMP=1.
    // See spec: docs/superpowers/specs/2026-06-11-h-chain-traceback-design.md
    bool d_log_ltf0_fft;

    // L-LTF0 FFT pre-compensation diagnostic (Phase 10): when true, dumps
    // the first 5 subcarriers of L-LTF0 FFT in complex (a+bi) form BEFORE
    // CFO/SFO compensation. Used to verify that L-SIG mis-decoding (enc=2/4/6/7
    // instead of 0) is not caused by upstream FFT corruption. Default OFF.
    // Enable via env var IEEE80211_LTF0_FFT_PRECOMP_DUMP=1.
    bool d_log_ltf0_fft_precomp;

    // H estimation robustness (Phase 4): when true, applies a 3-tap
    // median filter to single-frame H estimates to suppress impulse
    // outliers while preserving tracking response. Default OFF. Enable
    // via env var IEEE80211_H_MEDIAN_FILTER=1.
    // See spec: docs/superpowers/specs/2026-06-12-phase4-robust-h-estimation-design.md
    bool d_h_median_filter;

    // H52 post-filter diagnostic: when true, dumps |H52[i]| and arg(H52[i])
    // for all 52 subcarriers per frame AFTER the 3-tap median filter is
    // applied (at the call sites of estimate_header_channel_from_lltf52).
    // Companion to d_log_h52 which dumps the pre-filter H52. Default OFF.
    // Enable via env var IEEE80211_H52_DUMP_FILTERED=1.
    // See spec: docs/superpowers/specs/2026-06-12-phase4-robust-h-estimation-design.md
    bool d_log_h52_filtered;

    // H52 at equalizer-input diagnostic (Phase 10): when true, dumps
    // |Hhdr52[i]| and arg(Hhdr52[i]) for all 52 subcarriers per frame at
    // the point where Hhdr52 is consumed by the L-SIG/HT-SIG equalizer
    // (right after estimate_header_channel_from_lltf52 returns, before any
    // median filter). Distinct from d_log_h52 (which dumps H52 from the
    // earlier split-debug path). Default OFF. Enable via env var
    // IEEE80211_H52_EQ_INPUT_DUMP=1.
    bool d_log_h52_input;

    // Phase 108: FFT window position diagnostic. When true, dumps
    // (abs_in_off, d_data_start_rel, d_sym_idx_at_h52, d_internal_symbol_counter)
    // at the H52 compute site (once per frame at d_sym_idx=kHtSig1Rel=4,
    // the moment L-LTF0/L-LTF1 FFT vectors are read from d_early_eqsym
    // to feed estimate_header_channel_from_lltf52). Used to detect
    // upstream drift in FFT window alignment (splitter, sync_long
    // FRAME_START_BASE, UHD streaming). Default OFF. Enable via env var
    // IEEE80211_FFT_WINDOW_DUMP=1.
    bool d_log_fft_window;

    // HT-SIG diagnostic dumps (Phase 35): when true, dumps HT-SIG0/1
    // FFT bins and pilot phases for offline analysis. Two independent
    // env-vars targeting two layers of the HT-SIG chain:
    //   HTSIG_BIN_DUMP: raw FFT bins (post-extract, post-rotation at counter=4)
    //   HTSIG_PILOT_DUMP: 4 pilot phases
    // (HTSIG_EQ_INPUT_DUMP was removed per code review — it was a
    // no-op duplicate of BIN_DUMP at the counter=4 site.)
    // All flood-gated to first 10 frames per run.
    // Default OFF. Enable via env var IEEE80211_<NAME>=1.
    bool d_log_htsig_bin;
    bool d_log_htsig_pilot;

    // Phase 38 Step 7: equalized HT-SIG0/1 constellation dump. Dumps
    // d_early_eqsym[kHtSig0Rel/1Rel][i] / Hhdr52[i] for i=0..47 (data SCs)
    // to see what the equalizer output actually looks like at viterbi
    // input. If equalization is correct, expect QBPSK clusters on the
    // IMAG axis (±j). If random scatter, the equalization/H52 itself is
    // broken — which would explain why viterbi metrics are 12-17 (random)
    // across all 16 candidates on USRP. Opt-in via
    // IEEE80211_HTSIG_EQ_DUMP=1. Default OFF.
    bool d_log_htsig_eq;

    // Phase 35 Task 7c: per-symbol pilot-aided CPE on HT-SIG0/HT-SIG1.
    // Cancels per-symbol phase drift that the Phase 34 δ correction cannot
    // reach (δ is constant per-frame; per-symbol drift varies between
    // symbols). For each HT-SIG symbol, averages arg() over the 4 valid
    // pilots at bins {48,49,50,51} (SCs {-21,-7,7,21}) and rotates all 52
    // bins by exp(-j*phi). Default OFF. Enable via
    // IEEE80211_HTSIG_PILOT_CPE=1.
    bool d_apply_htsig_pilot_cpe;

    // Phase 77a: per-symbol pilot-aided CPE on L-SIG.
    // L-SIG is BPSK with 90° decision margin (structurally more robust
    // than HT-SIG QBPSK 45°). Pilots at bins {48,49,50,51} (SCs
    // {-21,-7,7,21}) same as HT-SIG, but L-SIG pilots are BPSK with
    // expected polarity {+1,+1,+1,+1} (no QBPSK rotation). Averages
    // arg() over the 4 pilots and rotates all 52 bins of d_early_eqsym
    // [kLSigRel] by exp(-j*phi). Applied BEFORE L-SIG viterbi decode so
    // the viterbi sees a phase-aligned constellation. Default OFF.
    // Enable via IEEE80211_LSIG_PILOT_CPE=1.
    bool d_apply_lsig_cpe;

    // Phase 108: constant CPE at L-SIG boundary. When true, computes
    // phi = arg(sum(eq_lsig[i])) over the 48 data SCs of the equalized
    // L-SIG constellation (skipping the 4 pilot bins at {48..51}) and
    // rotates all 52 bins of d_early_eqsym[kLSigRel] by exp(-j*phi).
    // Absorbs the static 30° phase offset between L-LTF and L-SIG FFT
    // windows (Phase 107 finding: 30° constant rotation, |H| CV 27-50%).
    // Distinct from d_apply_lsig_cpe (Phase 77a) which uses only 4 pilots;
    // this uses the full 48-SC ensemble average for a more stable phi.
    // Applied BEFORE L-SIG viterbi decode (kLSigRel path in general_work)
    // so the viterbi sees a phase-aligned constellation. Default OFF.
    // Enable via env var IEEE80211_CONST_CPE_APPLY=1.
    bool d_apply_const_cpe;

    // Phase 36: per-SC linear fit on HT-SIG pilots. Uses ht_expected_pilot
    // polarity-aware helper + linear regression on (sc_index, channel_phase)
    // to recover (a, b) coefficients. Replaces/supplements Phase 35 per-symbol
    // mean. Default OFF.
    bool d_apply_htsig_pilot_persc;

    // Phase 39: HT-SIG pilot-based H re-estimation. Replaces Hhdr52
    // (L-LTF0-based) for HT-SIG0/1 equalization only, using each symbol's
    // own 4 pilots at SCs {-21,-7,7,21} (known QBPSK values {+j,+j,+j,-j}).
    // Bypasses L-LTF0 deep nulls that amplify noise 7-50x during
    // equalization. Linear interpolate 4 pilot SCs to all 52 SCs. L-SIG
    // remains on Hhdr52 (Phase 34 fix). Default OFF. Enable via
    // IEEE80211_HTSIG_H_REESTIMATE=1.
    bool d_apply_htsig_h_reestimate;

    // Phase 39: H_htsig dump. Flood-gated to 10 frames. Dumps |H_htsig0|,
    // |H_htsig1|, and ratio |H_htsig|/|Hhdr52| per SC for offline
    // verification on USRP. Enable via IEEE80211_HTSIG_H52_DUMP=1.
    bool d_log_htsig_h52;

    // Phase 77c: SNR-weighted H52 averaging refinement. Replaces the
    // simple LTS0-only estimation in estimate_header_channel_from_lltf52()
    // with a per-SC average of LTS0 and LTS1 weighted by their magnitudes.
    // Conceptually: H52 = (w1*H_LTS0 + w0*H_LTS1)/(w0+w1) where
    // w_i = sum_i(|H_LTS_i|). Higher |H| LTS contributes more, so a
    // single LTS with deep nulls cannot corrupt the average. Phase 27
    // REFUTED simple average / sign-based / median variants; this is
    // structurally different (|H|-weighted, per-SC). Default OFF.
    // Enable via IEEE80211_H52_SNR_WEIGHTED=1.
    bool d_apply_h52_snr_weighted;

    // Phase 114 T4.D (alt): include HT-LTF as a third SNR-weighted source.
    // HT-Mixed single-stream frames have ONE HT-LTF symbol (extract_call==6),
    // so "2x averaging" was architecturally wrong. Instead, when enabled,
    // extract_header_channel_from_lltf52() blends H_HTLTF into the Phase 77c
    // |H|-weighted framework as a third source:
    //   H52 = (w_htl*H_LTS0 + w_ltf1*H_LTS1 + w_htltf*H_HTLTF) / sum(w)
    // Reduces per-SC H estimation noise from 2-way → 3-way averaging.
    // Default OFF preserves Phase 18/34/77c baseline. Enable via
    // IEEE80211_HTLTF_AVG=1 (requires IEEE80211_H52_SNR_WEIGHTED=1).
    bool d_apply_htltf_avg;

    // L-LTF0 entry time-domain gain diagnostic (Phase 13): when true,
    // dumps |sym64[j]|^2 sum (E_in) at the moment the L-LTF0 FFT window
    // is captured by extract_header52_from_sym64. Runs BEFORE the
    // d_early_eqsym_valid guard that blocks H52_DUMP / E_I_DUMP on USRP
    // (per Phase 4 lesson). Used to diagnose upstream gain/agc issues
    // causing L-LTF0 FFT corruption. Default OFF. Enable via env var
    // IEEE80211_FRAME_GAIN_DUMP=1.
    bool d_log_frame_gain;

    int d_frame_bytes;
    int d_frame_encoding;
    int d_frame_mcs;  // Original HT-MCS value (0-7) for output meta

    int d_frame_symbols;
    int d_frame_mod;
    int d_frame_n_bpsc;
    int d_frame_n_cbps;
    int d_frame_n_dbps;

    bool d_have_header;
    bool d_have_ht_header;
    bool d_is_ht;
    bool d_is_ht_frame;  // Frame type detection result: true=HT-Mixed, false=Legacy

    int d_sym_idx;
    int d_takeover_reject_symbols;
    int d_internal_symbol_counter;  // Tracks FFT output number, reset at wifi_start
    int d_first_valid_symbol;
    bool d_in_frame;
    bool d_discard_until_wifi_start;

    // early cache
    uint8_t d_early_bits[8][52];
    bool d_early_bits_valid[8];
    gr_complex d_early_eqsym[8][52];
    bool d_early_eqsym_valid[8];

    // Channel estimate for 52 HT-DATA subcarriers (tx_order)
    gr_complex d_H52_tx_order[52] = {gr_complex(0.0f, 0.0f)};
    bool d_H52_tx_order_valid = false;
    bool d_frame_bytes_tag_emitted = false;  // guard: emit frame_bytes tag only once per frame

    // dynamic header detection state
    bool d_have_lsig;
    int d_lsig_rel;
    int d_hdr_reorder_mode;
    bool d_hdr_inverted;
    int d_htsig0_rel;
    int d_htsig1_rel;
    int d_data_start_rel;

    // CFO/SFO tracking: estimated from L-LTF0/L-LTF1 phase difference
    float d_cfo_phase_per_symbol;   // CFO-induced phase per symbol (rad)
    int   d_cfo_ref_current_symbol; // d_current_symbol of L-LTF0 (reference)
    bool  d_cfo_estimated;          // true after L-LTF1 arrives
    float d_phase_diff_per_sc[52];  // per-subcarrier phase diff L-LTF1 vs L-LTF0 (rad)
    bool  d_phase_diff_valid;       // true after L-LTF1 arrives
    float d_sfo_per_sc_est = 0.0f;  // raw SFO estimate (rad/subcarrier) for logging
    bool  d_enable_cfo_comp;        // enable CFO/SFO compensation on HT-DATA

    // Phase 34: per-frame sub-sample timing offset (δ) estimation+correction.
    // Discovered via Phase 33b USRP validation: argH[b] = -2π·kScIndex52[b]·δ/64
    // with δ per-frame in [0,1) at 1/64 quantization, causing 64-PSK residual
    // that rotates BPSK across decision boundary → L-SIG viterbi fails.
    // Linear regression on argH vs SC index recovers δ, applied as per-SC
    // phase rotation. Default OFF. Enable via env var IEEE80211_TIMING_OFFSET_APPLY=1.
    float d_timing_offset_per_frame = 0.0f;  // in 1/64 sample units, [0, 1)
    bool  d_timing_offset_valid      = false;
    bool  d_log_timing_offset_dump   = false;
    bool  d_apply_timing_offset      = false;

    // Phase 38 Step 2: per-symbol δ drift diagnostic. After Phase 34 δ
    // correction is applied retroactively to L-SIG (counter=2), HT-SIG0 (3),
    // HT-SIG1 (4), estimate δ independently from each symbol's 4 pilots
    // (SCs {-21,-7,7,21} via bins {48,49,50,51}). If post-correction δ differs
    // across symbols by more than ~0.1 (=1/10 of 1/64 grid), per-symbol δ
    // drift is the bottleneck and Phase 34's constant-per-frame assumption
    // is wrong. Opt-in via IEEE80211_DELTA_PER_SYMBOL_DUMP=1. Default OFF.
    bool  d_log_delta_per_symbol      = false;

    // Phase 79: per-symbol δ tracking for HT-SIG + data symbols.
    // When true, replaces Phase 34's constant-per-frame δ with a
    // pilot-aware grid search estimator applied independently to each
    // symbol (counter >= 4). Default OFF preserves Phase 18/34/35 stack.
    // Enable via IEEE80211_HTSIG_PER_SYMBOL_DELTA=1.
    bool d_apply_htsig_per_symbol_delta = false;

    // Phase 79: optional diagnostic dump for per-symbol δ values.
    // Logs δ_htsig0, δ_htsig1, and per-data-symbol δ on USRP for triage.
    // Opt-in via IEEE80211_HTSIG_DELTA_DUMP=1. Default OFF.
    bool d_log_htsig_delta_dump        = false;

    // Phase 44: soft-LLR viterbi for HT-SIG unblock. When true, the
    // HT-SIG decoder feeds soft LLRs (sign(eq.imag()) * |H[i]|/max(|H|))
    // to viterbi_decode_133_171_soft instead of hard bits to the original
    // hard-bit viterbi. The branch metric is squared-error distance, so
    // channel-null SCs (|H[i]| ~ 0) contribute ~0 to the path metric and
    // don't poison the viterbi's choice. Hypothesis: bypasses Phase 41's
    // 50x noise amplification at Hhdr52 nulls. Default OFF.
    // Enable via IEEE80211_SOFT_LLR_VITERBI=1.
    bool d_use_soft_llr_viterbi       = false;

    // Phase 46 AR5: MMSE equalization for HT-SIG. When true, the HT-SIG0/1
    // bit-extraction path uses eq = conj(H)·rx / (|H|² + N0) instead of
    // safe_div(rx, H). Bypasses Phase 38's 50× noise amplification at
    // Hhdr52 channel nulls. N0 = 25th percentile of |H|² over the 48 data
    // SCs. Applied only to HT-SIG (L-SIG and data symbols keep safe_div).
    // Default OFF. Enable via IEEE80211_MMSE_EQUALIZE=1.
    bool d_mmse_equalize               = false;

    // Phase 47: N0 percentile for MMSE (1-100, default 25). 25th = robust
    // to a few nulls. 10 = more aggressive. 50+ behaves like median
    // (Phase 42 REFUTED, do not use). Read once at constructor.
    int  d_mmse_n0_percentile          = 25;

    // Phase 47: stash of H52 from L-LTF estimate, used to apply MMSE to
    // data symbols (which are equalized downstream after H52 leaves scope).
    // Set in general_work, read in data symbol override.
    gr_complex d_h52_stash[52]         = {};
    bool       d_h52_stash_valid       = false;

    // Phase 59: per-SC H52 null detection + 邻域插值.
    // Hypothesis: Hhdr52 channel nulls (|H|<0.15) cause 50x noise
    // amplification in safe_div, putting equalized HT-SIG on REAL axis.
    // Replace null SC |H| with mean of nearest non-null neighbors (radius=2)
    // to recover equalization fidelity. Default OFF. Enable via
    // IEEE80211_H52_NULL_INTERP=1.
    bool  d_h52_null_interp_enabled = false;  // master enable
    float d_h52_null_thresh         = 0.15f;  // |H[i]| < thresh -> null
    int   d_h52_interp_radius       = 2;      // left/right neighbor window
    bool  d_h52_null_dump_enabled   = false;  // diagnostic dump (default OFF)
    bool  d_h52_null_combo_enabled  = false;  // Phase 61: combo (thresh=0.10, radius=3, +pilot CPE).
                                              // Diagnostic-only field — read in no production code path.
                                              // Indicates the activation source: true means
                                              // IEEE80211_H52_NULL_COMBO=1 was set (which also
                                              // flipped d_h52_null_interp_enabled=true and
                                              // d_apply_htsig_pilot_cpe=true with combo-fixed
                                              // parameters); false means the individual sub-flags
                                              // were set directly. Reserved for future logging
                                              // or telemetry that distinguishes combo vs manual
                                              // activation. Cost is 1 bool — kept for traceability.

    // Phase 80b: per-SC phase calibration LUT for HT-SIG and data symbols.
    // d_htsig_per_sc_lut_data[0..51] covers all 52 SCs in kScIndex52 layout
    // (48 data + 4 pilots). HT-SIG paths use indices 0..47; data-symbol
    // paths use the full 52.
    // Loaded from a JSON file specified by IEEE80211_HTSIG_PER_SC_LUT.
    // When d_htsig_per_sc_lut_valid is false (default), LUT hooks are
    // no-ops — regression-safe for callers that never set the env var.
    gr_complex d_htsig_per_sc_lut_data[52] = {};
    bool       d_htsig_per_sc_lut_valid   = false;
    std::string d_htsig_per_sc_lut_path;

    // Phase 102: per-SC null mask (1 = treat as null, set LLR=0 in HT-SIG).
    // Populated from IEEE80211_HTSIG_NULL_SCS env var (CSV of indices 0..51).
    uint8_t d_htsig_null_sc_mask[52];

    // Phase 111: Kalman H52 tracker (per-frame symbol-by-symbol H refinement).
    // Hypothesis: Phase 107 found per-SC argH std=108° (random walk, not static)
    // and per-SC |H| CV=27-50%. Static equalizer H from L-LTF0+L-LTF1 cannot
    // track per-symbol drift in H[k] over the 19+ DATA symbols.
    //
    // Approach: maintain d_h_kalman[64] in FFT bin order (matching equalizer's
    // d_H[64]). Initialize from L-LTF0+L-LTF1 H52 estimate (in 64-bin order).
    // After each DATA symbol equalization, extract 4 pilot measurements
    // (rx_pilot[bin_i] / expected_polarity[i]) and run per-pilot Kalman update:
    //   K = P / (P + R)
    //   H_kalman[bin] = H_kalman[bin] + K * (H_meas - H_kalman[bin])
    //   P[bin] = (1 - K) * P[bin] + Q      (random walk prediction)
    // Then interpolate 4 pilot-bin updates to all 52 active bins (same scheme
    // as Phase 39 estimate_H_from_htsig_pilots piecewise linear) and inject via
    // d_equalizer->set_H(d_h_kalman) before the next symbol's equalize() call.
    //
    // Tunable: IEEE80211_H52_KALMAN_Q (process noise, default 0.01),
    // IEEE80211_H52_KALMAN_R (measurement noise, default 0.1).
    // Default OFF preserves Phase 18/34/35 baseline.
    // Enable via IEEE80211_H52_KALMAN_TRACK=1.
    bool  d_h52_kalman_track       = false;
    gr_complex d_h_kalman[64]      = {};
    float d_p_kalman[64]           = {};
    float d_kalman_q               = 0.01f;
    float d_kalman_r               = 0.1f;
    int   d_kalman_initialized     = 0;

    // Phase 111 T3: T3b = multi-symbol H averaging + δ correction.
    // δ estimation noise from 4 pilots (~0.06-0.08 sample units) drives
    // H drift in plain δ-correction (v3 REFUTED). Averaging over K
    // symbols reduces δ_est noise by sqrt(K), enabling lower threshold.
    // Enable: IEEE80211_H52_KALMAN_TRACK=1 + IEEE80211_H52_KALMAN_DELTA_CORRECT=1
    // + IEEE80211_H52_KALMAN_AVG=1. K via IEEE80211_H52_KALMAN_AVG_K (default 5).
    // Default OFF preserves v6 (threshold 10.0 no-op) behavior.
    bool  d_h52_kalman_dc          = false;  // per-symbol δ correction
    bool  d_h52_kalman_avg         = false;  // multi-symbol H averaging
    int   d_kalman_avg_k           = 5;
    gr_complex d_h_accum[4]        = {};
    int   d_kalman_avg_count       = 0;

    // Phase 112 T7e: decision-directed + multi-symbol H tracking.
    // R1 confirmed USRP analog chain phase noise std = 1.77 rad (101°) per
    // SC per OFDM symbol. L-LTF H52 carries this full noise. DATA symbols
    // have 4 pilots each = 4 SCs of clean-ish measurement. Averaging H52
    // across K DATA symbols reduces noise by sqrt(K).
    //   K=1: std=1.77 rad (101°) — same as L-LTF
    //   K=10: std=0.56 rad (32°) — partial reduction
    //   K=30: std=0.32 rad (18°) — close to viterbi capacity 4 error limit
    // Enable: IEEE80211_T7E_MULTISYM_H=1.
    // K via IEEE80211_T7E_MULTISYM_K (default 10).
    bool  d_t7e_multisym_h         = false;
    int   d_t7e_multisym_k         = 10;
    int   d_t7e_count              = 0;       // DATA symbols accumulated
    gr_complex d_t7e_h_accum[52]   = {};        // accumulator
    gr_complex d_t7e_h_avg[52]     = {};        // averaged H52 in active SC order
    bool  d_t7e_h_avg_valid        = false;
    // Phase 112 T7e D4: HT-SIG IQ buffer for buffer-and-decode.
    // Cache HT-SIG0/HT-SIG1 raw sym64 (64-bin FFT, before any compensation)
    // and L-LTF0/L-LTF1 raw sym64 so that after K DATA symbols we can
    // re-estimate H52 from L-LTF (or use averaged H52 from DATA) and
    // re-decode HT-SIG with the refined channel estimate.
    gr_complex d_t7e_htsig_iq_buf[2][64];       // [HT-SIG0, HT-SIG1]
    bool  d_t7e_htsig_iq_valid[2]  = {false, false};
    gr_complex d_t7e_l_ltf_iq_buf[2][64];       // [L-LTF0, L-LTF1] raw sym64
    bool  d_t7e_l_ltf_iq_valid[2]  = {false, false};
    // L-LTF H52 estimate in tx_order (52 SCs) — what was used for the
    // original HT-SIG decode. We need this to derive rx from the cached
    // equalized HT-SIG IQ.
    gr_complex d_t7e_l_ltf_h52_tx_order[52] = {};
    bool  d_t7e_l_ltf_h52_valid    = false;
    // Original HT-SIG IQ in tx_order (after L-LTF H52 equalization).
    // This is what `decode_htsig_from_rotated` expects — already equalized.
    gr_complex d_t7e_htsig_eq52[2][52] = {};
    bool  d_t7e_redecode_done      = false;
    bool  d_t7e_redecode_succeeded = false;

    // Compensated copies of L-LTF0 and L-LTF1 used for H estimation.
    // Populated in general_work() AFTER CFO/SFO estimation so that H and
    // the (also-compensated) L-SIG/HT-SIG symbols are in the same phase
    // domain, eliminating the residual rotation that otherwise leaks into
    // the equalized header symbols.
    //
    // Note: slot 0 (L-LTF0) has counter=0 so phase = 0, making this
    // element a byte-identical copy of d_early_eqsym[kLltf0Rel]. We keep
    // the symmetric structure for code clarity — only slot 1 actually
    // applies a meaningful rotation.
    gr_complex d_ltf_compensated[2][52];
    bool d_ltf_compensated_valid[2] = {false, false};

    void reset_frame_state(void);

    bool parse_signal(const uint8_t* decoded_bits,
                      int& encoding,
                      int& psdu_length);

    bool parse_signal_ht(const uint8_t* decoded_bits,
                         int& mcs,
                         int& psdu_length,
                         bool& aggregation,
                         bool& short_gi,
                         bool& use_ldpc);

    void set_ht_frame_params_from_mcs_len(int mcs, int len_bytes, bool use_ldpc = false);

    bool d_use_ldpc;
    int d_ldpc_n_sym;

    bool decode_lsig_from_bits52(const uint8_t* bits52,
                                 int reorder_mode,
                                 bool invert_bits,
                                 int& encoding,
                                 int& psdu_length);

    bool decode_htsig_from_bits52(const uint8_t* bits_a,
                                  const uint8_t* bits_b,
                                  int reorder_mode,
                                  bool swap_symbols,
                                  bool invert_bits,
                                  int& out_len_bytes,
                                  int& out_mcs,
                                  bool& out_sgi,
                                  bool& out_agg,
                                  bool& out_use_ldpc);

    bool decode_htsig_from_eqsym52(const gr_complex* sym_a,
                                   const gr_complex* sym_b,
                                   int reorder_mode,
                                   bool swap_symbols,
                                   bool invert_bits,
                                   int& out_len_bytes,
                                   int& out_mcs,
                                   bool& out_sgi,
                                   bool& out_agg,
                                   bool& out_use_ldpc);

    // QBPSK energy voting for frame type detection
    static void compute_subcarrier_energy(const gr_complex* eq52, double& Esum_I, double& Esum_Q);
    static int vote_qbpsk_rotation(const gr_complex* eq_data);

public:
    frame_equalizer_impl(Equalizer algo,
                         double freq,
                         double bw,
                         bool log,
                         bool debug);
    ~frame_equalizer_impl() override;

    void set_algorithm(Equalizer algo) override;
    void set_bandwidth(double bw) override;
    void set_frequency(double freq) override;
    void set_extra_header_symbols(int n) override;

    // Phase 80b: load per-SC phase LUT from a JSON file. On success, sets
    // d_htsig_per_sc_lut_data[0..51] (all 52 SCs in kScIndex52 layout)
    // and flips d_htsig_per_sc_lut_valid=true. On any error
    // (open/parse/shape), logs to std::cerr and returns false; the LUT
    // stays invalid.
    bool load_per_sc_lut_from_json(const char* path);

    // Phase 102: read-only accessor for the parsed null-SC mask. Used by
    // Python bindings (and tests) to verify IEEE80211_HTSIG_NULL_SCS was
    // parsed correctly at constructor time. Returns a pointer to the
    // 52-element uint8_t array (1 = null SC, 0 = non-null).
    const uint8_t* get_d_htsig_null_sc_mask() const { return d_htsig_null_sc_mask; }
    static constexpr int get_d_htsig_null_sc_mask_size() { return 52; }

    void forecast(int noutput_items,
                  gr_vector_int& ninput_items_required) override;

    int general_work(int noutput_items,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star& output_items) override;
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_FRAME_EQUALIZER_IMPL_H */