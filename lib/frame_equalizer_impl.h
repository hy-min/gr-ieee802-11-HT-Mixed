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