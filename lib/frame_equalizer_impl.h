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
    bool  d_enable_cfo_comp;        // enable CFO/SFO compensation on HT-DATA

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