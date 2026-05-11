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

static void extract_ht_data52_direct_tx_order(const gr_complex* sym64,
                                              int data_sym_idx,
                                              gr_complex* out52)
{
    const float cpe = estimate_ht_data_cpe_rad_from_sym64(sym64, data_sym_idx);
    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

    for (int i = 0; i < 52; i++) {
        out52[i] = sym64[sc_to_fft_bin(kTxOrder52[i])] * rot;
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

// L-LTF TX values for 48 data subcarriers (kHeader48Sc order)
// These are BPSK ±1 values (REAL axis), which is what the TX actually transmits
// The wifi_phy_hier.py uses digital.chunks_to_symbols_bc([-1, 1]) for preamble
// H = RX / TX gives proper channel estimate
static constexpr gr_complex kLltf48TX[48] = {
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc -26 to -20
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -19 to -14
    gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc -13 to -8
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc  -6 to  -1
    gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +1 to  +6
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f),  // sc  +8 to +13
    gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +14 to +19
    gr_complex(-1.0f, 0.0f), gr_complex(-1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f), gr_complex(+1.0f, 0.0f),  // sc +20 to +26
};

// L-LTF pilot signs {-21,-7,+7,+21}
static constexpr int kLltfPilotSign[4] = {
    1, -1, 1, 1
};

// L-LTF TX complex values for pilot subcarriers {-21,-7,+7,+21}
// Computed from FFT of LEGACY_LTF time-domain sequence with 1/sqrt(52) normalization
// These are the actual frequency-domain pilot values, not just ±j
static const gr_complex kLltfPilotTX[4] = {
    gr_complex(-0.6173f, -0.1253f),  // sc -21: FFT of LEGACY_LTF
    gr_complex( 0.3401f,  0.9423f),  // sc  -7: FFT of LEGACY_LTF
    gr_complex( 0.3401f, -0.9423f),  // sc  +7: FFT of LEGACY_LTF
    gr_complex(-0.6173f,  0.1253f)   // sc +21: FFT of LEGACY_LTF
};

// SIGNAL / HT-SIG pilot values after channel equalization
static constexpr int kHeaderPilotBase[4] = {
    1, 1, 1, -1
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
static gr_complex saved_ltf0_fft[64] = {gr_complex(0,0)};
static bool ltf0_saved = false;

static void extract_header52_from_sym64(const gr_complex* sym64, gr_complex* out52)
{
    // NAKED_TEST: Save LTF0 raw FFT
    static int extract_call_count = 0;

    // Call 0 = LTF0, Call 1 = LTF1
    if (extract_call_count == 0) {
        memcpy(saved_ltf0_fft, sym64, 64 * sizeof(gr_complex));
        ltf0_saved = true;
    }

    if (extract_call_count == 1 && ltf0_saved) {
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
        ltf0_saved = false;
        fprintf(stderr, "[NAKED_TEST] End comparison\n\n");
    }

    extract_call_count++;
    // 调试：打印前几个子载波索引和值
    static int call_count = 0;
    if (call_count < 10) {
        std::fprintf(stderr, "[EXTRACT] called, first 5 subcarriers:\n");
        for (int i = 0; i < 5 && i < 48; i++) {
            int fft_bin = kHeader48Bin[i];  // EXPLICIT bin mapping!
            gr_complex val = sym64[fft_bin];
            std::fprintf(stderr, "  i=%d, sc=%d, bin=%d, val=%.3f+%.3fi\n",
                        i, kHeader48Sc[i], fft_bin,
                        val.real(), val.imag());
        }
        // NAKED_TEST: Print specific FFT bins for physical layer verification
        // These are actual bin indices, not subcarrier indices
        std::fprintf(stderr, "[NAKED_FFT] Physical FFT bins (not SC indices):\n");
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
    for (int i = 0; i < 48; i++) {
        const gr_complex lltf0 = lltf0_52[i];
        const gr_complex tx = kLltf48TX[i];

        if (std::abs(tx) > 0.001f) {
            H52[i] = (lltf0 / tx) / kFftNormalize;
        } else {
            H52[i] = lltf0 / kFftNormalize;  // fallback for null subcarriers
        }
    }
    for (int i = 0; i < 4; i++) {
        const gr_complex lltf0 = lltf0_52[48 + i];
        // FIX: Use actual TX pilot values kHeaderPilotBase (real ±1), not kLltfPilotTX (complex FFT values)
        // The TX pilots for L-SIG are {1, 1, 1, -1} (real), not the complex FFT of LTF sequence
        const gr_complex tx = gr_complex((float)kHeaderPilotBase[i], 0.0f);

        if (std::abs(tx) > 0.001f) {
            H52[48 + i] = (lltf0 / tx) / kFftNormalize;
        } else {
            H52[48 + i] = lltf0 / kFftNormalize;  // fallback
        }
    }
}

static float estimate_header_cpe_rad(const gr_complex* rx52,
                                     const gr_complex* H52)
{
    gr_complex acc(0.0f, 0.0f);

    for (int i = 0; i < 4; i++) {
        const gr_complex eqp = safe_div(rx52[48 + i], H52[48 + i]);
        // Use kHeaderPilotBase (real ±1) as expected pilot values
        // The TX pilots for L-SIG are {1, 1, 1, -1} (real), which is kHeaderPilotBase
        const gr_complex expect = gr_complex((float)kHeaderPilotBase[i], 0.0f);
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
                                               uint8_t* out_bits48)
{
    const float cpe = estimate_header_cpe_rad(rx52, H52);
    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

    std::fprintf(stderr, "[EQ_HEADER] CPE estimate: %.3f rad, rot=%.3f+%.3fi\n",
                cpe, rot.real(), rot.imag());

    int zero_H_count = 0;
    float rx_mag_sum = 0.0f, eq_mag_sum = 0.0f;

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
        out_eq48[i] = eq;
        eq_mag_sum += std::abs(eq);
        out_bits48[i] = hard_bit_from_complex(eq);
    }

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
                                        gr_complex* out_eq48 = nullptr)
{
    gr_complex tmp_eq48[48];
    equalize_header52_to_eq48_and_bits(rx52, H52, tmp_eq48, out_bits48);
    if (out_eq48) {
        std::memcpy(out_eq48, tmp_eq48, 48 * sizeof(gr_complex));
    }
}

// ============================================================
// BPSK deinterleaver / Viterbi / CRC
// ============================================================

// TX interleave:
//   out[k] = in[i], i = 3*(k mod 16) + floor(k/16)
//
// RX inverse:
//   out[i] = in[k]
static void deinterleave_bpsk_48(const uint8_t* in48, uint8_t* out48)
{
    std::memset(out48, 0, 48);

    for (int k = 0; k < 48; k++) {
        const int j = 16 * (k % 3) + k / 3;  // FIX: k/3 correctly deinterleaves i = 3*(k%16) + k/16
        out48[k] = in48[j] & 0x1;
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

    equalize_header52_to_bits48(rx52, H52, eqbits48, nullptr);

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

    fprintf(stderr, "[VITERBI_IN] 48 bits:\n");
    for (int di = 0; di < 48; di++) {
        fprintf(stderr, "%d", deintl48[di]);
        if ((di+1) % 12 == 0) fprintf(stderr, "\n");
    }

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
        fprintf(stderr, "[LSIG_DECODE] Unknown rate field: 0x%02X\n", rate_field);
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

    equalize_header52_to_bits48(rx52_a, H52, eqbits48_a, nullptr);
    equalize_header52_to_bits48(rx52_b, H52, eqbits48_b, nullptr);

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
        // bit 0 → +j (imag >= 0), bit 1 → -j (imag < 0)
        eqbits48_a[i] = (eq.imag() >= 0.0f) ? 0 : 1;
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
        eqbits48_b[i] = (eq.imag() >= 0.0f) ? 0 : 1;
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
    // Inverse (deinterleaver): k = 16*(j%3) + j/3, so j = 16*(k%3) + k/3
    for (int k = 0; k < 48; k++) {
        const int j = 16 * (k % 3) + k / 3;  // FIX: k/3 correctly deinterleaves
        deintl48_a[k] = eqbits48_a[j] & 0x1;
    }
    for (int k = 0; k < 48; k++) {
        const int j = 16 * (k % 3) + k / 3;  // FIX: k/3 correctly deinterleaves
        deintl48_b[k] = eqbits48_b[j] & 0x1;
    }

    for (int i = 0; i < 48; i++) {
        enc96[i]      = deintl48_a[i];
        enc96[48 + i] = deintl48_b[i];
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

                // If HT-SIG0's E_Q/E_I ratio is significantly higher than L-SIG, it's HT-Mixed
                // DEBUG: Force HT-Mixed for loopback testing (ratio_ht > 1.0 indicates QBPSK)
                if (ratio_ht > 1.0 && ratio_ht > ratio_ls) {
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
                    continue;
                }

                if (lsig_enc != 0) {
                    continue;
                }

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

                            if (!decode_htsig_from_rotated(rot_htsig0,
                                                           rot_htsig1,
                                                           Hhdr52,
                                                           inv_a != 0,
                                                           inv_b != 0,
                                                           parsed_len,
                                                           parsed_mcs,
                                                           parsed_sgi,
                                                           parsed_agg)) {
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
                extract_ht_data52_direct_tx_order(sym64, data_sym_idx, out52);
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

                extract_ht_data52_direct_tx_order(sym64, data_sym_idx, dbg52);

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

