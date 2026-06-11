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