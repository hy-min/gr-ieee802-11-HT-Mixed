#include "frame_equalizer_impl.h"

// USRP debug log control - uncomment to enable verbose logs
#define USRP_DEBUG_LOGS
#ifdef USRP_DEBUG_LOGS
#define USRP_LOG(...) do { fprintf(stderr, __VA_ARGS__); } while(0)
#define USRP_LOG_STD(...) do { std::fprintf(stderr, __VA_ARGS__); } while(0)
#else
#define USRP_LOG(...) ((void)0)
#define USRP_LOG_STD(...) ((void)0)
#endif


#include <gnuradio/io_signature.h>
#include <gnuradio/digital/constellation.h>
#include <pmt/pmt.h>
#include <ieee802_11/constellations.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstring>
#include <limits>
#include <set>
#include <string>
#include <vector>
#include <fstream>
#include <cstdlib>

#include "ieee80211_constants.h"

namespace gr {
namespace ieee802_11 {

namespace {

// ============================================================
// mixed-mode relative symbol positions seen by frame_equalizer
// ============================================================
//
// 结合你当前链路日志，frame_equalizer 看到的 mixed-mode 位置应为：
//
// rel=1,2 : L-LTF
// rel=3   : L-SIG
// rel=4,5 : HT-SIG
// rel=6,7 : HT training
// rel=8.. : HT DATA
//
static constexpr int kLltf0Rel      = 0;
static constexpr int kLltf1Rel      = 1;
static constexpr int kLSigRel       = 2;
static constexpr int kHtSig0Rel     = 3;
static constexpr int kHtSig1Rel     = 4;
static constexpr int kHtTrain0Rel   = 5;
static constexpr int kHtTrain1Rel   = 6;
static constexpr int kDataStartRel  = 7;

static constexpr int kMaxFrameRel = 128;

// FFT bin mapping - CORRECTED: sc + 64 mod 64
// IEEE 802.11 OFDM 64-point FFT natural memory order:
//   FFT bin  0         = DC (subcarrier 0)
//   FFT bin  1 to 26   = positive frequencies (subcarriers +1 to +26)
//   FFT bin 27 to 37   = guard band / nulls
//   FFT bin 38 to 63   = negative frequencies (subcarriers -26 to -1)
//
// Input: subcarrier index sc (-32 to +31)
// Output: FFT bin index (0 to 63)
static inline int sc_to_fft_bin(int sc)
{
    return (sc + 64) % 64;
}

static inline int ones8_local(int n)
{
    int s = 0;
    for (int i = 0; i < 8; i++) {
        if (n & (1 << i)) {
            s++;
        }
    }
    return s;
}

static inline uint8_t hard_bit_from_complex(const gr_complex& x)
{
    // BPSK映射：符号+1 -> 比特1，符号-1 -> 比特0
    // 发送端使用 digital.chunks_to_symbols_bc([-1, 1], 1)
    // 即：比特0 -> -1，比特1 -> +1
    // 所以接收端：正实数(+1) -> 比特1，负实数(-1) -> 比特0
    return (x.real() >= 0.0f) ? 1 : 0;
}

static inline gr_complex safe_div(const gr_complex& a, const gr_complex& b)
{
    const float d = std::norm(b);
    if (d < 1e-12f || !std::isfinite(d)) {
        return gr_complex(0.0f, 0.0f);
    }
    return a * std::conj(b) / d;
}

// ============================================================
// HT tables
// ============================================================

static inline int ht_n_bpsc_from_mcs(int mcs)
{
    switch (mcs) {
    case 0: return 1;
    case 1: return 2;
    case 2: return 2;
    case 3: return 4;
    case 4: return 4;
    case 5: return 6;
    case 6: return 6;
    case 7: return 6;
    default: return 1;
    }
}

static inline int ht_n_cbps_from_mcs(int mcs)
{
    switch (mcs) {
    case 0: return 52;
    case 1: return 104;
    case 2: return 104;
    case 3: return 208;
    case 4: return 208;
    case 5: return 312;
    case 6: return 312;
    case 7: return 312;
    default: return 52;
    }
}

static inline int ht_n_dbps_from_mcs(int mcs)
{
    switch (mcs) {
    case 0: return 26;
    case 1: return 52;
    case 2: return 78;
    case 3: return 104;
    case 4: return 156;
    case 5: return 208;
    case 6: return 234;
    case 7: return 260;
    default: return 26;
    }
}

static std::shared_ptr<gr::digital::constellation> make_bpsk_constellation()
{
    return gr::digital::constellation_bpsk::make();
}

static std::shared_ptr<gr::digital::constellation> make_qpsk_constellation()
{
    return gr::digital::constellation_qpsk::make();
}

static std::shared_ptr<gr::digital::constellation> make_16qam_constellation()
{
    return gr::digital::constellation_16qam::make();
}

static std::shared_ptr<gr::digital::constellation> make_64qam_constellation()
{
    return gr::ieee802_11::constellation_64qam::make();
}

// Map standard 802.11n HT-MCS (0-7, as carried in HT-SIG) back to our
// Encoding enum values.  These differ because the enum inserts BPSK_3_4
// at value 1 and shifts everything above QPSK_1_2 by one.
static inline int ht_mcs_to_encoding(int ht_mcs)
{
    switch (ht_mcs) {
    case 0: return 0;  // BPSK 1/2
    case 1: return 2;  // QPSK 1/2
    case 2: return 3;  // QPSK 3/4
    case 3: return 4;  // 16QAM 1/2
    case 4: return 5;  // 16QAM 3/4
    case 5: return 6;  // 64QAM 2/3
    case 6: return 7;  // 64QAM 3/4
    case 7: return 8;  // 64QAM 5/6
    default: return 0;
    }
}

// ============================================================
// Fixed 52-data order helpers (ID / TX mapper order)
// ============================================================
//
// 52 HT data subcarriers, excluding pilots, in TX mapper order
//
static constexpr int kTxOrder52[52] = {
    -28,-27,-26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26,27,28
};

static constexpr int kCandA52[52] = {
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26,27,28,
    -28,-27,-26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1
};

static constexpr int kCandB52[52] = {
    -1,-2,-3,-4,-5,-6,-8,-9,-10,-11,-12,-13,-14,-15,-16,-17,-18,-19,-20,
    -22,-23,-24,-25,-26,-27,-28,
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26,27,28
};

static constexpr int kCandC52[52] = {
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26,27,28,
    -28,-27,-26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1
};


static constexpr int kPilot4Sc[4] = { -21, -7, 7, 21 };

// EXPLICIT FFT bin mapping for pilots: SC → bin
// SC -21 → bin 43, SC -7 → bin 57, SC +7 → bin 7, SC +21 → bin 21
static constexpr int kPilot4Bin[4] = { 43, 57, 7, 21 };

static constexpr int kHtPilotPolarity127[127] = {
    1, 1, 1, 1, -1, -1, -1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1, -1, 1, 1,
    -1, 1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1, 1, 1, -1, 1, 1, -1, -1, 1, 1, 1,
    -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1, -1, -1, 1, 1, 1, 1, 1, -1, -1,
    1, 1, -1, -1, 1, -1, 1, -1, 1, 1, -1, -1, -1, 1, 1, -1, -1, -1, -1, 1,
    -1, -1, 1, -1, 1, 1, 1, 1, -1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, 1, -1, -1,
    -1, -1, -1, -1, -1
};

static inline gr_complex ht_expected_pilot(int data_sym_idx, int pilot_idx)
{
    const int p = kHtPilotPolarity127[data_sym_idx % 127];
    const int sign = (pilot_idx == 3) ? -p : p;
    return gr_complex((float)sign, 0.0f);
}

static float estimate_ht_data_cpe_rad_from_sym64(const gr_complex* sym64,
                                                 int data_sym_idx,
                                                 const gr_complex* H52_tx_order)
{
    gr_complex acc(0.0f, 0.0f);

    for (int i = 0; i < 4; i++) {
        const int sc = kPilot4Sc[i];
        int h_idx = -1;
        for (int j = 0; j < 52; j++) {
            if (kTxOrder52[j] == sc) {
                h_idx = j;
                break;
            }
        }
        if (h_idx < 0 || std::abs(H52_tx_order[h_idx]) < 0.001f) {
            continue;
        }
        // Use EQUALIZED pilot to estimate residual CPE (not raw pilot)
        const gr_complex eq_pilot = sym64[kPilot4Bin[i]] / H52_tx_order[h_idx];
        acc += eq_pilot * std::conj(ht_expected_pilot(data_sym_idx, i));
    }
    if (std::abs(acc) < 1e-9f) {
        return 0.0f;
    }
    return std::arg(acc);
}

// Forward declarations for saved LTF0 FFT (defined later in extract_header52_from_sym64)
extern gr_complex saved_ltf0_fft[64];
extern bool ltf0_saved;
extern bool ltf0_ever_saved;
extern bool g_log_ltf0_fft;

static void extract_ht_data52_direct_tx_order(const gr_complex* sym64,
                                              int data_sym_idx,
                                              const gr_complex* H52_tx_order,
                                              gr_complex* out52)
{
    const float cpe = estimate_ht_data_cpe_rad_from_sym64(sym64, data_sym_idx, H52_tx_order);
    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

    USRP_LOG( "[EQ_HTDATA] sym=%d cpe_deg=%.1f rot=%.4f%+.4fi H[0]=%.4f%+.4fi sym64[%d]=%.4f%+.4fi eq[0]=...\n",
            data_sym_idx, cpe * 180.0f / M_PI, rot.real(), rot.imag(),
            H52_tx_order[0].real(), H52_tx_order[0].imag(),
            sc_to_fft_bin(kTxOrder52[0]), sym64[sc_to_fft_bin(kTxOrder52[0])].real(), sym64[sc_to_fft_bin(kTxOrder52[0])].imag());

    for (int i = 0; i < 52; i++) {
        const int bin = sc_to_fft_bin(kTxOrder52[i]);
        const float h_mag = std::abs(H52_tx_order[i]);
        if (h_mag > 0.001f) {
            out52[i] = sym64[bin] / H52_tx_order[i] * rot;
        } else {
            out52[i] = gr_complex(0.0f, 0.0f);
        }
    }
    // Compensate for kFftNormalize in H estimate (H includes kFftNormalize
    // in the denominator, so equalized symbols are scaled up by kFftNormalize).
    // This matches the LS equalizer path (raw_eq52[k] /= kFftNormalize).
    for (int i = 0; i < 52; i++) {
        out52[i] /= kFftNormalize;
    }
    USRP_LOG( "[EQ_HTDATA] sym=%d eq[0]=%.4f%+.4fi eq[25]=%.4f%+.4fi eq[26]=%.4f%+.4fi eq[51]=%.4f%+.4fi\n",
            data_sym_idx, out52[0].real(), out52[0].imag(), out52[25].real(), out52[25].imag(), out52[26].real(), out52[26].imag(), out52[51].real(), out52[51].imag());
}


static bool reorder_from_candidate_bits(const int* src_order,
                                        const uint8_t* src_bits,
                                        uint8_t* dst_bits)
{
    for (int dst = 0; dst < 52; dst++) {
        const int want_sc = kTxOrder52[dst];
        bool found = false;

        for (int src = 0; src < 52; src++) {
            if (src_order[src] == want_sc) {
                dst_bits[dst] = src_bits[src];
                found = true;
                break;
            }
        }

        if (!found) {
            return false;
        }
    }

    return true;
}

static bool reorder_from_candidate_eq(const int* src_order,
                                      const gr_complex* src_eq,
                                      gr_complex* dst_eq)
{
    for (int dst = 0; dst < 52; dst++) {
        const int want_sc = kTxOrder52[dst];
        bool found = false;

        for (int src = 0; src < 52; src++) {
            if (src_order[src] == want_sc) {
                dst_eq[dst] = src_eq[src];
                found = true;
                break;
            }
        }

        if (!found) {
            return false;
        }
    }

    return true;
}

static bool reorder_bits_52_mode(const uint8_t* src_bits,
                                 uint8_t* dst_bits,
                                 int /*reorder_mode*/)
{
    // ID has been verified as the correct HT 52-carrier order.
    std::memcpy(dst_bits, src_bits, 52);
    return true;
}

static bool reorder_eq_52_mode(const gr_complex* src_eq,
                               gr_complex* dst_eq,
                               int /*reorder_mode*/)
{
    // ID has been verified as the correct HT 52-carrier order.
    std::memcpy(dst_eq, src_eq, 52 * sizeof(gr_complex));
    return true;
}

// ============================================================
// Header subcarrier orders
// ============================================================
//
// 对 L-SIG / HT-SIG，明确只取 48 个 data subcarrier：
//   -26..-1, +1..+26，跳过 pilots {-21,-7,+7,+21}
// 并单独缓存 4 个 pilots
//
// EXPLICIT FFT BIN MAPPING for RX extraction:
// This maps kHeader48Sc order → FFT bin indices in NATURAL memory order
// Negative freq (SC -26 to -1): bins 38-63
// Positive freq (SC +1 to +26): bins 1-26
//
static constexpr int kHeader48Sc[48] = {
    -26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
     1, 2, 3, 4, 5, 6,
     8, 9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26
};

// EXPLICIT FFT bin indices corresponding to kHeader48Sc order
// This ensures TX and RX use the EXACT same bin mapping!
static constexpr int kHeader48Bin[48] = {
    // Negative freq (SC -26 to -1): bins 38-63
    38, 39, 40, 41, 42,         // SC -26 to -22
    44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, // SC -20 to -8 (skip -7 pilot)
    58, 59, 60, 61, 62, 63,     // SC -6 to -1
    // Positive freq (SC +1 to +26): bins 1-26
    1, 2, 3, 4, 5, 6,           // SC +1 to +6
    8, 9,10,11,12,13,14,15,16,17,18,19,20,  // SC +8 to +20 (skip +7 pilot)
    22,23,24,25,26              // SC +22 to +26
};

static gr_complex saved_ltf0_raw52[52] = {gr_complex(0,0)};
static bool have_saved_ltf0_raw52 = false;

// Saved HT-LTF edge SC raw FFT values for H computation (natural FFT bins)
// [0]=SC-28(bin36), [1]=SC-27(bin37), [2]=SC+27(bin27), [3]=SC+28(bin28)
static gr_complex saved_htltf_edge[4] = {{0,0},{0,0},{0,0},{0,0}};
static bool htltf_edge_saved = false;

// Compute channel estimate H for 52 HT data subcarriers in tx_order from L-LTF0.
// lltf0_52: 48 data SCs in kHeader48Sc order + 4 pilots in kPilot4Sc order.
static void compute_H52_tx_order(const gr_complex* lltf0_52, gr_complex* H52_out)
{
    static const gr_complex kPilot4TX[4] = {
        gr_complex(+1.0f, 0.0f),   // SC -21
        gr_complex(-1.0f, 0.0f),   // SC -7
        gr_complex(+1.0f, 0.0f),   // SC +7
        gr_complex(+1.0f, 0.0f),   // SC +21
    };

    gr_complex H_sc[114] = {gr_complex(0.0f, 0.0f)};  // indexed by sc+56, covers -28..+28

    // Fill H for 48 header data subcarriers.
    // Use kLltf64Binned (correct TX reference at each FFT bin) * kFftNormalize
    // as the TX reference, matching the LS equalizer's approach:
    //   H = RX / (TX_ref * kFftNormalize)
    for (int i = 0; i < 48; i++) {
        int sc = kHeader48Sc[i];
        const int bin = kHeader48Bin[i];
        const gr_complex tx_ref = kLltf64Binned[bin];
        const gr_complex tx_scaled = tx_ref * kFftNormalize;
        if (std::abs(tx_scaled) > 1e-9f) {
            H_sc[sc + 56] = lltf0_52[i] / tx_scaled;
        }
    }
    // Fill H for 4 pilots
    for (int i = 0; i < 4; i++) {
        int sc = kPilot4Sc[i];
        const int bin = kPilot4Bin[i];
        const gr_complex tx_ref = kLltf64Binned[bin];
        const gr_complex tx_scaled = tx_ref * kFftNormalize;
        if (std::abs(tx_scaled) > 1e-9f) {
            H_sc[sc + 56] = lltf0_52[48 + i] / tx_scaled;
        }
    }

    // Compute H for edge subcarriers from saved HT-LTF raw FFT values.
    // Edge SCs (-28,-27,+27,+28) are NOT in the 52-element input array
    // (which contains only legacy 48 data + 4 pilots).
    // Use the saved HT-LTF raw FFT values captured at extract_call==6.
    // HT-LTF TX reference is +1 for all 4 edge SCs.
    if (htltf_edge_saved) {
        // Edge subcarriers use HT-LTF1 TX reference (+1.0f).
        // Include kFftNormalize for consistency with data/pilot H estimates.
        H_sc[-28 + 56] = saved_htltf_edge[0] / (+1.0f * kFftNormalize);  // SC -28, natural bin 36
        H_sc[-27 + 56] = saved_htltf_edge[1] / (+1.0f * kFftNormalize);  // SC -27, natural bin 37
        H_sc[27 + 56]  = saved_htltf_edge[2] / (+1.0f * kFftNormalize);  // SC +27, natural bin 27
        H_sc[28 + 56]  = saved_htltf_edge[3] / (+1.0f * kFftNormalize);  // SC +28, natural bin 28
    }

    // Copy to tx_order output
    for (int i = 0; i < 52; i++) {
        H52_out[i] = H_sc[kTxOrder52[i] + 56];
    }
}

// legacy L-LTF known signs on the 48 data carriers above
// These are just ±1 real values - used for sign check only
static constexpr int kLltf48Sign[48] = {
     1, 1,-1,-1, 1,
    -1, 1,-1, 1, 1, 1, 1, 1, 1,-1,-1, 1, 1,
     1,-1, 1, 1, 1, 1,
     1,-1,-1, 1, 1,-1,
    -1, 1,-1,-1,-1,-1,-1, 1, 1,-1,-1, 1,-1,
    -1, 1, 1, 1, 1
};

// SIGNAL / HT-SIG pilot values after channel equalization
static constexpr int kHeaderPilotBase[4] = {
    1, 1, 1, -1
};

// LTF pilot subcarrier values (SC -21, -7, +7, +21) from LEGACY_LTF
// These are the TX reference values for LTF pilot channel estimation
// kPilot4Sc order: {-21, -7, +7, +21}
static constexpr int kLltfPilotTX[4] = {
    1, -1, 1, 1
};

// ============================================================
// Header direct extraction from raw 64 FFT bins
// ============================================================
//
// d_early_eqsym[rel] 里缓存的布局固定为：
//   [0..47]  = 48 个 header data carriers（按 kHeader48Sc 顺序）
//   [48..51] = 4 个 pilots（按 kPilot4Sc 顺序）
//

// NAKED_TEST: Save raw LTF0 FFT for comparison with LTF1
gr_complex saved_ltf0_fft[64] = {gr_complex(0,0)};
bool ltf0_saved = false;
bool ltf0_ever_saved = false;
bool g_log_ltf0_fft = false;  // Bridge from d_log_ltf0_fft to static extract_header52_from_sym64
bool g_log_ltf0_fft_precomp = false;  // Bridge from d_log_ltf0_fft_precomp to static extract_header52_from_sym64
bool g_h_median_filter = false;  // Bridge from d_h_median_filter to static estimate_header_channel_from_lltf52
bool g_log_h52_filtered = false;  // Bridge from d_log_h52_filtered to call-site dump (post-filter)
bool g_log_h52_input = false;     // Bridge from d_log_h52_input to call-site dump (Hhdr52 at equalizer input)
bool g_log_frame_gain = false;    // Bridge from d_log_frame_gain to static extract_header52_from_sym64

static int g_extract_call_count = 0;

static void extract_header52_from_sym64(const gr_complex* sym64, gr_complex* out52)
{
    int extract_call_count = g_extract_call_count;

    // Call 0 = LTF0: save raw FFT for later edge H computation
    if (extract_call_count == 0) {
        memcpy(saved_ltf0_fft, sym64, 64 * sizeof(gr_complex));
        ltf0_saved = true;
        ltf0_ever_saved = true;

        // [FRAME_GAIN_DUMP] Phase 13 Task 1: dump time-domain input
        // energy for the L-LTF0 FFT window (64 samples). This runs at the
        // entry point of extract_header52_from_sym64, BEFORE any guard
        // (unlike H52_DUMP / E_I_DUMP which are blocked by
        // d_early_eqsym_valid on USRP per Phase 4). Used to confirm
        // upstream gain/agc state at the moment L-LTF0 FFT is captured.
        // Opt-in via IEEE80211_FRAME_GAIN_DUMP=1.
        if (g_log_frame_gain) {
            double e_in = 0.0;
            for (int j = 0; j < 64; j++) {
                e_in += std::norm(sym64[j]);
            }
            static int frame_gain_dump_counter = 0;
            // Note: single fprintf() with 2 args is safe (stderr unbuffered,
            // glibc serializes). Phase 9 snprintf+USRP_LOG rule applies to
            // multi-value dumps only. Format includes e_in_mean for
            // cross-gain AGC analysis (sum can be 64x larger than mean).
            fprintf(stderr, "[FRAME_GAIN_DUMP] fidx=%d e_in=%.2f e_in_mean=%.4f\n",
                    frame_gain_dump_counter++, e_in, e_in / 64.0);
        }

        // [LTF0_FFT_DUMP] Diagnostic: dump |saved_ltf0_fft[i]| and arg() for all
        // 64 FFT bins (then 52 active SCs) per frame. Opt-in via
        // IEEE80211_LTF0_FFT_DUMP=1. Atomic snprintf+USRP_LOG prevents
        // sync_short stdout shredding. Used in Phase 3 Stage 1 (reorganized)
        // to determine if L-LTF0 FFT is corrupted at the equalizer input.
        // Note: g_extract_call_count is static and may be 0 here, so we use
        // a separate file-static counter for per-frame uniqueness.
        if (g_log_ltf0_fft) {
            static const int sc_idx[52] = {
                -26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1,
                1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26
            };
            int n_bins = 64;
            static int ltf0_fft_dump_counter = 0;
            double sum_mag = 0.0, sum_mag2 = 0.0;
            int cnt = 0;
            for (int s = 0; s < 52; s++) {
                int k = sc_idx[s];
                if (k < 0) k += n_bins;
                float m = std::abs(saved_ltf0_fft[k]);
                sum_mag += m;
                sum_mag2 += (double)m * m;
                cnt++;
            }
            double mean_mag = (cnt > 0) ? sum_mag / cnt : 0.0;
            double var_mag = (cnt > 0) ? (sum_mag2 / cnt - mean_mag * mean_mag) : 0.0;
            double std_mag = (var_mag > 0) ? std::sqrt(var_mag) : 0.0;

            char dump[2048];
            int pn = snprintf(dump, sizeof(dump),
                              "[LTF0_FFT_DUMP] counter=%d |LLTF|=",
                              ltf0_fft_dump_counter++);
            for (int s = 0; s < 52 && pn < (int)sizeof(dump) - 32; s++) {
                int k = sc_idx[s];
                if (k < 0) k += n_bins;
                int w = snprintf(dump + pn, sizeof(dump) - pn, "%.3f,",
                                 std::abs(saved_ltf0_fft[k]));
                if (w < 0) break;
                pn += w;
            }
            pn += snprintf(dump + pn, sizeof(dump) - pn, " arg(LLTF)=");
            for (int s = 0; s < 52 && pn < (int)sizeof(dump) - 16; s++) {
                int k = sc_idx[s];
                if (k < 0) k += n_bins;
                int w = snprintf(dump + pn, sizeof(dump) - pn, "%.3f,",
                                 std::arg(saved_ltf0_fft[k]));
                if (w < 0) break;
                pn += w;
            }
            pn += snprintf(dump + pn, sizeof(dump) - pn,
                           " mean|LLTF|=%.3f std|LLTF|=%.3f\n",
                           mean_mag, std_mag);
            USRP_LOG("%s", dump);
        }

        // [LTF0_FFT_PRECOMP_DUMP] Companion diagnostic to LTF0_FFT_DUMP: dumps
        // the first 5 active subcarriers of the L-LTF0 FFT in complex (a+bi)
        // form, BEFORE any CFO/SFO compensation. Phase 10 root-cause finding:
        // L-SIG is decoded as enc=2/4/6/7 (non-BPSK) on USRP, which the
        // candidate loop then rejects. If L-LTF0 FFT is clean (BPSK ±1 on
        // data SCs) here, the bug is downstream (equalizer/H path). If
        // corrupted, the bug is upstream (splitter/timing/IQ/RF).
        // Enable via IEEE80211_LTF0_FFT_PRECOMP_DUMP=1. Atomic snprintf +
        // USRP_LOG("%s", buf) prevents sync_short stdout shredding (Phase 9).
        if (g_log_ltf0_fft_precomp) {
            static const int sc_idx[52] = {
                -26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1,
                1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26
            };
            static int ltf0_precomp_dump_counter = 0;
            char dump[1024];
            int pn = snprintf(dump, sizeof(dump),
                              "[LTF0_FFT_PRECOMP] counter=%d SC[0:5]=",
                              ltf0_precomp_dump_counter++);
            int n_bins = 64;
            for (int s = 0; s < 5 && pn < (int)sizeof(dump) - 80; s++) {
                int k = sc_idx[s];
                if (k < 0) k += n_bins;
                int w = snprintf(dump + pn, sizeof(dump) - pn,
                                 "%.3f%+.3fi ",
                                 saved_ltf0_fft[k].real(),
                                 saved_ltf0_fft[k].imag());
                if (w < 0) break;
                pn += w;
            }
            int w = snprintf(dump + pn, sizeof(dump) - pn,
                             " |SC[26]|=%.3f arg[26]=%.3f\n",
                             std::abs(saved_ltf0_fft[sc_idx[26] >= 0 ? sc_idx[26] : sc_idx[26] + n_bins]),
                             std::arg(saved_ltf0_fft[sc_idx[26] >= 0 ? sc_idx[26] : sc_idx[26] + n_bins]));
            if (w > 0) pn += w;
            (void)pn;
            USRP_LOG("%s", dump);
        }
    }

    if (extract_call_count == 1 && ltf0_saved) {
        ltf0_saved = false;
    }

    if (extract_call_count == 6 && ltf0_ever_saved) {
        // Save HT-LTF edge SC raw values for H computation
        // Edge bins in natural FFT order: SC-28→36, SC-27→37, SC+27→27, SC+28→28
        saved_htltf_edge[0] = sym64[36];  // SC -28
        saved_htltf_edge[1] = sym64[37];  // SC -27
        saved_htltf_edge[2] = sym64[27];  // SC +27
        saved_htltf_edge[3] = sym64[28];  // SC +28
        htltf_edge_saved = true;
    }

    extract_call_count++;
    g_extract_call_count = extract_call_count;

    for (int i = 0; i < 48; i++) {
        out52[i] = sym64[kHeader48Bin[i]];  // EXPLICIT bin mapping!
    }
    for (int i = 0; i < 4; i++) {
        out52[48 + i] = sym64[kPilot4Bin[i]];  // EXPLICIT bin mapping!
    }
}

static void extract_header_raw48_bits_from_cache52(const gr_complex* hdr52, uint8_t* out48)
{
    for (int i = 0; i < 48; i++) {
        out48[i] = hard_bit_from_complex(hdr52[i]);
    }
}

// 3-tap median filter over complex H52 (or Hhdr52).
// Sort key is |H[k]|; returns the complex value at the median position.
// Boundary handling: window=2 at i=0 and i=n-1.
//
// MUST match examples/test_h_median_filter_synthetic.py::apply_h_median_filter.
// Uses std::stable_sort on indices for the interior median, which guarantees
// stable tie-breaking (lower index wins on equal magnitudes) matching Python's
// sorted() stability.
//
// Opt-in via IEEE80211_H_MEDIAN_FILTER=1 (g_h_median_filter file-static, set in ctor).
// Caller is responsible for guarding on the flag.
static void apply_h_median_filter(const gr_complex* in, gr_complex* out, int n)
{
    if (n <= 0) {
        return;
    }
    if (n == 1) {
        out[0] = in[0];
        return;
    }

    // Pre-compute magnitudes once (3 abs calls per SC is wasteful otherwise)
    std::vector<float> mags(n);
    for (int i = 0; i < n; i++) {
        mags[i] = std::abs(in[i]);
    }

    // i = 0: window {0, 1}; lower |H| wins (use <= for stable tie-break)
    out[0] = (mags[0] <= mags[1]) ? in[0] : in[1];

    // i = 1..n-2: window {i-1, i, i+1}; pick complex with median |H|
    // On equal magnitudes, stable_sort preserves original-index order,
    // which matches Python's sorted() stability.
    for (int i = 1; i < n - 1; i++) {
        std::array<int, 3> idx = {i - 1, i, i + 1};
        std::stable_sort(idx.begin(), idx.end(),
                         [&mags](int a, int b) { return mags[a] < mags[b]; });
        out[i] = in[idx[1]];  // median position
    }

    // i = n-1: window {n-2, n-1}; lower |H| wins (use <= for stable tie-break)
    out[n - 1] = (mags[n - 2] <= mags[n - 1]) ? in[n - 2] : in[n - 1];
}

// NOTE: lltf1_52 is reserved for future use. The current implementation
// builds H52 from lltf0_52 only. Call sites may pass the same pointer
// for both args. Do not remove the parameter without updating both
// call sites in general_work.
static void estimate_header_channel_from_lltf52(const gr_complex* lltf0_52,
                                                const gr_complex* lltf1_52,
                                                gr_complex* H52)
{
    // Channel estimation using LTF0.
    // Use kLltf48TX (matching data path approach, no kFftNormalize).
    // The data path uses kLltf48TX for both H estimation and equalization.
    // The double error cancellation makes it work on both software loopback
    // and USRP. The header path previously used kLltf64Binned * kFftNormalize,
    // which produced wrong equalized symbols on USRP due to different FFT
    // normalization.

    // Compute H from LTF0 data subcarriers
    // Use kLltf48TX directly (matching data path approach).
    for (int i = 0; i < 48; i++) {
        const gr_complex lltf0 = lltf0_52[i];
        const gr_complex tx = kLltf48TX[i];
        if (std::abs(tx) > 0.001f) {
            H52[i] = lltf0 / tx;
        } else {
            H52[i] = lltf0;
        }
    }
    // Compute H from LTF0 pilot subcarriers
    // kPilot4Bin -> SC: -21, -7, +7, +21 -> kLltfPilotTX index: 0, 1, 2, 3
    for (int i = 0; i < 4; i++) {
        const gr_complex lltf0 = lltf0_52[48 + i];
        const gr_complex tx = gr_complex((float)kLltfPilotTX[i], 0.0f);
        if (std::abs(tx) > 0.001f) {
            H52[48 + i] = lltf0 / tx;
        } else {
            H52[48 + i] = lltf0;
        }
    }
}

static float estimate_header_cpe_rad(const gr_complex* rx52,
                                     const gr_complex* H52,
                                     bool is_ht_sig)
{
    gr_complex acc(0.0f, 0.0f);

    for (int i = 0; i < 4; i++) {
        const gr_complex eqp = safe_div(rx52[48 + i], H52[48 + i]);
        // For L-SIG: pilots are {1, 1, 1, -1} (real) - kHeaderPilotBase
        // For HT-SIG: pilots are {j, j, j, -j} (imaginary) due to QBPSK rotation
        gr_complex expect = gr_complex((float)kHeaderPilotBase[i], 0.0f);
        if (is_ht_sig) {
            expect *= gr_complex(0.0f, 1.0f);  // multiply by j for QBPSK rotated pilots
        }
        acc += eqp * std::conj(expect);
    }

    if (std::abs(acc) < 1e-9f) {
        return 0.0f;
    }

    return std::arg(acc);
}

// Alternative CPE estimation that directly uses rx pilots without H
static float estimate_cpe_direct_from_rx_pilots(const gr_complex* rx52)
{
    gr_complex acc(0.0f, 0.0f);

    for (int i = 0; i < 4; i++) {
        // Direct phase of received pilot (should be ±1 real if channel had no phase)
        // The TX pilots are {1, 1, 1, -1} (real)
        // So arg(rx) should be arg(H) if tx was real
        acc += rx52[48 + i];
    }

    if (std::abs(acc) < 1e-9f) {
        return 0.0f;
    }

    // The accumulated phase is the average channel phase at pilots
    return std::arg(acc);
}

static void equalize_header52_to_eq48_and_bits(const gr_complex* rx52,
                                               const gr_complex* H52,
                                               gr_complex* out_eq48,
                                               uint8_t* out_bits48,
                                               bool is_ht_sig)
{
    // NOTE: rx52 has already been phase-compensated in general_work.
    // Do NOT apply additional CPE compensation here.
    (void)is_ht_sig; // unused

    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52[i], H52[i]);
        }
        out_eq48[i] = eq;
        out_bits48[i] = hard_bit_from_complex(eq);
    }
}

