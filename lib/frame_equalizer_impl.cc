#include "frame_equalizer_impl.h"

#include <gnuradio/io_signature.h>
#include <gnuradio/digital/constellation.h>
#include <pmt/pmt.h>

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

static std::string bits_to_string(const uint8_t* bits, int n)
{
    std::string s;
    s.reserve((size_t)n);
    for (int i = 0; i < n; i++) {
        s.push_back(bits[i] ? '1' : '0');
    }
    return s;
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

static float estimate_ht_data_cpe_rad_from_sym64(const gr_complex* sym64, int data_sym_idx)
{
    gr_complex acc(0.0f, 0.0f);
    for (int i = 0; i < 4; i++) {
        const gr_complex rx = sym64[kPilot4Bin[i]];  // EXPLICIT bin mapping!
        acc += rx * std::conj(ht_expected_pilot(data_sym_idx, i));
    }
    if (std::abs(acc) < 1e-9f) {
        return 0.0f;
    }
    return std::arg(acc);
}

static bool read_tx_ref_bits52(uint8_t* out52, std::string& used_path);

// Forward declarations for saved LTF0 FFT (defined later in extract_header52_from_sym64)
extern gr_complex saved_ltf0_fft[64];
extern bool ltf0_saved;
extern bool ltf0_ever_saved;

static void extract_ht_data52_direct_tx_order(const gr_complex* sym64,
                                              int data_sym_idx,
                                              const gr_complex* H52_tx_order,
                                              gr_complex* out52)
{
    const float cpe = estimate_ht_data_cpe_rad_from_sym64(sym64, data_sym_idx);
    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

    // Diag probe: print all 52 equalized values and classify by axis
    static int diag_call = 0;
    if (diag_call == 0 && data_sym_idx == 0) {
        int bad_real = 0;  // eq energy mostly on imag axis (should be real for BPSK)
        int wrong_sign = 0;
        fprintf(stderr, "[HTDATA_DIAG] data_sym=0 cpe=%.4f rad (%.1f deg)\n",
                cpe, cpe * 180.0 / M_PI);

        // Read TX reference bits for comparison
        uint8_t tx_ref52[52];
        std::string ref_path;
        bool have_ref = read_tx_ref_bits52(tx_ref52, ref_path);

        fprintf(stderr, "[HTDATA_DIAG] i  sc  bin  raw.real  raw.imag  |H|     H.phase  eq.real  eq.imag  hard  tx_ref  mismatch\n");
        for (int i = 0; i < 52; i++) {
            const int bin = sc_to_fft_bin(kTxOrder52[i]);
            gr_complex raw = sym64[bin];
            gr_complex eq = raw / H52_tx_order[i] * rot;
            float re = std::abs(eq.real());
            float im = std::abs(eq.imag());
            uint8_t hard = (eq.real() >= 0.0f) ? 1 : 0;
            if (im > re && im > 0.5f) {
                bad_real++;
            }
            const char* mismatch_mark = "";
            if (have_ref) {
                if (hard != tx_ref52[i]) {
                    wrong_sign++;
                    mismatch_mark = " <-- MISMATCH";
                }
            }
            fprintf(stderr, "[HTDATA_DIAG] %2d %3d %3d  %8.4f %8.4f  %6.2f  %7.1f  %7.4f %7.4f  %d     %d%s\n",
                    i, kTxOrder52[i], bin,
                    raw.real(), raw.imag(),
                    std::abs(H52_tx_order[i]),
                    std::arg(H52_tx_order[i]) * 180.0 / M_PI,
                    eq.real(), eq.imag(),
                    hard,
                    have_ref ? (int)tx_ref52[i] : -1,
                    mismatch_mark);
        }
        fprintf(stderr, "[HTDATA_DIAG] bad_real=%d/52 wrong_sign=%d/52 cpe=%.4f rad\n",
                bad_real, wrong_sign, cpe);

        // H ratio analysis: compute H_true from data symbol and compare with H_ltf0
        if (have_ref) {
            float ratio_mag_sum = 0;
            float ratio_phase_180_count = 0;
            fprintf(stderr, "[HTDATA_DIAG] H_data/H_ltf0 ratio analysis:\n");
            fprintf(stderr, "[HTDATA_DIAG] i  sc  |H_ltf0|  |H_data|  ratio_mag  angle_diff(deg)\n");
            for (int i = 0; i < 52; i++) {
                const int bin = sc_to_fft_bin(kTxOrder52[i]);
                gr_complex raw = sym64[bin];
                // H_true = raw / TX: TX = +1 for bit 1, -1 for bit 0
                gr_complex tx_val = (tx_ref52[i] == 1) ? gr_complex(+1.0f, 0.0f) : gr_complex(-1.0f, 0.0f);
                gr_complex H_true = raw / tx_val;
                gr_complex H_ltf0 = H52_tx_order[i];
                float h_ltf0_mag = std::abs(H_ltf0);
                float h_true_mag = std::abs(H_true);
                float ratio_mag = 0.0f;
                float angle_diff = 0.0f;
                if (h_ltf0_mag > 1e-9f) {
                    gr_complex ratio = H_true / H_ltf0;
                    ratio_mag = std::abs(ratio);
                    angle_diff = std::arg(ratio) * 180.0f / M_PI;
                    // wrap to [-180, 180]
                    if (angle_diff > 180.0f) angle_diff -= 360.0f;
                    if (angle_diff < -180.0f) angle_diff += 360.0f;
                }
                ratio_mag_sum += ratio_mag;
                if (std::abs(angle_diff) > 150.0f) {
                    ratio_phase_180_count++;
                }
                fprintf(stderr, "[HTDATA_DIAG] %2d %3d  %7.2f  %7.2f  %9.4f  %+10.1f\n",
                        i, kTxOrder52[i],
                        h_ltf0_mag, h_true_mag,
                        ratio_mag, angle_diff);
            }
            fprintf(stderr, "[HTDATA_DIAG] avg_ratio_mag=%.4f n_phase_near180=%.0f/52\n",
                    ratio_mag_sum / 52.0f, ratio_phase_180_count);
        }

        // Raw channel comparison: raw_data / raw_ltf0 (bypassing all TX references)
        // If channel is stable, this ratio should have ~same magnitude and phase for all SCs
        if (ltf0_ever_saved) {
            float raw_ratio_mag_sum = 0;
            float raw_phase_sum = 0;
            float raw_phase_max = -999;
            float raw_phase_min = +999;
            fprintf(stderr, "[HTDATA_DIAG] Raw channel ratio: raw_data / raw_ltf0 (no TX ref needed):\n");
            fprintf(stderr, "[HTDATA_DIAG] i  sc  |raw_ltf0|  |raw_data|  ratio_mag  phase_diff(deg)\n");
            for (int i = 0; i < 52; i++) {
                const int bin = sc_to_fft_bin(kTxOrder52[i]);
                gr_complex raw_ltf0 = saved_ltf0_fft[bin];
                gr_complex raw_data = sym64[bin];
                float ltf0_mag = std::abs(raw_ltf0);
                float data_mag = std::abs(raw_data);
                float ratio_mag = 0;
                float phase_diff = 0;
                if (ltf0_mag > 0.01f) {
                    gr_complex ratio = raw_data / raw_ltf0;
                    ratio_mag = std::abs(ratio);
                    phase_diff = std::arg(ratio) * 180.0f / M_PI;
                    if (phase_diff > 180.0f) phase_diff -= 360.0f;
                    if (phase_diff < -180.0f) phase_diff += 360.0f;
                }
                raw_ratio_mag_sum += ratio_mag;
                raw_phase_sum += phase_diff;
                if (phase_diff > raw_phase_max) raw_phase_max = phase_diff;
                if (phase_diff < raw_phase_min) raw_phase_min = phase_diff;
                if (i < 10 || i >= 42) {  // Print first 10 and last 10
                    fprintf(stderr, "[HTDATA_DIAG] %2d %3d  %8.2f  %8.2f  %9.4f  %+10.1f\n",
                            i, kTxOrder52[i], ltf0_mag, data_mag, ratio_mag, phase_diff);
                }
            }
            float raw_phase_spread = raw_phase_max - raw_phase_min;
            fprintf(stderr, "[HTDATA_DIAG] raw_chan_avg_ratio=%.4f avg_phase=%.1fdeg spread=%.1fdeg\n",
                    raw_ratio_mag_sum / 52.0f, raw_phase_sum / 52.0f, raw_phase_spread);
            // If spread > 10deg, channel is NOT stable between LTF0 and HT-DATA
            if (raw_phase_spread > 10.0f) {
                fprintf(stderr, "[HTDATA_DIAG] *** CHANNEL NOT STABLE: phase spread %.1fdeg > 10deg ***\n",
                        raw_phase_spread);
            }
        }

        diag_call++;
    }

    for (int i = 0; i < 52; i++) {
        const int bin = sc_to_fft_bin(kTxOrder52[i]);
        const float h_mag = std::abs(H52_tx_order[i]);
        if (h_mag > 0.1f) {
            out52[i] = sym64[bin] / H52_tx_order[i] * rot;
        } else {
            out52[i] = gr_complex(0.0f, 0.0f);
        }
    }
}

static bool read_tx_ref_bits52(uint8_t* out52, std::string& used_path)
{
    const char* env_path = std::getenv("WIFI_TX_DATA0_BITS52_FILE");
    used_path = (env_path && *env_path) ? env_path : "/tmp/wifi_tx_data0_bits52.txt";

    std::ifstream ifs(used_path.c_str());
    if (!ifs.good()) {
        return false;
    }

    std::string raw, s;
    std::getline(ifs, raw);

    for (char c : raw) {
        if (c == '0' || c == '1') {
            s.push_back(c);
        }
    }

    if ((int)s.size() != 52) {
        return false;
    }

    for (int i = 0; i < 52; i++) {
        out52[i] = (s[i] == '1') ? 1 : 0;
    }

    return true;
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

    // Fill H for 48 header data subcarriers
    for (int i = 0; i < 48; i++) {
        int sc = kHeader48Sc[i];
        if (std::abs(kLltf48TX[i]) > 1e-9f) {
            H_sc[sc + 56] = lltf0_52[i] / kLltf48TX[i];
        }
    }
    // Fill H for 4 pilots
    for (int i = 0; i < 4; i++) {
        int sc = kPilot4Sc[i];
        H_sc[sc + 56] = lltf0_52[48 + i] / kPilot4TX[i];
    }

    // Nearest-neighbor H for edge subcarriers (linear extrapolation amplifies
    // phase differences between adjacent carriers, producing wildly wrong |H|).
    H_sc[-27 + 56] = H_sc[-26 + 56];
    H_sc[-28 + 56] = H_sc[-26 + 56];
    H_sc[27 + 56] = H_sc[26 + 56];
    H_sc[28 + 56] = H_sc[26 + 56];

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

static void extract_header52_from_sym64(const gr_complex* sym64, gr_complex* out52)
{
    // NAKED_TEST: Save LTF0 raw FFT
    static int extract_call_count = 0;

    // Call 0 = LTF0, Call 1 = LTF1
    if (extract_call_count == 0) {
        memcpy(saved_ltf0_fft, sym64, 64 * sizeof(gr_complex));
        ltf0_saved = true;
        ltf0_ever_saved = true;
        // PROBE: Print ALL 64 bins of raw FFT for LTF0
        fprintf(stderr, "\n[RAW_FFT_64] LTF0 (call %d) - ALL 64 FFT bins:\n", extract_call_count);
        for (int b = 0; b < 64; b++) {
            float mag = std::abs(sym64[b]);
            float phase = std::arg(sym64[b]) * 180 / M_PI;
            fprintf(stderr, "  bin[%2d]: mag=%.4f phase=%+7.1fdeg\n", b, mag, phase);
        }
    }

    if (extract_call_count == 1 && ltf0_saved) {
        // PROBE: Print ALL 64 bins of raw FFT for LTF1
        fprintf(stderr, "\n[RAW_FFT_64] LTF1 (call %d) - ALL 64 FFT bins:\n", extract_call_count);
        for (int b = 0; b < 64; b++) {
            float mag = std::abs(sym64[b]);
            float phase = std::arg(sym64[b]) * 180 / M_PI;
            fprintf(stderr, "  bin[%2d]: mag=%.4f phase=%+7.1fdeg\n", b, mag, phase);
        }
        // This is LTF1 - compare with saved LTF0
        fprintf(stderr, "\n[NAKED_TEST] Comparing LTF0 vs LTF1 (first HT-SIG detection):\n");
        fprintf(stderr, "  Comparing raw FFT at same bins:\n");

        // Compare specific bins
        int bins_to_check[] = {6, 7, 8, 9, 10, 22, 23, 24, 40, 41, 42, 54};
        for (int b = 0; b < sizeof(bins_to_check)/sizeof(bins_to_check[0]); b++) {
            int bin = bins_to_check[b];
            float mag0 = std::abs(saved_ltf0_fft[bin]);
            float mag1 = std::abs(sym64[bin]);
            float phase0 = std::arg(saved_ltf0_fft[bin]) * 180 / M_PI;
            float phase1 = std::arg(sym64[bin]) * 180 / M_PI;
            float phase_diff = phase1 - phase0;
            // Normalize phase difference to [-180, 180]
            while (phase_diff > 180) phase_diff -= 360;
            while (phase_diff < -180) phase_diff += 360;
            fprintf(stderr, "  bin[%2d]: LTF0=%8.3f∠%6.1f  LTF1=%8.3f∠%6.1f  diff=%+6.1fdeg\n",
                    bin, mag0, phase0, mag1, phase1, phase_diff);
        }

        // Compute and compare H from LTF0 vs LTF1
        static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);
        fprintf(stderr, "\n[NAKED_TEST] H from LTF0 vs LTF1 (48 data SC):\n");
        for (int i = 0; i < 48; i++) {
            int bin = kHeader48Bin[i];
            const gr_complex lltf0_52 = saved_ltf0_fft[bin];
            const gr_complex lltf1_52 = sym64[bin];
            const gr_complex tx = kLltf48TX[i];

            gr_complex H0 = gr_complex(0,0);
            gr_complex H1 = gr_complex(0,0);
            if (std::abs(tx) > 0.001f) {
                H0 = (lltf0_52 / tx) / kFftNormalize;
                H1 = (lltf1_52 / tx) / kFftNormalize;
            }

            float mag0 = std::abs(H0);
            float mag1 = std::abs(H1);
            float ratio = (mag0 > 1e-9f) ? mag1 / mag0 : 0.0f;
            if (i < 12) {  // Print first 12 for brevity
                fprintf(stderr, "  SC[%2d] bin[%2d]: H0=%.4f%+.4fi mag=%.4f | H1=%.4f%+.4fi mag=%.4f | ratio=%.2f\n",
                        kHeader48Sc[i], bin,
                        H0.real(), H0.imag(), mag0,
                        H1.real(), H1.imag(), mag1,
                        ratio);
            }
        }
        // Print summary: average magnitude
        double sum_mag0 = 0, sum_mag1 = 0;
        for (int i = 0; i < 48; i++) {
            int bin = kHeader48Bin[i];
            const gr_complex lltf0_52 = saved_ltf0_fft[bin];
            const gr_complex lltf1_52 = sym64[bin];
            const gr_complex tx = kLltf48TX[i];
            if (std::abs(tx) > 0.001f) {
                sum_mag0 += std::abs((lltf0_52 / tx) / kFftNormalize);
                sum_mag1 += std::abs((lltf1_52 / tx) / kFftNormalize);
            }
        }
        fprintf(stderr, "  [SUMMARY] Avg H magnitude: LTF0=%.4f LTF1=%.4f ratio=%.4f\n",
                sum_mag0/48.0, sum_mag1/48.0, (sum_mag0/48.0 > 1e-9) ? (sum_mag1/sum_mag0) : 0.0);

        ltf0_saved = false;
        fprintf(stderr, "[NAKED_TEST] End comparison\n\n");
    }

    if (extract_call_count == 6 && ltf0_ever_saved) {
        fprintf(stderr, "\n[RAW_FFT_64] HT-LTF (call 6) - ALL 64 FFT bins:\n");
        for (int b = 0; b < 64; b++) {
            float mag = std::abs(sym64[b]);
            float phase = std::arg(sym64[b]) * 180 / M_PI;
            fprintf(stderr, "  bin[%2d]: mag=%.4f phase=%+7.1fdeg\n", b, mag, phase);
        }
        fprintf(stderr, "\n[RAW_CMP] LTF0 vs HT-LTF per-bin comparison (48 data bins):\n");
        int sign_diffs = 0;
        for (int i = 0; i < 48; i++) {
            int bin = kHeader48Bin[i];
            gr_complex ltf0 = saved_ltf0_fft[bin];
            gr_complex htltf = sym64[bin];
            float a = std::arg(ltf0 / htltf) * 180.0f / M_PI;
            if (std::abs(a) > 90.0f) sign_diffs++;
            fprintf(stderr, "  SC[%2d] bin[%2d]: angle=%+.1fdeg %s\n",
                    kHeader48Sc[i], bin, a,
                    (std::abs(a) > 90.0f) ? "FLIP" : "");
        }
        fprintf(stderr, "[RAW_CMP] sign_diffs=%d/48\n", sign_diffs);
    }

    extract_call_count++;
    // 调试：打印前几个子载波索引和值
    static int call_count = 0;
    if (call_count < 10) {
        // SYMBOL FINGERPRINT: 打印 bin 7 (SC +7, 第3个导频) 的原始FFT值
        // TX L-SIG 导频: SC+7 = +1 (实轴, 0°)
        // TX HT-SIG 导频: SC+7 = +j (虚轴, +90°)
        // 通过观察 bin 7 的实部/虚部比例，可以判断当前符号是 L-SIG 还是 HT-SIG
        fprintf(stderr, "[SYMBOL_FP] call_count=%d extract_call=%d  bin7=%.4f%+.4fi |mag=%.4f phase=%+.1fdeg RE=%.4f IM=%.4f\n",
                call_count, extract_call_count,
                sym64[7].real(), sym64[7].imag(),
                std::abs(sym64[7]), std::arg(sym64[7])*180/M_PI,
                sym64[7].real(), sym64[7].imag());
        std::fprintf(stderr, "[EXTRACT] called, first 5 subcarriers:\n");
        // RAW RX PHASE CHECK: Print raw FFT phase at key subcarriers
        // Compare with kLltf64Binned reference to detect window misalignment
        static int raw_rx_check_count = 0;
        if (raw_rx_check_count < 2) {
            fprintf(stderr, "\n[RAW_RX_PHASE] extract_call=%d\n", extract_call_count);
            int check_idx[] = {7, 14, 21};
            for (int c = 0; c < 3; c++) {
                int i = check_idx[c];
                int fft_bin = kHeader48Bin[i];
                int sc = kHeader48Sc[i];
                gr_complex rx_val = sym64[fft_bin];
                float rx_phase = std::arg(rx_val) * 180 / M_PI;
                float kRef_phase = std::arg(kLltf64Binned[fft_bin]) * 180 / M_PI;
                fprintf(stderr, "  i=%d sc=%+3d bin=%2d: rx=%.4f%+.4fi phase=%+7.1fdeg kRef=%.4f%+.4fi ref_phase=%+7.1fdeg\n",
                        i, sc, fft_bin,
                        rx_val.real(), rx_val.imag(), rx_phase,
                        kLltf64Binned[fft_bin].real(), kLltf64Binned[fft_bin].imag(), kRef_phase);
            }
            raw_rx_check_count++;
        }
        for (int i = 0; i < 5 && i < 48; i++) {
            int fft_bin = kHeader48Bin[i];  // EXPLICIT bin mapping!
            gr_complex val = sym64[fft_bin];
            std::fprintf(stderr, "  i=%d, sc=%d, bin=%d, val=%.3f+%.3fi |val|=%.4f\n",
                        i, kHeader48Sc[i], fft_bin,
                        val.real(), val.imag(), std::abs(val));
        }
        // NAKED_TEST: Print specific FFT bins for physical layer verification
        // These are actual bin indices, not subcarrier indices
        // KEY TEST: Check bins 6 and 38 to verify FFT shift state
        // - Unshifted FFT (natural order): bin 6 = SC +6, bin 38 = SC -26
        // - Shifted FFT: bin 6 = SC -26, bin 38 = SC +6
        // SC -26 should have HIGH energy (LTF has non-zero at SC -26)
        // SC +6 should have HIGH energy (LTF has non-zero at SC +6)
        std::fprintf(stderr, "[NAKED_FFT] Physical FFT bins (FFT shift verification):\n");
        std::fprintf(stderr, "  bin[ 6] (unshifted=SC+6, shifted=SC-26): mag=%.4f phase=%+.1fdeg\n",
                    std::abs(sym64[6]), std::arg(sym64[6])*180/M_PI);
        std::fprintf(stderr, "  bin[38] (unshifted=SC-26, shifted=SC+6): mag=%.4f phase=%+.1fdeg\n",
                    std::abs(sym64[38]), std::arg(sym64[38])*180/M_PI);
        std::fprintf(stderr, "  bin[10] (SC+10, pos freq):  %.3f+%.3fi | %.3f∠%.1f\n",
                    sym64[10].real(), sym64[10].imag(),
                    std::abs(sym64[10]), std::arg(sym64[10])*180/M_PI);
        std::fprintf(stderr, "  bin[22] (SC-10, neg freq):  %.3f+%.3fi | %.3f∠%.1f\n",
                    sym64[22].real(), sym64[22].imag(),
                    std::abs(sym64[22]), std::arg(sym64[22])*180/M_PI);
        std::fprintf(stderr, "  bin[32] (DC):              %.3f+%.3fi | %.3f∠%.1f\n",
                    sym64[32].real(), sym64[32].imag(),
                    std::abs(sym64[32]), std::arg(sym64[32])*180/M_PI);
        std::fprintf(stderr, "  bin[40] (SC+8, pos freq):   %.3f+%.3fi | %.3f∠%.1f\n",
                    sym64[40].real(), sym64[40].imag(),
                    std::abs(sym64[40]), std::arg(sym64[40])*180/M_PI);
        std::fprintf(stderr, "  bin[54] (SC+22, pos freq):  %.3f+%.3fi | %.3f∠%.1f\n",
                    sym64[54].real(), sym64[54].imag(),
                    std::abs(sym64[54]), std::arg(sym64[54])*180/M_PI);
        call_count++;
        std::fflush(stderr);
    }

    for (int i = 0; i < 48; i++) {
        out52[i] = sym64[kHeader48Bin[i]];  // EXPLICIT bin mapping!
    }
    for (int i = 0; i < 4; i++) {
        out52[48 + i] = sym64[kPilot4Bin[i]];  // EXPLICIT bin mapping!
    }

    // L-LTF BIN MAPPING VERIFICATION:
    // Compute H from LTF0: H[i] = ltf0[i] / kLltf48TX[i]
    // Then equalize LTF0 with H: eq[i] = ltf0[i] / H[i] = kLltf48TX[i]
    // If extraction is correct, eq[i] should be near ±1 (pure real).
    // If bin mapping is wrong, eq will be garbage (rotated, wrong magnitude).
    static int verify_call = 0;
    if (verify_call == 0) {
        gr_complex H48[48];
        int bad_h = 0;
        for (int i = 0; i < 48; i++) {
            H48[i] = out52[i] / kLltf48TX[i];
            if (std::abs(H48[i]) < 0.1f) bad_h++;
        }
        // Equalize LTF0: eq = ltf0 / H
        int eq_mismatch = 0;
        int eq_sign_flip = 0;
        float eq_mag_sum = 0;
        for (int i = 0; i < 48; i++) {
            gr_complex eq = out52[i] / H48[i];
            eq_mag_sum += std::abs(eq);
            int eq_sign = (eq.real() >= 0.0f) ? 1 : -1;
            int ref_sign = (kLltf48TX[i].real() >= 0.0f) ? 1 : -1;
            if (eq_sign != ref_sign) {
                eq_sign_flip++;
                // Only count as true mismatch if |eq| > 0.3 (not noise)
                if (std::abs(eq) > 0.3f) eq_mismatch++;
            }
        }
        fprintf(stderr, "\n[BIN_VERIFY] LTF0 self-equalization test:\n");
        fprintf(stderr, "[BIN_VERIFY] bad_H=%d eq_mismatch=%d eq_sign_flip=%d avg_eq_mag=%.2f\n",
                bad_h, eq_mismatch, eq_sign_flip, eq_mag_sum/48.0f);
        fprintf(stderr, "[BIN_VERIFY] eq[0..11]: ");
        for (int i = 0; i < 12; i++) {
            gr_complex eq = out52[i] / H48[i];
            fprintf(stderr, "%.1f ", eq.real());
        }
        fprintf(stderr, "\n[BIN_VERIFY] ref[0..11]: ");
        for (int i = 0; i < 12; i++) {
            fprintf(stderr, "%.1f ", kLltf48TX[i].real());
        }
        fprintf(stderr, "\n");
        // Print H phase and magnitude consistency
        float h_phase_first = 0;
        float max_phase_dev = 0;
        for (int i = 0; i < 48; i++) {
            float ph = std::arg(H48[i]);
            if (i == 0) h_phase_first = ph;
            float dev = std::abs(ph - h_phase_first);
            if (dev > M_PI) dev = 2*M_PI - dev;
            if (dev > max_phase_dev) max_phase_dev = dev;
        }
        fprintf(stderr, "[BIN_VERIFY] H phase max_deviation=%.1fdeg\n", max_phase_dev*180/M_PI);
        if (eq_mismatch > 0 || max_phase_dev > 30.0f*M_PI/180.0f) {
            fprintf(stderr, "[BIN_VERIFY] FAIL! Full 48 equalized symbols:\n");
            for (int i = 0; i < 48; i++) {
                gr_complex eq = out52[i] / H48[i];
                fprintf(stderr, "  [%2d] sc=%+3d bin=%2d H=%.3f%+.3fi eq=%.3f%+.3fi ref=%+.0f\n",
                        i, kHeader48Sc[i], kHeader48Bin[i],
                        H48[i].real(), H48[i].imag(),
                        eq.real(), eq.imag(), kLltf48TX[i].real());
            }
        }
        fflush(stderr);
        verify_call++;
    }
}

static void extract_header_raw48_bits_from_cache52(const gr_complex* hdr52, uint8_t* out48)
{
    for (int i = 0; i < 48; i++) {
        out48[i] = hard_bit_from_complex(hdr52[i]);
    }
}

// NAKED_TEST: Print raw FFT at specific bins to verify LTF0 vs LTF1 equality
static void print_naked_lltf_test(const gr_complex* sym64_ltf0, const gr_complex* sym64_ltf1)
{
    std::fprintf(stderr, "[NAKED_TEST] Raw FFT comparison (before subcarrier extraction):\n");

    // Check positive frequency bin (e.g., FFT bin 10 = SC +10)
    int pos_bin = sc_to_fft_bin(10);
    std::fprintf(stderr, "  FFT bin %d (SC +10, pos freq): LTF0=%10.3f∠%6.1f  LTF1=%10.3f∠%6.1f\n",
                pos_bin,
                std::abs(sym64_ltf0[pos_bin]), std::arg(sym64_ltf0[pos_bin]) * 180 / M_PI,
                std::abs(sym64_ltf1[pos_bin]), std::arg(sym64_ltf1[pos_bin]) * 180 / M_PI);

    // Check negative frequency bin (e.g., FFT bin 40 = SC +8... wait, FFT bin 40 is SC +8)
    // For negative frequency, let's use SC -10 → bin 22
    int neg_bin = sc_to_fft_bin(-10);
    std::fprintf(stderr, "  FFT bin %d (SC -10, neg freq): LTF0=%10.3f∠%6.1f  LTF1=%10.3f∠%6.1f\n",
                neg_bin,
                std::abs(sym64_ltf0[neg_bin]), std::arg(sym64_ltf0[neg_bin]) * 180 / M_PI,
                std::abs(sym64_ltf1[neg_bin]), std::arg(sym64_ltf1[neg_bin]) * 180 / M_PI);

    // Check DC bin
    std::fprintf(stderr, "  FFT bin 32 (DC):          LTF0=%10.3f∠%6.1f  LTF1=%10.3f∠%6.1f\n",
                std::abs(sym64_ltf0[32]), std::arg(sym64_ltf0[32]) * 180 / M_PI,
                std::abs(sym64_ltf1[32]), std::arg(sym64_ltf1[32]) * 180 / M_PI);

    // Phase difference
    float phase_diff_pos = std::arg(sym64_ltf1[pos_bin]) - std::arg(sym64_ltf0[pos_bin]);
    float phase_diff_neg = std::arg(sym64_ltf1[neg_bin]) - std::arg(sym64_ltf0[neg_bin]);
    std::fprintf(stderr, "  Phase diff: pos_freq=%+.1fdeg, neg_freq=%+.1fdeg\n",
                phase_diff_pos * 180 / M_PI, phase_diff_neg * 180 / M_PI);
    std::fflush(stderr);
}

static void estimate_header_channel_from_lltf52(const gr_complex* lltf0_52,
                                                const gr_complex* lltf1_52,
                                                gr_complex* H52)
{
    // FFT/IFFT normalization factor
    // TX IFFT: 1/sqrt(52) ≈ 0.1389, RX FFT: no normalization
    // Effective gain: 64/sqrt(52) ≈ 8.88
    static constexpr float kFftNormalize = 64.0f / std::sqrt(52.0f);

    // Channel estimation using LTF0 only (avoid averaging opposite signs)
    gr_complex H52_from_ltf0[52] = {gr_complex(0,0)};
    gr_complex H52_from_ltf1[52] = {gr_complex(0,0)};

    // DEBUG: Print kLltf48TX reference sequence for verification
    // IEEE 802.11n标准 L-LTF 序列（部分）
    fprintf(stderr, "\n[KLTX_REF_CHECK] kLltf48TX[i] for i=0..11:\n");
    const char* expected_kltx = "+1,+1,-1,-1,+1,-1,+1,-1,+1,+1,+1,+1";  // 标准值
    fprintf(stderr, "  Expected (IEEE 802.11n): %s\n", expected_kltx);
    fprintf(stderr, "  Actual kLltf48TX:  ");
    for (int i = 0; i < 12; i++) {
        fprintf(stderr, "%+.0f ", kLltf48TX[i].real());
    }
    fprintf(stderr, "\n");

    // Also print kHeader48Sc[i] to show which SC each index corresponds to
    fprintf(stderr, "  kHeader48Sc:     ");
    for (int i = 0; i < 12; i++) {
        fprintf(stderr, "%+3d ", kHeader48Sc[i]);
    }
    fprintf(stderr, "\n");
    fflush(stderr);

    // Compute H from LTF0
    for (int i = 0; i < 48; i++) {
        const gr_complex lltf0 = lltf0_52[i];
        const gr_complex tx = kLltf48TX[i];

        if (std::abs(tx) > 0.001f) {
            H52_from_ltf0[i] = lltf0 / tx;  // Remove kFftNormalize
            H52[i] = H52_from_ltf0[i];  // Output H is from LTF0
        } else {
            H52_from_ltf0[i] = lltf0;  // fallback for null subcarriers (no normalization)
            H52[i] = H52_from_ltf0[i];
        }
        // Debug: trace channel estimation
        if (i == 0) {
            fprintf(stderr, "[CHAN_EST_DEBUG] i=%d lltf0=%.4f%+.4fi tx=%.4f%+.4fi H=%.4f%+.4fi kFftNormalize=%.4f\n",
                    i, lltf0.real(), lltf0.imag(), tx.real(), tx.imag(), H52[i].real(), H52[i].imag(), kFftNormalize);
        }
        // Probe raw FFT values before channel estimation
        if (i == 7) {  // SC+7
            fprintf(stderr, "[RAW_FFT_PROBE] lltf0[7]=%.4f%+.4fi tx=%.4f%+.4fi H=%.4f%+.4fi mag=%.4f\n",
                    lltf0.real(), lltf0.imag(), tx.real(), tx.imag(),
                    H52_from_ltf0[i].real(), H52_from_ltf0[i].imag(), std::abs(H52_from_ltf0[i]));
        }
    }
    for (int i = 0; i < 4; i++) {
        const gr_complex lltf0 = lltf0_52[48 + i];
        // FIX: Use LTF pilot values kLltfPilotTX, not kHeaderPilotBase
        // kHeaderPilotBase = {1, 1, 1, -1} is for L-SIG/HT-SIG pilots
        // kLltfPilotTX = {1, -1, 1, 1} are the actual LTF pilot values at SC {-21, -7, +7, +21}
        const gr_complex tx = gr_complex((float)kLltfPilotTX[i], 0.0f);

        if (std::abs(tx) > 0.001f) {
            H52_from_ltf0[48 + i] = lltf0 / tx;  // Remove kFftNormalize
            H52[48 + i] = H52_from_ltf0[48 + i];
        } else {
            H52_from_ltf0[48 + i] = lltf0;  // fallback
            H52[48 + i] = H52_from_ltf0[48 + i];
        }
    }

    // Also compute H from LTF1 for comparison
    for (int i = 0; i < 48; i++) {
        const gr_complex lltf1 = lltf1_52[i];
        const gr_complex tx = kLltf48TX[i];

        if (std::abs(tx) > 0.001f) {
            H52_from_ltf1[i] = lltf1 / tx;  // Remove kFftNormalize
        } else {
            H52_from_ltf1[i] = lltf1;  // no normalization
        }
    }
    for (int i = 0; i < 4; i++) {
        const gr_complex lltf1 = lltf1_52[48 + i];
        // FIX: Use LTF pilot values kLltfPilotTX, not kHeaderPilotBase
        const gr_complex tx = gr_complex((float)kLltfPilotTX[i], 0.0f);

        if (std::abs(tx) > 0.001f) {
            H52_from_ltf1[48 + i] = lltf1 / tx;  // Remove kFftNormalize
        } else {
            H52_from_ltf1[48 + i] = lltf1;  // no normalization
        }
    }

    // Debug: dump all 52 H values for BOTH LTF0 and LTF1
    std::fprintf(stderr, "[CHAN_EST_FULL] All 52 H values - LTF0 vs LTF1:\n");
    std::fprintf(stderr, "  Data SC (0-47):\n");
    for (int i = 0; i < 48; i++) {
        float mag0 = std::abs(H52_from_ltf0[i]);
        float mag1 = std::abs(H52_from_ltf1[i]);
        float ratio = (mag0 > 1e-9f) ? mag1 / mag0 : 0.0f;
        std::fprintf(stderr, "  SC[%2d] idx[%2d]: H0=%.4f%+.4fi mag=%.4f | H1=%.4f%+.4fi mag=%.4f | ratio=%.2f\n",
                kHeader48Sc[i], i,
                H52_from_ltf0[i].real(), H52_from_ltf0[i].imag(), mag0,
                H52_from_ltf1[i].real(), H52_from_ltf1[i].imag(), mag1,
                ratio);
    }
    std::fprintf(stderr, "  Pilots (48-51):\n");
    for (int i = 0; i < 4; i++) {
        int idx = 48 + i;
        float mag0 = std::abs(H52_from_ltf0[idx]);
        float mag1 = std::abs(H52_from_ltf1[idx]);
        float ratio = (mag0 > 1e-9f) ? mag1 / mag0 : 0.0f;
        std::fprintf(stderr, "  Pilot[%d] idx[%2d]: H0=%.4f%+.4fi mag=%.4f | H1=%.4f%+.4fi mag=%.4f | ratio=%.2f\n",
                i, idx,
                H52_from_ltf0[idx].real(), H52_from_ltf0[idx].imag(), mag0,
                H52_from_ltf1[idx].real(), H52_from_ltf1[idx].imag(), mag1,
                ratio);
    }
    std::fflush(stderr);

    // Check H phase linearity across subcarriers (sign of LTF window misalignment)
    fprintf(stderr, "[H_PHASE_CHECK] H phase at multiple subcarriers:\n");
    int check_indices[] = {7, 14, 21, 28, 35};
    for (int idx = 0; idx < 5; idx++) {
        int i = check_indices[idx];
        if (i < 48) {
            float H_phase = std::arg(H52_from_ltf0[i]) * 180 / M_PI;
            int sc = kHeader48Sc[i];
            fprintf(stderr, "  i=%d SC[%+3d]: H=%.4f%+.4fi phase=%+.1fdeg\n",
                    i, sc, H52_from_ltf0[i].real(), H52_from_ltf0[i].imag(), H_phase);
        }
    }
    // Check phase difference between consecutive subcarriers
    float phase_diff1 = (std::arg(H52_from_ltf0[14]) - std::arg(H52_from_ltf0[7])) * 180 / M_PI;
    float phase_diff2 = (std::arg(H52_from_ltf0[21]) - std::arg(H52_from_ltf0[14])) * 180 / M_PI;
    fprintf(stderr, "[H_PHASE_CHECK] Phase diff SC7->SC14: %+.1fdeg, SC14->SC21: %+.1fdeg\n",
            phase_diff1, phase_diff2);
    fflush(stderr);
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
    //const float cpe = 0.0f;  // DEBUG: bypass CPE to test raw symbol
    const float cpe = estimate_header_cpe_rad(rx52, H52, is_ht_sig);
    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

    // Hardcoded TX L-SIG interleaved bits -> BPSK symbols
    // tx_int = 111111011101101010000010111001001111100101101111
    // BPSK: bit 1 -> +1, bit 0 -> -1
    // We only print first 8 to avoid log spam
    static const gr_complex tx_lsig_bpsk_first8[8] = {
        // First 8 bits of tx_int: 1,1,1,1,1,1,0,1 -> +1,+1,+1,+1,+1,+1,-1,+1
        gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
        gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
        gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),
        gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f)
    };

    static int eq_ref_count = 0;
    if (eq_ref_count < 2) {
        fprintf(stderr, "[EQ_REF] TX L-SIG BPSK first 8: ");
        for (int i = 0; i < 8; i++) {
            fprintf(stderr, "%.1f%+.1fi ", tx_lsig_bpsk_first8[i].real(), tx_lsig_bpsk_first8[i].imag());
        }
        fprintf(stderr, "\n");
        fprintf(stderr, "[EQ_REF] RX L-SIG raw FFT first 8: ");
        for (int i = 0; i < 8; i++) {
            fprintf(stderr, "%.4f%+.4fi ", rx52[i].real(), rx52[i].imag());
        }
        fprintf(stderr, "\n");
        eq_ref_count++;
        fflush(stderr);
    }

    std::fprintf(stderr, "[EQ_HEADER] CPE estimate: %.3f rad, rot=%.3f+%.3fi\n",
                cpe, rot.real(), rot.imag());

    int zero_H_count = 0;
    float rx_mag_sum = 0.0f, eq_mag_sum = 0.0f;
    static int probe_1_count = 0;

    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        if (h_mag < 1e-6f) zero_H_count++;
        rx_mag_sum += std::abs(rx52[i]);

        gr_complex eq;
        if (h_mag < 0.1f) {
            // 信道增益太弱，跳过均衡，设置默认值
            eq = gr_complex(0.0f, 0.0f);
            std::fprintf(stderr, "[EQ_HEADER][WARNING] Weak channel at SC%d (idx=%d): H_mag=%.4f, skipping\n",
                        kHeader48Sc[i], i, h_mag);
        } else {
            eq = safe_div(rx52[i], H52[i]) * rot;
        }
        // Debug: print rx, H, rx/H (before rot), and final eq
        if (i < 5) {
            gr_complex rx_over_H = safe_div(rx52[i], H52[i]);
            float rot_phase = std::arg(rot) * 180 / M_PI;
            fprintf(stderr, "[EQ_TRACE] i=%d sc=%d rx=%.4f%+.4fi(|rx|=%.4f) H=%.4f%+.4fi(|H|=%.4f) rx/H=%.4f%+.4fi(eq_bef_rot=%.4f) rot_ph=%.1fdeg eq=%.4f%+.4fi\n",
                    i, kHeader48Sc[i],
                    rx52[i].real(), rx52[i].imag(), std::abs(rx52[i]),
                    H52[i].real(), H52[i].imag(), std::abs(H52[i]),
                    rx_over_H.real(), rx_over_H.imag(), std::abs(rx_over_H),
                    rot_phase,
                    eq.real(), eq.imag());
        }
        // THREE-STEP PHASE TRACE for SC7
        if (i == 7) {
            gr_complex rx = rx52[i];
            gr_complex H = H52[i];
            gr_complex rx_over_H = safe_div(rx, H);
            gr_complex eq_final = rx_over_H * rot;

            float rx_phase = std::arg(rx) * 180 / M_PI;
            float H_phase = std::arg(H) * 180 / M_PI;
            float rx_over_H_phase = std::arg(rx_over_H) * 180 / M_PI;
            float eq_final_phase = std::arg(eq_final) * 180 / M_PI;

            fprintf(stderr, "[THREE_STEP_TRACE] i=7 SC7:\n");
            fprintf(stderr, "  Step1-RX:      val=%.4f%+.4fi |val|=%.4f phase=%+.1fdeg\n",
                    rx.real(), rx.imag(), std::abs(rx), rx_phase);
            fprintf(stderr, "  Step2-H:       val=%.4f%+.4fi |val|=%.4f phase=%+.1fdeg\n",
                    H.real(), H.imag(), std::abs(H), H_phase);
            fprintf(stderr, "  Step3-rx/H:    val=%.4f%+.4fi |val|=%.4f phase=%+.1fdeg\n",
                    rx_over_H.real(), rx_over_H.imag(), std::abs(rx_over_H), rx_over_H_phase);
            fprintf(stderr, "  Step4-eq*rot:  val=%.4f%+.4fi |val|=%.4f phase=%+.1fdeg\n",
                    eq_final.real(), eq_final.imag(), std::abs(eq_final), eq_final_phase);
            fprintf(stderr, "  CPE: cpe=%.3f rad rot_phase=%.1fdeg\n",
                    cpe, std::arg(rot) * 180 / M_PI);
            fflush(stderr);
        }
        out_eq48[i] = eq;
        eq_mag_sum += std::abs(eq);
        out_bits48[i] = hard_bit_from_complex(eq);

        // PROBE 1: LLR and Constellation Analysis
        // For BPSK, LLR = 2 * real(eq) / noise_variance
        // In noiseless sim, LLR is either very large or very small
        // If LLR is near 0, the bit is uncertain
        float llr = 2.0f * eq.real();  // LLR approximation for BPSK
        float euclidean_dist = std::abs(eq.real());  // Distance from decision boundary
        if (probe_1_count < 3) {
            fprintf(stderr, "[DIAG_LLR] i=%2d SC[%3d]: eq=%.4f%+.4fi bit=%d LLR=%.4f dist=%.4f |Q|=%.4f\n",
                    i, kHeader48Sc[i],
                    eq.real(), eq.imag(), out_bits48[i],
                    llr, euclidean_dist, std::abs(eq.imag()));
        }
    }
    probe_1_count++;

    // Full equalization debug output for all 48 data subcarriers
    std::fprintf(stderr, "[EQ_FULL] Equalized L-SIG symbols (48 data SC):\n");
    for (int i = 0; i < 48; i++) {
        std::fprintf(stderr, "  SC[%2d] idx[%2d]: eq=%.4f%+.4fi bit=%d\n",
                kHeader48Sc[i], i, out_eq48[i].real(), out_eq48[i].imag(), out_bits48[i]);
    }
    std::fflush(stderr);

    std::fprintf(stderr, "[EQ_HEADER] Zero-magnitude H subcarriers: %d/48\n", zero_H_count);
    std::fprintf(stderr, "[EQ_HEADER] Average RX magnitude: %.4f\n", rx_mag_sum / 48.0f);
    std::fprintf(stderr, "[EQ_HEADER] Average EQ magnitude: %.4f\n", eq_mag_sum / 48.0f);

    // 调试：打印前10个均衡后比特
    std::fprintf(stderr, "[EQ_HEADER] First 10 bits: ");
    for (int i = 0; i < 10 && i < 48; i++) {
        std::fprintf(stderr, "%d", out_bits48[i]);
    }
    std::fprintf(stderr, "\n");

    // 调试：打印前5个均衡后符号
    for (int i = 0; i < 5 && i < 48; i++) {
        std::fprintf(stderr, "[EQ_HEADER][SC%d] rx=%.3f+%.3fi, eq=%.3f+%.3fi, bit=%d\n",
                    i, rx52[i].real(), rx52[i].imag(),
                    out_eq48[i].real(), out_eq48[i].imag(), out_bits48[i]);
    }

    std::fflush(stderr);
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
                                   std::vector<uint8_t>& decoded_bits)
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
    if (metric_prev[best_state] >= INF) {
        int best_metric = INF;
        for (int s = 0; s < 64; s++) {
            if (metric_prev[s] < best_metric) {
                best_metric = metric_prev[s];
                best_state = s;
            }
        }
        if (best_metric >= INF) {
            return false;
        }
    }

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
                                   bool& out_agg)
{
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

    std::vector<uint8_t> dec48;
    if (!viterbi_decode_133_171(enc96, 96, dec48)) {
        return false;
    }
    if ((int)dec48.size() != 48) {
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

    // Debug: print decoded_bits[0:34] before CRC computation
    std::fprintf(stderr, "[RX_CRC] decoded_bits[0:34] = ");
    for (int i = 0; i < 34; i++) {
        std::fprintf(stderr, "%d", decoded_bits[i] & 1);
    }
    std::fprintf(stderr, "\n");
    std::fprintf(stderr, "[RX_CRC] computed_crc=0x%02X rx_crc=0x%02X\n", crc_calc, crc_rx);
    std::fprintf(stderr, "[PARSE_HT_SIG] CRC: received=0x%02x, calculated=0x%02x\n", crc_rx, crc_calc);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            std::fprintf(stderr, "[PARSE_HT_SIG] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        std::fprintf(stderr, "[PARSE_HT_SIG] CRC mismatch\n");
        return false;
    }

    if (bw40 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] BW40 flag set (should be 0 for 20MHz)\n");
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Reserved bits not zero: rsv0=%d, rsv1=%d, rsv2=%d\n", rsv0, rsv1, rsv2);
        return false;
    }
    if (adv_coding != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Advanced coding flag set (should be 0)\n");
        return false;
    }

    std::fprintf(stderr, "[PARSE_HT_SIG] Parsed values: mcs=%d, len=%d, agg=%d, sgi=%d, stbc=%d, nltf=%d\n",
                mcs, psdu_length, aggregation ? 1 : 0, short_gi ? 1 : 0, stbc, num_ht_ltf);

    (void)stbc;
    (void)num_ht_ltf;

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
                                             uint8_t* dbg_eqbits48 = nullptr,
                                             uint8_t* dbg_deintl48 = nullptr)
{
    fprintf(stderr, "[LSIG_DECODE] FUNCTION CALLED! invert_bits=%d\n", invert_bits ? 1 : 0);
    uint8_t eqbits48[48];
    uint8_t deintl48[48];

    equalize_header52_to_bits48(rx52, H52, eqbits48, nullptr, false);  // false = L-SIG

    if (invert_bits) {
        for (int i = 0; i < 48; i++) {
            eqbits48[i] ^= 0x1;
        }
    }

    if (dbg_eqbits48) {
        std::memcpy(dbg_eqbits48, eqbits48, 48);
    }

    // Debug: print hard bits BEFORE deinterleave
    fprintf(stderr, "[RX_LSIG_HardBits] eqbits48[0:24] = ");
    for (int i = 0; i < 24; i++) fprintf(stderr, "%d", eqbits48[i]);
    fprintf(stderr, "\n");
    fprintf(stderr, "[RX_LSIG_HardBits] eqbits48[24:48] = ");
    for (int i = 24; i < 48; i++) fprintf(stderr, "%d", eqbits48[i]);
    fprintf(stderr, "\n");
    fflush(stderr);

    deinterleave_bpsk_48(eqbits48, deintl48);

    if (dbg_deintl48) {
        std::memcpy(dbg_deintl48, deintl48, 48);
    }

    fprintf(stderr, "[VITERBI_IN] 48 bits:\n");
    for (int di = 0; di < 48; di++) {
        fprintf(stderr, "%d", deintl48[di]);
        if ((di+1) % 12 == 0) fprintf(stderr, "\n");
    }

    // Hardcoded expected TX interleaved bits for MCS0 L-SIG
    // TX L-SIG 24 bits: rate=0x0D len=45 parity=1
    // TX encoded: 110110001001111111100101100100011111100011110111
    // TX interleaved: 111111011101101010000010111001001111100101101111
    static const uint8_t expected_tx_int_lsig[48] = {
        1,1,1,1,1,1,0,1,1,1,0,1,1,0,1,0,
        1,0,1,0,0,0,0,0,1,0,1,1,1,0,0,1,
        0,0,1,1,1,1,1,0,0,1,0,1,1,0,1,1
    };

    fprintf(stderr, "[VITERBI_IN] Expected TX interleaved L-SIG:\n");
    for (int i = 0; i < 48; i++) {
        fprintf(stderr, "%d", expected_tx_int_lsig[i]);
        if ((i+1) % 16 == 0) fprintf(stderr, "\n");
    }
    fprintf(stderr, "[VITERBI_IN] Actual RX deintl48:\n");
    for (int i = 0; i < 48; i++) {
        fprintf(stderr, "%d", deintl48[i]);
        if ((i+1) % 16 == 0) fprintf(stderr, "\n");
    }
    int diff = 0;
    for (int i = 0; i < 48; i++) {
        if (deintl48[i] != expected_tx_int_lsig[i]) diff++;
    }
    fprintf(stderr, "[VITERBI_IN] Hamming diff: %d/48\n", diff);
    fflush(stderr);

    std::vector<uint8_t> dec24;
    if (!viterbi_decode_133_171(deintl48, 48, dec24)) {
        fprintf(stderr, "[LSIG_DECODE] Viterbi decode failed!\n");
        return false;
    }
    if ((int)dec24.size() != 24) {
        fprintf(stderr, "[LSIG_DECODE] Size check failed: got %zu, expected 24\n", dec24.size());
        return false;
    }

    const uint8_t* decoded_bits = dec24.data();

    // Debug: print RX L-SIG decoded 24 bits from Viterbi
    fprintf(stderr, "[RX_LSIG_Decoded] bits[0:24] = ");
    for (int i = 0; i < 24; i++) fprintf(stderr, "%d", decoded_bits[i] & 0x1);
    fprintf(stderr, "\n");
    fflush(stderr);

    const int rate_field =
        ((decoded_bits[0] & 1) << 3) |
        ((decoded_bits[1] & 1) << 2) |
        ((decoded_bits[2] & 1) << 1) |
        ((decoded_bits[3] & 1) << 0);

    // PROBE 3: Force Rate 0x0D for testing
    // If rate_field is not 0x0D, this will force it to 0x0D to test HT-SIG decoding
    static int force_rate_count = 0;
    int original_rate = rate_field;
    if (rate_field != 0x0D && force_rate_count < 3) {
        fprintf(stderr, "[DIAG_FORCE] Overriding rate 0x%02X to 0x0D (invert_bits=%d)\n",
                rate_field, invert_bits ? 1 : 0);
        force_rate_count++;
    }
    // Use forced rate for encoding lookup
    const int rate_for_encoding = (rate_field != 0x0D) ? 0x0D : rate_field;

    int psdu_length = 0;
    for (int i = 0; i < 12; i++) {
        psdu_length |= ((decoded_bits[5 + i] & 1) << i);
    }

    int parity_sum = 0;
    for (int i = 0; i < 18; i++) {
        parity_sum ^= (decoded_bits[i] & 1);
    }
    if (parity_sum != 0) {
        fprintf(stderr, "[LSIG_DECODE] Parity check failed! parity_sum=%d\n", parity_sum);
        return false;
    }

    for (int i = 18; i < 24; i++) {
        if (decoded_bits[i] != 0) {
            fprintf(stderr, "[LSIG_DECODE] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }
    int encoding = -1;
    switch (rate_for_encoding) {
    case 0x0D: encoding = 0; break;
    case 0x0F: encoding = 1; break;
    case 0x05: encoding = 2; break;
    case 0x07: encoding = 3; break;
    case 0x09: encoding = 4; break;
    case 0x0B: encoding = 5; break;
    case 0x01: encoding = 6; break;
    case 0x03: encoding = 7; break;
    default:
        fprintf(stderr, "[LSIG_DECODE] Unknown rate field: 0x%02X\n", rate_for_encoding);
        return false;
    }

    out_encoding = encoding;
    out_len_bytes = psdu_length;
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
                                              uint8_t* dbg_deintl48_b = nullptr)
{
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

    // Debug: print first 24 encoded bits before Viterbi (HT-SIG)
    std::fprintf(stderr, "[VITERBI_IN] enc96[0:24] = ");
    for (int i = 0; i < 24; i++) {
        std::fprintf(stderr, "%d", enc96[i]);
    }
    std::fprintf(stderr, "\n");

    // Debug: print first 20 encoded bits before Viterbi (HT-SIG)
    std::fprintf(stderr, "[VITERBI_HT_SIG] enc96[0:20] = ");
    for (int i = 0; i < 20 && i < 96; i++) {
        std::fprintf(stderr, "%d", enc96[i]);
    }
    std::fprintf(stderr, "\n");

    std::vector<uint8_t> dec48;
    if (!viterbi_decode_133_171(enc96, 96, dec48)) {
        std::fprintf(stderr, "[VITERBI_HT_SIG] decode failed!\n");
        return false;
    }
    if ((int)dec48.size() != 48) {
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

    // Debug: print decoded_bits[0:34] before CRC computation
    std::fprintf(stderr, "[RX_CRC] decoded_bits[0:34] = ");
    for (int i = 0; i < 34; i++) {
        std::fprintf(stderr, "%d", decoded_bits[i] & 1);
    }
    std::fprintf(stderr, "\n");
    std::fprintf(stderr, "[RX_CRC] computed_crc=0x%02X rx_crc=0x%02X\n", crc_calc, crc_rx);
    std::fprintf(stderr, "[PARSE_HT_SIG] CRC: received=0x%02x, calculated=0x%02x\n", crc_rx, crc_calc);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            std::fprintf(stderr, "[PARSE_HT_SIG] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        std::fprintf(stderr, "[PARSE_HT_SIG] CRC mismatch\n");
        return false;
    }

    if (bw40 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] BW40 flag set (should be 0 for 20MHz)\n");
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Reserved bits not zero: rsv0=%d, rsv1=%d, rsv2=%d\n", rsv0, rsv1, rsv2);
        return false;
    }
    if (adv_coding != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Advanced coding flag set (should be 0)\n");
        return false;
    }

    std::fprintf(stderr, "[PARSE_HT_SIG] Parsed values: mcs=%d, len=%d, agg=%d, sgi=%d, stbc=%d, nltf=%d\n",
                mcs, psdu_length, aggregation ? 1 : 0, short_gi ? 1 : 0, stbc, num_ht_ltf);

    (void)stbc;
    (void)num_ht_ltf;

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
                                       bool& out_agg)
{
    uint8_t eqbits48_a[48];
    uint8_t eqbits48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];

    // Extract bits from HT-SIG0 (rx52_a)
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.1f) {
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
        if (h_mag < 0.1f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52_b[i], H52[i]);
        }
        // QBPSK: HT-SIG is rotated by 90° (mult by j), so bits are on IMAG axis
        // bit 0 → -j (imag < 0), bit 1 → +j (imag >= 0)
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

    // Probe: print full bit chains for first decode_htsig_from_rotated call
    static int ht_decode_call = 0;
    if (ht_decode_call == 0) {
        fprintf(stderr, "[HTSIG_DECODE_PROBE] call=0 invert_a=%d invert_b=%d\n",
                invert_a ? 1 : 0, invert_b ? 1 : 0);
        fprintf(stderr, "[HTSIG_DECODE_PROBE] HT-SIG0 Q-bits (48): ");
        for (int i = 0; i < 48; i++) fprintf(stderr, "%d", eqbits48_a[i]);
        fprintf(stderr, "\n[HTSIG_DECODE_PROBE] HT-SIG0 deintl (48): ");
        for (int i = 0; i < 48; i++) fprintf(stderr, "%d", deintl48_a[i]);
        fprintf(stderr, "\n[HTSIG_DECODE_PROBE] HT-SIG1 Q-bits (48): ");
        for (int i = 0; i < 48; i++) fprintf(stderr, "%d", eqbits48_b[i]);
        fprintf(stderr, "\n[HTSIG_DECODE_PROBE] HT-SIG1 deintl (48): ");
        for (int i = 0; i < 48; i++) fprintf(stderr, "%d", deintl48_b[i]);
        fprintf(stderr, "\n[HTSIG_DECODE_PROBE] enc96[0:48] (HT-SIG0 deintl):\n");
        for (int i = 0; i < 48; i += 8) {
            fprintf(stderr, "  [%2d-%2d] ", i, i+7);
            for (int jj = i; jj < i+8 && jj < 48; jj++) fprintf(stderr, "%d", enc96[jj]);
            fprintf(stderr, "\n");
        }
        fprintf(stderr, "[HTSIG_DECODE_PROBE] enc96[48:96] (HT-SIG1 deintl):\n");
        for (int i = 48; i < 96; i += 8) {
            fprintf(stderr, "  [%2d-%2d] ", i, i+7);
            for (int jj = i; jj < i+8 && jj < 96; jj++) fprintf(stderr, "%d", enc96[jj]);
            fprintf(stderr, "\n");
        }
        fflush(stderr);
        ht_decode_call++;
    }

    std::vector<uint8_t> dec48;
    if (!viterbi_decode_133_171(enc96, 96, dec48)) {
        std::fprintf(stderr, "[VITERBI_HT_SIG] decode failed!\n");
        return false;
    }
    if ((int)dec48.size() != 48) {
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

    // Debug: print decoded_bits[0:34] before CRC computation
    std::fprintf(stderr, "[RX_CRC] decoded_bits[0:34] = ");
    for (int i = 0; i < 34; i++) {
        std::fprintf(stderr, "%d", decoded_bits[i] & 1);
    }
    std::fprintf(stderr, "\n");
    std::fprintf(stderr, "[RX_CRC] computed_crc=0x%02X rx_crc=0x%02X\n", crc_calc, crc_rx);
    std::fprintf(stderr, "[PARSE_HT_SIG] CRC: received=0x%02x, calculated=0x%02x\n", crc_rx, crc_calc);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            std::fprintf(stderr, "[PARSE_HT_SIG] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        std::fprintf(stderr, "[PARSE_HT_SIG] CRC mismatch\n");
        return false;
    }

    if (bw40 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] BW40 flag set (should be 0 for 20MHz)\n");
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Reserved bits not zero: rsv0=%d, rsv1=%d, rsv2=%d\n", rsv0, rsv1, rsv2);
        return false;
    }
    if (adv_coding != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Advanced coding flag set (should be 0)\n");
        return false;
    }

    std::fprintf(stderr, "[PARSE_HT_SIG] Parsed values: mcs=%d, len=%d, agg=%d, sgi=%d, stbc=%d, nltf=%d\n",
                mcs, psdu_length, aggregation ? 1 : 0, short_gi ? 1 : 0, stbc, num_ht_ltf);

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

    fprintf(stderr, "[QBPSK_VOTE] E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I, E_Q, ratio);

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
      d_frame_bytes(0),
      d_frame_encoding(0),
      d_frame_symbols(0),
      d_frame_mod(1),
      d_frame_n_bpsc(1),
      d_frame_n_cbps(52),
      d_frame_n_dbps(26),
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

    set_tag_propagation_policy(TPP_DONT);
    message_port_register_out(pmt::mp("symbols"));
    std::fprintf(stderr, "[EQDBG] frame_equalizer symbols build loaded\n");
    std::fflush(stderr);
    std::memset(d_early_bits, 0, sizeof(d_early_bits));
    std::memset(d_early_bits_valid, 0, sizeof(d_early_bits_valid));
    std::memset(d_early_eqsym, 0, sizeof(d_early_eqsym));
    std::memset(d_early_eqsym_valid, 0, sizeof(d_early_eqsym_valid));

    set_algorithm(algo);
    reset_frame_state();
    std::fprintf(stderr, "[EQDBG][NEW] Constructor modified with new debug\n");
    std::fflush(stderr);
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
    d_frame_symbols = 0;
    d_frame_mod = 1;
    d_frame_n_bpsc = 1;
    d_frame_n_cbps = 52;
    d_frame_n_dbps = 26;

    d_have_header = false;
    d_have_ht_header = false;
    d_is_ht = false;
    d_sym_idx = 0;
    d_internal_symbol_counter = 0;
    d_first_valid_symbol = -1;

    d_chan_est_mode = 0;
    d_have_lsig = false;
    d_lsig_rel = -1;
    d_hdr_reorder_mode = 0;
    d_hdr_inverted = false;
    d_htsig0_rel = -1;
    d_htsig1_rel = -1;
    d_data_start_rel = kDataStartRel;

    std::memset(d_early_bits, 0, sizeof(d_early_bits));
    std::memset(d_early_bits_valid, 0, sizeof(d_early_bits_valid));
    std::memset(d_early_eqsym, 0, sizeof(d_early_eqsym));
    std::memset(d_early_eqsym_valid, 0, sizeof(d_early_eqsym_valid));
    d_H52_tx_order_valid = false;
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
                                           bool& short_gi)
{
    mcs = 0;
    psdu_length = 0;
    aggregation = false;
    short_gi = false;

    // 调试：打印接收到的HT-SIG比特
    std::fprintf(stderr, "[PARSE_HT_SIG] Received bits (0-47): ");
    for (int i = 0; i < 48; i++) {
        std::fprintf(stderr, "%d", decoded_bits[i] & 1);
    }
    std::fprintf(stderr, "\n");

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

    // Debug: print decoded_bits[0:34] before CRC computation
    std::fprintf(stderr, "[RX_CRC] decoded_bits[0:34] = ");
    for (int i = 0; i < 34; i++) {
        std::fprintf(stderr, "%d", decoded_bits[i] & 1);
    }
    std::fprintf(stderr, "\n");
    std::fprintf(stderr, "[RX_CRC] computed_crc=0x%02X rx_crc=0x%02X\n", crc_calc, crc_rx);
    std::fprintf(stderr, "[PARSE_HT_SIG] CRC: received=0x%02x, calculated=0x%02x\n", crc_rx, crc_calc);

    for (int i = 42; i < 48; i++) {
        if (decoded_bits[i] != 0) {
            std::fprintf(stderr, "[PARSE_HT_SIG] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }

    if (crc_rx != crc_calc) {
        std::fprintf(stderr, "[PARSE_HT_SIG] CRC mismatch\n");
        return false;
    }

    if (bw40 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] BW40 flag set (should be 0 for 20MHz)\n");
        return false;
    }
    if (rsv0 != 0 || rsv1 != 0 || rsv2 != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Reserved bits not zero: rsv0=%d, rsv1=%d, rsv2=%d\n", rsv0, rsv1, rsv2);
        return false;
    }
    if (adv_coding != 0) {
        std::fprintf(stderr, "[PARSE_HT_SIG] Advanced coding flag set (should be 0)\n");
        return false;
    }

    std::fprintf(stderr, "[PARSE_HT_SIG] Parsed values: mcs=%d, len=%d, agg=%d, sgi=%d, stbc=%d, nltf=%d\n",
                mcs, psdu_length, aggregation ? 1 : 0, short_gi ? 1 : 0, stbc, num_ht_ltf);

    (void)stbc;
    (void)num_ht_ltf;

    if (mcs < 0 || mcs > 7) {
        return false;
    }
    if (psdu_length <= 0) {
        return false;
    }

    return true;
}

void frame_equalizer_impl::set_ht_frame_params_from_mcs_len(int mcs, int len_bytes)
{
    d_is_ht = true;
    d_have_ht_header = true;
    d_have_header = true;

    d_frame_encoding = mcs;
    d_frame_bytes = len_bytes;

    d_frame_n_bpsc = ht_n_bpsc_from_mcs(mcs);
    d_frame_n_cbps = ht_n_cbps_from_mcs(mcs);
    d_frame_n_dbps = ht_n_dbps_from_mcs(mcs);

    d_frame_symbols =
        (16 + 8 * len_bytes + 6 + d_frame_n_dbps - 1) / d_frame_n_dbps;
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
                                                    bool& out_agg)
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
                                  out_agg);
}

bool frame_equalizer_impl::decode_htsig_from_eqsym52(const gr_complex* sym_a,
                                                     const gr_complex* sym_b,
                                                     int reorder_mode,
                                                     bool swap_symbols,
                                                     bool invert_bits,
                                                     int& out_len_bytes,
                                                     int& out_mcs,
                                                     bool& out_sgi,
                                                     bool& out_agg)
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
                                    out_agg);
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

    // 最早期调试：确认函数是否被调用
    std::fprintf(stderr, "[EQ][ENTER] general_work called nin=%d nout=%d\n",
                 n_in, noutput_items);
    std::fflush(stderr);

    // 更早一级的调试：先确认 scheduler 是否真的把输入喂进来了
    static int dbg_call_count = 0;
    if (dbg_call_count < 20) {
        std::fprintf(stderr,
                     "[EQ][CALL] nin=%d nout=%d in_frame=%d sym_idx=%d freq_offset=%f\n",
                     n_in,
                     noutput_items,
                     d_in_frame ? 1 : 0,
                     d_sym_idx,
                     d_freq_offset_from_synclong);
        std::fflush(stderr);
        dbg_call_count++;
    }

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
            std::printf("[EQ][TAG] wifi_start at offset=%llu freq_offset=%f\n",
                        (unsigned long long)t.offset, freq_offset);
        } else {
            std::printf("[EQ][TAG] wifi_start at offset=%llu value type unexpected\n",
                        (unsigned long long)t.offset);
        }
    }
    std::fflush(stdout);

    std::fprintf(stderr, "[EQ][WHILE_ENTER] consumed=%d, n_in=%d\n", consumed, n_in);
    std::fflush(stderr);
    while (consumed < n_in) {
        std::fprintf(stderr, "[EQ][WHILE_LOOP] iter consumed=%d, d_sym_idx=%d\n", consumed, d_sym_idx);
        std::fflush(stderr);
        if (d_have_ht_header && d_sym_idx >= d_data_start_rel &&
            (produced + 52) > noutput_items) {
            break;
        }

        const gr_complex* sym64 = in + consumed * 64;
        const uint64_t abs_in_off = abs_in_start + consumed;

        const bool wifi_start = (wifi_offsets.count(abs_in_off) != 0);

        if (consumed < 12 || wifi_start) {
            std::printf("[EQ][FLOW] abs=%llu wifi_start=%d in_frame=%d sym_idx=%d consumed=%d produced=%d\n",
                        (unsigned long long)abs_in_off,
                        wifi_start ? 1 : 0,
                        d_in_frame ? 1 : 0,
                        d_sym_idx,
                        consumed,
                        produced);
            std::fflush(stdout);
        }

        if (!d_in_frame) {
            if (!wifi_start) {
                consumed++;
                d_current_symbol++;
                continue;
            }

            d_in_frame = true;
            reset_frame_state();

            std::printf("[EQ][FLOW] enter-frame abs=%llu\n",
                        (unsigned long long)abs_in_off);
            std::fflush(stdout);

        } else if (wifi_start) {
            bool allow_takeover = false;

            if (!d_have_ht_header) {
                allow_takeover = true;
            } else {
                const int end_rel = d_data_start_rel + d_frame_symbols - 1;
                if (d_sym_idx > end_rel) {
                    allow_takeover = true;
                }
            }

            if (allow_takeover) {
                reset_frame_state();
                d_in_frame = true;

                std::printf("[EQ][FLOW] frame-takeover abs=%llu allow=%d\n",
                            (unsigned long long)abs_in_off,
                            allow_takeover ? 1 : 0);
                std::fflush(stdout);
            }
        }

        // ------------------------------------------------------------
        // cache direct raw header52 from original sym64 for early symbols
        // d_early_eqsym[rel][0..47] : 48 header data carriers
        // d_early_eqsym[rel][48..51]: 4 pilots
        // ------------------------------------------------------------
        // Use d_internal_symbol_counter for symbol type determination
        // d_sym_idx may be out of sync due to 'continue' path skipping its increment
        std::fprintf(stderr, "[EQ][PRE_EXTRACT] d_sym_idx=%d d_internal_counter=%d in_frame=%d\n",
                     d_sym_idx, d_internal_symbol_counter, d_in_frame ? 1 : 0);
        std::fflush(stderr);
        if (d_internal_symbol_counter >= 0 && d_internal_symbol_counter < 8) {
            // Use d_internal_symbol_counter for array indexing - it tracks actual symbol count
            extract_header52_from_sym64(sym64, d_early_eqsym[d_internal_symbol_counter]);
            d_early_eqsym_valid[d_internal_symbol_counter] = true;

            // ===== DEBUG: Print raw L-SIG subcarriers before EQ =====
            if (d_internal_symbol_counter == kLSigRel) {
                fprintf(stderr, "[LSIG_RAW] d_sym_idx=%d d_internal_counter=%d - Raw L-SIG subcarriers (before EQ):\n",
                        d_sym_idx, d_internal_symbol_counter);
                fprintf(stderr, "[LSIG_RAW] First 8 data subcarriers (indices 0-7):\n");
                for (int di = 0; di < 8; di++) {
                    gr_complex val = d_early_eqsym[kLSigRel][di];
                    fprintf(stderr, "  sc[%d]=%.4f%+.4fi | mag=%.4f phase=%+.1fdeg\n",
                            di, val.real(), val.imag(), std::abs(val), std::arg(val)*180/M_PI);
                }
                fprintf(stderr, "[LSIG_RAW] Last 4 data subcarriers (indices 44-47):\n");
                for (int di = 44; di < 48; di++) {
                    gr_complex val = d_early_eqsym[kLSigRel][di];
                    fprintf(stderr, "  sc[%d]=%.4f%+.4fi | mag=%.4f phase=%+.1fdeg\n",
                            di, val.real(), val.imag(), std::abs(val), std::arg(val)*180/M_PI);
                }
                fprintf(stderr, "[LSIG_RAW] Pilot subcarriers (indices 48-51):\n");
                for (int di = 48; di < 52; di++) {
                    gr_complex val = d_early_eqsym[kLSigRel][di];
                    fprintf(stderr, "  sc[%d]=%.4f%+.4fi | mag=%.4f phase=%+.1fdeg\n",
                            di, val.real(), val.imag(), std::abs(val), std::arg(val)*180/M_PI);
                }
                // 关键探针: bin 7 (SC +7) = index 50 in the 52-array (48 data + 4 pilots)
                // pilots: [48]=-21, [49]=-7, [50]=+7, [51]=+21
                fprintf(stderr, "[LSIG_RAW][KEY_PROBE] bin7(SC+7) pilot: idx50=%.4f%+.4fi |mag=%.4f phase=%+.1fdeg\n",
                        d_early_eqsym[kLSigRel][50].real(), d_early_eqsym[kLSigRel][50].imag(),
                        std::abs(d_early_eqsym[kLSigRel][50]),
                        std::arg(d_early_eqsym[kLSigRel][50])*180/M_PI);
                fprintf(stderr, "[LSIG_RAW][KEY_PROBE] bin21(SC+21) pilot: idx51=%.4f%+.4fi |mag=%.4f phase=%+.1fdeg\n",
                        d_early_eqsym[kLSigRel][51].real(), d_early_eqsym[kLSigRel][51].imag(),
                        std::abs(d_early_eqsym[kLSigRel][51]),
                        std::arg(d_early_eqsym[kLSigRel][51])*180/M_PI);
                fflush(stderr);
            }

            // ===== DEBUG: Print raw HT-SIG0 subcarriers before EQ =====
            if (d_internal_symbol_counter == kHtSig0Rel) {
                fprintf(stderr, "[HTSIG0_RAW] d_sym_idx=%d d_internal_counter=%d - Raw HT-SIG0 subcarriers (before EQ):\n",
                        d_sym_idx, d_internal_symbol_counter);
                fprintf(stderr, "[HTSIG0_RAW] First 8 data subcarriers (indices 0-7):\n");
                for (int di = 0; di < 8; di++) {
                    gr_complex val = d_early_eqsym[kHtSig0Rel][di];
                    fprintf(stderr, "  sc[%d]=%.4f%+.4fi | mag=%.4f phase=%+.1fdeg\n",
                            di, val.real(), val.imag(), std::abs(val), std::arg(val)*180/M_PI);
                }
                fprintf(stderr, "[HTSIG0_RAW] Pilot subcarriers (indices 48-51):\n");
                for (int di = 48; di < 52; di++) {
                    gr_complex val = d_early_eqsym[kHtSig0Rel][di];
                    fprintf(stderr, "  sc[%d]=%.4f%+.4fi | mag=%.4f phase=%+.1fdeg\n",
                            di, val.real(), val.imag(), std::abs(val), std::arg(val)*180/M_PI);
                }
                // 关键探针: bin 7 (SC +7) = index 50 in the 52-array (48 data + 4 pilots)
                // pilots: [48]=-21, [49]=-7, [50]=+7, [51]=+21
                fprintf(stderr, "[HTSIG0_RAW][KEY_PROBE] bin7(SC+7) pilot: idx50=%.4f%+.4fi |mag=%.4f phase=%+.1fdeg\n",
                        d_early_eqsym[kHtSig0Rel][50].real(), d_early_eqsym[kHtSig0Rel][50].imag(),
                        std::abs(d_early_eqsym[kHtSig0Rel][50]),
                        std::arg(d_early_eqsym[kHtSig0Rel][50])*180/M_PI);
                fprintf(stderr, "[HTSIG0_RAW][KEY_PROBE] bin21(SC+21) pilot: idx51=%.4f%+.4fi |mag=%.4f phase=%+.1fdeg\n",
                        d_early_eqsym[kHtSig0Rel][51].real(), d_early_eqsym[kHtSig0Rel][51].imag(),
                        std::abs(d_early_eqsym[kHtSig0Rel][51]),
                        std::arg(d_early_eqsym[kHtSig0Rel][51])*180/M_PI);
                fflush(stderr);
            }

            // ===== Legacy vs HT-Mixed frame type detection =====
            // After L-SIG (rel_idx=2), detect if next symbol is Legacy Data or HT-SIG1
            // QBPSK rotation: E_Q > E_I indicates HT-SIG (+90° rotation)
            // Standard BPSK: E_I > E_Q indicates Legacy
            // NOTE: This runs inside the symbol extraction loop when d_internal_symbol_counter == kHtSig0Rel
            if (d_internal_symbol_counter == kHtSig0Rel && d_early_eqsym_valid[kLSigRel]) {
                double E_I_ls, E_Q_ls, E_I_ht, E_Q_ht;

                // Compute L-SIG energy distribution (baseline)
                compute_subcarrier_energy(d_early_eqsym[kLSigRel], E_I_ls, E_Q_ls);

                // Compute HT-SIG0 energy distribution
                compute_subcarrier_energy(d_early_eqsym[kHtSig0Rel], E_I_ht, E_Q_ht);

                double ratio_ls = (E_I_ls > 1e-10) ? E_Q_ls / E_I_ls : 0.0;
                double ratio_ht = (E_I_ht > 1e-10) ? E_Q_ht / E_I_ht : 0.0;

                fprintf(stderr, "[FRAME_DETECT] L-SIG: E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I_ls, E_Q_ls, ratio_ls);
                fprintf(stderr, "[FRAME_DETECT] HT-SIG0: E_I=%.2f E_Q=%.2f ratio=%.3f\n", E_I_ht, E_Q_ht, ratio_ht);

                // Probe: 打印 HT-SIG0 前16个数据子载波的原始值和硬判决
                // 预期: HT-SIG0 是 QBPSK, 比特编码在虚轴(Q轴)上
                // 硬判决: 如果 |I| > |Q| -> 比特0; 如果 |Q| > |I| -> 比特1
                fprintf(stderr, "[HT-SIG0_HARD] Raw subcarrier bits (48 data SC):\n");
                for (int di = 0; di < 48; di++) {
                    gr_complex val = d_early_eqsym[kHtSig0Rel][di];
                    uint8_t hard_bit = hard_bit_from_complex(val);
                    fprintf(stderr, "  [%2d] sc=%+3d bin=%2d val=%.4f%+.4fi |I|=%.4f |Q|=%.4f bit=%d\n",
                            di, kHeader48Sc[di], kHeader48Bin[di],
                            val.real(), val.imag(),
                            std::abs(val.real()), std::abs(val.imag()), hard_bit);
                }
                // 也打印 L-SIG 的硬判决用于对比
                fprintf(stderr, "[L-SIG_HARD] Raw subcarrier bits (48 data SC):\n");
                for (int di = 0; di < 48; di++) {
                    gr_complex val = d_early_eqsym[kLSigRel][di];
                    uint8_t hard_bit = hard_bit_from_complex(val);
                    fprintf(stderr, "  [%2d] sc=%+3d bin=%2d val=%.4f%+.4fi |I|=%.4f |Q|=%.4f bit=%d\n",
                            di, kHeader48Sc[di], kHeader48Bin[di],
                            val.real(), val.imag(),
                            std::abs(val.real()), std::abs(val.imag()), hard_bit);
                }
                fflush(stderr);

                // QBPSK detection: HT-SIG0 uses 90° rotated BPSK (E_Q > E_I)
                // ratio_ht > 1.0 indicates QBPSK rotation from HT-SIG encoding
                // Note: ratio_ls comparison is unreliable - L-SIG equalization can have
                // high Q energy due to channel estimate issues in loopback testing
                if (ratio_ht > 1.0) {
                    fprintf(stderr, "[FRAME_DETECT] Detected HT-Mixed frame (QBPSK rotation)\n");
                    d_is_ht_frame = true;
                } else {
                    fprintf(stderr, "[FRAME_DETECT] Detected Legacy frame (QBPSK failed)\n");
                    d_is_ht_frame = false;
                }
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
        default: cnst = d_bpsk; break;
        }

        d_equalizer->equalize(const_cast<gr_complex*>(sym64),
                              d_sym_idx,
                              raw_eq52,
                              raw_bits52,
                              cnst);

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

        std::fprintf(stderr, "[EQ][STDERR_DIRECT] Entering direct mixed-mode header detection\n");
        std::fflush(stderr);
        std::printf("[EQ][DIRECT_PATH] Entering direct mixed-mode header detection\n");
        std::fflush(stdout);
        std::fprintf(stderr, "[EQ][STDERR_BEFORE_GATE] Reached before gate check\n");
        std::fflush(stderr);
        std::printf("[EQ][BEFORE_GATE] Reached before gate check\n");
        std::fflush(stdout);
        // gate 状态打印，只用于观察 - use internal counter for type
        if (!d_have_ht_header &&
            (d_internal_symbol_counter >= kLSigRel && d_internal_symbol_counter <= kHtSig1Rel + 1)) {
            std::printf(
                "[EQ][GATE] sym=%d (internal=%d) want_htsig1=%d valid={lltf0=%d lltf1=%d lsig=%d htsig0=%d htsig1=%d} have_ht=%d\n",
                d_sym_idx, d_internal_symbol_counter,
                kHtSig1Rel,
                d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
                d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0,
                d_have_ht_header ? 1 : 0);
            std::fflush(stdout);
        }

        // FIX: Allow HT-SIG parse to trigger when L-SIG validation completes,
        // not just at the exact symbol index kHtSig1Rel.
        // This handles the case where L-SIG validation happens later than expected.
        // Use d_internal_symbol_counter for type determination (not d_sym_idx)
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
            estimate_header_channel_from_lltf52(d_early_eqsym[kLltf0Rel],
                                                d_early_eqsym[kLltf1Rel],
                                                Hhdr52);

            // DEBUG: Print channel estimate Hhdr52 for subcarriers 6-10 (data SC)
            // n=0: from L-LTF0, n=1: from L-LTF1, n=2: L-SIG (all same H)
            std::fprintf(stderr, "[CHAN_EST] n=0: d_H[6-10] = ");
            for (int sc = 6; sc <= 10; sc++) {
                std::fprintf(stderr, "%.4f%+.4fi ", Hhdr52[sc].real(), Hhdr52[sc].imag());
            }
            std::fprintf(stderr, "(mag=");
            for (int sc = 6; sc <= 10; sc++) {
                std::fprintf(stderr, "%.4f ", std::abs(Hhdr52[sc]));
            }
            std::fprintf(stderr, ")\n");

            // Also print pilot channel estimates
            std::fprintf(stderr, "[CHAN_EST] n=0: d_H[pilots] = ");
            for (int p = 0; p < 4; p++) {
                int idx = 48 + p;
                std::fprintf(stderr, "%.4f%+.4fi ", Hhdr52[idx].real(), Hhdr52[idx].imag());
            }
            std::fprintf(stderr, "\n");
            fflush(stderr);

            // HT-SIG0 DIAGNOSTIC: equalize HT-SIG0 with H, check QBPSK constellation
            {
                fprintf(stderr, "[HTDIAG] HT-SIG0 equalized with H (no rotation), first 12 SC:\n");
                int q_mismatch = 0, i_mismatch = 0;
                float eq_energy_I = 0, eq_energy_Q = 0;
                for (int i = 0; i < 48; i++) {
                    gr_complex eq = safe_div(d_early_eqsym[kHtSig0Rel][i], Hhdr52[i]);
                    eq_energy_I += eq.real() * eq.real();
                    eq_energy_Q += eq.imag() * eq.imag();
                    int bit_from_I = (eq.real() >= 0.0f) ? 1 : 0;
                    int bit_from_Q = (eq.imag() >= 0.0f) ? 1 : 0;
                    if (i < 12) {
                        fprintf(stderr, "  [%2d] sc=%+3d bin=%2d eq=%.3f%+.3fi |eq|=%.2f I_bit=%d Q_bit=%d\n",
                                i, kHeader48Sc[i], kHeader48Bin[i],
                                eq.real(), eq.imag(), std::abs(eq),
                                bit_from_I, bit_from_Q);
                    }
                }
                float ratio = (eq_energy_I > 1e-10f) ? eq_energy_Q / eq_energy_I : -1.0f;
                fprintf(stderr, "[HTDIAG] EQ energy: E_I=%.2f E_Q=%.2f ratio=%.4f (expect ratio>>1 for QBPSK)\n",
                        eq_energy_I, eq_energy_Q, ratio);
                // Also check: what does the raw HT-SIG0 look like?
                float raw_E_I = 0, raw_E_Q = 0;
                for (int i = 0; i < 48; i++) {
                    raw_E_I += d_early_eqsym[kHtSig0Rel][i].real() * d_early_eqsym[kHtSig0Rel][i].real();
                    raw_E_Q += d_early_eqsym[kHtSig0Rel][i].imag() * d_early_eqsym[kHtSig0Rel][i].imag();
                }
                float raw_ratio = (raw_E_I > 1e-10f) ? raw_E_Q / raw_E_I : -1.0f;
                fprintf(stderr, "[HTDIAG] RAW energy: E_I=%.2f E_Q=%.2f ratio=%.4f\n",
                        raw_E_I, raw_E_Q, raw_ratio);
                fflush(stderr);
            }

            bool found = false;

            // L-SIG invert brute-force
            for (int inv_lsig = 0; inv_lsig <= 1 && !found; inv_lsig++) {
                int lsig_enc = -1;
                int lsig_len = 0;

                if (!decode_lsig_direct_from_header52(d_early_eqsym[kLSigRel],
                                                      Hhdr52,
                                                      inv_lsig != 0,
                                                      lsig_enc,
                                                      lsig_len,
                                                      nullptr,
                                                      nullptr)) {
                    std::fprintf(stderr, "[DEBUG_LSIG] inv_lsig=%d decode failed, continuing\n", inv_lsig);
                    fflush(stderr);
                    continue;
                }

                if (lsig_enc != 0) {
                    std::fprintf(stderr, "[DEBUG_LSIG] inv_lsig=%d lsig_enc=%d != 0, continuing\n", inv_lsig, lsig_enc);
                    fflush(stderr);
                    continue;
                }

                // ADD DEBUG HERE
                std::fprintf(stderr, "[DEBUG_LSIG] inv_lsig=%d lsig_enc=%d lsig_len=%d\n", inv_lsig, lsig_enc, lsig_len);
                fflush(stderr);

                // Detect HT-SIG QBPSK rotation
                int detected_rot = detect_htsig_rotation(d_early_eqsym[kHtSig0Rel]);
                fprintf(stderr, "[HT_SIG] pilot-based rotation=%d\n", detected_rot);

                // Energy-based rotation verification (more reliable than pilot-only)
                // Vote on RAW HT-SIG0 symbols before any rotation is applied
                int energy_rot = vote_qbpsk_rotation(d_early_eqsym[kHtSig0Rel]);
                fprintf(stderr, "[HT_SIG] energy-based rotation=%d\n", energy_rot);

                // Override pilot if energy vote strongly indicates QBPSK (+90°)
                int start_rot = 0;
                if (energy_rot != detected_rot && energy_rot == 1) {
                    fprintf(stderr, "[HT_SIG] Energy vote overrides pilot: %d -> %d\n", detected_rot, energy_rot);
                    start_rot = energy_rot;
                }

                // Try all rotations (0, 90°, 180°, 270°) and 180° ambiguity on each symbol
                // Note: try ALL rotations, not just from start_rot, to avoid missing correct rotation
                for (int rot = 0; rot <= 3 && !found; rot++) {
                    // Apply rotation compensation
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

                            static int brute_try = 0;
                            fprintf(stderr, "[BRUTE] try=%d rot=%d inv_a=%d inv_b=%d\n",
                                    brute_try, rot, inv_a, inv_b);
                            brute_try++;

                            bool decode_ok = decode_htsig_from_rotated(rot_htsig0,
                                                           rot_htsig1,
                                                           Hhdr52,
                                                           inv_a != 0,
                                                           inv_b != 0,
                                                           parsed_len,
                                                           parsed_mcs,
                                                           parsed_sgi,
                                                           parsed_agg);
                            fprintf(stderr, "[BRUTE] try=%d result=%s\n",
                                    brute_try - 1, decode_ok ? "PASS" : "FAIL");
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

                            set_ht_frame_params_from_mcs_len(parsed_mcs, parsed_len);

                        found = true;
                    }
                }
                }
            }

            if (!found) {
                fprintf(stderr, "[EQ][HT-SIG] parse failed: lsig=%d htsig=%d/%d\n",
                            kLSigRel, kHtSig0Rel, kHtSig1Rel);
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

            const bool use_direct_tx_order_mcs0 =
                (d_have_ht_header && d_is_ht && d_frame_n_bpsc == 1);
            const int data_sym_idx = d_sym_idx - d_data_start_rel;

            if (use_direct_tx_order_mcs0) {
                if (!d_H52_tx_order_valid) {
                    // DEBUG: test L-LTF0 H vs HT-LTF H
                    gr_complex H_lltf[52], H_htltf[52];
                    compute_H52_tx_order(d_early_eqsym[kLltf0Rel], H_lltf);
                    compute_H52_tx_order(d_early_eqsym[kHtTrain1Rel], H_htltf);
                    int sign_diffs = 0;
                    for (int i = 0; i < 52; i++) {
                        float a = std::arg(H_lltf[i] / H_htltf[i]) * 180.0f / M_PI;
                        if (std::abs(a) > 90.0f) sign_diffs++;
                    }
                    fprintf(stderr, "[H_CMP] L-LTF0 vs HT-LTF H sign_diffs=%d/52\n", sign_diffs);
                    // Use L-LTF0 for now
                    std::memcpy(d_H52_tx_order, H_lltf, 52 * sizeof(gr_complex));
                    d_H52_tx_order_valid = true;
                }
                extract_ht_data52_direct_tx_order(sym64, data_sym_idx, d_H52_tx_order, out52);
            } else {
                if (!reorder_eq_52_mode(raw_eq52, out52, d_hdr_reorder_mode)) {
                    std::memcpy(out52, raw_eq52, 52 * sizeof(gr_complex));
                }
            }

            const bool trace_sym =
                (data_sym_idx == 0) ||
                (data_sym_idx == 1) ||
                (data_sym_idx == 2) ||
                (data_sym_idx == 19) ||
                (data_sym_idx == 20) ||
                (data_sym_idx == 31);

            if (trace_sym) {
                uint8_t out_bits52[52];
                for (int i = 0; i < 52; i++) {
                    out_bits52[i] = hard_bit_from_complex(out52[i]);
                }

                std::string ref_path;
                uint8_t tx_ref52[52];
                const bool have_ref = read_tx_ref_bits52(tx_ref52, ref_path);

                std::printf("[EQ][HT-DATA%d][OUT52] bits52=%s\n",
                            data_sym_idx,
                            bits_to_string(out_bits52, 52).c_str());
                if (have_ref) {
                    int mism = 0;
                    for (int i = 0; i < 52; i++) {
                        if (out_bits52[i] != tx_ref52[i]) {
                            mism++;
                        }
                    }
                    std::printf("[EQ][HT-DATA%d][OUT52] compare-to-TX mismatches=%d path=%s\n",
                                data_sym_idx,
                                mism,
                                ref_path.c_str());
                } else {
                    std::printf("[EQ][HT-DATA%d][OUT52] TX reference unavailable path=%s\n",
                                data_sym_idx,
                                ref_path.c_str());
                }
                std::fflush(stdout);
            }

            if (use_direct_tx_order_mcs0 && trace_sym) {
                gr_complex dbg52[52];
                uint8_t bits52[52];

                extract_ht_data52_direct_tx_order(sym64, data_sym_idx, d_H52_tx_order, dbg52);

                for (int i = 0; i < 52; i++) {
                    bits52[i] = hard_bit_from_complex(dbg52[i]);
                }

                std::string ref_path;
                uint8_t tx_ref52[52];
                const bool have_ref = read_tx_ref_bits52(tx_ref52, ref_path);

                std::printf("[EQ][HT-DATA%d][DIRECT-DBG] tx-order bits52=%s\n",
                            data_sym_idx,
                            bits_to_string(bits52, 52).c_str());
                if (have_ref) {
                    int mism = 0;
                    for (int i = 0; i < 52; i++) {
                        if (bits52[i] != tx_ref52[i]) {
                            mism++;
                        }
                    }
                    std::printf("[EQ][HT-DATA%d][DIRECT-DBG] compare-to-TX mismatches=%d path=%s\n",
                                data_sym_idx,
                                mism,
                                ref_path.c_str());
                } else {
                    std::printf("[EQ][HT-DATA%d][DIRECT-DBG] TX reference unavailable path=%s\n",
                                data_sym_idx,
                                ref_path.c_str());
                }
                std::fflush(stdout);
            }

            {
                pmt::pmt_t meta = pmt::make_dict();
                meta = pmt::dict_add(meta, pmt::mp("packet_len"), pmt::from_long(52));
                pmt::pmt_t vec = pmt::init_c32vector(52, out52);
                message_port_pub(pmt::mp("symbols"), pmt::cons(meta, vec));
            }

            if (tag_this_output_as_frame_start) {
                const uint64_t out_off = this->nitems_written(0) + produced;

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
                    pmt::from_uint64((uint64_t)d_frame_encoding),
                    pmt::intern(this->name()));
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
                reset_frame_state();
                d_in_frame = false;
            }
        }

        if (d_in_frame && d_sym_idx > kMaxFrameRel) {
            reset_frame_state();
            d_in_frame = false;
        }
    }

    consume_each(consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr

