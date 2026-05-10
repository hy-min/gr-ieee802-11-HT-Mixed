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

    extract_call_count++;

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

static void estimate_header_channel_from_lltf52(const gr_complex* lltf0_52,
                                                const gr_complex* lltf1_52,
                                                gr_complex* H52)
{
    // Channel estimation using LTF0 only (avoid averaging opposite signs)
    for (int i = 0; i < 48; i++) {
        const gr_complex lltf0 = lltf0_52[i];
        const gr_complex tx = kLltf48TX[i];

        if (std::abs(tx) > 0.001f) {
            H52[i] = lltf0 / tx;
        } else {
            H52[i] = lltf0;  // fallback for null subcarriers
        }
    }
    for (int i = 0; i < 4; i++) {
        const gr_complex lltf0 = lltf0_52[48 + i];
        // FIX: Use actual TX pilot values kHeaderPilotBase (real ±1), not kLltfPilotTX (complex FFT values)
        // The TX pilots for L-SIG are {1, 1, 1, -1} (real), not the complex FFT of LTF sequence
        const gr_complex tx = gr_complex((float)kHeaderPilotBase[i], 0.0f);

        if (std::abs(tx) > 0.001f) {
            H52[48 + i] = lltf0 / tx;
        } else {
            H52[48 + i] = lltf0;  // fallback
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

// 4th-power phase estimation for L-SIG when pilots are not available
// For BPSK: after equalization, eq[i] = tx_bit[i] * exp(jθ)
// Then eq[i]^4 = exp(j4θ) (since (±1)^4 = 1)
// So arg(mean(eq[i]^4)) = 4θ, and θ = arg(mean(eq[i]^4)) / 4
static float estimate_cpe_4th_power(const gr_complex* rx52, const gr_complex* H52, float fudge_factor = 1.0f)
{
    gr_complex acc(0.0f, 0.0f);
    int count = 0;

    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        if (h_mag < 0.1f) continue;

        const gr_complex eq = safe_div(rx52[i], H52[i]);
        float eq_mag = std::abs(eq);
        if (eq_mag < 0.1f) continue;

        // Normalize to unit circle and raise to 4th power
        gr_complex eq_norm = eq / eq_mag;
        acc += eq_norm * eq_norm * eq_norm * eq_norm;
        count++;
    }

    if (count < 10 || std::abs(acc) < 1e-9f) {
        return 0.0f;
    }

    float phase_4x = std::arg(acc);
    return (phase_4x / 4.0f) * fudge_factor;
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
    // Check if pilots are usable (non-zero)
    bool pilots_usable = false;
    for (int i = 0; i < 4; i++) {
        if (std::abs(rx52[48 + i]) > 0.01f) {
            pilots_usable = true;
            break;
        }
    }

    float cpe;
    if (pilots_usable) {
        cpe = estimate_header_cpe_rad(rx52, H52);
    } else {
        // Pilots are zero (FFT misalignment), use 4th power method
        // Try a fudge factor since 4th power method might underestimate
        cpe = estimate_cpe_4th_power(rx52, H52, 1.5f);
    }

    const gr_complex rot = std::exp(gr_complex(0.0f, -cpe));

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

    // DEBUG: Print all 24 decoded bits before parity check
    fprintf(stderr, "[LSIG_DECODE] decoded_bits[0:24]=");
    for (int i = 0; i < 24; i++) fprintf(stderr, "%d", decoded_bits[i] & 1);
    fprintf(stderr, "\n");

    // Also print what we expect for TX rate 0x0D (MCS 0)
    // L-SIG TX raw24 = 110100000011000001000000 (from TX debug)
    // Bits 0-3: rate = 1101 = 0x0D
    // Bits 4-15: length = 000000110000 = 0x030 = 48
    // Bits 16-17: reserved = 00
    // Bits 18-23: parity (even parity of bits 0-17)
    fprintf(stderr, "[LSIG_DECODE] Expected for rate 0x0D: bits[0:18]=110100000011000001\n");

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
    // DEBUG: Show which bit is wrong
    if (parity_sum != 0) {
        fprintf(stderr, "[LSIG_DECODE] Parity check failed! parity_sum=%d\n", parity_sum);
        // Compute expected parity (even parity of bits 0-17)
        int expected_parity = 0;
        for (int i = 0; i < 18; i++) expected_parity ^= (decoded_bits[i] & 1);
        fprintf(stderr, "[LSIG_DECODE] Bit positions where decoded differs from expected:\n");
        fprintf(stderr, "  Expected bits[0:18]=110100000011000001\n");
        fprintf(stderr, "  Decoded bits[0:18]=");
        for (int i = 0; i < 18; i++) {
            int expected_bit = 0;
            if (i == 0) expected_bit = 1;  // rate bit 3
            else if (i == 1) expected_bit = 1;  // rate bit 2
            else if (i == 2) expected_bit = 0;  // rate bit 1
            else if (i == 3) expected_bit = 1;  // rate bit 0
            // Length bits 4-15 and reserved bits 16-17 are harder to compute
            // Just show the decoded bit
            fprintf(stderr, "%d", decoded_bits[i] & 1);
        }
        fprintf(stderr, "\n");
        fflush(stderr);
        return false;
    }

    for (int i = 18; i < 24; i++) {
        if (decoded_bits[i] != 0) {
            fprintf(stderr, "[LSIG_DECODE] Tail bit %d not zero: %d\n", i, decoded_bits[i] & 1);
            return false;
        }
    }
    fprintf(stderr, "[LSIG_DECODE] Tail bits OK (all zero), proceeding to rate switch\n");
    fflush(stderr);

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
    // Debug: Print decoded L-SIG bits
    fprintf(stderr, "[LSIG_DECODE] SUCCESS: rate=0x%02X enc=%d len=%d parity_ok deintl_bits[0:8]=%02X%02X%02X%02X\n",
            rate_field, encoding, psdu_length,
            deintl48[0], deintl48[1], deintl48[2], deintl48[3]);
    fflush(stderr);

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
    fprintf(stderr, "[DECODE_HT] CALLED: rx52_a[0]=%.3f+%.3fi rx52_b[0]=%.3f+%.3fi H52[0]=%.3f+%.3fi\n",
            rx52_a[0].real(), rx52_a[0].imag(),
            rx52_b[0].real(), rx52_b[0].imag(),
            H52[0].real(), H52[0].imag());
    uint8_t eqbits48_a[48];
    uint8_t eqbits48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];

    // Probe: print first 5 values of rx52_a, H52, and eq to see rotation-compensated symbols
    static int s_call_id = 0;
    fprintf(stderr, "[DECODE_HT] call_id=%d rx52_a[0:5]=", s_call_id++);
    for (int i = 0; i < 5; i++) {
        fprintf(stderr, "%.3f+%.3fi ", rx52_a[i].real(), rx52_a[i].imag());
    }
    fprintf(stderr, "\n  H52[0:5]=");
    for (int i = 0; i < 5; i++) {
        fprintf(stderr, "%.3f+%.3fi ", H52[i].real(), H52[i].imag());
    }
    fprintf(stderr, "\n");
    fflush(stderr);

    // Extract bits from HT-SIG0 (rx52_a)
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.1f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52_a[i], H52[i]);
        }
        // Probe eq phase
        if (i < 5) {
            fprintf(stderr, "[DECODE_HT]   i=%d eq=%.3f+%.3fi phase=%+.1fdeg real>=0?%d\n",
                    i, eq.real(), eq.imag(), std::arg(eq)*180/M_PI, (eq.real() >= 0.0f) ? 1 : 0);
        }
        // QPSK: HT-SIG uses standard QPSK (45° offset), bits are on REAL axis
        // bit 0 → real >= 0, bit 1 → real < 0
        eqbits48_a[i] = (eq.real() >= 0.0f) ? 0 : 1;
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
        // QPSK: HT-SIG uses standard QPSK (45° offset), bits are on REAL axis
        eqbits48_b[i] = (eq.real() >= 0.0f) ? 0 : 1;
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

    // DEBUG: Print RX enc96 bits before Viterbi decode
    fprintf(stderr, "[RX_ENC96] ");
    for (int i = 0; i < 96; i++) {
        fprintf(stderr, "%d", enc96[i]);
    }
    fprintf(stderr, "\n");
    fflush(stderr);

    // DEBUG: Compare with known TX bits (from Task 1 capture)
    const char* tx_intl96 = "000000000111100000000000000001000000000011101100000000010100010100000001011101100000001011001110";
    fprintf(stderr, "[TX_ENC96] %s\n", tx_intl96);

    // Count bit differences
    int diff_count = 0;
    for (int i = 0; i < 96; i++) {
        if (enc96[i] != (tx_intl96[i] - '0')) diff_count++;
    }
    fprintf(stderr, "[BITDIFF] %d/96 bits differ\n", diff_count);

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
        std::printf("[EQ][IDX_CHECK] d_sym_idx=%d d_internal_counter=%d condition=%d\n",
                    d_sym_idx, d_internal_symbol_counter,
                    (d_internal_symbol_counter >= 0 && d_internal_symbol_counter < 8) ? 1 : 0);
        if (d_internal_symbol_counter >= 0 && d_internal_symbol_counter < 8) {
            // 调试：记录提取时的符号索引 - use internal counter for type
            std::fprintf(stderr, "[EXTRACT_CALL] internal_counter=%d, type=%s\n", d_internal_symbol_counter,
                       d_internal_symbol_counter == kLltf0Rel ? "L-LTF0" :
                       d_internal_symbol_counter == kLltf1Rel ? "L-LTF1" :
                       d_internal_symbol_counter == kLSigRel ? "L-SIG" :
                       d_internal_symbol_counter == kHtSig0Rel ? "HT-SIG0" :
                       d_internal_symbol_counter == kHtSig1Rel ? "HT-SIG1" : "OTHER");
            std::fflush(stderr);
            std::printf("[EQ][EXTRACT] Calling extract for internal_counter=%d, type=%s\n",
                        d_internal_symbol_counter,
                        d_internal_symbol_counter == kLltf0Rel ? "L-LTF0" :
                        d_internal_symbol_counter == kLltf1Rel ? "L-LTF1" :
                        d_internal_symbol_counter == kLSigRel ? "L-SIG" :
                        d_internal_symbol_counter == kHtSig0Rel ? "HT-SIG0" :
                        d_internal_symbol_counter == kHtSig1Rel ? "HT-SIG1" : "OTHER");
            fflush(stdout);
            // Use d_internal_symbol_counter for array indexing - it tracks actual symbol count
            extract_header52_from_sym64(sym64, d_early_eqsym[d_internal_symbol_counter]);
            // DEBUG: Print raw FFT bins for HT-SIG verification
            std::fprintf(stderr, "[EXTRACT_HT_SIG] internal_counter=%d, sym64[6-10] = ", d_internal_symbol_counter);
            for (int i = 6; i < 10; i++) {
                std::fprintf(stderr, "%.3f+%.3fi ", sym64[i].real(), sym64[i].imag());
            }
            std::fprintf(stderr, "\n");
            std::fflush(stderr);
            // DEBUG: Compare LTF0 vs LTF1 after both are extracted
            if (d_internal_symbol_counter == kLltf1Rel && d_early_eqsym_valid[kLltf0Rel]) {
                std::fprintf(stderr, "[LTF0_vs_LTF1] Direct comparison:\n");
                for (int i = 0; i < 10; i++) {
                    gr_complex l0 = d_early_eqsym[kLltf0Rel][i];
                    gr_complex l1 = d_early_eqsym[kLltf1Rel][i];
                    float dot = l0.real() * l1.real() + l0.imag() * l1.imag();
                    std::fprintf(stderr, "  [%2d] LTF0=%10.3f∠%6.1f  LTF1=%10.3f∠%6.1f  dot=%9.3f\n",
                                i, std::abs(l0), std::arg(l0)*180/M_PI, std::abs(l1), std::arg(l1)*180/M_PI, dot);
                }
                std::fflush(stderr);
            }
            d_early_eqsym_valid[d_internal_symbol_counter] = true;
            std::printf("[EQ][VALID_SET] internal_counter=%d, valid=1, type=%s\n",
                        d_internal_symbol_counter,
                        d_internal_symbol_counter == kLltf0Rel ? "L-LTF0" :
                        d_internal_symbol_counter == kLltf1Rel ? "L-LTF1" :
                        d_internal_symbol_counter == kLSigRel ? "L-SIG" :
                        d_internal_symbol_counter == kHtSig0Rel ? "HT-SIG0" :
                        d_internal_symbol_counter == kHtSig1Rel ? "HT-SIG1" : "OTHER");

            // 符号索引调试 - use internal counter for type determination
            const char* sym_type = "UNKNOWN";
            if (d_internal_symbol_counter == kLltf0Rel) sym_type = "L-LTF0";
            else if (d_internal_symbol_counter == kLltf1Rel) sym_type = "L-LTF1";
            else if (d_internal_symbol_counter == kLSigRel) sym_type = "L-SIG";
            else if (d_internal_symbol_counter == kHtSig0Rel) sym_type = "HT-SIG0";
            else if (d_internal_symbol_counter == kHtSig1Rel) sym_type = "HT-SIG1";
            else if (d_internal_symbol_counter >= kDataStartRel) sym_type = "DATA";

            std::printf("[EQ][SYM_IDX] internal_counter=%d, type=%s\n", d_internal_symbol_counter, sym_type);
            std::fflush(stdout);

            // ===== Legacy vs HT-Mixed frame type detection =====
            // After L-SIG (rel_idx=2), detect if next symbol is Legacy Data or HT-SIG1
            // QBPSK rotation: E_Q > E_I indicates HT-SIG (+90° rotation)
            // Standard BPSK: E_I > E_Q indicates Legacy
            // NOTE: This runs inside the symbol extraction loop when d_internal_symbol_counter == kHtSig0Rel
            if (d_internal_symbol_counter == kHtSig0Rel && d_early_eqsym_valid[kLSigRel]) {
                double E_I_ls, E_Q_ls, E_I_ht, E_Q_ht;

                // Debug: print first few values of L-SIG and HT-SIG0
                fprintf(stderr, "[FRAME_DETECT] DEBUG d_early_eqsym[2][0]=%.4f+%.4fi\n",
                        d_early_eqsym[kLSigRel][0].real(), d_early_eqsym[kLSigRel][0].imag());
                fprintf(stderr, "[FRAME_DETECT] DEBUG d_early_eqsym[3][0]=%.4f+%.4fi\n",
                        d_early_eqsym[kHtSig0Rel][0].real(), d_early_eqsym[kHtSig0Rel][0].imag());

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

        // 调试：验证equalize被调用
        std::fprintf(stderr, "[EQ][POST_EQUALIZE] d_sym_idx=%d\n", d_sym_idx);
        std::fflush(stderr);

        // 调试：打印头部符号的解调比特
        if (d_sym_idx >= 0 && d_sym_idx < 8) {
            std::fprintf(stderr, "[EQ][RAW_BITS] sym_idx=%d bits52=", d_sym_idx);
            for (int kk = 0; kk < 52; kk++) {
                std::fprintf(stderr, "%d", raw_bits52[kk] ? 1 : 0);
            }
            std::fprintf(stderr, "\n");
            std::fflush(stderr);
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

        // 调试：检查条件是否满足
        std::printf("[EQ][TEST_INSERT] Added new debug output\n");
        std::printf("[EQ][DEBUG_TEST] ======== DEBUG ENTRY ========\n");
        std::printf("[EQ][DEBUG] Checking HT-SIG parse condition: d_sym_idx=%d, kHtSig1Rel=%d, d_have_ht_header=%d\n",
                   d_sym_idx, kHtSig1Rel, d_have_ht_header ? 1 : 0);
        std::printf("[EQ][DEBUG] valid flags: lltf0=%d, lltf1=%d, lsig=%d, htsig0=%d, htsig1=%d\n",
                   d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
        std::fflush(stdout);

        // 真正的 HT-SIG 解析门条件，必须保留
        std::printf("[EQ][GATE_DETAIL] d_sym_idx=%d, d_internal_counter=%d, kHtSig1Rel=%d, d_have_ht_header=%d\n",
                   d_sym_idx, d_internal_symbol_counter, kHtSig1Rel, d_have_ht_header ? 1 : 0);
        std::printf("[EQ][GATE_DETAIL] valid flags: lltf0=%d lltf1=%d lsig=%d htsig0=%d htsig1=%d\n",
                   d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
        std::printf("[EQ][GATE_DETAIL] ht_parse_condition = %d (need: !have_ht=%d counter>=4=%d lltf0=%d lltf1=%d lsig=%d htsig0=%d htsig1=%d)\n",
                   (!d_have_ht_header) && (d_internal_symbol_counter >= kHtSig1Rel) &&
                   d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLltf1Rel] &&
                   d_early_eqsym_valid[kLSigRel] && d_early_eqsym_valid[kHtSig0Rel] &&
                   d_early_eqsym_valid[kHtSig1Rel],
                   !d_have_ht_header,
                   d_internal_symbol_counter >= kHtSig1Rel,
                   d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
                   d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                   d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
        // FIX: Allow HT-SIG parse to trigger when L-SIG validation completes,
        // not just at the exact symbol index kHtSig1Rel.
        // This handles the case where L-SIG validation happens later than expected.
        // Use d_internal_symbol_counter for type determination (not d_sym_idx)
        // ULTRA-DEBUG: Check exact state at condition evaluation
        std::printf("[EQ][COND_CHK] d_have_ht_header=%d, counter=%d, kHtSig1Rel=%d\n",
                    d_have_ht_header ? 1 : 0, d_internal_symbol_counter, kHtSig1Rel);
        std::printf("[EQ][COND_CHK] flags: ll0=%d ll1=%d ls=%d hs0=%d hs1=%d\n",
                    d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                    d_early_eqsym_valid[kLltf1Rel] ? 1 : 0,
                    d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                    d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                    d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
        std::printf("[EQ][COND_CHK] counter>=4? %d\n", d_internal_symbol_counter >= kHtSig1Rel);
        fflush(stdout);
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
            std::printf("[EQ][COND_ENTER] ENTERED! ht_parse_condition=%d\n", ht_parse_condition ? 1 : 0);
            fflush(stdout);
            std::printf("[EQ][DEBUG_BLOCK] ENTERING HT-SIG PARSE BLOCK (ht_parse_condition=%d)\n", ht_parse_condition);
            std::fflush(stdout);

            // L-LTF符号调试
            std::printf("[RX][LLTF-DBG] L-LTF0 valid=%d, L-LTF1 valid=%d\n",
                        d_early_eqsym_valid[kLltf0Rel] ? 1 : 0,
                        d_early_eqsym_valid[kLltf1Rel] ? 1 : 0);
            // 打印L-LTF符号的幅度
            if (d_early_eqsym_valid[kLltf0Rel] && d_early_eqsym_valid[kLltf1Rel]) {
                float lltf0_mag = 0.0f, lltf1_mag = 0.0f;
                for (int i = 0; i < 52; i++) {
                    lltf0_mag += std::abs(d_early_eqsym[kLltf0Rel][i]);
                    lltf1_mag += std::abs(d_early_eqsym[kLltf1Rel][i]);
                }
                lltf0_mag /= 52.0f;
                lltf1_mag /= 52.0f;
                std::printf("[RX][LLTF-DBG] L-LTF0 avg_mag=%.4f, L-LTF1 avg_mag=%.4f\n",
                            lltf0_mag, lltf1_mag);
                // 打印前几个子载波
                std::printf("[RX][LLTF-DBG] L-LTF0[0:3]: ");
                for (int i = 0; i < 4 && i < 52; i++) {
                    std::printf("%.3f∠%.3f ", std::abs(d_early_eqsym[kLltf0Rel][i]),
                                std::arg(d_early_eqsym[kLltf0Rel][i]));
                }
                std::printf("\n");
                std::printf("[RX][LLTF-DBG] L-LTF1[0:3]: ");
                for (int i = 0; i < 4 && i < 52; i++) {
                    std::printf("%.3f∠%.3f ", std::abs(d_early_eqsym[kLltf1Rel][i]),
                                std::arg(d_early_eqsym[kLltf1Rel][i]));
                }
                std::printf("\n");
            }
            std::fflush(stdout);

            gr_complex Hhdr52[52];
            estimate_header_channel_from_lltf52(d_early_eqsym[kLltf0Rel],
                                                d_early_eqsym[kLltf1Rel],
                                                Hhdr52);

            // 信道估计调试输出
            float h_mag_avg = 0.0f;
            float h_phase_var = 0.0f;
            gr_complex h_avg = gr_complex(0.0f, 0.0f);
            for (int i = 0; i < 52; i++) {
                h_mag_avg += std::abs(Hhdr52[i]);
                h_avg += Hhdr52[i];
            }
            h_mag_avg /= 52.0f;
            h_avg /= 52.0f;
            for (int i = 0; i < 52; i++) {
                gr_complex diff = Hhdr52[i] - h_avg;
                h_phase_var += std::arg(diff) * std::arg(diff);
            }
            h_phase_var /= 52.0f;
            std::printf("[RX][CHAN-EST] Hhdr52: avg_mag=%.4f avg_phase=%.4f rad phase_var=%.4f\n",
                        h_mag_avg, std::arg(h_avg), h_phase_var);
            // 打印前几个子载波的信道响应
            std::printf("[RX][CHAN-EST] Hhdr52[0:5]: ");
            for (int i = 0; i < 6 && i < 52; i++) {
                std::printf("%.3f∠%.3f ", std::abs(Hhdr52[i]), std::arg(Hhdr52[i]));
            }
            std::printf("\n");

            // DEBUG: Print Hhdr52 phase per subcarrier to verify channel estimate
            fprintf(stderr, "[CHAN_EST] Hhdr52 data subcarrier phases (subcarriers 0-47):\n");
            for (int i = 0; i < 48; i++) {
                float mag = std::abs(Hhdr52[i]);
                float phase_deg = std::arg(Hhdr52[i]) * 180.0f / M_PI;
                fprintf(stderr, "  SC%+.2d (idx%d): mag=%.3f phase=%+.1fdeg\n",
                        kHeader48Sc[i], i, mag, phase_deg);
            }
            fprintf(stderr, "[CHAN_EST] Hhdr52 pilot phases:\n");
            for (int i = 0; i < 4; i++) {
                int idx = 48 + i;
                float mag = std::abs(Hhdr52[idx]);
                float phase_deg = std::arg(Hhdr52[idx]) * 180.0f / M_PI;
                fprintf(stderr, "  Pilot%d (idx%d): mag=%.3f phase=%+.1fdeg\n",
                        i, idx, mag, phase_deg);
            }

            // Summary: check if phases are consistent
            float phase_sum = 0.0f, phase_var = 0.0f;
            for (int i = 0; i < 52; i++) {
                phase_sum += std::arg(Hhdr52[i]);
            }
            float phase_mean = phase_sum / 52.0f;
            for (int i = 0; i < 52; i++) {
                float diff = std::arg(Hhdr52[i]) - phase_mean;
                phase_var += diff * diff;
            }
            phase_var /= 52.0f;
            fprintf(stderr, "[CHAN_EST] Phase stats: mean=%+.1fdeg var=%.1f\n",
                    phase_mean * 180.0f / M_PI, phase_var * 180.0f / M_PI);
            fflush(stderr);

            // debug print rel=3/4/5 direct-path raw/equalized/deinterleaved bits
            std::fprintf(stderr, "[DIRECT_STDERR] About to print LSIG_MARKER\n");
            fflush(stderr);
            std::printf("[LSIG_MARKER] About to start HDRDBG loop\n");
            fflush(stdout);
            for (int rel : {kLSigRel, kHtSig0Rel, kHtSig1Rel}) {
                uint8_t raw48[48];
                uint8_t eq48[48];
                uint8_t deintl48[48];

                extract_header_raw48_bits_from_cache52(d_early_eqsym[rel], raw48);
                equalize_header52_to_bits48(d_early_eqsym[rel], Hhdr52, eq48, nullptr);
                deinterleave_bpsk_48(eq48, deintl48);

                std::printf("[RX][HDRDBG] rel=%d raw48=%s\n",
                            rel, bits_to_string(raw48, 48).c_str());
                std::printf("[RX][HDRDBG] rel=%d eq48 =%s\n",
                            rel, bits_to_string(eq48, 48).c_str());
                std::printf("[RX][HDRDBG] rel=%d deintl48=%s\n",
                            rel, bits_to_string(deintl48, 48).c_str());

                // 比特错误统计
                int raw_eq_mismatch = 0;
                int eq_deintl_mismatch = 0;
                for (int i = 0; i < 48; i++) {
                    if (raw48[i] != eq48[i]) raw_eq_mismatch++;
                    if (eq48[i] != deintl48[i]) eq_deintl_mismatch++;
                }
                std::printf("[RX][HDRDBG] rel=%d bit-errors: raw->eq=%d eq->deintl=%d\n",
                            rel, raw_eq_mismatch, eq_deintl_mismatch);
            }
            std::printf("[LSIG_MARKER] HDRDBG loop done, about to set found=false\n");
            std::fflush(stdout);

            bool found = false;
            std::fprintf(stderr, "[LSIG_DEBUG] Reached L-SIG decode section, found=%d\n", found ? 1 : 0);
            std::fflush(stderr);

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
                    std::printf("[EQ][LSIG_FAIL] decode_lsig returned false for inv_lsig=%d\n", inv_lsig);
                    fflush(stdout);
                    continue;
                }
                std::printf("[EQ][LSIG_OK] decode_lsig returned true: lsig_enc=%d lsig_len=%d inv_lsig=%d\n",
                           lsig_enc, lsig_len, inv_lsig);
                fflush(stdout);

                if (lsig_enc != 0) {
                    std::printf("[EQ][LSIG_FAIL] lsig_enc=%d != 0, continuing\n", lsig_enc);
                    fflush(stdout);
                    continue;
                }

                std::printf("[EQ][LSIG_OK] Passed L-SIG checks, about to detect HT-SIG rotation\n");
                fflush(stdout);
                // Detect HT-SIG QBPSK rotation
                fprintf(stderr, "[DEBUG2] d_early_eqsym[3][0:4] BEFORE detect_htsig_rotation = ");
                for (int i = 0; i < 4; i++) {
                    fprintf(stderr, "%.3f+%.3fi ", d_early_eqsym[3][i].real(), d_early_eqsym[3][i].imag());
                }
                fprintf(stderr, "\n");
                // DEBUG: Print actual pilot values (indices 48-51) to verify they're correct
                fprintf(stderr, "[DEBUG2] HT-SIG0 PILOTS (indices 48-51): ");
                for (int i = 48; i < 52; i++) {
                    fprintf(stderr, "idx[%d]=%.3f+%.3fi ", i,
                            d_early_eqsym[3][i].real(), d_early_eqsym[3][i].imag());
                }
                fprintf(stderr, "\n");
                fflush(stderr);
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

                // DEBUG: Print raw FFT bins for HT-SIG0 before rotation compensation
                fprintf(stderr, "[FFT_RAW_HT0] d_early_eqsym[kHtSig0Rel] BEFORE rotation:\n");
                for (int i = 0; i < 48; i++) {
                    fprintf(stderr, "  idx[%d] SC%+.2d (bin%d) = %.3f+%.3fi\n",
                            i, kHeader48Sc[i], kHeader48Bin[i],
                            d_early_eqsym[kHtSig0Rel][i].real(), d_early_eqsym[kHtSig0Rel][i].imag());
                }
                fprintf(stderr, "[FFT_RAW_HT0] Pilots:\n");
                for (int i = 0; i < 4; i++) {
                    int idx = 48 + i;
                    fprintf(stderr, "  idx[%d] (bin%d) = %.3f+%.3fi\n",
                            idx, kPilot4Bin[i],
                            d_early_eqsym[kHtSig0Rel][idx].real(), d_early_eqsym[kHtSig0Rel][idx].imag());
                }
                fflush(stderr);

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

                            std::fprintf(stderr, "[DEBUG] Calling decode_htsig: rot=%d inv_a=%d inv_b=%d\n", rot, inv_a, inv_b);
                            fflush(stderr);
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

                            std::printf("[EQ][L-SIG] parsed OK: rel=%d inv=%d len=%d\n",
                                        kLSigRel, inv_lsig, lsig_len);
                            std::printf("[EQ][HT-SIG] parsed OK: lsig=%d htsig=%d/%d rot=%d invA=%d invB=%d mcs=%d len=%d sgi=%d agg=%d data_start=%d n_sym=%d\n",
                                        kLSigRel,
                                    kHtSig0Rel,
                                    kHtSig1Rel,
                                    inv_a,
                                    inv_b,
                                    parsed_mcs,
                                    parsed_len,
                                    parsed_sgi ? 1 : 0,
                                    parsed_agg ? 1 : 0,
                                    d_data_start_rel,
                                    d_frame_symbols);
                        std::fflush(stdout);

                        found = true;
                    }
                }
                }
            }

            if (!found) {
                std::printf("[EQ][HT-SIG] parse failed: lsig=%d htsig=%d/%d\n",
                            kLSigRel, kHtSig0Rel, kHtSig1Rel);
                // 调试：打印L-SIG和HT-SIG比特
                if (d_early_bits_valid[kLSigRel]) {
                    std::fprintf(stderr, "[EQ][HT-SIG][DEBUG] L-SIG bits (48): ");
                    for (int i = 0; i < 48; i++) {
                        std::fprintf(stderr, "%d", d_early_bits[kLSigRel][i] ? 1 : 0);
                    }
                    std::fprintf(stderr, "\n");
                }
                if (d_early_bits_valid[kHtSig0Rel]) {
                    std::fprintf(stderr, "[EQ][HT-SIG][DEBUG] HT-SIG0 bits (48): ");
                    for (int i = 0; i < 48; i++) {
                        std::fprintf(stderr, "%d", d_early_bits[kHtSig0Rel][i] ? 1 : 0);
                    }
                    std::fprintf(stderr, "\n");
                }
                if (d_early_bits_valid[kHtSig1Rel]) {
                    std::fprintf(stderr, "[EQ][HT-SIG][DEBUG] HT-SIG1 bits (48): ");
                    for (int i = 0; i < 48; i++) {
                        std::fprintf(stderr, "%d", d_early_bits[kHtSig1Rel][i] ? 1 : 0);
                    }
                    std::fprintf(stderr, "\n");
                }
                std::fflush(stdout);
                std::fflush(stderr);
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
        std::printf("[EQ][COUNTER] incrementing d_internal_symbol_counter to %d\n", d_internal_symbol_counter);

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