static void equalize_header52_to_bits48(const gr_complex* rx52,
                                        const gr_complex* H52,
                                        uint8_t* out_bits48,
                                        gr_complex* out_eq48 = nullptr,
                                        bool is_ht_sig = false)
{
    gr_complex tmp_eq48[48];
    equalize_header52_to_eq48_and_bits(rx52, H52, tmp_eq48, out_bits48, is_ht_sig);
    if (out_eq48) {
        std::memcpy(out_eq48, tmp_eq48, 48 * sizeof(gr_complex));
    }
}

// ============================================================
// BPSK deinterleaver / Viterbi / CRC
// ============================================================

// TX interleave (802.11a/g clause 17.3.9.6):
//   Forward: bit at position k goes to position i = 3*(k mod 16) + floor(k/16)
//
// RX deinterleave (inverse operation):
//   To recover original position k from interleaved position i:
//   k = inv[i] where inv[] is the precomputed inverse mapping
//
// Precomputed inverse mapping for 48 subcarriers:
//   i:  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23
//   k:  0 16 32  1 17 33  2 18 34  3 19 35  4 20 36  5 21 37  6 22 38  7 23 39
//   i: 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47
//   k:  8 24 40  9 25 41 10 26 42 11 27 43 12 28 44 13 29 45 14 30 46 15 31 47
static void deinterleave_bpsk_48(const uint8_t* in48, uint8_t* out48)
{
    static const int deintl_inv_48[48] = {
        0, 16, 32,  1, 17, 33,  2, 18, 34,  3, 19, 35,  4, 20, 36,  5,
       21, 37,  6, 22, 38,  7, 23, 39,  8, 24, 40,  9, 25, 41, 10, 26,
       42, 11, 27, 43, 12, 28, 44, 13, 29, 45, 14, 30, 46, 15, 31, 47
    };
    std::memset(out48, 0, 48);

    // Correct inverse: out[inv[i]] = in[i]
    for (int i = 0; i < 48; i++) {
        out48[deintl_inv_48[i]] = in48[i] & 0x1;
    }
}

static bool viterbi_decode_133_171(const uint8_t* rx_bits,
                                   int n_encoded_bits,
                                   std::vector<uint8_t>& decoded_bits,
                                   int* out_best_metric = nullptr)
{
    if (n_encoded_bits <= 0 || (n_encoded_bits & 0x1)) {
        return false;
    }

    const int n_steps = n_encoded_bits / 2;
    const int INF = std::numeric_limits<int>::max() / 4;

    std::array<int, 64> metric_prev;
    std::array<int, 64> metric_curr;
    metric_prev.fill(INF);
    metric_prev[0] = 0;

    std::vector<std::array<int, 64>> prev_state(n_steps + 1);
    std::vector<std::array<uint8_t, 64>> prev_bit(n_steps + 1);

    for (int t = 0; t <= n_steps; t++) {
        prev_state[t].fill(-1);
        prev_bit[t].fill(0);
    }

    for (int t = 0; t < n_steps; t++) {
        metric_curr.fill(INF);

        const uint8_t r0 = rx_bits[2 * t] & 0x1;
        const uint8_t r1 = rx_bits[2 * t + 1] & 0x1;

        for (int s = 0; s < 64; s++) {
            const int mp = metric_prev[s];
            if (mp >= INF) {
                continue;
            }

            for (int b = 0; b <= 1; b++) {
                const int reg = ((s << 1) | b) & 0x7f;
                const uint8_t o0 = ones8_local(reg & 0133) & 0x1;
                const uint8_t o1 = ones8_local(reg & 0171) & 0x1;
                const int ns = reg & 0x3f;

                const int bm = ((o0 != r0) ? 1 : 0) + ((o1 != r1) ? 1 : 0);
                const int mc = mp + bm;

                if (mc < metric_curr[ns]) {
                    metric_curr[ns] = mc;
                    prev_state[t + 1][ns] = s;
                    prev_bit[t + 1][ns] = (uint8_t)b;
                }
            }
        }

        metric_prev = metric_curr;
    }

    int best_state = 0;
    int best_metric = metric_prev[best_state];
    if (best_metric >= INF) {
        best_metric = INF;
        for (int s = 0; s < 64; s++) {
            if (metric_prev[s] < best_metric) {
                best_metric = metric_prev[s];
                best_state = s;
            }
        }
        if (best_metric >= INF) {
            if (out_best_metric) *out_best_metric = INF;
            return false;
        }
    }
    if (out_best_metric) *out_best_metric = best_metric;

    decoded_bits.assign(n_steps, 0);

    for (int t = n_steps; t >= 1; t--) {
        decoded_bits[t - 1] = prev_bit[t][best_state];
        best_state = prev_state[t][best_state];
        if (best_state < 0 && t > 1) {
            return false;
        }
    }

    return true;
}

// HT-SIG CRC8:
// init all ones, polynomial x^8 + x^2 + x + 1, final invert
// input bits[0..33], LSB-first
static uint8_t ht_sig_crc8_calc(const uint8_t* bits0_33)
{
    int c[8];
    for (int i = 0; i < 8; i++) {
        c[i] = 1;
    }

    for (int i = 0; i < 34; i++) {
        const int m = bits0_33[i] ? 1 : 0;

        const int c0 = c[0];
        const int c1 = c[1];
        const int c2 = c[2];
        const int c3 = c[3];
        const int c4 = c[4];
        const int c5 = c[5];
        const int c6 = c[6];
        const int c7 = c[7];

        const int new7 = c6;
        const int new6 = c5;
        const int new5 = c4;
        const int new4 = c3;
        const int new3 = c2;
        const int new2 = c1 ^ c7 ^ m;
        const int new1 = c0 ^ c7 ^ m;
        const int new0 = c7 ^ m;

        c[0] = new0;
        c[1] = new1;
        c[2] = new2;
        c[3] = new3;
        c[4] = new4;
        c[5] = new5;
        c[6] = new6;
        c[7] = new7;
    }

    uint8_t crc = 0;
    for (int j = 0; j < 8; j++) {
        const int bit = (c[j] ^ 1) & 0x1;
        crc |= (uint8_t)(bit << j);
    }
    return crc;
}

// ============================================================
// 52-bit helper path (kept for compatibility with header methods)
// ============================================================

static void extract_header48_from_52_bits(const uint8_t* in52, uint8_t* out48)
{
    for (int i = 0; i < 48; i++) {
        out48[i] = in52[i + 2] & 0x1;
    }
}

static void extract_header48_from_52_eqsym(const gr_complex* in52, gr_complex* out48)
{
    for (int i = 0; i < 48; i++) {
        out48[i] = in52[i + 2];
    }
}

static bool decode_lsig_candidate(const uint8_t* raw_bits52,
                                  int reorder_mode,
                                  bool inverted,
                                  int& out_encoding,
                                  int& out_len_bytes)
{
    uint8_t bits52[52];
    uint8_t sig48[48];
    uint8_t deintl48[48];

    if (!reorder_bits_52_mode(raw_bits52, bits52, reorder_mode)) {
        return false;
    }

    extract_header48_from_52_bits(bits52, sig48);

    if (inverted) {
        for (int i = 0; i < 48; i++) {
            sig48[i] ^= 0x1;
        }
    }

    deinterleave_bpsk_48(sig48, deintl48);

    std::vector<uint8_t> dec24;
    if (!viterbi_decode_133_171(deintl48, 48, dec24)) {
        return false;
    }
    if ((int)dec24.size() != 24) {
        return false;
    }

    const uint8_t* decoded_bits = dec24.data();

    const int rate_field =
        ((decoded_bits[0] & 1) << 3) |
        ((decoded_bits[1] & 1) << 2) |
        ((decoded_bits[2] & 1) << 1) |
        ((decoded_bits[3] & 1) << 0);

    int psdu_length = 0;
    for (int i = 0; i < 12; i++) {
        psdu_length |= ((decoded_bits[5 + i] & 1) << i);
    }

    int parity_sum = 0;
    for (int i = 0; i < 18; i++) {
        parity_sum ^= (decoded_bits[i] & 1);
    }
    if (parity_sum != 0) {
        return false;
    }

    for (int i = 18; i < 24; i++) {
        if (decoded_bits[i] != 0) {
            return false;
        }
    }

    int encoding = -1;
    switch (rate_field) {
    case 0x0D: encoding = 0; break; // BPSK 1/2
    case 0x0F: encoding = 1; break; // BPSK 3/4
    case 0x05: encoding = 2; break; // QPSK 1/2
    case 0x07: encoding = 3; break; // QPSK 3/4
    case 0x09: encoding = 4; break; // 16QAM 1/2
    case 0x0B: encoding = 5; break; // 16QAM 3/4
    case 0x01: encoding = 6; break; // 64QAM 2/3
    case 0x03: encoding = 7; break; // 64QAM 3/4
    default:
        return false;
    }

    out_encoding = encoding;
    out_len_bytes = psdu_length;
    return true;
}

static bool decode_htsig_candidate(const uint8_t* raw_bits52_a,
                                   const uint8_t* raw_bits52_b,
                                   int reorder_mode,
                                   bool inverted_a,
                                   bool inverted_b,
                                   int& out_len_bytes,
                                   int& out_mcs,
                                   bool& out_sgi,
                                   bool& out_agg,
                                   bool& out_use_ldpc,
                                   int* out_vit_metric = nullptr,
                                   const char** out_fail_reason = nullptr)
{
    if (out_vit_metric) *out_vit_metric = -1;
    if (out_fail_reason) *out_fail_reason = "init";
    uint8_t bits52_a[52];
    uint8_t bits52_b[52];
    uint8_t sig48_a[48];
    uint8_t sig48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];

    if (!reorder_bits_52_mode(raw_bits52_a, bits52_a, reorder_mode)) {
        return false;
    }
    if (!reorder_bits_52_mode(raw_bits52_b, bits52_b, reorder_mode)) {
        return false;
    }

    extract_header48_from_52_bits(bits52_a, sig48_a);
    extract_header48_from_52_bits(bits52_b, sig48_b);

    if (inverted_a) {
        for (int i = 0; i < 48; i++) {
            sig48_a[i] ^= 0x1;
        }
    }
    if (inverted_b) {
        for (int i = 0; i < 48; i++) {
            sig48_b[i] ^= 0x1;
        }
    }

    deinterleave_bpsk_48(sig48_a, deintl48_a);
    deinterleave_bpsk_48(sig48_b, deintl48_b);

    for (int i = 0; i < 48; i++) {
        enc96[i]      = deintl48_a[i];
        enc96[48 + i] = deintl48_b[i];
    }

    // Phase 19 Task 2: HT-SIG input constellation dump.
    // Records the 96 bits fed to viterbi_decode_133_171 for post-mortem
    // analysis. Opt-in via IEEE80211_HTSIG_INPUT_DUMP=1.
    if (getenv("IEEE80211_HTSIG_INPUT_DUMP")) {
        char buf[512];
        int n = snprintf(buf, sizeof(buf),
                         "[HTSIG_INPUT_DUMP] inv_a=%d inv_b=%d enc96=",
                         inverted_a ? 1 : 0, inverted_b ? 1 : 0);
        for (int i = 0; i < 96 && n < (int)sizeof(buf); i++)
            n += snprintf(buf + n, sizeof(buf) - n, "%d", enc96[i]);
        snprintf(buf + n, sizeof(buf) - n, "\n");
        USRP_LOG("%s", buf);
    }

    std::vector<uint8_t> dec48;
    int vit_metric = -1;
    if (!viterbi_decode_133_171(enc96, 96, dec48, &vit_metric)) {
        // Phase 18 Task 3: HT-SIG viterbi audit log. Records the 96-bit
        // input that failed to converge to a valid 48-bit HT-SIG frame,
        // along with the path-metric from viterbi and the inversion state
        // of each HT-SIG OFDM symbol. Opt-in via
        // IEEE80211_HT_VITERBI_AUDIT=1. Single-call snprintf keeps the line
        // atomic against concurrent stdout writes from sync_short.
        if (getenv("IEEE80211_HT_VITERBI_AUDIT")) {
            // inv_a/inv_b are exposed by the calling function as
            // `inverted_a`/`inverted_b` (decode_htsig_candidate) or
            // `invert_a`/`invert_b` (decode_htsig_direct_from_header52,
            // decode_htsig_from_rotated). All three are bool, so we use
            // conditional reads guarded by site labels in the log header
            // so the consumer can tell which decoder produced the line.
            const char* site = "htsig_candidate";
            int inv_a = inverted_a ? 1 : 0;
            int inv_b = inverted_b ? 1 : 0;
            char ht_audit[896];
            int n = snprintf(ht_audit, sizeof(ht_audit),
                             "[HT_VITERBI_AUDIT] site=%s inv_a=%d inv_b=%d metric=%d enc96=",
                             site, inv_a, inv_b, vit_metric);
            for (int i = 0; i < 96 && n < (int)sizeof(ht_audit); i++)
                n += snprintf(ht_audit + n, sizeof(ht_audit) - n, "%d", enc96[i]);
            snprintf(ht_audit + n, sizeof(ht_audit) - n, "\n");
            USRP_LOG("%s", ht_audit);
        }
        if (out_vit_metric) *out_vit_metric = vit_metric;
        if (out_fail_reason) *out_fail_reason = "viterbi_fail";
        return false;
    }
    if (out_vit_metric) *out_vit_metric = vit_metric;
    if ((int)dec48.size() != 48) {
        if (out_fail_reason) *out_fail_reason = "dec48_size";
        return false;
    }

    const uint8_t* decoded_bits = dec48.data();

    int mcs = 0;
    int psdu_length = 0;
    bool aggregation = false;
    bool short_gi = false;

    for (int i = 0; i < 7; i++) {
        mcs |= ((decoded_bits[i] & 1) << i);
    }

    const int bw40 = decoded_bits[7] & 1;

    for (int i = 0; i < 16; i++) {
        psdu_length |= ((decoded_bits[8 + i] & 1) << i);
    }

    const int rsv0 = decoded_bits[24] & 1;
    const int rsv1 = decoded_bits[25] & 1;
    const int rsv2 = decoded_bits[26] & 1;

    aggregation = ((decoded_bits[27] & 1) != 0);

    const int stbc =
        ((decoded_bits[28] & 1) << 0) |
        ((decoded_bits[29] & 1) << 1);

    const int adv_coding = decoded_bits[30] & 1;
    short_gi = ((decoded_bits[31] & 1) != 0);

    const int num_ht_ltf =
        ((decoded_bits[32] & 1) << 0) |
        ((decoded_bits[33] & 1) << 1);

    uint8_t crc_rx = 0;
    for (int i = 0; i < 8; i++) {
        crc_rx |= ((decoded_bits[34 + i] & 1) << i);
    }

    const uint8_t crc_calc = ht_sig_crc8_calc(decoded_bits);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
                char buf[256];
                int n = snprintf(buf, sizeof(buf),
                                 "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                                 "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                                 "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                                 "tail_nonzero", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                                 adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                                 crc_rx, crc_calc);
                for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                    n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
                snprintf(buf + n, sizeof(buf) - n, "\n");
                USRP_LOG("%s", buf);
            }
            if (out_fail_reason) *out_fail_reason = "tail_nonzero";
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "crc_fail", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "crc_fail";
        return false;
    }

    if (bw40 != 0) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "bw40_set", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "bw40_set";
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "rsv_set", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "rsv_set";
        return false;
    }
    // adv_coding: 0=BCC, 1=LDPC - both are valid now
    if (adv_coding != 0 && adv_coding != 1) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "adv_coding_bad", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "adv_coding_bad";
        return false;
    }

    if (mcs < 0 || mcs > 7) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "mcs_oor", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "mcs_oor";
        return false;
    }
    if (psdu_length <= 0) {
        if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                             "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                             "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                             "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                             "len_zero", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                             adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                             crc_rx, crc_calc);
            for (int i = 0; i < 48 && n < (int)sizeof(buf); i++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[i]);
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
        }
        if (out_fail_reason) *out_fail_reason = "len_zero";
        return false;
    }

    out_len_bytes = psdu_length;
    out_mcs = mcs;
    out_sgi = short_gi;
    out_agg = aggregation;
    out_use_ldpc = (adv_coding == 1);
    if (out_fail_reason) *out_fail_reason = "OK";
    return true;
}

// ============================================================
// HT-SIG QBPSK rotation detection and compensation
// ============================================================

// Rotation codes:
//   0 = no rotation (0°)
//   1 = +90° rotation (multiply by j)
//   2 = -90° rotation (multiply by -j)
//   3 = 180° rotation (multiply by -1)

static inline gr_complex get_htsig_rotation_factor(int rotation)
{
    switch (rotation) {
        case 0: return gr_complex(1.0f, 0.0f);   // 0°
        case 1: return gr_complex(0.0f, 1.0f);    // +90°
        case 2: return gr_complex(0.0f, -1.0f);   // -90°
        case 3: return gr_complex(-1.0f, 0.0f);   // 180°
        default: return gr_complex(1.0f, 0.0f);
    }
}

// Detect HT-SIG QBPSK rotation by analyzing pilot phases
// HT-SIG pilots are at indices 48-51 (subcarriers -21, -7, +7, +21)
static int detect_htsig_rotation(const gr_complex* ht_sig_eq52)
{
    gr_complex pilot_sum(0.0f, 0.0f);
    int pilot_count = 0;

    for (int i = 0; i < 4; i++) {
        const gr_complex pilot = ht_sig_eq52[48 + i];
        pilot_sum += pilot;
        pilot_count++;
    }

    if (pilot_count == 0) {
        return 0;
    }

    float avg_phase = std::arg(pilot_sum);
    const float PI = 3.14159265358979f;

    // Classify based on phase angle (±45° tolerance)
    if (avg_phase >= -PI/4 && avg_phase < PI/4) {
        return 0;  // No rotation (0°)
    } else if (avg_phase >= PI/4 && avg_phase < 3*PI/4) {
        return 1;  // +90° rotation
    } else if (avg_phase >= -3*PI/4 && avg_phase < -PI/4) {
        return 2;  // -90° rotation
    } else {
        return 3;  // 180° rotation
    }
}

// Apply rotation compensation to HT-SIG before decoding
static void apply_htsig_rotation(const gr_complex* in52, gr_complex* out52, int rotation)
{
    gr_complex rot = get_htsig_rotation_factor(rotation);
    for (int i = 0; i < 52; i++) {
        out52[i] = in52[i] * std::conj(rot);
    }
}

// ============================================================
// Direct header decode from raw sym64-derived cached header52
// ============================================================

static bool decode_lsig_direct_from_header52(const gr_complex* rx52,
                                             const gr_complex* H52,
                                             bool invert_bits,
                                             int& out_encoding,
                                             int& out_len_bytes,
                                             int* out_rate_field = nullptr,
                                             int* out_psdu_length = nullptr,
                                             int* out_parity_ok = nullptr,
                                             uint8_t* dbg_eqbits48 = nullptr,
                                             uint8_t* dbg_deintl48 = nullptr)
{
    uint8_t eqbits48[48];
    uint8_t deintl48[48];

    // NOTE: rx52 (d_early_eqsym) has already been phase-compensated in general_work
    // using per-subcarrier linear regression (CFO+SFO). Do NOT apply CPE again.
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52[i], H52[i]);
        }
        eqbits48[i] = hard_bit_from_complex(eq);
    }

    if (invert_bits) {
        for (int i = 0; i < 48; i++) {
            eqbits48[i] ^= 0x1;
        }
    }

    if (dbg_eqbits48) {
        std::memcpy(dbg_eqbits48, eqbits48, 48);
    }

    deinterleave_bpsk_48(eqbits48, deintl48);

    if (dbg_deintl48) {
        std::memcpy(dbg_deintl48, deintl48, 48);
    }

    // Diagnostic: print first 6 eq symbols (real, imag) and eqbits
    {
        USRP_LOG("[LSIG_EQ] inv=%d eq[0-5]=", invert_bits?1:0);
        for (int i = 0; i < 6; i++) {
            gr_complex eqsym = safe_div(rx52[i], H52[i]);
            USRP_LOG("(%.2f,%.2f) ", eqsym.real(), eqsym.imag());
        }
        USRP_LOG(" bits=");
        for (int i = 0; i < 12; i++) USRP_LOG("%d", eqbits48[i]);
        USRP_LOG("\n");
    }

    std::vector<uint8_t> dec24;
    if (!viterbi_decode_133_171(deintl48, 48, dec24)) {
        USRP_LOG("[LSIG_DECODE] FAIL: viterbi decode failed\n");
        // Path-metric audit: write the entire audit line in a single snprintf
        // so concurrent stdout writes from sync_short can't interleave mid-line.
        // FAIL path also dumps the first 6 equalized L-SIG constellation values
        // (eqsym_r/eqsym_i) so we can see whether inputs are noise-like or
        // signal-with-residual-rotation.
        char audit[384];
        int n = snprintf(audit, sizeof(audit), "[LSIG_VITERBI_AUDIT] inv=%d deintl48=", invert_bits?1:0);
        for (int i = 0; i < 48 && n < (int)sizeof(audit); i++)
            n += snprintf(audit+n, sizeof(audit)-n, "%d", deintl48[i]);
        n += snprintf(audit+n, sizeof(audit)-n, " eqsym_r=");
        for (int i = 0; i < 6 && n < (int)sizeof(audit); i++) {
            gr_complex eqsym = safe_div(rx52[i], H52[i]);
            n += snprintf(audit+n, sizeof(audit)-n, "%.2f ", eqsym.real());
        }
        n += snprintf(audit+n, sizeof(audit)-n, "eqsym_i=");
        for (int i = 0; i < 6 && n < (int)sizeof(audit); i++) {
            gr_complex eqsym = safe_div(rx52[i], H52[i]);
            n += snprintf(audit+n, sizeof(audit)-n, "%.2f ", eqsym.imag());
        }
        snprintf(audit+n, sizeof(audit)-n, "\n");
        USRP_LOG("%s", audit);
        return false;
    }
    if ((int)dec24.size() != 24) {
        return false;
    }

    const uint8_t* decoded_bits = dec24.data();

    const int rate_field =
        ((decoded_bits[0] & 1) << 3) |
        ((decoded_bits[1] & 1) << 2) |
        ((decoded_bits[2] & 1) << 1) |
        ((decoded_bits[3] & 1) << 0);

    int psdu_length = 0;
    for (int i = 0; i < 12; i++) {
        psdu_length |= ((decoded_bits[5 + i] & 1) << i);
    }

    int parity_sum = 0;
    for (int i = 0; i < 18; i++) {
        parity_sum ^= (decoded_bits[i] & 1);
    }
    const int parity_ok = (parity_sum == 0) ? 1 : 0;

    if (out_rate_field)   *out_rate_field   = rate_field;
    if (out_psdu_length)  *out_psdu_length  = psdu_length;
    if (out_parity_ok)    *out_parity_ok    = parity_ok;

    if (parity_sum != 0) {
        return false;
    }

    for (int i = 18; i < 24; i++) {
        if (decoded_bits[i] != 0) {
            return false;
        }
    }
    int encoding = -1;
    switch (rate_field) {
    case 0x0D: encoding = 0; break;
    case 0x0F: encoding = 1; break;
    case 0x05: encoding = 2; break;
    case 0x07: encoding = 3; break;
    case 0x09: encoding = 4; break;
    case 0x0B: encoding = 5; break;
    case 0x01: encoding = 6; break;
    case 0x03: encoding = 7; break;
    default:
        return false;
    }

    out_encoding = encoding;
    out_len_bytes = psdu_length;
    USRP_LOG("[LSIG_DECODE] OK enc=%d len=%d\n", encoding, psdu_length);
    // Path-metric audit: write the entire audit line in a single snprintf
    // so concurrent stdout writes from sync_short can't interleave mid-line.
    char audit[384];
    int n = snprintf(audit, sizeof(audit), "[LSIG_VITERBI_AUDIT] inv=%d deintl48=", invert_bits?1:0);
    for (int i = 0; i < 48 && n < (int)sizeof(audit); i++)
        n += snprintf(audit+n, sizeof(audit)-n, "%d", deintl48[i]);
    n += snprintf(audit+n, sizeof(audit)-n, " decoded24=");
    for (int i = 0; i < 24 && n < (int)sizeof(audit); i++)
        n += snprintf(audit+n, sizeof(audit)-n, "%d", decoded_bits[i]);
    snprintf(audit+n, sizeof(audit)-n, "\n");
    USRP_LOG("%s", audit);
    // Phase 18 Task 3: L-SIG structural validity check. After viterbi
    // decode returns 24 bits, check whether they actually parse as a
    // valid L-SIG word (rate=0xD for HT, length>0, parity=0, tail=000000).
    // If the decoder reached this SUCCESS branch the viterbi returned 24
    // bits, but the L-SIG fields may still be garbage if upstream noise
    // caused the viterbi to converge on a wrong codeword. Opt-in via
    // IEEE80211_LSIG_VALIDITY_AUDIT=1.
    if (getenv("IEEE80211_LSIG_VALIDITY_AUDIT")) {
        uint32_t decoded24_int = 0;
        for (int i = 0; i < 24; i++) {
            decoded24_int |= ((uint32_t)(decoded_bits[i] & 1) << (23 - i));
        }
        const int rate_f  = (decoded24_int >> 20) & 0xF;
        const int len_f   = (decoded24_int >> 8)  & 0xFFF;
        const int par     = (decoded24_int >> 7)  & 0x1;
        const int tail_f  = decoded24_int & 0x3F;
        // Recompute parity over the 18-bit SIGNAL field per 802.11n §17.3.4.
        int parity_recomputed = 0;
        for (int i = 0; i < 18; i++) parity_recomputed ^= (decoded_bits[i] & 1);
        // "valid" means: rate_field is 0xD (BPSK 1/2 — required for HT),
        // length > 0, parity bit = 0, and tail bits all zero.
        const int valid = (rate_f == 0xD && len_f > 0 && par == 0 && tail_f == 0) ? 1 : 0;
        char validity[256];
        snprintf(validity, sizeof(validity),
                 "[LSIG_VALIDITY] rate_field=0x%X length_field=%d parity=%d tail_field=0x%02X "
                 "parity_recomputed=%d valid=%d\n",
                 rate_f, len_f, par, tail_f, parity_recomputed, valid);
        USRP_LOG("%s", validity);
    }
    // Phase 18 Task 4: Reject L-SIG decodes whose rate_field doesn't match the
    // configured expected rate (default 0xD — BPSK 1/2 required for HT).
    // Without this, the viterbi converges on noise-induced wrong codewords
    // (rate_field 0x1/0x3/0x5/0x7/0x9/0xB/0xF appearing in ~94% of cases at
    // 5 GHz A:0). Those wrong-rate codewords either get skipped at the
    // lsig_enc != 0 gating (line 3286) or — with FORCE_HTSIG=1 — proceed to
    // HT-SIG brute-force, which they can't possibly satisfy. Rejecting them
    // at the source avoids both failure modes. Override the expected rate via
    // IEEE80211_LSIG_RATE_FORCE=<hex>; default 0xD.
    if (getenv("IEEE80211_LSIG_RATE_FORCE")) {
        uint32_t decoded24_int = 0;
        for (int i = 0; i < 24; i++) {
            decoded24_int |= ((uint32_t)(decoded_bits[i] & 1) << (23 - i));
        }
        const int rate_f = (decoded24_int >> 20) & 0xF;
        const int expected_rate = 0xD;  // BPSK 1/2 — required for 802.11n HT
        if (rate_f != expected_rate) {
            // Opt-in audit log for the rejection (same env-gate as the validity check)
            if (getenv("IEEE80211_LSIG_VALIDITY_AUDIT")) {
                char reject[160];
                snprintf(reject, sizeof(reject),
                         "[LSIG_REJECT] rate_field=0x%X expected=0x%X reason=rate_mismatch\n",
                         rate_f, expected_rate);
                USRP_LOG("%s", reject);
            }
            return false;
        }
    }
    return true;
}

static bool decode_htsig_direct_from_header52(const gr_complex* rx52_a,
                                              const gr_complex* rx52_b,
                                              const gr_complex* H52,
                                              bool invert_a,
                                              bool invert_b,
                                              int& out_len_bytes,
                                              int& out_mcs,
                                              bool& out_sgi,
                                              bool& out_agg,
                                              uint8_t* dbg_eqbits48_a = nullptr,
                                              uint8_t* dbg_eqbits48_b = nullptr,
                                              uint8_t* dbg_deintl48_a = nullptr,
                                              uint8_t* dbg_deintl48_b = nullptr,
                                              int* out_vit_metric = nullptr,
                                              const char** out_fail_reason = nullptr)
{
    if (out_vit_metric) *out_vit_metric = -1;
    if (out_fail_reason) *out_fail_reason = "init";
    uint8_t eqbits48_a[48];
    uint8_t eqbits48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];

    equalize_header52_to_bits48(rx52_a, H52, eqbits48_a, nullptr, true);  // true = HT-SIG
    equalize_header52_to_bits48(rx52_b, H52, eqbits48_b, nullptr, true);  // true = HT-SIG

    if (invert_a) {
        for (int i = 0; i < 48; i++) {
            eqbits48_a[i] ^= 0x1;
        }
    }
    if (invert_b) {
        for (int i = 0; i < 48; i++) {
            eqbits48_b[i] ^= 0x1;
        }
    }

    if (dbg_eqbits48_a) {
        std::memcpy(dbg_eqbits48_a, eqbits48_a, 48);
    }
    if (dbg_eqbits48_b) {
        std::memcpy(dbg_eqbits48_b, eqbits48_b, 48);
    }

    deinterleave_bpsk_48(eqbits48_a, deintl48_a);
    deinterleave_bpsk_48(eqbits48_b, deintl48_b);

    if (dbg_deintl48_a) {
        std::memcpy(dbg_deintl48_a, deintl48_a, 48);
    }
    if (dbg_deintl48_b) {
        std::memcpy(dbg_deintl48_b, deintl48_b, 48);
    }

    for (int i = 0; i < 48; i++) {
        enc96[i]      = deintl48_a[i];
        enc96[48 + i] = deintl48_b[i];
    }

    std::vector<uint8_t> dec48;
    int vit_metric = -1;
    if (!viterbi_decode_133_171(enc96, 96, dec48, &vit_metric)) {
        // Phase 18 Task 3: HT-SIG viterbi audit log. Records the 96-bit
        // input that failed to converge to a valid 48-bit HT-SIG frame,
        // along with the path-metric from viterbi and the inversion state
        // of each HT-SIG OFDM symbol. Opt-in via
        // IEEE80211_HT_VITERBI_AUDIT=1. Single-call snprintf keeps the line
        // atomic against concurrent stdout writes from sync_short.
        if (getenv("IEEE80211_HT_VITERBI_AUDIT")) {
            const char* site = "htsig_direct";
            int inv_a = invert_a ? 1 : 0;
            int inv_b = invert_b ? 1 : 0;
            char ht_audit[896];
            int n = snprintf(ht_audit, sizeof(ht_audit),
                             "[HT_VITERBI_AUDIT] site=%s inv_a=%d inv_b=%d metric=%d enc96=",
                             site, inv_a, inv_b, vit_metric);
            for (int i = 0; i < 96 && n < (int)sizeof(ht_audit); i++)
                n += snprintf(ht_audit + n, sizeof(ht_audit) - n, "%d", enc96[i]);
            snprintf(ht_audit + n, sizeof(ht_audit) - n, "\n");
            USRP_LOG("%s", ht_audit);
        }
        if (out_vit_metric) *out_vit_metric = vit_metric;
        if (out_fail_reason) *out_fail_reason = "viterbi_fail";
        return false;
    }
    if (out_vit_metric) *out_vit_metric = vit_metric;
    if ((int)dec48.size() != 48) {
        if (out_fail_reason) *out_fail_reason = "dec48_size";
        return false;
    }

    const uint8_t* decoded_bits = dec48.data();

    int mcs = 0;
    int psdu_length = 0;
    bool aggregation = false;
    bool short_gi = false;

    for (int i = 0; i < 7; i++) {
        mcs |= ((decoded_bits[i] & 1) << i);
    }

    const int bw40 = decoded_bits[7] & 1;

    for (int i = 0; i < 16; i++) {
        psdu_length |= ((decoded_bits[8 + i] & 1) << i);
    }

    const int rsv0 = decoded_bits[24] & 1;
    const int rsv1 = decoded_bits[25] & 1;
    const int rsv2 = decoded_bits[26] & 1;

    aggregation = ((decoded_bits[27] & 1) != 0);

    const int stbc =
        ((decoded_bits[28] & 1) << 0) |
        ((decoded_bits[29] & 1) << 1);

    const int adv_coding = decoded_bits[30] & 1;
    short_gi = ((decoded_bits[31] & 1) != 0);

    const int num_ht_ltf =
        ((decoded_bits[32] & 1) << 0) |
        ((decoded_bits[33] & 1) << 1);

    uint8_t crc_rx = 0;
    for (int i = 0; i < 8; i++) {
        crc_rx |= ((decoded_bits[34 + i] & 1) << i);
    }

    const uint8_t crc_calc = ht_sig_crc8_calc(decoded_bits);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        return false;
    }

    if (bw40 != 0) {
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        return false;
    }
    if (adv_coding != 0) {
        return false;
    }

    if (mcs < 0 || mcs > 7) {
        return false;
    }
    if (psdu_length <= 0) {
        return false;
    }

    out_len_bytes = psdu_length;
    out_mcs = mcs;
    out_sgi = short_gi;
    out_agg = aggregation;
    return true;
}

// Simplified HT-SIG decode for QBPSK-rotated symbols
// Skips CPE rotation since QBPSK already compensates for phase
static bool decode_htsig_from_rotated(const gr_complex* rx52_a,
                                       const gr_complex* rx52_b,
                                       const gr_complex* H52,
                                       bool invert_a,
                                       bool invert_b,
                                       int& out_len_bytes,
                                       int& out_mcs,
                                       bool& out_sgi,
                                       bool& out_agg,
                                       bool& out_use_ldpc,
                                       int rot = -1,
                                       int* out_vit_metric = nullptr,
                                       const char** out_fail_reason = nullptr)
{
    if (out_vit_metric) *out_vit_metric = -1;
    if (out_fail_reason) *out_fail_reason = "init";
    uint8_t eqbits48_a[48];
    uint8_t eqbits48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];

    // NOTE: rx52_a/b (d_early_eqsym) has already been phase-compensated in general_work.
    // Do NOT apply additional CPE compensation here.

    // Extract bits from HT-SIG0 (rx52_a)
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52_a[i], H52[i]);
        }
        // QBPSK: HT-SIG is rotated by 90° (mult by j), so bits are on IMAG axis
        // bit 0 → -j (imag < 0), bit 1 → +j (imag >= 0)
        eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
    }

    // Extract bits from HT-SIG1 (rx52_b)
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52_b[i], H52[i]);
        }
        // QBPSK: HT-SIG is rotated by 90° (mult by j), so bits are on IMAG axis
        eqbits48_b[i] = (eq.imag() >= 0.0f) ? 1 : 0;
    }

    if (invert_a) {
        for (int i = 0; i < 48; i++) {
            eqbits48_a[i] ^= 0x1;
        }
    }
    if (invert_b) {
        for (int i = 0; i < 48; i++) {
            eqbits48_b[i] ^= 0x1;
        }
    }

    // HT-SIG Deinterleaving: undo the 802.11 permutation
    // Forward interleaver: j = 3*(k%16) + k/16
    // Deinterleaver uses same formula (since 2nd permutation is identity for BPSK)
    for (int k = 0; k < 48; k++) {
        const int j = 3 * (k % 16) + k / 16;
        deintl48_a[k] = eqbits48_a[j] & 0x1;
    }
    for (int k = 0; k < 48; k++) {
        const int j = 3 * (k % 16) + k / 16;
        deintl48_b[k] = eqbits48_b[j] & 0x1;
    }

    for (int i = 0; i < 48; i++) {
        enc96[i]      = deintl48_a[i];
        enc96[48 + i] = deintl48_b[i];
    }

    std::vector<uint8_t> dec48;
    int vit_metric = -1;
    if (!viterbi_decode_133_171(enc96, 96, dec48, &vit_metric)) {
        // Phase 18 Task 3: HT-SIG viterbi audit log. Records the 96-bit
        // input that failed to converge to a valid 48-bit HT-SIG frame,
        // along with the path-metric from viterbi and the inversion state
        // of each HT-SIG OFDM symbol. Opt-in via
        // IEEE80211_HT_VITERBI_AUDIT=1. Single-call snprintf keeps the line
        // atomic against concurrent stdout writes from sync_short.
        if (getenv("IEEE80211_HT_VITERBI_AUDIT")) {
            const char* site = "htsig_rotated";
            int inv_a = invert_a ? 1 : 0;
            int inv_b = invert_b ? 1 : 0;
            char ht_audit[896];
            int n = snprintf(ht_audit, sizeof(ht_audit),
                             "[HT_VITERBI_AUDIT] site=%s inv_a=%d inv_b=%d metric=%d enc96=",
                             site, inv_a, inv_b, vit_metric);
            for (int i = 0; i < 96 && n < (int)sizeof(ht_audit); i++)
                n += snprintf(ht_audit + n, sizeof(ht_audit) - n, "%d", enc96[i]);
            snprintf(ht_audit + n, sizeof(ht_audit) - n, "\n");
            USRP_LOG("%s", ht_audit);
        }
        if (out_vit_metric) *out_vit_metric = vit_metric;
        if (out_fail_reason) *out_fail_reason = "viterbi_fail";
        return false;
    }
    if (out_vit_metric) *out_vit_metric = vit_metric;
    if ((int)dec48.size() != 48) {
        if (out_fail_reason) *out_fail_reason = "dec48_size";
        return false;
    }

    const uint8_t* decoded_bits = dec48.data();

    int mcs = 0;
    int psdu_length = 0;
    bool aggregation = false;
    bool short_gi = false;

    for (int i = 0; i < 7; i++) {
        mcs |= ((decoded_bits[i] & 1) << i);
    }

    const int bw40 = decoded_bits[7] & 1;

    for (int i = 0; i < 16; i++) {
        psdu_length |= ((decoded_bits[8 + i] & 1) << i);
    }

    const int rsv0 = decoded_bits[24] & 1;
    const int rsv1 = decoded_bits[25] & 1;
    const int rsv2 = decoded_bits[26] & 1;

    aggregation = ((decoded_bits[27] & 1) != 0);

    const int stbc =
        ((decoded_bits[28] & 1) << 0) |
        ((decoded_bits[29] & 1) << 1);

    const int adv_coding = decoded_bits[30] & 1;
    short_gi = ((decoded_bits[31] & 1) != 0);

    const int num_ht_ltf =
        ((decoded_bits[32] & 1) << 0) |
        ((decoded_bits[33] & 1) << 1);

    uint8_t crc_rx = 0;
    for (int i = 0; i < 8; i++) {
        crc_rx |= ((decoded_bits[34 + i] & 1) << i);
    }

    const uint8_t crc_calc = ht_sig_crc8_calc(decoded_bits);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            if (out_fail_reason) *out_fail_reason = "tail_nonzero";
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        if (out_fail_reason) *out_fail_reason = "crc_fail";
        return false;
    }

    if (bw40 != 0) {
        if (out_fail_reason) *out_fail_reason = "bw40_set";
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        if (out_fail_reason) *out_fail_reason = "rsv_set";
        return false;
    }
    // adv_coding: 0=BCC, 1=LDPC - both are valid now
    if (adv_coding != 0 && adv_coding != 1) {
        if (out_fail_reason) *out_fail_reason = "adv_coding_bad";
        return false;
    }

    if (mcs < 0 || mcs > 7) {
        if (out_fail_reason) *out_fail_reason = "mcs_oor";
        return false;
    }
    if (psdu_length <= 0) {
        if (out_fail_reason) *out_fail_reason = "len_zero";
        return false;
    }

    out_len_bytes = psdu_length;
    out_mcs = mcs;
    out_sgi = short_gi;
    out_agg = aggregation;
    out_use_ldpc = (adv_coding == 1);
    if (out_fail_reason) *out_fail_reason = "OK";
    return true;
}

// Estimate CFO from L-LTF0 and L-LTF1 phase difference.
// L-LTF0 and L-LTF1 transmit the same sequence; any common phase
// rotation between them is due to CFO. Returns rad/sample.
static float estimate_cfo_from_lltf52(const gr_complex* lltf0,
                                       const gr_complex* lltf1)
{
    double phase_sum = 0.0;
    int count = 0;
    for (int i = 0; i < 52; i++) {
        // Skip bins with near-zero energy (guard/pilot holes)
        if (std::abs(lltf0[i]) < 1e-6f || std::abs(lltf1[i]) < 1e-6f) {
            continue;
        }
        gr_complex ratio = lltf1[i] * std::conj(lltf0[i]);
        float phase = std::arg(ratio);
        phase_sum += phase;
        count++;
    }
    return (count > 0) ? (float)(phase_sum / count) : 0.0f;
}

} // anonymous namespace

// ======================================================================

frame_equalizer::sptr
frame_equalizer::make(Equalizer algo, double freq, double bw, bool log, bool debug)
{
    return frame_equalizer::sptr(
        new frame_equalizer_impl(algo, freq, bw, log, debug));
}

// Calculate energy distribution across 52 subcarriers
void frame_equalizer_impl::compute_subcarrier_energy(const gr_complex* eq52, double& Esum_I, double& Esum_Q)
{
    Esum_I = 0.0;
    Esum_Q = 0.0;
    for (int i = 0; i < 48; i++) {  // 48 data subcarriers (excluding pilots)
        Esum_I += (double)eq52[i].real() * eq52[i].real();
        Esum_Q += (double)eq52[i].imag() * eq52[i].imag();
    }
}

// QBPSK rotation detection via constellation energy voting
int frame_equalizer_impl::vote_qbpsk_rotation(const gr_complex* eq_data)
{
    double E_I, E_Q;
    compute_subcarrier_energy(eq_data, E_I, E_Q);

    // Epsilon 1e-10: prevents division by zero when E_I is negligible
    // Threshold 1.0: QBPSK should have E_Q > E_I (ratio > 1.0)
    //   - HT-SIG with QBPSK rotation: E_Q > E_I
    //   - Legacy BPSK: E_I > E_Q
    double ratio = (E_I > 1e-10) ? E_Q / E_I : 0.0;

    return (ratio > 1.0) ? 1 : 0;
}

frame_equalizer_impl::frame_equalizer_impl(Equalizer algo,
                                           double freq,
                                           double bw,
                                           bool log,
                                           bool debug)
    : gr::block("frame_equalizer",
                gr::io_signature::make(1, 1, sizeof(gr_complex) * 64),
                gr::io_signature::make(1, 1, sizeof(gr_complex))),
      d_current_symbol(0),
      d_copied(0),
      d_debug(debug),
      d_log(log),
      d_freq_offset_from_synclong(freq),
      d_bw((int)bw),
      d_chan_est_mode(0),
      d_enable_soft_output(false),
      d_use_lltf1_for_h(false),  // OFF by default; flip to true via env
      d_frame_bytes(0),
      d_frame_encoding(0),
      d_frame_mcs(0),
      d_frame_symbols(0),
      d_frame_mod(1),
      d_frame_n_bpsc(1),
      d_frame_n_cbps(52),
      d_frame_n_dbps(26),
      d_use_ldpc(false),
      d_ldpc_n_sym(-1),
      d_have_header(false),
      d_have_ht_header(false),
      d_is_ht(false),
      d_sym_idx(0),
      d_internal_symbol_counter(0),
      d_first_valid_symbol(-1),
      d_in_frame(false),
      d_have_lsig(false),
      d_lsig_rel(-1),
      d_hdr_reorder_mode(0),
      d_hdr_inverted(false),
      d_htsig0_rel(-1),
      d_htsig1_rel(-1),
      d_data_start_rel(kDataStartRel),
      d_is_ht_frame(false)
{
    d_bpsk = make_bpsk_constellation();
    d_qpsk = make_qpsk_constellation();
    d_16qam = make_16qam_constellation();
    d_64qam = make_64qam_constellation();

    set_tag_propagation_policy(TPP_DONT);
    message_port_register_out(pmt::mp("symbols"));
    std::memset(d_early_bits, 0, sizeof(d_early_bits));
    std::memset(d_early_bits_valid, 0, sizeof(d_early_bits_valid));
    std::memset(d_early_eqsym, 0, sizeof(d_early_eqsym));
    std::memset(d_early_eqsym_valid, 0, sizeof(d_early_eqsym_valid));

    // Allow opt-in via env var for the L-LTF1 experiment
    const char* env_lltf1 = std::getenv("IEEE80211_H_LLTF1");
    if (env_lltf1 && env_lltf1[0] == '1') {
        d_use_lltf1_for_h = true;
        std::cout << "[FRAME_EQ] H-estimation: L-LTF1 (counter=1) ENABLED via env\n";
    }

    // Allow opt-in via env var for phase residual diagnostic
    const char* env_phase_residual = std::getenv("IEEE80211_PHASE_RESIDUAL");
    d_log_phase_residual = (env_phase_residual && env_phase_residual[0] == '1');
    if (d_log_phase_residual) {
        std::cout << "[FRAME_EQ] Phase residual dump ENABLED via env\n";
    }

    // Allow opt-in via env var for H52 diagnostic
    const char* env_h52_dump = std::getenv("IEEE80211_H52_DUMP");
    d_log_h52 = (env_h52_dump && env_h52_dump[0] == '1');
    if (d_log_h52) {
        std::cout << "[FRAME_EQ] H52 dump ENABLED via env\n";
    }

    // Allow opt-in via env var for LTF0 FFT diagnostic (Phase 3 Stage 1, reorganized)
    const char* env_ltf0_fft_dump = std::getenv("IEEE80211_LTF0_FFT_DUMP");
    d_log_ltf0_fft = (env_ltf0_fft_dump && env_ltf0_fft_dump[0] == '1');
    g_log_ltf0_fft = d_log_ltf0_fft;  // Propagate to file-global for static extract_header52_from_sym64
    if (d_log_ltf0_fft) {
        std::cout << "[FRAME_EQ] LTF0 FFT dump ENABLED via env\n";
    }

    // Allow opt-in via env var for LTF0 FFT pre-compensation diagnostic (Phase 10)
    // Dumps the first 5 subcarriers of L-LTF0 FFT in complex (a+bi) form, BEFORE
    // any CFO/SFO compensation is applied. Used to determine if L-SIG mis-decoding
    // (enc=2/4/6/7 instead of 0) is caused by upstream FFT corruption. Compare USRP
    // vs loopback: if USRP shows much higher std, the bug is in splitter/timing/IQ.
    const char* env_ltf0_fft_precomp_dump = std::getenv("IEEE80211_LTF0_FFT_PRECOMP_DUMP");
    d_log_ltf0_fft_precomp = (env_ltf0_fft_precomp_dump && env_ltf0_fft_precomp_dump[0] == '1');
    g_log_ltf0_fft_precomp = d_log_ltf0_fft_precomp;  // Propagate to file-global for static fn
    if (d_log_ltf0_fft_precomp) {
        std::cout << "[FRAME_EQ] LTF0 FFT PRE-COMP dump ENABLED via env\n";
    }

    // Allow opt-in via env var for H estimation robustness (Phase 4)
    const char* env_h_median_filter = std::getenv("IEEE80211_H_MEDIAN_FILTER");
    d_h_median_filter = (env_h_median_filter && env_h_median_filter[0] == '1');
    g_h_median_filter = d_h_median_filter;  // Propagate to file-global for static estimate_header_channel_from_lltf52
    if (d_h_median_filter) {
        std::cout << "[FRAME_EQ] H median filter ENABLED via env\n";
    }

    // Allow opt-in via env var for post-filter H52 diagnostic (Phase 4)
    const char* env_h52_dump_filtered = std::getenv("IEEE80211_H52_DUMP_FILTERED");
    d_log_h52_filtered = (env_h52_dump_filtered && env_h52_dump_filtered[0] == '1');
    g_log_h52_filtered = d_log_h52_filtered;  // Propagate to file-global for call-site dump
    if (d_log_h52_filtered) {
        std::cout << "[FRAME_EQ] H52 post-filter dump ENABLED via env\n";
    }

    // Allow opt-in via env var for Hhdr52 at equalizer-input diagnostic
    // (Phase 10). Dumps |Hhdr52[i]| and arg(Hhdr52[i]) for all 52
    // subcarriers per frame at the moment Hhdr52 is finalized for
    // L-SIG/HT-SIG equalization. Compare USRP vs loopback to confirm
    // whether Hhdr52 magnitude/phase coherence is intact at the equalizer
    // input. Default OFF. Enable via IEEE80211_H52_EQ_INPUT_DUMP=1.
    const char* env_h52_eq_input_dump = std::getenv("IEEE80211_H52_EQ_INPUT_DUMP");
    d_log_h52_input = (env_h52_eq_input_dump && env_h52_eq_input_dump[0] == '1');
    g_log_h52_input = d_log_h52_input;  // Propagate to file-global
    if (d_log_h52_input) {
        std::cout << "[FRAME_EQ] H52 at equalizer-input dump ENABLED via env\n";
    }

    // Allow opt-in via env var for L-LTF0 entry time-domain gain
    // diagnostic (Phase 13 Task 1). Dumps |sym64[j]|^2 sum at the FFT
    // window capture point (BEFORE d_early_eqsym_valid guard). Used to
    // confirm upstream gain/agc at L-LTF0 FFT entry on USRP. Default OFF.
    // Enable via IEEE80211_FRAME_GAIN_DUMP=1.
    const char* env_frame_gain_dump = std::getenv("IEEE80211_FRAME_GAIN_DUMP");
    d_log_frame_gain = (env_frame_gain_dump && env_frame_gain_dump[0] == '1');
    g_log_frame_gain = d_log_frame_gain;  // Propagate to file-global
    if (d_log_frame_gain) {
        std::cout << "[FRAME_EQ] Frame gain dump ENABLED via env\n";
    }

    set_algorithm(algo);
    reset_frame_state();
}

frame_equalizer_impl::~frame_equalizer_impl() {}

void frame_equalizer_impl::set_algorithm(Equalizer algo)
{
    switch (algo) {
    case COMB:
        d_equalizer = std::make_shared<equalizer::comb>();
        break;
    case LS:
        d_equalizer = std::make_shared<equalizer::ls>();
        break;
    case LMS:
        d_equalizer = std::make_shared<equalizer::lms>();
        break;
    case STA:
        d_equalizer = std::make_shared<equalizer::sta>();
        break;
    default:
        d_equalizer = std::make_shared<equalizer::ls>();
        break;
    }
}

void frame_equalizer_impl::set_bandwidth(double bw) { d_bw = (int)bw; }
void frame_equalizer_impl::set_frequency(double freq) { d_freq_offset_from_synclong = freq; }
void frame_equalizer_impl::set_extra_header_symbols(int) {}

void frame_equalizer_impl::forecast(int noutput_items,
                                    gr_vector_int& ninput_items_required)
{
    ninput_items_required[0] = std::max(1, (noutput_items + 51) / 52);
}

void frame_equalizer_impl::reset_frame_state(void)
{
    d_frame_bytes = 0;
    d_frame_encoding = 0;
    d_frame_mcs = 0;
    d_frame_symbols = 0;
    d_frame_mod = 1;
    d_frame_n_bpsc = 1;
    d_frame_n_cbps = 52;
    d_frame_n_dbps = 26;
    d_use_ldpc = false;
    d_ldpc_n_sym = -1;

    d_have_header = false;
    d_have_ht_header = false;
    d_is_ht = false;
    d_sym_idx = 0;
    d_takeover_reject_symbols = 0;
    d_internal_symbol_counter = 0;
    d_first_valid_symbol = -1;
    d_discard_until_wifi_start = false;

    d_chan_est_mode = 0;
    d_have_lsig = false;
    d_lsig_rel = -1;
    d_hdr_reorder_mode = 0;
    d_hdr_inverted = false;
    d_htsig0_rel = -1;
    d_htsig1_rel = -1;
    d_data_start_rel = kDataStartRel;

    d_cfo_phase_per_symbol = 0.0f;
    d_cfo_ref_current_symbol = 0;
    d_cfo_estimated = false;
    std::memset(d_phase_diff_per_sc, 0, sizeof(d_phase_diff_per_sc));
    d_phase_diff_valid = false;

    std::memset(d_early_bits, 0, sizeof(d_early_bits));
    std::memset(d_early_bits_valid, 0, sizeof(d_early_bits_valid));
    std::memset(d_early_eqsym, 0, sizeof(d_early_eqsym));
    std::memset(d_early_eqsym_valid, 0, sizeof(d_early_eqsym_valid));
    d_ltf_compensated_valid[0] = false;
    d_ltf_compensated_valid[1] = false;
    d_H52_tx_order_valid = false;
    d_frame_bytes_tag_emitted = false;

    g_extract_call_count = 0;
    htltf_edge_saved = false;
    ltf0_ever_saved = false;
    ltf0_saved = false;
    std::memset(saved_ltf0_fft, 0, sizeof(saved_ltf0_fft));
    std::memset(saved_htltf_edge, 0, sizeof(saved_htltf_edge));
    d_equalizer->reset();
}

bool frame_equalizer_impl::parse_signal(const uint8_t* decoded_bits,
                                        int& encoding,
                                        int& psdu_length)
{
    const int rate_field =
        ((decoded_bits[0] & 1) << 3) |
        ((decoded_bits[1] & 1) << 2) |
        ((decoded_bits[2] & 1) << 1) |
        ((decoded_bits[3] & 1) << 0);

    psdu_length = 0;
    for (int i = 0; i < 12; i++) {
        psdu_length |= ((decoded_bits[5 + i] & 1) << i);
    }

    int parity_sum = 0;
    for (int i = 0; i < 18; i++) {
        parity_sum ^= (decoded_bits[i] & 1);
    }
    if (parity_sum != 0) {
        return false;
    }

    for (int i = 18; i < 24; i++) {
        if (decoded_bits[i] != 0) {
            return false;
        }
    }

    switch (rate_field) {
    case 0x0D: encoding = 0; break;
    case 0x0F: encoding = 1; break;
    case 0x05: encoding = 2; break;
    case 0x07: encoding = 3; break;
    case 0x09: encoding = 4; break;
    case 0x0B: encoding = 5; break;
    case 0x01: encoding = 6; break;
    case 0x03: encoding = 7; break;
    default:
        return false;
    }

    return true;
}

bool frame_equalizer_impl::parse_signal_ht(const uint8_t* decoded_bits,
                                           int& mcs,
                                           int& psdu_length,
                                           bool& aggregation,
                                           bool& short_gi,
                                           bool& use_ldpc)
{
    mcs = 0;
    psdu_length = 0;
    aggregation = false;
    short_gi = false;
    use_ldpc = false;

    for (int i = 0; i < 7; i++) {
        mcs |= ((decoded_bits[i] & 1) << i);
    }

    const int bw40 = decoded_bits[7] & 1;

    for (int i = 0; i < 16; i++) {
        psdu_length |= ((decoded_bits[8 + i] & 1) << i);
    }

    const int rsv0 = decoded_bits[24] & 1;
    const int rsv1 = decoded_bits[25] & 1;
    const int rsv2 = decoded_bits[26] & 1;

    aggregation = ((decoded_bits[27] & 1) != 0);

    const int stbc =
        ((decoded_bits[28] & 1) << 0) |
        ((decoded_bits[29] & 1) << 1);

    const int adv_coding = decoded_bits[30] & 1;
    short_gi = ((decoded_bits[31] & 1) != 0);

    const int num_ht_ltf =
        ((decoded_bits[32] & 1) << 0) |
        ((decoded_bits[33] & 1) << 1);

    uint8_t crc_rx = 0;
    for (int i = 0; i < 8; i++) {
        crc_rx |= ((decoded_bits[34 + i] & 1) << i);
    }

    const uint8_t crc_calc = ht_sig_crc8_calc(decoded_bits);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        return false;
    }

    if (bw40 != 0) {
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        return false;
    }
    // adv_coding: 0=BCC, 1=LDPC - both are valid now
    if (adv_coding != 0 && adv_coding != 1) {
        return false;
    }

    if (mcs < 0 || mcs > 7) {
        return false;
    }
    if (psdu_length <= 0) {
        return false;
    }

    use_ldpc = (adv_coding == 1);
    return true;
}

void frame_equalizer_impl::set_ht_frame_params_from_mcs_len(int mcs, int len_bytes, bool use_ldpc)
{
    d_is_ht = true;
    d_have_ht_header = true;
    d_have_header = true;

    d_frame_encoding = ht_mcs_to_encoding(mcs);
    d_frame_mcs = mcs;
    d_frame_bytes = len_bytes;
    d_use_ldpc = use_ldpc;

    d_frame_n_bpsc = ht_n_bpsc_from_mcs(mcs);
    d_frame_n_cbps = ht_n_cbps_from_mcs(mcs);
    d_frame_n_dbps = ht_n_dbps_from_mcs(mcs);

    // For LDPC, n_sym is determined by the LDPC block size
    if (use_ldpc) {
        // 802.11n standard LDPC: use padded data_bits (same as TX mapper)
        int raw_data_bits = 16 + 8 * len_bytes + 6; // SERVICE + DATA + TAIL
        int data_bits = ((raw_data_bits + d_frame_n_dbps - 1) / d_frame_n_dbps) * d_frame_n_dbps;
        // LDPC code rates for each MCS (rate_index)
        int rate_index;
        switch (mcs) {
        case 0: case 1: case 3: rate_index = 0; break; // 1/2
        case 5: rate_index = 1; break; // 2/3
        case 2: case 4: case 6: rate_index = 2; break; // 3/4
        case 7: rate_index = 3; break; // 5/6
        default: rate_index = 0; break;
        }
        // Block length selection based on data_bits (same as TX)
        int block_length = (data_bits <= 324) ? 648 :
                           (data_bits <= 648) ? 1296 : 1944;
        int k = block_length / 2;
        switch (rate_index) {
        case 0: k = block_length / 2; break;
        case 1: k = block_length * 2 / 3; break;
        case 2: k = block_length * 3 / 4; break;
        case 3: k = block_length * 5 / 6; break;
        }
        int m = block_length - k; // parity bits per block
        int num_blocks = (data_bits + k - 1) / k;
        if (num_blocks < 1) num_blocks = 1;
        // Standard: encoded bits = data_bits + num_blocks * m (without shortening)
        // Align to full OFDM symbols (repetition fills the gap)
        int ldpc_encoded_bits = data_bits + num_blocks * m;
        int n_cbps = d_frame_n_cbps;
        d_frame_symbols = (ldpc_encoded_bits + n_cbps - 1) / n_cbps;
        int aligned_encoded = d_frame_symbols * n_cbps;
        d_ldpc_n_sym = d_frame_symbols;
        USRP_LOG( "[EQ_LDPC_PARAMS] mcs=%d len=%d data_bits=%d block=%d k=%d m=%d blocks=%d raw=%d aligned=%d n_sym=%d\n",
                mcs, len_bytes, data_bits, block_length, k, m, num_blocks, ldpc_encoded_bits, aligned_encoded, d_frame_symbols);
    } else {
        d_frame_symbols =
            (16 + 8 * len_bytes + 6 + d_frame_n_dbps - 1) / d_frame_n_dbps;
        d_ldpc_n_sym = -1;
        USRP_LOG( "[EQ_CONV_PARAMS] mcs=%d len=%d n_dbps=%d n_sym=%d\n",
                mcs, len_bytes, d_frame_n_dbps, d_frame_symbols);
    }
}

// ============================================================
// Member wrappers required by header
// ============================================================

bool frame_equalizer_impl::decode_lsig_from_bits52(const uint8_t* bits52,
                                                   int reorder_mode,
                                                   bool invert_bits,
                                                   int& encoding,
                                                   int& psdu_length)
{
    return decode_lsig_candidate(bits52,
                                 reorder_mode,
                                 invert_bits,
                                 encoding,
                                 psdu_length);
}

bool frame_equalizer_impl::decode_htsig_from_bits52(const uint8_t* bits_a,
                                                    const uint8_t* bits_b,
                                                    int reorder_mode,
                                                    bool swap_symbols,
                                                    bool invert_bits,
                                                    int& out_len_bytes,
                                                    int& out_mcs,
                                                    bool& out_sgi,
                                                    bool& out_agg,
                                                    bool& out_use_ldpc)
{
    const uint8_t* a = swap_symbols ? bits_b : bits_a;
    const uint8_t* b = swap_symbols ? bits_a : bits_b;

    return decode_htsig_candidate(a, b,
                                  reorder_mode,
                                  invert_bits,
                                  invert_bits,
                                  out_len_bytes,
                                  out_mcs,
                                  out_sgi,
                                  out_agg,
                                  out_use_ldpc);
}

bool frame_equalizer_impl::decode_htsig_from_eqsym52(const gr_complex* sym_a,
                                                     const gr_complex* sym_b,
                                                     int reorder_mode,
                                                     bool swap_symbols,
                                                     bool invert_bits,
                                                     int& out_len_bytes,
                                                     int& out_mcs,
                                                     bool& out_sgi,
                                                     bool& out_agg,
                                                     bool& out_use_ldpc)
{
    uint8_t bits_a[52];
    uint8_t bits_b[52];

    for (int i = 0; i < 52; i++) {
        bits_a[i] = hard_bit_from_complex(sym_a[i]);
        bits_b[i] = hard_bit_from_complex(sym_b[i]);
    }

    return decode_htsig_from_bits52(bits_a, bits_b,
                                    reorder_mode,
                                    swap_symbols,
                                    invert_bits,
                                    out_len_bytes,
                                    out_mcs,
                                    out_sgi,
                                    out_agg,
                                    out_use_ldpc);
}

// ============================================================
// general_work
// ============================================================

int frame_equalizer_impl::general_work(int noutput_items,
                                       gr_vector_int& ninput_items,
                                       gr_vector_const_void_star& input_items,
                                       gr_vector_void_star& output_items)
{
    const gr_complex* in = (const gr_complex*)input_items[0];
    gr_complex* out = (gr_complex*)output_items[0];

    const int n_in = ninput_items[0];

    if (n_in <= 0 || noutput_items <= 0) {
        return 0;
    }

    int produced = 0;
    int consumed = 0;

    const uint64_t abs_in_start = this->nitems_read(0);
    const uint64_t abs_in_end = abs_in_start + n_in;

    std::vector<tag_t> wifi_tags;
    get_tags_in_range(
        wifi_tags,
        0,
        abs_in_start,
        abs_in_end,
        pmt::intern("wifi_start"));

    std::set<uint64_t> wifi_offsets;
    std::map<uint64_t, double> wifi_freq_offsets;
    for (const auto& t : wifi_tags) {
        wifi_offsets.insert((uint64_t)t.offset);
        if (pmt::is_real(t.value)) {
            double freq_offset = pmt::to_double(t.value);
            wifi_freq_offsets[(uint64_t)t.offset] = freq_offset;
        }
    }

    while (consumed < n_in) {
        if (d_have_ht_header && d_sym_idx >= d_data_start_rel &&
            (produced + 52) > noutput_items) {
            break;
        }

        const gr_complex* sym64 = in + consumed * 64;
        const uint64_t abs_in_off = abs_in_start + consumed;

        const bool wifi_start = (wifi_offsets.count(abs_in_off) != 0);

        if (!d_in_frame) {
            if (d_discard_until_wifi_start) {
                if (wifi_start) {
                    d_discard_until_wifi_start = false;
                } else {
                    consumed++;
                    d_current_symbol++;
                    continue;
                }
            }

            if (!wifi_start) {
                consumed++;
                d_current_symbol++;
                continue;
            }

            d_in_frame = true;
            reset_frame_state();

        } else if (wifi_start) {
            bool allow_takeover = false;

            if (!d_have_ht_header) {
                allow_takeover = true;
            } else {
                // Only allow takeover after Frame 1 has emitted all data symbols.
                // With correct SPLITTER tag timing, Frame 2 arrives after Frame 1 ends.
                const int end_rel = d_data_start_rel + d_frame_symbols - 1;
                if (d_sym_idx >= end_rel) {
                    allow_takeover = true;
                }
            }

            // If we are still inside the data region but a new wifi_start arrived,
            // preempt the current frame. Better to lose tail of old frame than entire new frame.
            if (!allow_takeover && d_sym_idx >= d_data_start_rel) {
                allow_takeover = true;
                const int end_rel = d_data_start_rel + d_frame_symbols - 1;
                USRP_LOG(
                        "[EQ_FRAME_PREEMPT] abs_in_off=%llu d_sym_idx=%d end_rel=%d\n",
                        (unsigned long long)abs_in_off, d_sym_idx, end_rel);
            }

            if (allow_takeover) {
                reset_frame_state();
                d_in_frame = true;
            } else {
                int remaining = (d_data_start_rel + d_frame_symbols) - d_sym_idx;
                USRP_LOG(
                        "[EQ_FRAME_TAKEOVER_REJECT] abs_in_off=%llu d_sym_idx=%d end_rel=%d remaining=%d\n",
                        (unsigned long long)abs_in_off, d_sym_idx,
                        d_data_start_rel + d_frame_symbols - 1, remaining);
                d_takeover_reject_symbols++;
            }
        }

        // ------------------------------------------------------------
        // cache direct raw header52 from original sym64 for early symbols
        // d_early_eqsym[rel][0..47] : 48 header data carriers
        // d_early_eqsym[rel][48..51]: 4 pilots
        // ------------------------------------------------------------
        // Use d_internal_symbol_counter for symbol type determination
        // d_sym_idx may be out of sync due to 'continue' path skipping its increment
        if (d_internal_symbol_counter >= 0 && d_internal_symbol_counter < 8) {
            // Use d_internal_symbol_counter for array indexing - it tracks actual symbol count
            extract_header52_from_sym64(sym64, d_early_eqsym[d_internal_symbol_counter]);
            d_early_eqsym_valid[d_internal_symbol_counter] = true;

            // Apply CFO+SFO compensation to header symbols (L-SIG, HT-SIG0, HT-SIG1).
            // L-LTF0 (counter=0) is the H reference — do NOT compensate it.
            // L-LTF1 (counter=1) is used for CFO/SFO estimation — do NOT compensate it.
            // L-SIG (counter=2), HT-SIG0 (3), HT-SIG1 (4) need compensation.
            // Use d_phase_diff_per_sc[i] which contains CFO + SFO*sc for each subcarrier.
            // This is more accurate than d_cfo_phase_per_symbol alone (which lacks SFO).
            if (d_phase_diff_valid && d_internal_symbol_counter >= kLSigRel) {
                for (int i = 0; i < 52; i++) {
                    float total_phase = d_phase_diff_per_sc[i] * d_internal_symbol_counter;
                    gr_complex rot = std::exp(gr_complex(0.0f, -total_phase));
                    d_early_eqsym[d_internal_symbol_counter][i] *= rot;
                }
                USRP_LOG("[HDR_COMP] counter=%d phase[0]=%.4f phase[26]=%.4f\n",
                         d_internal_symbol_counter,
                         d_phase_diff_per_sc[0] * d_internal_symbol_counter,
                         d_phase_diff_per_sc[26] * d_internal_symbol_counter);
            }

            // CFO estimation from L-LTF0 / L-LTF1 phase difference
            // Use 64-bin FFT correlation (saved_ltf0_fft vs sym64) for reliability.
            // The 52-carrier extraction can introduce spurious phase offsets due to
            // bin mapping and guard band edge effects.
            if (d_internal_symbol_counter == kLltf1Rel &&
                d_early_eqsym_valid[kLltf0Rel] && ltf0_ever_saved) {
                // CFO estimation is deferred to the 52-subcarrier method below
                // (after SFO estimation), which is more accurate than 64-bin FFT
                // correlation because it excludes noise bins (DC, guard bands).
                // We still need d_cfo_estimated=true for the data path check.
                d_cfo_ref_current_symbol = d_current_symbol - 1; // L-LTF0's index
                d_cfo_estimated = true;

                // Estimate SFO using linear regression on all 52 subcarriers.
                // phase_diff[i] = CFO + SFO * sc_index[i].
                // Since 64-bin CFO ≈ 0, we fit phase_diff vs sc_index to get SFO.
                static const int kScIndex52[52] = {
                    -26,-25,-24,-23,-22,  // i=0..4
                    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,  // i=5..17
                    -6,-5,-4,-3,-2,-1,  // i=18..23
                    1,2,3,4,5,6,  // i=24..29
                    8,9,10,11,12,13,  // i=30..35
                    14,15,16,17,18,19,  // i=36..41
                    20,22,23,24,25,26,  // i=42..47
                    -21,-7,7,21  // i=48..51 (pilots)
                };
                double sum_sc2 = 0.0, sum_sc_phase = 0.0;
                double sum_phase = 0.0;
                for (int i = 0; i < 52; i++) {
                    gr_complex ratio = d_early_eqsym[kLltf1Rel][i] *
                                       std::conj(d_early_eqsym[kLltf0Rel][i]);
                    float pd = std::arg(ratio);
                    d_phase_diff_per_sc[i] = pd;
                    int sc = kScIndex52[i];
                    sum_sc2 += (double)sc * sc;
                    sum_sc_phase += (double)sc * pd;
                    sum_phase += pd;
                }
                float sfo_est = (sum_sc2 > 1e-6) ? (float)(sum_sc_phase / sum_sc2) : 0.0f;
                float cfo_est = (float)(sum_phase / 52.0); // mean phase = intercept
                d_sfo_per_sc_est = sfo_est;

                // Use the more accurate 52-subcarrier mean instead of 64-bin correlation
                d_cfo_phase_per_symbol = cfo_est;
                USRP_LOG("[CFO_EST] phase_per_symbol=%.4f rad (52-sc mean, was 64-bin)\n",
                         d_cfo_phase_per_symbol);

                USRP_LOG("[SFO_RAW] cfo=%.6f sfo_raw=%.6f abs=%.6f soft_clamp_knee=1e-2\n",
                         cfo_est, sfo_est, std::abs(sfo_est));
                // Soft-clamp SFO: clip the magnitude at 1e-2 rad/SC instead of
                // hard-zeroing. The hard-zero at 1e-3 (60% of USRP frames) discarded
                // legitimate SFO estimates, leaving ~0.013 rad residual on L-SIG
                // (per Task C synthetic test in
                // docs/superpowers/plans/2026-06-10-fix-lsig-viterbi-equalization.md).
                // That residual is enough to flip BPSK soft decisions and breaks
                // the L-SIG viterbi decoder. With soft-clamp at 1e-2:
                //   - |sfo_est| < 1e-2: pass through (most frames)
                //   - |sfo_est| > 1e-2: clip at +/-1e-2 (rare outliers)
                // The clip discontinuity at 1e-2 affects <10% of frames but keeps
                // the magnitude in a physically reasonable range. The original
                // 0.001 threshold fired on noisy L-LTF linear-fit estimates
                // (mean=-0.00139, range -0.00793..+0.00847 across 5 frames on
                // 2026-06-10) and threw away the SFO correction entirely.
                if (std::abs(sfo_est) > 1e-2f) {
                    float clipped = sfo_est > 0 ? 1e-2f : -1e-2f;
                    USRP_LOG("[SFO_SOFT] clipped %.6f -> %.6f\n", sfo_est, clipped);
                    sfo_est = clipped;
                }
                // Save full linear fit: CFO + SFO*SC for each subcarrier
                for (int i = 0; i < 52; i++) {
                    d_phase_diff_per_sc[i] = cfo_est + sfo_est * kScIndex52[i];
                }
                d_phase_diff_valid = true;
                USRP_LOG("[SFO_EST] cfo=%.4f sfo=%.6f rad/SC d_cfo=%.4f\n",
                         cfo_est, sfo_est, d_cfo_phase_per_symbol);
            }

            // Store compensated L-LTF0 and L-LTF1 for later H estimation.
            // Counter for L-LTF0 is 0, for L-LTF1 is 1. We populate and log
            // only once per frame (at kHtSig0Rel), since d_early_eqsym and
            // d_phase_diff_per_sc do not change after counter=1.
            if (d_internal_symbol_counter == kHtSig0Rel &&
                d_phase_diff_valid &&
                d_early_eqsym_valid[kLltf0Rel]) {
                for (int i = 0; i < 52; i++) {
                    // counter=0 -> ph=0; this is a copy, not a real
                    // compensation, but kept symmetric with slot 1 for clarity.
                    float ph = d_phase_diff_per_sc[i] * 0;
                    d_ltf_compensated[0][i] = d_early_eqsym[kLltf0Rel][i] *
                        std::exp(gr_complex(0.0f, -ph));
                }
                d_ltf_compensated_valid[0] = true;
                if (d_early_eqsym_valid[kLltf1Rel]) {
                    for (int i = 0; i < 52; i++) {
                        float ph = d_phase_diff_per_sc[i] * 1;
                        d_ltf_compensated[1][i] = d_early_eqsym[kLltf1Rel][i] *
                            std::exp(gr_complex(0.0f, -ph));
                    }
                    d_ltf_compensated_valid[1] = true;
                }
                USRP_LOG("[LTF_COMP] cfo=%.4f sfo=%.6f stored compensated L-LTF0/L-LTF1 (valid0=%d valid1=%d)\n",
                         d_cfo_phase_per_symbol,
                         d_sfo_per_sc_est,
                         d_ltf_compensated_valid[0] ? 1 : 0,
                         d_ltf_compensated_valid[1] ? 1 : 0);
            }

            // Header CFO+SFO compensation is applied above using d_phase_diff_per_sc.
            // This compensates both common CFO and per-subcarrier SFO (more complete
            // than the data path which only compensates common CFO). The 52-subcarrier
            // estimation (below) is
            // more accurate than the old 64-bin FFT correlation because it
            // excludes noise bins (DC, Nyquist, guard bands).

            // Legacy vs HT-Mixed frame type detection via QBPSK rotation
            // HT-SIG0 uses 90 rotated BPSK: E_Q > E_I after equalization.
            // Using raw FFT is WRONG - channel phase smears I/Q energy equally.
            if (d_internal_symbol_counter == kHtSig0Rel && d_early_eqsym_valid[kLSigRel] &&
                d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLltf1Rel]) {
                // Use raw LTF0 for channel estimation (no CFO, no CPE).
                // CFO cancels when dividing RX/H because both have the same CFO rotation.
                gr_complex H52[52];
                const gr_complex* lltf_for_H = nullptr;
                if (d_use_lltf1_for_h) {
                    // Experiment: use L-LTF1 (counter=1) for H estimation. Halves the
                    // time gap to L-SIG (counter=2) from 8us to 4us.
                    lltf_for_H = d_ltf_compensated_valid[1]
                        ? d_ltf_compensated[1]
                        : d_early_eqsym[kLltf1Rel];
                    USRP_LOG("[H_SRC] using L-LTF1 (counter=1) for H estimation\n");
                } else {
                    lltf_for_H = d_ltf_compensated_valid[0]
                        ? d_ltf_compensated[0]
                        : d_early_eqsym[kLltf0Rel];
                }
                estimate_header_channel_from_lltf52(lltf_for_H,
                                                    lltf_for_H,  // arg2 is unused, pass same ptr
                                                    H52);
                // [H52_DUMP] Diagnostic: dump |H52[i]| and arg(H52[i]) for all
                // 52 subcarriers per frame. Opt-in via IEEE80211_H52_DUMP=1.
                // Atomic snprintf+USRP_LOG prevents sync_short stdout shredding
                // (see commit 9ebd74f pattern). Used to compare USRP H52 vs
                // software loopback H52 — see spec
                // docs/superpowers/specs/2026-06-10-h52-diagnosis-design.md
                if (d_log_h52) {
                    double sum_mag = 0.0, sum_mag2 = 0.0;
                    double sum_arg = 0.0, sum_arg2 = 0.0;
                    int cnt = 0;
                    for (int i = 0; i < 52; i++) {
                        float m = std::abs(H52[i]);
                        float a = std::arg(H52[i]);
                        sum_mag += m;
                        sum_mag2 += (double)m * m;
                        sum_arg += a;
                        sum_arg2 += (double)a * a;
                        cnt++;
                    }
                    double mean_mag = (cnt > 0) ? sum_mag / cnt : 0.0;
                    double var_mag = (cnt > 0) ? (sum_mag2 / cnt - mean_mag * mean_mag) : 0.0;
                    double std_mag = (var_mag > 0) ? std::sqrt(var_mag) : 0.0;
                    double mean_arg = (cnt > 0) ? sum_arg / cnt : 0.0;
                    double var_arg = (cnt > 0) ? (sum_arg2 / cnt - mean_arg * mean_arg) : 0.0;
                    double std_arg = (var_arg > 0) ? std::sqrt(var_arg) : 0.0;

                    char h52_dump[2048];
                    int pn = snprintf(h52_dump, sizeof(h52_dump),
                                      "[H52_DUMP] counter=%d |H|=",
                                      d_internal_symbol_counter);
                    for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 32; i++) {
                        int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                         std::abs(H52[i]));
                        if (w < 0) break;
                        pn += w;
                    }
                    pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                                   " arg(H)=");
                    for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 16; i++) {
                        int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                         std::arg(H52[i]));
                        if (w < 0) break;
                        pn += w;
                    }
                    pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                                   " mean|H|=%.3f std|H|=%.3f mean(argH)=%.3f std(argH)=%.3f\n",
                                   mean_mag, std_mag, mean_arg, std_arg);
                    USRP_LOG("%s", h52_dump);
                }
                // [Phase 4] Apply 3-tap median filter at the call site (not
                // inside estimate_header_channel_from_lltf52) to keep the
                // function pure and enable clean pre/post dumps. Opt-in via
                // IEEE80211_H_MEDIAN_FILTER=1. Spec §6.1, plan Task 4+5.
                if (g_h_median_filter) {
                    apply_h_median_filter(H52, H52, 52);
                }
                // [H52_DUMP_FILTERED] Post-filter dump. Same format as
                // [H52_DUMP] but with H52_DUMP_FILTERED prefix. Uses a
                // separate counter so pre/post counters don't share state.
                if (g_log_h52_filtered) {
                    static int h52_filtered_counter = 0;
                    h52_filtered_counter++;
                    double sum_mag = 0.0, sum_mag2 = 0.0;
                    double sum_arg = 0.0, sum_arg2 = 0.0;
                    int cnt = 0;
                    for (int i = 0; i < 52; i++) {
                        float m = std::abs(H52[i]);
                        float a = std::arg(H52[i]);
                        sum_mag += m;
                        sum_mag2 += (double)m * m;
                        sum_arg += a;
                        sum_arg2 += (double)a * a;
                        cnt++;
                    }
                    double mean_mag = (cnt > 0) ? sum_mag / cnt : 0.0;
                    double var_mag = (cnt > 0) ? (sum_mag2 / cnt - mean_mag * mean_mag) : 0.0;
                    double std_mag = (var_mag > 0) ? std::sqrt(var_mag) : 0.0;
                    double mean_arg = (cnt > 0) ? sum_arg / cnt : 0.0;
                    double var_arg = (cnt > 0) ? (sum_arg2 / cnt - mean_arg * mean_arg) : 0.0;
                    double std_arg = (var_arg > 0) ? std::sqrt(var_arg) : 0.0;

                    char h52_dump[2048];
                    int pn = snprintf(h52_dump, sizeof(h52_dump),
                                      "[H52_DUMP_FILTERED] counter=%d |H|=",
                                      h52_filtered_counter);
                    for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 32; i++) {
                        int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                         std::abs(H52[i]));
                        if (w < 0) break;
                        pn += w;
                    }
                    pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                                   " arg(H)=");
                    for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 16; i++) {
                        int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                         std::arg(H52[i]));
                        if (w < 0) break;
                        pn += w;
                    }
                    pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                                   " mean|H|=%.3f std|H|=%.3f mean(argH)=%.3f std(argH)=%.3f\n",
                                   mean_mag, std_mag, mean_arg, std_arg);
                    USRP_LOG("%s", h52_dump);
                }
                USRP_LOG("[H_FROM_COMP] used_comp=ltf0:%d ltf1:%d\n",
                         d_ltf_compensated_valid[0] ? 1 : 0,
                         d_ltf_compensated_valid[1] ? 1 : 0);

                USRP_LOG("[H_DIAG] lltf0[0]=(%.3f%+.3fi) lltf0[25]=(%.3f%+.3fi) "
                         "lsig[0]=(%.3f%+.3fi) lsig[25]=(%.3f%+.3fi) "
                         "H[0]=(%.3f%+.3fi) H[25]=(%.3f%+.3fi) d_phase_diff_valid=%d\n",
                         d_early_eqsym[kLltf0Rel][0].real(), d_early_eqsym[kLltf0Rel][0].imag(),
                         d_early_eqsym[kLltf0Rel][25].real(), d_early_eqsym[kLltf0Rel][25].imag(),
                         d_early_eqsym[kLSigRel][0].real(), d_early_eqsym[kLSigRel][0].imag(),
                         d_early_eqsym[kLSigRel][25].real(), d_early_eqsym[kLSigRel][25].imag(),
                         H52[0].real(), H52[0].imag(), H52[25].real(), H52[25].imag(),
                         d_phase_diff_valid ? 1 : 0);

                // Equalize HT-SIG0 raw with H
                gr_complex eq_htsig0[52];
                for (int i = 0; i < 52; i++) {
                    if (std::abs(H52[i]) > 0.01f) {
                        eq_htsig0[i] = d_early_eqsym[kHtSig0Rel][i] / H52[i];
                    } else {
                        eq_htsig0[i] = gr_complex(0.0f, 0.0f);
                    }
                }
                double E_I_ht, E_Q_ht;
                compute_subcarrier_energy(eq_htsig0, E_I_ht, E_Q_ht);
                double ratio_ht = (E_I_ht > 1e-10) ? E_Q_ht / E_I_ht : 0.0;

                USRP_LOG( "[FRAME_DETECT] EQ ratio_ht=%.3f E_I=%.2f E_Q=%.2f\n",
                        ratio_ht, E_I_ht, E_Q_ht);

                // FIX: Lower threshold for USRP over-the-air reception.
                // CFO residue and low SNR reduce QBPSK rotation visibility.
                // Observed ratio_ht ~1.37 for valid HT-Mixed frames.
                if (ratio_ht > 1.2) {
                    d_is_ht_frame = true;
                } else {
                    d_is_ht_frame = false;
                }

                // Equalize L-SIG raw with H (no CPE)
                gr_complex eq_lsig[52];
                for (int i = 0; i < 52; i++) {
                    if (std::abs(H52[i]) > 0.01f) {
                        eq_lsig[i] = d_early_eqsym[kLSigRel][i] / H52[i];
                    } else {
                        eq_lsig[i] = gr_complex(0.0f, 0.0f);
                    }
                }
                // Full 52-subcarrier L-SIG constellation dump (Task 1 of
                // 2026-06-10-eqlsig-constellation-diagnosis.md). Atomic
                // snprintf+USRP_LOG so sync_short stdout writes cannot
                // interleave mid-line (lessons learned from e90e3f5).
                //
                // Format:
                //   [LSIG_EQ_FULL] is_ht=N H_mag=H0,H1,...,H51
                //                   rx=R0,R1,...,R51
                //                   eq=Er0,Ei0,Er1,Ei1,...,Er51,Ei51
                // Subcarrier order: 802.11n standard 52-SC index.
                //
                // Relies on stderr being unbuffered (glibc default).
                // Do NOT setvbuf(stderr, ...) — atomicity depends on it.
                //
                // On buffer overflow, append " TRUNC" so the offline
                // classifier can detect and skip the line.
                char dump[2560];
                bool truncated = false;
                int n = snprintf(dump, sizeof(dump),
                                 "[LSIG_EQ_FULL] is_ht=%d H_mag=",
                                 d_is_ht_frame ? 1 : 0);
                if (n < 0 || n >= (int)sizeof(dump)) { truncated = true; n = (int)sizeof(dump) - 8; }
                for (int i = 0; i < 52; i++) {
                    if (n >= (int)sizeof(dump) - 16) { truncated = true; break; }
                    int w = snprintf(dump+n, sizeof(dump)-n, "%.3f,",
                                     std::abs(H52[i]));
                    if (w < 0) { truncated = true; break; }
                    n += w;
                }
                if (n < (int)sizeof(dump) - 8)
                    n += snprintf(dump+n, sizeof(dump)-n, " rx=");
                for (int i = 0; i < 52; i++) {
                    if (n >= (int)sizeof(dump) - 16) { truncated = true; break; }
                    int w = snprintf(dump+n, sizeof(dump)-n, "%.3f,",
                                     std::abs(d_early_eqsym[kLSigRel][i]));
                    if (w < 0) { truncated = true; break; }
                    n += w;
                }
                if (n < (int)sizeof(dump) - 8)
                    n += snprintf(dump+n, sizeof(dump)-n, " eq=");
                for (int i = 0; i < 52; i++) {
                    if (n >= (int)sizeof(dump) - 24) { truncated = true; break; }
                    int w = snprintf(dump+n, sizeof(dump)-n, "%.3f,%.3f,",
                                     eq_lsig[i].real(), eq_lsig[i].imag());
                    if (w < 0) { truncated = true; break; }
                    n += w;
                }
                if (truncated) {
                    // Make sure we have room for " TRUNC\n"
                    if (n > (int)sizeof(dump) - 8)
                        n = (int)sizeof(dump) - 8;
                    n += snprintf(dump+n, sizeof(dump)-n, " TRUNC\n");
                } else {
                    snprintf(dump+n, sizeof(dump)-n, "\n");
                }
                USRP_LOG("%s", dump);

                // Phase residual diagnostic (Task 5.1 of spec):
                // Dump arg(eq_lsig[i]) for all 48 data subcarriers per frame.
                // Goal: quantify how far the equalized L-SIG constellation is
                // from the I-axis (BPSK). mean_phase ≈ 0 means no common
                // rotation; std_phase ≈ 0 means no per-subcarrier phase noise.
                // See spec: docs/superpowers/specs/2026-06-10-phase-noise-lsig-design.md
                if (d_log_phase_residual) {
                    double sum_arg = 0.0, sum_arg2 = 0.0;
                    int cnt = 0;
                    for (int i = 0; i < 48; i++) {
                        float a = std::arg(eq_lsig[i]);
                        sum_arg += a;
                        sum_arg2 += (double)a * a;
                        cnt++;
                    }
                    double mean_phase = (cnt > 0) ? sum_arg / cnt : 0.0;
                    double var_phase = (cnt > 0) ? (sum_arg2 / cnt - mean_phase * mean_phase) : 0.0;
                    double std_phase = (var_phase > 0) ? std::sqrt(var_phase) : 0.0;

                    char phase_dump[1024];
                    int pn = snprintf(phase_dump, sizeof(phase_dump),
                                      "[PHASE_RESIDUAL] counter=%d eq_phase=",
                                      d_internal_symbol_counter);
                    for (int i = 0; i < 48 && pn < (int)sizeof(phase_dump) - 16; i++) {
                        int w = snprintf(phase_dump + pn, sizeof(phase_dump) - pn, "%.3f,",
                                         std::arg(eq_lsig[i]));
                        if (w < 0) break;
                        pn += w;
                    }
                    pn += snprintf(phase_dump + pn, sizeof(phase_dump) - pn,
                                   " mean=%.3f std=%.3f\n", mean_phase, std_phase);
                    USRP_LOG("%s", phase_dump);
                }

                double E_I_lsig, E_Q_lsig;
                compute_subcarrier_energy(eq_lsig, E_I_lsig, E_Q_lsig);
                double ratio_lsig = (E_I_lsig > 1e-10) ? E_Q_lsig / E_I_lsig : 0.0;
                USRP_LOG( "[FRAME_DETECT] L-SIG EQ ratio=%.3f E_I=%.2f E_Q=%.2f (expect < 1.0 for BPSK)\n",
                        ratio_lsig, E_I_lsig, E_Q_lsig);
                USRP_LOG( "[FRAME_DETECT] Detected %s frame (HT-SIG ratio=%.3f, L-SIG ratio=%.3f)\n",
                        d_is_ht_frame ? "HT" : "Legacy", ratio_ht, ratio_lsig);
            }
        }

        // ------------------------------------------------------------
        // legacy equalizer path for downstream 52-value data output
        // ------------------------------------------------------------
        gr_complex raw_eq52[52];
        uint8_t raw_bits52[52];

        std::memset(raw_bits52, 0, sizeof(raw_bits52));
        for (int k = 0; k < 52; k++) {
            raw_eq52[k] = gr_complex(0.0f, 0.0f);
        }

        std::shared_ptr<gr::digital::constellation> cnst = d_bpsk;
        switch (d_frame_n_bpsc) {
        case 1: cnst = d_bpsk;  break;
        case 2: cnst = d_qpsk;  break;
        case 4: cnst = d_16qam; break;
        case 6: cnst = d_64qam; break;
        default: cnst = d_bpsk; break;
        }

        d_equalizer->equalize(const_cast<gr_complex*>(sym64),
                              d_sym_idx,
                              raw_eq52,
                              raw_bits52,
                              cnst);

        // Normalize symbols to correct for kFftNormalize scaling in channel estimate
        for (int k = 0; k < 52; k++) {
            raw_eq52[k] /= kFftNormalize;
        }

        // CFO compensation for data symbols
        if (d_cfo_estimated && d_sym_idx >= d_data_start_rel) {
            int sym_offset = d_current_symbol - d_cfo_ref_current_symbol;
            float cfo_phase = d_cfo_phase_per_symbol * sym_offset;
            gr_complex rot = std::exp(gr_complex(0.0f, -cfo_phase));
            for (int k = 0; k < 52; k++) {
                raw_eq52[k] *= rot;
            }
            USRP_LOG("[CFO_COMP_DATA] sym_idx=%d sym_offset=%d phase=%.4f rad\n",
                     d_sym_idx, sym_offset, cfo_phase);
        }

        int nonzero_cnt = 0;
        double eqp52 = 0.0;

        for (int k = 0; k < 52; k++) {
            const float re = raw_eq52[k].real();
            const float im = raw_eq52[k].imag();

            if (!std::isfinite(re) || !std::isfinite(im)) {
                raw_eq52[k] = gr_complex(0.0f, 0.0f);
                raw_bits52[k] = 0;
                continue;
            }

            if (std::fabs(re) > 1e-6f || std::fabs(im) > 1e-6f) {
                nonzero_cnt++;
            }

            eqp52 += (double)re * re + (double)im * im;
            raw_bits52[k] = hard_bit_from_complex(raw_eq52[k]);
        }

        const bool valid = (nonzero_cnt > 0 && std::isfinite(eqp52) && eqp52 > 1.0);

        if (valid && d_first_valid_symbol < 0) {
            d_first_valid_symbol = d_sym_idx;
        }

        if (d_sym_idx >= 0 && d_sym_idx < 8) {
            std::memcpy(d_early_bits[d_sym_idx], raw_bits52, sizeof(raw_bits52));
            d_early_bits_valid[d_sym_idx] = valid;
        }

        // ------------------------------------------------------------
        // direct mixed-mode header detection:
        //   L-LTF : rel=1/2
        //   L-SIG : rel=3
        //   HTSIG : rel=4/5
        //
        // IMPORTANT:
        //   This path does NOT depend on d_equalizer->equalize() output.
        // ------------------------------------------------------------

        const bool ht_parse_condition =
            !d_have_ht_header &&
            // d_is_ht_frame &&     // Temporarily disabled - ratio threshold too strict
            d_internal_symbol_counter >= kHtSig1Rel &&
            d_early_eqsym_valid[kLltf0Rel] &&
            d_early_eqsym_valid[kLltf1Rel] &&
            d_early_eqsym_valid[kLSigRel] &&
            d_early_eqsym_valid[kHtSig0Rel] &&
            d_early_eqsym_valid[kHtSig1Rel];
        if (ht_parse_condition) {
            gr_complex Hhdr52[52];
            const gr_complex* lltf_for_H2 = nullptr;
            if (d_use_lltf1_for_h) {
                lltf_for_H2 = d_ltf_compensated_valid[1]
                    ? d_ltf_compensated[1]
                    : d_early_eqsym[kLltf1Rel];
            } else {
                lltf_for_H2 = d_ltf_compensated_valid[0]
                    ? d_ltf_compensated[0]
                    : d_early_eqsym[kLltf0Rel];
            }
            estimate_header_channel_from_lltf52(lltf_for_H2,
                                                lltf_for_H2,
                                                Hhdr52);
            // [H52_EQ_INPUT_DUMP] Phase 10 diagnostic: dump |Hhdr52[i]| and
            // arg(Hhdr52[i]) for all 52 subcarriers per frame at the moment
            // Hhdr52 is finalized for L-SIG/HT-SIG equalization (BEFORE the
            // median filter, so this is the true equalizer-input H). Opt-in
            // via IEEE80211_H52_EQ_INPUT_DUMP=1. Atomic snprintf+USRP_LOG
            // prevents sync_short stdout shredding (Phase 9 lesson).
            // Used to compare USRP Hhdr52 vs loopback Hhdr52 — if USRP shows
            // wild |H| std or arg jumps, H estimation (or upstream L-LTF0
            // FFT) is the root cause of L-SIG mis-decoding.
            if (g_log_h52_input) {
                static int h52_input_counter = 0;
                h52_input_counter++;
                double sum_mag = 0.0, sum_mag2 = 0.0;
                double sum_arg = 0.0, sum_arg2 = 0.0;
                int cnt = 0;
                for (int i = 0; i < 52; i++) {
                    float m = std::abs(Hhdr52[i]);
                    float a = std::arg(Hhdr52[i]);
                    sum_mag += m;
                    sum_mag2 += (double)m * m;
                    sum_arg += a;
                    sum_arg2 += (double)a * a;
                    cnt++;
                }
                double mean_mag = (cnt > 0) ? sum_mag / cnt : 0.0;
                double var_mag = (cnt > 0) ? (sum_mag2 / cnt - mean_mag * mean_mag) : 0.0;
                double std_mag = (var_mag > 0) ? std::sqrt(var_mag) : 0.0;
                double mean_arg = (cnt > 0) ? sum_arg / cnt : 0.0;
                double var_arg = (cnt > 0) ? (sum_arg2 / cnt - mean_arg * mean_arg) : 0.0;
                double std_arg = (var_arg > 0) ? std::sqrt(var_arg) : 0.0;

                char dump[8192];
                int off = snprintf(dump, sizeof(dump),
                                   "[H52_EQ_INPUT] sym=%d nSC=52 |H|=",
                                   d_internal_symbol_counter);
                for (int i = 0; i < 52 && off < (int)sizeof(dump) - 32; i++) {
                    int w = snprintf(dump + off, sizeof(dump) - off, "%.2f,",
                                     std::abs(Hhdr52[i]));
                    if (w < 0) break;
                    off += w;
                }
                off += snprintf(dump + off, sizeof(dump) - off, " arg=");
                for (int i = 0; i < 52 && off < (int)sizeof(dump) - 16; i++) {
                    int w = snprintf(dump + off, sizeof(dump) - off, "%.2f,",
                                     std::arg(Hhdr52[i]));
                    if (w < 0) break;
                    off += w;
                }
                off += snprintf(dump + off, sizeof(dump) - off,
                                " mean|H|=%.3f std|H|=%.3f mean(argH)=%.3f std(argH)=%.3f cnt=%d\n",
                                mean_mag, std_mag, mean_arg, std_arg, h52_input_counter);
                USRP_LOG("%s", dump);
            }
            // [Phase 4] Apply 3-tap median filter at the call site (not
            // inside estimate_header_channel_from_lltf52) to keep the
            // function pure and enable clean pre/post dumps. Opt-in via
            // IEEE80211_H_MEDIAN_FILTER=1. Spec §6.1, plan Task 4+5.
            if (g_h_median_filter) {
                apply_h_median_filter(Hhdr52, Hhdr52, 52);
            }
            // [H52_DUMP_FILTERED] Post-filter dump for Hhdr52. Same format
            // as the H52 [H52_DUMP_FILTERED] block but reads from Hhdr52.
            // Separate counter (h52_filtered_counter_hdr) so the two call
            // sites' dumps don't share state.
            if (g_log_h52_filtered) {
                static int h52_filtered_counter_hdr = 0;
                h52_filtered_counter_hdr++;
                double sum_mag = 0.0, sum_mag2 = 0.0;
                double sum_arg = 0.0, sum_arg2 = 0.0;
                int cnt = 0;
                for (int i = 0; i < 52; i++) {
                    float m = std::abs(Hhdr52[i]);
                    float a = std::arg(Hhdr52[i]);
                    sum_mag += m;
                    sum_mag2 += (double)m * m;
                    sum_arg += a;
                    sum_arg2 += (double)a * a;
                    cnt++;
                }
                double mean_mag = (cnt > 0) ? sum_mag / cnt : 0.0;
                double var_mag = (cnt > 0) ? (sum_mag2 / cnt - mean_mag * mean_mag) : 0.0;
                double std_mag = (var_mag > 0) ? std::sqrt(var_mag) : 0.0;
                double mean_arg = (cnt > 0) ? sum_arg / cnt : 0.0;
                double var_arg = (cnt > 0) ? (sum_arg2 / cnt - mean_arg * mean_arg) : 0.0;
                double std_arg = (var_arg > 0) ? std::sqrt(var_arg) : 0.0;

                char h52_dump[2048];
                int pn = snprintf(h52_dump, sizeof(h52_dump),
                                  "[H52_DUMP_FILTERED] counter=%d |H|=",
                                  h52_filtered_counter_hdr);
                for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 32; i++) {
                    int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                     std::abs(Hhdr52[i]));
                    if (w < 0) break;
                    pn += w;
                }
                pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                               " arg(H)=");
                for (int i = 0; i < 52 && pn < (int)sizeof(h52_dump) - 16; i++) {
                    int w = snprintf(h52_dump + pn, sizeof(h52_dump) - pn, "%.3f,",
                                     std::arg(Hhdr52[i]));
                    if (w < 0) break;
                    pn += w;
                }
                pn += snprintf(h52_dump + pn, sizeof(h52_dump) - pn,
                               " mean|H|=%.3f std|H|=%.3f mean(argH)=%.3f std(argH)=%.3f\n",
                               mean_mag, std_mag, mean_arg, std_arg);
                USRP_LOG("%s", h52_dump);
            }

            bool found = false;

            // ----- Diagnostic state (Task 5: capture L-SIG/HT-SIG parse-failure details) -----
            // We log once per parse attempt on the failure path with the most informative
            // stats so we can see *why* USRP frames are failing.
            int  lsig_last_rate        = -1;
            int  lsig_last_len         = -1;
            int  lsig_last_parity_ok   = -1;
            int  lsig_last_inv         = -1;
            bool lsig_saw_viterbi_fail = false;  // viterbi decode failed (rate/length not extractable)
            int  lsig_viterbi_fail_inv = -1;
            int  lsig_decode_calls     = 0;      // # of inv_lsig calls that ran viterbi
            int  htsig_candidates_tried = 0;     // 4 rot * 2 inv_a * 2 inv_b max = 16
            int  htsig_lsig_enc        = -1;     // L-SIG enc passed to HT-SIG path
            int  htsig_last_rot        = -1;
            int  htsig_last_inv_a      = -1;
            int  htsig_last_inv_b      = -1;

            // Average SNR of equalized L-SIG/HT-SIG symbols (BPSK/QBPSK). Computed once
            // for diagnostic use from the equalized L-SIG (no CPE) and HT-SIG0.
            double avg_snr_lsig = 0.0;
            double avg_snr_htsig = 0.0;
            {
                // L-SIG: 48 data subcarriers
                double sum_mag2 = 0.0;
                int    cnt      = 0;
                for (int i = 0; i < 48; i++) {
                    if (std::abs(Hhdr52[i]) > 0.001f) {
                        gr_complex eq = safe_div(d_early_eqsym[kLSigRel][i], Hhdr52[i]);
                        sum_mag2 += (double)eq.real() * eq.real() + (double)eq.imag() * eq.imag();
                        cnt++;
                    }
                }
                if (cnt > 0) {
                    // For ideal BPSK at unit amplitude, E[|eq|^2] = 1.0.
                    // avg_snr = avg_mag2 / 1.0 (signal power ~ 1).
                    avg_snr_lsig = (sum_mag2 / (double)cnt);
                }
            }
            {
                // HT-SIG0: 48 data subcarriers (BPSK with QBPSK 90° rotation)
                double sum_mag2 = 0.0;
                int    cnt      = 0;
                for (int i = 0; i < 48; i++) {
                    if (std::abs(Hhdr52[i]) > 0.001f) {
                        gr_complex eq = safe_div(d_early_eqsym[kHtSig0Rel][i], Hhdr52[i]);
                        sum_mag2 += (double)eq.real() * eq.real() + (double)eq.imag() * eq.imag();
                        cnt++;
                    }
                }
                if (cnt > 0) {
                    avg_snr_htsig = (sum_mag2 / (double)cnt);
                }
            }

            // L-SIG invert brute-force
            for (int inv_lsig = 0; inv_lsig <= 1 && !found; inv_lsig++) {
                int lsig_enc = -1;
                int lsig_len = 0;
                int lsig_rate_field = -1;
                int lsig_parity_ok_int = -1;

                bool lsig_ok = decode_lsig_direct_from_header52(d_early_eqsym[kLSigRel],
                                                                 Hhdr52,
                                                                 inv_lsig != 0,
                                                                 lsig_enc,
                                                                 lsig_len,
                                                                 &lsig_rate_field,
                                                                 nullptr,         // out_psdu_length: not needed (already in lsig_len)
                                                                 &lsig_parity_ok_int,
                                                                 nullptr,
                                                                 nullptr);
                if (lsig_ok) {
                    lsig_decode_calls++;
                    lsig_last_inv       = inv_lsig;
                    lsig_last_rate      = lsig_rate_field;
                    lsig_last_len       = lsig_len;
                    lsig_last_parity_ok = lsig_parity_ok_int;
                } else {
                    // viterbi-decode failure means we never extracted rate/len/parity
                    // (we still want to distinguish viterbi fail from "rate/length wrong")
                    lsig_viterbi_fail_inv = inv_lsig;
                    lsig_saw_viterbi_fail = true;
                }

                if (!lsig_ok) {
                    continue;
                }

                if (lsig_enc != 0 && !getenv("IEEE80211_FORCE_HTSIG")) {
                    // L-SIG succeeded with non-BPSK 1/2 rate - skip and try other inv
                    continue;
                }
                if (lsig_enc != 0) {
                    USRP_LOG("[FORCE_HTSIG] sym=%d lsig_enc=%d, attempting HT-SIG despite non-zero enc\n",
                             d_internal_symbol_counter, lsig_enc);
                }

                htsig_lsig_enc = lsig_enc;

                // Detect HT-SIG QBPSK rotation
                int detected_rot = detect_htsig_rotation(d_early_eqsym[kHtSig0Rel]);
                // Energy-based rotation verification
                int energy_rot = vote_qbpsk_rotation(d_early_eqsym[kHtSig0Rel]);

                int start_rot = 0;
                if (energy_rot != detected_rot && energy_rot == 1) {
                    start_rot = energy_rot;
                }

                // Try all 4 rotations and 180 degree ambiguity on each symbol
                for (int rot = 0; rot <= 3 && !found; rot++) {
                    gr_complex rot_htsig0[52];
                    gr_complex rot_htsig1[52];
                    apply_htsig_rotation(d_early_eqsym[kHtSig0Rel], rot_htsig0, rot);
                    apply_htsig_rotation(d_early_eqsym[kHtSig1Rel], rot_htsig1, rot);

                    for (int inv_a = 0; inv_a <= 1 && !found; inv_a++) {
                        for (int inv_b = 0; inv_b <= 1 && !found; inv_b++) {
                            int parsed_len = 0;
                            int parsed_mcs = -1;
                            bool parsed_sgi = false;
                            bool parsed_agg = false;
                            bool parsed_use_ldpc = false;

                            htsig_candidates_tried++;
                            htsig_last_rot   = rot;
                            htsig_last_inv_a = inv_a;
                            htsig_last_inv_b = inv_b;

                            int cand_metric = -1;
                            const char* cand_fail = "init";
                            bool decode_ok = decode_htsig_from_rotated(rot_htsig0,
                                                           rot_htsig1,
                                                           Hhdr52,
                                                           inv_a != 0,
                                                           inv_b != 0,
                                                           parsed_len,
                                                           parsed_mcs,
                                                           parsed_sgi,
                                                           parsed_agg,
                                                           parsed_use_ldpc,
                                                           rot,
                                                           &cand_metric,
                                                           &cand_fail);
                            // Per-rotation metric trace: log ALL 16 candidates so we can
                            // see which rotations produce a meaningful viterbi best-path
                            // metric, vs. metrics that are saturated (RANDOM-like).
                            USRP_LOG("[HT_SIG_CAND] sym=%d rot=%d inv_a=%d inv_b=%d "
                                     "metric=%d fail=%s\n",
                                     d_internal_symbol_counter,
                                     rot, inv_a, inv_b,
                                     cand_metric, cand_fail);
                            if (!decode_ok) {
                                continue;
                            }

                            d_have_lsig = true;
                            d_lsig_rel = kLSigRel;
                            d_hdr_reorder_mode = 0;
                            d_hdr_inverted = false;
                            d_htsig0_rel = kHtSig0Rel;
                            d_htsig1_rel = kHtSig1Rel;
                            d_data_start_rel = kDataStartRel;
                            d_chan_est_mode = 0;

                            set_ht_frame_params_from_mcs_len(parsed_mcs, parsed_len, parsed_use_ldpc);

                        found = true;
                    }
                }
                }
            }

            if (!found) {
                // ----------------------------------------------------------------
                // Task 5 diagnostic: log L-SIG/HT-SIG parse failure with details.
                // Distinguish two failure modes:
                //   (a) L-SIG never produced a valid BPSK-1/2 frame (so we never
                //       even tried HT-SIG).
                //   (b) L-SIG succeeded but HT-SIG brute-force exhausted all 16
                //       candidates (4 rot * 2 inv_a * 2 inv_b).
                // ----------------------------------------------------------------
                const int lsig_calls_ran = lsig_decode_calls;
                if (lsig_calls_ran == 0) {
                    // L-SIG never even got past viterbi decode.
                    // Distinguish: viterbi failed vs rate/length invalid.
                    const char* reason = lsig_saw_viterbi_fail
                        ? "viterbi_fail"
                        : "rate_or_length_invalid";
                    USRP_LOG("[LSIG_PARSE_FAIL] sym=%d reason='%s' rate=%d length=%d "
                             "parity_ok=%d avg_snr=%.2f avg_snr_ht=%.2f inv_tried=0,1 "
                             "is_ht_frame=%d\n",
                             d_internal_symbol_counter,
                             reason,
                             lsig_last_rate,
                             lsig_last_len,
                             lsig_last_parity_ok,
                             avg_snr_lsig,
                             avg_snr_htsig,
                             d_is_ht_frame ? 1 : 0);
                } else {
                    // L-SIG succeeded (enc=0 BPSK 1/2) but HT-SIG decode failed across
                    // all 16 candidates.
                    USRP_LOG("[HT_SIG_PARSE_FAIL] timeout_sym=%d n_candidates=%d "
                             "best_metric=N/A threshold=N/A avg_snr_lsig=%.2f "
                             "avg_snr_htsig=%.2f lsig_rate=0x%X lsig_len=%d "
                             "lsig_inv=%d last_rot=%d last_inv_a=%d last_inv_b=%d "
                             "is_ht_frame=%d\n",
                             d_internal_symbol_counter,
                             htsig_candidates_tried,
                             avg_snr_lsig,
                             avg_snr_htsig,
                             lsig_last_rate,
                             lsig_last_len,
                             lsig_last_inv,
                             htsig_last_rot,
                             htsig_last_inv_a,
                             htsig_last_inv_b,
                             d_is_ht_frame ? 1 : 0);
                }
            }
        }

        bool tag_this_output_as_frame_start = false;
        bool emit_this_symbol = false;

        if (d_have_ht_header) {
            if (d_sym_idx == d_data_start_rel) {
                tag_this_output_as_frame_start = true;
            }
            if (d_sym_idx >= d_data_start_rel) {
                emit_this_symbol = true;
            }
        }

        if (emit_this_symbol && (produced + 52) <= noutput_items) {
            gr_complex* out52 = out + produced;

            const bool use_direct_tx_order =
                (d_have_ht_header && d_is_ht);
            const int data_sym_idx = d_sym_idx - d_data_start_rel;

            if (use_direct_tx_order) {
                if (!d_H52_tx_order_valid) {
                    // Always use L-LTF0 for H estimation.
                    // compute_H52_tx_order is designed for L-LTF0 data (uses kLltf64Binned).
                    // Using it with HT-LTF1 data is a category error - HT-LTF has
                    // different TX reference sequence (PHT_LTF vs legacy LTF).
                    // Edge subcarriers (-28,-27,+27,+28) already come from HT-LTF1
                    // via saved_htltf_edge, so the edge improvement is preserved.
                    compute_H52_tx_order(d_early_eqsym[kLltf0Rel], d_H52_tx_order);
                    d_H52_tx_order_valid = true;
                }
                extract_ht_data52_direct_tx_order(sym64, data_sym_idx, d_H52_tx_order, out52);
            } else {
                if (!reorder_eq_52_mode(raw_eq52, out52, d_hdr_reorder_mode)) {
                    std::memcpy(out52, raw_eq52, 52 * sizeof(gr_complex));
                }
            }

            {
                pmt::pmt_t meta = pmt::make_dict();
                meta = pmt::dict_add(meta, pmt::mp("packet_len"), pmt::from_long(52));
                meta = pmt::dict_add(meta, pmt::mp("mcs"), pmt::from_long(d_frame_mcs));
                pmt::pmt_t vec = pmt::init_c32vector(52, out52);
                message_port_pub(pmt::mp("symbols"), pmt::cons(meta, vec));
            }

            USRP_LOG( "[EQ_EMIT] sym=%d/%d produced=%d nout=%d\n", d_sym_idx, d_data_start_rel, produced, noutput_items);

            if (tag_this_output_as_frame_start && !d_frame_bytes_tag_emitted) {
                d_frame_bytes_tag_emitted = true;
                const uint64_t out_off = this->nitems_written(0) + produced;
                USRP_LOG( "[EQ_TAG] frame_bytes out_off=%llu nwritten=%llu produced=%d\n",
                        (unsigned long long)out_off, (unsigned long long)this->nitems_written(0), produced);

                this->add_item_tag(
                    0,
                    out_off,
                    pmt::intern("frame_bytes"),
                    pmt::from_uint64((uint64_t)d_frame_bytes),
                    pmt::intern(this->name()));

                this->add_item_tag(
                    0,
                    out_off,
                    pmt::intern("frame bytes"),
                    pmt::from_uint64((uint64_t)d_frame_bytes),
                    pmt::intern(this->name()));

                this->add_item_tag(
                    0,
                    out_off,
                    pmt::intern("encoding"),
                    pmt::from_uint64((uint64_t)d_frame_encoding),
                    pmt::intern(this->name()));

                this->add_item_tag(
                    0,
                    out_off,
                    pmt::intern("mcs"),
                    pmt::from_uint64((uint64_t)d_frame_mcs),
                    pmt::intern(this->name()));

                // Forward LDPC info so decode_mac can collect the right number of symbols
                this->add_item_tag(
                    0,
                    out_off,
                    pmt::intern("use_ldpc"),
                    pmt::from_bool(d_use_ldpc),
                    pmt::intern(this->name()));

                if (d_use_ldpc && d_ldpc_n_sym > 0) {
                    this->add_item_tag(
                        0,
                        out_off,
                        pmt::intern("ldpc_n_sym"),
                        pmt::from_long(d_ldpc_n_sym),
                        pmt::intern(this->name()));
                    USRP_LOG( "[EQ_TAG] use_ldpc=1 ldpc_n_sym=%d\n", d_ldpc_n_sym);
                }
            }

            produced += 52;
        }

        consumed++;
        d_current_symbol++;
        d_sym_idx++;
        d_internal_symbol_counter++;  // Track actual symbol count per FFT output

        if (d_have_ht_header && d_frame_symbols > 0) {
            const int end_rel = d_data_start_rel + d_frame_symbols;
            if (d_sym_idx >= end_rel) {
                USRP_LOG( "[EQ_FRAME_END] frame end reached sym_idx=%d end_rel=%d misproc=%d\n",
                        d_sym_idx, end_rel, d_takeover_reject_symbols);
                reset_frame_state();
                d_in_frame = false;
            }
        } else if (d_in_frame && !d_have_ht_header && d_sym_idx >= d_data_start_rel + 5) {
            USRP_LOG( "[EQ_FRAME_END] HT-SIG timeout sym_idx=%d, discarding remaining symbols until next wifi_start\n", d_sym_idx);
            reset_frame_state();
            d_discard_until_wifi_start = true;
            d_in_frame = false;
        }

        if (d_in_frame && d_sym_idx > kMaxFrameRel) {
            USRP_LOG( "[EQ_FRAME_END] max frame exceeded sym_idx=%d\n", d_sym_idx);
            reset_frame_state();
            d_discard_until_wifi_start = true;
            d_in_frame = false;
        }
    }

    consume_each(consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr


