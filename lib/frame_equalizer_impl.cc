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

// Phase 46 AR5: MMSE equalization for HT-SIG. eq = conj(H)·rx / (|H|² + N0).
// At strong SCs (|H|² >> N0): behaves like ZF. At null SCs (|H|² << N0):
// returns ~0 instead of amplifying noise by 1/|H|². N0 is estimated as
// the 25th percentile of |H|² over the 48 data SCs — robust to outlier
// null SCs dragging the estimate down. Operates on the full 52-element
// HT subcarrier vector (data SCs at [0..48), pilots at [48..52)).
//   in[52]:  rx52 (post-rotation, post-δ-corrected)
//   H[52]:   Hhdr52 (or H_htsig if Phase 39 re-estimation is enabled)
//   out[48]: eq48 — imaginary axis carries QBPSK bit
static void mmse_equalize_htsig(const gr_complex* in,
                                const gr_complex* H,
                                gr_complex* out,
                                int n0_percentile = 25)
{
    // 1. Compute |H|² over the 48 data SCs and find the requested percentile as N0.
    double h_sq[48];
    for (int i = 0; i < 48; i++) {
        h_sq[i] = std::norm(H[i]);  // |H[i]|²
    }
    std::array<double, 48> sorted_h_sq{};
    for (int i = 0; i < 48; i++) sorted_h_sq[i] = h_sq[i];
    std::sort(sorted_h_sq.begin(), sorted_h_sq.end());
    // Map percentile P in [1,49] to linear-interp index in [0, 47].
    // For P=25, idx = 0.25 * 47 = 11.75 → average of sorted[11] and sorted[12].
    if (n0_percentile < 1) n0_percentile = 1;
    if (n0_percentile > 49) n0_percentile = 49;
    double idx_d = (n0_percentile / 100.0) * 47.0;
    int idx_lo = (int)idx_d;
    int idx_hi = idx_lo + 1;
    if (idx_hi > 47) idx_hi = 47;
    double frac = idx_d - idx_lo;
    double N0 = sorted_h_sq[idx_lo] * (1.0 - frac) + sorted_h_sq[idx_hi] * frac;
    if (N0 < 1e-9) N0 = 1e-9;  // floor to prevent division by zero

    // 2. MMSE equalize all 48 data SCs.
    for (int i = 0; i < 48; i++) {
        gr_complex denom(h_sq[i] + N0, 0.0f);
        out[i] = (gr_complex)std::conj(H[i]) * in[i] / denom;
    }
}

// Phase 72: MMSE N0 estimation for header equalization (L-SIG and HT-SIG).
// Computes the n0_percentile-th percentile of |H|² over the 48 data SCs.
// Returns the value (floored at 1e-9 to prevent division by zero).
static double estimate_mmse_n0(const gr_complex* H52, int n0_percentile)
{
    double h_sq[48];
    for (int i = 0; i < 48; i++) {
        h_sq[i] = std::norm(H52[i]);
    }
    std::array<double, 48> sorted_h_sq{};
    for (int i = 0; i < 48; i++) sorted_h_sq[i] = h_sq[i];
    std::sort(sorted_h_sq.begin(), sorted_h_sq.end());
    if (n0_percentile < 1) n0_percentile = 1;
    if (n0_percentile > 49) n0_percentile = 49;
    double idx_d = (n0_percentile / 100.0) * 47.0;
    int idx_lo = (int)idx_d;
    int idx_hi = idx_lo + 1;
    if (idx_hi > 47) idx_hi = 47;
    double frac = idx_d - idx_lo;
    double N0 = sorted_h_sq[idx_lo] * (1.0 - frac) + sorted_h_sq[idx_hi] * frac;
    if (N0 < 1e-9) N0 = 1e-9;
    return N0;
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

// EXPLICIT FFT bin mapping for 52 active subcarriers (SC → bin index in
// 52-bin TX order, including pilots at the end).
static constexpr int kScIndex52[52] = {
    -26,-25,-24,-23,-22,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,1,2,3,4,5,6,8,9,10,11,12,13,14,15,16,17,18,19,
    20,22,23,24,25,26,-21,-7,7,21
};

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

// Phase 36: estimate per-symbol per-SC phase (a, b) from HT-SIG pilots.
// Uses ht_expected_pilot polarity-aware helper. Linear regression on
// (sc_index, channel_phase) over the 4 pilots. Returns true if at least
// 2 valid pilots contributed; false otherwise.
//
//   channel_phase[sc] = a + b * sc
//   rx_pilot[bin] = expected_pilot * H * exp(j * (a + b * sc))
//   ⇒ arg(rx_pilot * conj(expected)) = channel_phase
//
// In practice:
//   For each pilot i:
//     expected = ht_expected_pilot(sym_idx, i)  // ±1 with polarity
//     eq_pilot = rx52[bin_i] / H52[bin_i]        // equalized
//     channel_phase_i = arg(eq_pilot * conj(expected))
//   Linear fit: (a, b) = polyfit(kPilot4Sc, channel_phase, 1)
//
// `rx52` and `H52` are in 52-bin TX-order where bin indices 48..51
// are pilots at SCs -21, -7, 7, 21 (matches kPilot4Sc order).
static bool estimate_htsig_pilot_persc(const gr_complex* rx52,
                                       const gr_complex* H52,
                                       int sym_idx,
                                       float& out_a,
                                       float& out_b) {
    // Per-pilot channel phase accumulator (for linear regression).
    double sum_sc = 0.0, sum_sc2 = 0.0;
    double sum_phi = 0.0, sum_sc_phi = 0.0;
    int n_valid = 0;
    for (int i = 0; i < 4; i++) {
        const int sc = kPilot4Sc[i];          // -21, -7, 7, 21
        const int bin = 48 + i;                // bin 48..51 in 52-bin order
        const gr_complex& h = H52[bin];
        const gr_complex& rx = rx52[bin];
        if (std::abs(h) < 1e-3f || std::abs(rx) < 1e-3f) {
            continue;  // skip null pilots
        }
        const gr_complex expected = ht_expected_pilot(sym_idx, i);
        const gr_complex eq = rx / h;             // equalized symbol
        const gr_complex phase_c = eq * std::conj(expected);
        // Channel phase at this pilot's SC.
        const float phi = std::arg(phase_c);
        sum_sc += sc;
        sum_sc2 += (double)sc * sc;
        sum_phi += phi;
        sum_sc_phi += (double)sc * phi;
        n_valid++;
    }
    if (n_valid < 2) {
        return false;  // need at least 2 points for linear fit
    }
    const double denom = (double)n_valid * sum_sc2 - sum_sc * sum_sc;
    if (std::abs(denom) < 1e-6) {
        return false;  // all pilots at same SC (shouldn't happen)
    }
    out_b = (float)(((double)n_valid * sum_sc_phi - sum_sc * sum_phi) / denom);
    out_a = (float)((sum_phi - out_b * sum_sc) / n_valid);
    return true;
}

// Phase 39: Estimate channel H from HT-SIG symbol's own 4 pilot SCs.
// QBPSK pilot TX values per 802.11n Table 19-9/19-10:
//   SC -21: +j
//   SC -7:  +j
//   SC +7:  +j
//   SC +21: -j
// (Stored at bins {48,49,50,51} in the 52-element rx52 layout.)
//
// Algorithm:
//   1. For each pilot bin: H_at_pilot[p] = safe_div(rx52[48+p], qbpsk[p])
//   2. Mark pilot_valid[p] = (|H_at_pilot[p]| > 0.05)
//   3. If <2 valid pilots: copy H_fallback to H_htsig52, return false
//   4. Pilot bins (48..51): H_htsig52[48+p] = H_at_pilot[p]
//      (or H_fallback[48+p] if pilot invalid; the linear interp below
//      skips invalid anchors and uses the next valid neighbor)
//   5. Data bins (0..47): piecewise linear interpolation in the complex
//      plane (real/imag independently) between the surrounding valid
//      pilot anchors. Edge SCs (outside pilot range) extrapolate from
//      the nearest segment using its slope. If a pilot anchor is invalid,
//      we skip it: the segment endpoints become the nearest valid pair
//      on each side of the bin's SC index. If no valid anchor exists on
//      one side, the bin falls back to H_fallback[bin].
//
// Returns: true if >=2 pilots were valid (output has re-estimated H);
//          false if helper fell back to H_fallback (output is mostly a
//          copy of H_fallback, with only valid pilot bins overridden).
static bool estimate_H_from_htsig_pilots(
    const gr_complex* rx52,
    const gr_complex* H_fallback,
    gr_complex* H_htsig52)
{
    // HT-SIG pilot values in QBPSK (imag axis): {+j, +j, +j, -j}
    static const gr_complex kHtsigPilotQbpsk[4] = {
        gr_complex(0.0f,  1.0f),   // SC -21: +j
        gr_complex(0.0f,  1.0f),   // SC -7:  +j
        gr_complex(0.0f,  1.0f),   // SC +7:  +j
        gr_complex(0.0f, -1.0f)    // SC +21: -j
    };
    static const int kPilotSc[4] = {-21, -7, 7, 21};

    gr_complex H_at_pilot[4];
    bool pilot_valid[4] = {false, false, false, false};
    for (int p = 0; p < 4; p++) {
        const int bin = 48 + p;
        const gr_complex p_rx = rx52[bin];
        if (std::abs(p_rx) < 1e-3f) {
            H_at_pilot[p] = H_fallback[bin];
            continue;
        }
        H_at_pilot[p] = safe_div(p_rx, kHtsigPilotQbpsk[p]);
        pilot_valid[p] = (std::abs(H_at_pilot[p]) > 0.05f);
    }

    int n_valid = 0;
    for (int p = 0; p < 4; p++) if (pilot_valid[p]) n_valid++;
    if (n_valid < 2) {
        // Bootstrap failed: copy fallback H wholesale.
        std::memcpy(H_htsig52, H_fallback, 52 * sizeof(gr_complex));
        return false;
    }

    // Pilot bins: use the re-estimated H (or fallback if invalid).
    for (int p = 0; p < 4; p++) {
        H_htsig52[48 + p] = pilot_valid[p] ? H_at_pilot[p] : H_fallback[48 + p];
    }

    // Data bins: piecewise linear interpolation in the complex plane.
    // For each bin, find the surrounding valid pilot anchors (left/right)
    // by SC index. If no valid anchor exists on one side, fall back to
    // H_fallback[bin] for that bin.
    auto lerp_complex = [](gr_complex a, gr_complex b, float t) -> gr_complex {
        return a + (b - a) * t;
    };

    for (int i = 0; i < 48; i++) {
        const int sc = kScIndex52[i];
        // Find nearest valid pilot with sc_p <= sc (left_idx)
        int left_idx = -1;
        for (int p = 0; p < 4; p++) {
            if (pilot_valid[p] && kPilotSc[p] <= sc) {
                left_idx = p;
            }
        }
        // Find nearest valid pilot with sc_p >= sc (right_idx)
        int right_idx = -1;
        for (int p = 3; p >= 0; p--) {
            if (pilot_valid[p] && kPilotSc[p] >= sc) {
                right_idx = p;
            }
        }
        if (left_idx == -1 || right_idx == -1) {
            // Outside the valid range; use fallback.
            H_htsig52[i] = H_fallback[i];
            continue;
        }
        if (left_idx == right_idx) {
            // Single anchor on both sides (degenerate).
            H_htsig52[i] = H_at_pilot[left_idx];
            continue;
        }
        const int sc_l = kPilotSc[left_idx];
        const int sc_r = kPilotSc[right_idx];
        const float t = (float)(sc - sc_l) / (float)(sc_r - sc_l);
        H_htsig52[i] = lerp_complex(H_at_pilot[left_idx], H_at_pilot[right_idx], t);
    }
    return true;
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

// Phase 59: detect H52 channel nulls. Return indices where |H52[i]| < thresh.
// Skip DC (i=0) since |H52[0]| is always 0 (no subcarrier at DC in 802.11n).
// Mirrors Python detect_h52_nulls() in
// examples/test_h52_null_interp_synthetic.py.
static std::vector<int> detect_h52_nulls(const gr_complex* h52, float thresh)
{
    std::vector<int> nulls;
    for (int i = 1; i < 52; i++) {  // skip DC
        if (std::abs(h52[i]) < thresh) {
            nulls.push_back(i);
        }
    }
    return nulls;
}

// Phase 59: replace null SCs with mean of nearest non-null neighbors within
// `radius` (left/right). Cluster nulls (no valid neighbors) are left
// unchanged (don't make them worse than baseline).
// Mirrors Python interp_h52_nulls() in
// examples/test_h52_null_interp_synthetic.py.
static void interp_h52_nulls(gr_complex* h52,
                              const std::vector<int>& nulls,
                              int radius)
{
    // Use set for O(1) membership test
    std::set<int> null_set(nulls.begin(), nulls.end());
    for (int null_idx : nulls) {
        std::complex<float> sum(0.0f, 0.0f);
        int count = 0;
        for (int d = 1; d <= radius; d++) {
            int left  = null_idx - d;
            int right = null_idx + d;
            if (left >= 0 && null_set.find(left) == null_set.end()) {
                sum += h52[left];
                count++;
            }
            if (right < 52 && null_set.find(right) == null_set.end()) {
                sum += h52[right];
                count++;
            }
        }
        if (count > 0) {
            h52[null_idx] = sum / static_cast<float>(count);
        }
        // else: cluster null -> keep original (no-op)
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
bool g_eq_lltf_timing_dump = false;  // Phase 31: bridge to H52 compute site dump in general_work

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

    // Phase 72: Hann window envelope compensation.
    // When L-LTF RX FFT uses a Hann window (IEEE80211_RX_FFT_WINDOW=hann),
    // the FFT output is scaled by Hann's main-lobe gain (0.5 at DC).
    // This shows up as a 2x magnitude reduction in H52, which the viterbi
    // interprets as a 2x channel attenuation. Compensate by multiplying
    // H52 by 1/0.5 = 2.0 to restore the magnitude scale.
    //
    // This is a FIRST-ORDER correction (only DC gain). Full 3-tap
    // deconvolution (correcting leakage between adjacent bins) is future
    // work. The MMSE EQ (Task 2) provides additional noise regularization
    // that handles the residual leakage.
    //
    // Default behavior:
    //   - IEEE80211_RX_FFT_WINDOW=rectangular (default) -> compensation OFF
    //   - IEEE80211_RX_FFT_WINDOW=hann AND IEEE80211_RX_FFT_WINDOW_COMPENSATE=1
    //     (default ON for non-rectangular) -> compensation ON (multiply by 2.0)
    //   - IEEE80211_RX_FFT_WINDOW=hann AND IEEE80211_RX_FFT_WINDOW_COMPENSATE=0
    //     -> compensation OFF (raw Hann-windowed H52, matches Phase 71's broken behavior)
    static bool hann_comp_cached = false;
    static bool hann_comp_enabled = false;
    if (!hann_comp_cached) {
        const char* env_window = getenv("IEEE80211_RX_FFT_WINDOW");
        // Manual case-insensitive compare against "rectangular"
        bool is_non_rect = false;
        if (env_window && env_window[0] != '\0') {
            const char* w = env_window;
            const char* target = "rectangular";
            int wi = 0, ti = 0;
            while (w[wi] && target[ti]) {
                char cw = (w[wi] >= 'A' && w[wi] <= 'Z') ? (w[wi] + 32) : w[wi];
                char ct = (target[ti] >= 'A' && target[ti] <= 'Z') ? (target[ti] + 32) : target[ti];
                if (cw != ct) break;
                wi++; ti++;
            }
            is_non_rect = (w[wi] != '\0' || target[ti] != '\0');
        }
        const char* env_comp = getenv("IEEE80211_RX_FFT_WINDOW_COMPENSATE");
        // Default ON for non-rectangular; explicit "0" or "false" disables
        bool comp_explicit_off = (env_comp && (env_comp[0] == '0' || env_comp[0] == 'f' || env_comp[0] == 'F'));
        hann_comp_enabled = is_non_rect && !comp_explicit_off;
        hann_comp_cached = true;
    }
    if (hann_comp_enabled) {
        // Hann(64) DC gain = 0.5 -> multiply by 2.0 to restore magnitude
        const gr_complex comp_factor(2.0f, 0.0f);
        for (int i = 0; i < 52; i++) {
            H52[i] = H52[i] * comp_factor;
        }
    }
}

// Phase 34: estimate per-frame sub-sample timing offset δ from H52.
// argH[i] ≈ a + b·kScIndex52[i] (assuming flat channel + linear δ phase);
// δ = -b × 64 / (2π) mod 1.0, in units of 1/64 sample (range [0, 1)).
// Discovered via Phase 33b USRP validation: argH[b] = -2π·kScIndex52[b]·δ/64
// with δ per-frame in [0,1) at 1/64 quantization. Causes 64-PSK residual on
// USRP frames. Loopback has δ=0 always (no air path).
static float estimate_timing_offset_from_h52(const gr_complex* H52)
{
    double sum_sc = 0.0, sum_sc2 = 0.0, sum_arg = 0.0, sum_sc_arg = 0.0;
    double sum_w = 0.0;
    for (int i = 0; i < 52; i++) {
        float a = std::arg(H52[i]);
        int sc = kScIndex52[i];
        // |H|-weighted regression: stronger SCs contribute more (channel flatness assumption)
        float w = std::abs(H52[i]);
        sum_sc += sc * w;
        sum_sc2 += (double)sc * sc * w;
        sum_arg += a * w;
        sum_sc_arg += (double)sc * a * w;
        sum_w += w;
    }
    if (sum_w < 1e-9) return 0.0f;
    // Weighted least-squares: argH = a + b·SC
    double mean_sc = sum_sc / sum_w;
    double mean_arg = sum_arg / sum_w;
    double cov_sc_arg = 0.0, var_sc = 0.0;
    for (int i = 0; i < 52; i++) {
        float a = std::arg(H52[i]);
        int sc = kScIndex52[i];
        float w = std::abs(H52[i]);
        double dsc = sc - mean_sc;
        cov_sc_arg += w * dsc * (a - mean_arg);
        var_sc += w * dsc * dsc;
    }
    if (var_sc < 1e-9) return 0.0f;
    double b = cov_sc_arg / var_sc;  // slope of argH vs SC
    float delta = (float)(-b * 64.0 / (2.0 * M_PI));
    // Wrap to [0, 1) (1/64 sample units)
    delta = delta - std::floor(delta);
    return delta;
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

    // Phase 72: opt-in MMSE equalization. Default OFF (ZF) preserves
    // existing behavior. When ON, eq = conj(H)*rx / (|H|² + N0) where
    // N0 is the n0_percentile-th percentile of |H|² over the 48 data SCs.
    // This regularizes the noise amplification at H52 nulls identified
    // in Phase 27/30/38/41 as the root cause of viterbi failures.
    static bool mmse_cached = false;
    static bool mmse_enabled = false;
    static int  n0_percentile = 25;
    if (!mmse_cached) {
        const char* env_mmse = getenv("IEEE80211_MMSE_EQUALIZE");
        mmse_enabled = (env_mmse && env_mmse[0] != '\0' && env_mmse[0] != '0');
        const char* env_pct = getenv("IEEE80211_MMSE_N0_PERCENTILE");
        if (env_pct) {
            n0_percentile = atoi(env_pct);
        }
        mmse_cached = true;
    }

    double N0 = 0.0;
    if (mmse_enabled) {
        N0 = estimate_mmse_n0(H52, n0_percentile);
    }

    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else if (mmse_enabled) {
            // MMSE: conj(H) * rx / (|H|² + N0)
            double denom = (double)h_mag * (double)h_mag + N0;
            eq = std::conj(H52[i]) * rx52[i] / (gr_complex)denom;
        } else {
            // ZF (default)
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

// =====================================================================
// Phase 44: Soft-LLR viterbi variant.
//
// Same K=7, rate 1/2, polynomials 133/171 trellis, but the branch
// metric uses SQUARED-ERROR distance from the soft observation to the
// expected BPSK constellation point. Magnitude of the input controls
// confidence weighting: a near-zero LLR contributes little to the path
// metric even if it disagrees with the branch's expected output.
//
// Soft input format: rx_soft[2*t] = LLR for output bit o0, where
//   - sign = expected bit value (+1 -> bit 1, -1 -> bit 0)
//   - magnitude = confidence (proportional to |H[i]| / max(|H|))
//
// Branch metric for transition producing (o0, o1):
//   bm = (rx_soft[2*t]   - (1 if o0 else -1))^2
//      + (rx_soft[2*t+1] - (1 if o1 else -1))^2
// Returned metric is the float sum (not int) because squared-error
// needs fractional precision. Callers compare across candidates.
// =====================================================================
static bool viterbi_decode_133_171_soft(const float* rx_soft,
                                        int n_encoded_bits,
                                        std::vector<uint8_t>& decoded_bits,
                                        int* out_best_metric_q8 = nullptr)
{
    if (n_encoded_bits <= 0 || (n_encoded_bits & 0x1)) {
        return false;
    }

    const int n_steps = n_encoded_bits / 2;
    // Metrics are sums of squared errors in Q8.8 fixed-point to avoid
    // float divergence across long paths while keeping enough precision
    // for ordering. INF = INT_MAX/4 like the hard-bit version.
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

        // Soft observations are LLRs in roughly [-1, +1]. Scale to Q8.8
        // (multiply by 256) so squared-error comparisons stay ordered.
        const int r0_q = (int)std::lroundf(rx_soft[2 * t] * 256.0f);
        const int r1_q = (int)std::lroundf(rx_soft[2 * t + 1] * 256.0f);

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

                // Reference LLR for o=1 is +1 (Q8.8 = +256), for o=0 is -1 (Q8.8 = -256)
                const int ref0 = o0 ? 256 : -256;
                const int ref1 = o1 ? 256 : -256;
                const int err0 = r0_q - ref0;
                const int err1 = r1_q - ref1;
                // Q8.8 squared gives Q16.16; shift right 8 to keep ints small.
                const int bm = ((err0 * err0) >> 8) + ((err1 * err1) >> 8);
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
            if (out_best_metric_q8) *out_best_metric_q8 = INF;
            return false;
        }
    }
    if (out_best_metric_q8) *out_best_metric_q8 = best_metric;

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

// Phase 44: Convert per-SC QBPSK equalized symbol + H magnitude into a
// 48-element LLR array. LLR[i] = sign(eq.imag()) * |H[i]| / max(|H|).
// The sign carries the bit decision; the magnitude carries confidence.
// Null SCs (|H[i]| near 0) get near-zero LLR, which the soft viterbi
// treats as erasure — exactly the down-weighting we need.
static void compute_soft_llr_qbpsk(const gr_complex* eq48,
                                   const gr_complex* H52,
                                   float llr_out[48])
{
    // Compute max |H| over the 48 data SCs (skip pilots which are unused here).
    float max_h = 0.0f;
    for (int i = 0; i < 48; i++) {
        const float hm = std::abs(H52[i]);
        if (hm > max_h) max_h = hm;
    }
    if (max_h < 1e-6f) max_h = 1e-6f;  // avoid div by zero

    for (int i = 0; i < 48; i++) {
        const float hm = std::abs(H52[i]);
        const float conf = hm / max_h;
        const float s = (eq48[i].imag() >= 0.0f) ? 1.0f : -1.0f;
        llr_out[i] = s * conf;
    }
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
                                             uint8_t* dbg_deintl48 = nullptr,
                                             int rot_idx = 0)
{
    uint8_t eqbits48[48];
    uint8_t deintl48[48];

    // NOTE: rx52 (d_early_eqsym) has already been phase-compensated in general_work
    // using per-subcarrier linear regression (CFO+SFO). Do NOT apply CPE again.
    // Phase 70: Apply optional phase rotation before hard-decision.
    // rot_idx in [0, 3] corresponds to 0°/90°/180°/270° rotation,
    // allowing the 8-candidate search (4 rot × 2 inv) to undo any
    // residual phase error in the equalized L-SIG constellation.
    const gr_complex rot_factor = std::polar(1.0f, rot_idx * (float)(M_PI / 2.0));
    for (int i = 0; i < 48; i++) {
        float h_mag = std::abs(H52[i]);
        gr_complex eq;
        if (h_mag < 0.001f) {
            eq = gr_complex(0.0f, 0.0f);
        } else {
            eq = safe_div(rx52[i], H52[i]);
        }
        // Rotate the equalized symbol by rot_idx * 90° to align constellation
        // back to the real axis for hard-decision.
        eq = eq * rot_factor;
        eqbits48[i] = hard_bit_from_complex(eq);
    }

    // Phase 31 Task 18 (RC-C L-SIG viterbi): dump the raw 48 L-SIG
    // equalized constellation points (real, imag), |Hhdr52|, arg(Hhdr52),
    // and hard bits. This verifies the H-X2+H-X6 hypothesis: H52
    // over-equalization inflates |eq|^2 and per-SC phase error rotates
    // BPSK constellation, producing 50/50 hard bits and viterbi failure.
    // Atomic snprintf+USRP_LOG("%s", buf) for thread safety. Flood-gated
    // to first 10 calls. Opt-in via IEEE80211_LSIG_EQ_DUMP=1.
    if (getenv("IEEE80211_LSIG_EQ_DUMP") && getenv("IEEE80211_LSIG_EQ_DUMP")[0] != '\0') {
        static int g_lsig_eq_dump_counter = 0;
        if (g_lsig_eq_dump_counter < 10) {
            char buf[4096];
            int n = snprintf(buf, sizeof(buf),
                             "[LSIG_EQ_DUMP] counter=%d inv=%d |H|=",
                             g_lsig_eq_dump_counter, invert_bits ? 1 : 0);
            for (int i = 0; i < 48 && n < (int)sizeof(buf) - 64; i++) {
                n += snprintf(buf + n, sizeof(buf) - n, "%.3f,",
                              std::abs(H52[i]));
            }
            n += snprintf(buf + n, sizeof(buf) - n, " argH=");
            for (int i = 0; i < 48 && n < (int)sizeof(buf) - 64; i++) {
                n += snprintf(buf + n, sizeof(buf) - n, "%.2f,",
                              std::arg(H52[i]));
            }
            n += snprintf(buf + n, sizeof(buf) - n, " eq=");
            for (int i = 0; i < 48 && n < (int)sizeof(buf) - 64; i++) {
                float h_mag_d = std::abs(H52[i]);
                gr_complex eq_d;
                if (h_mag_d < 0.001f) {
                    eq_d = gr_complex(0.0f, 0.0f);
                } else {
                    eq_d = safe_div(rx52[i], H52[i]);
                }
                n += snprintf(buf + n, sizeof(buf) - n, "(%.2f,%.2f) ",
                              eq_d.real(), eq_d.imag());
            }
            n += snprintf(buf + n, sizeof(buf) - n, " bits=");
            for (int i = 0; i < 48 && n < (int)sizeof(buf) - 16; i++) {
                n += snprintf(buf + n, sizeof(buf) - n, "%d",
                              (int)eqbits48[i]);
            }
            snprintf(buf + n, sizeof(buf) - n, "\n");
            USRP_LOG("%s", buf);
            g_lsig_eq_dump_counter++;
        }
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

// Forward declarations for Phase 20 helpers (defined below).
static int estimate_per_sc_phase_from_htsig0(
    const gr_complex* rx_htsig0,
    const gr_complex* expected_htsig0,
    float* phase_per_sc);

// Simplified HT-SIG decode for QBPSK-rotated symbols
// Skips CPE rotation since QBPSK already compensates for phase
static bool decode_htsig_from_rotated(const gr_complex* rx52_a,
                                       const gr_complex* rx52_b,
                                       const gr_complex* H52_a,
                                       const gr_complex* H52_b,
                                       bool invert_a,
                                       bool invert_b,
                                       int& out_len_bytes,
                                       int& out_mcs,
                                       bool& out_sgi,
                                       bool& out_agg,
                                       bool& out_use_ldpc,
                                       int rot = -1,
                                       int* out_vit_metric = nullptr,
                                       const char** out_fail_reason = nullptr,
                                       bool use_soft_llr = false,
                                       int  n0_percentile = 0)  // Phase 47: 0=disabled, 1-49=enabled
{
    if (out_vit_metric) *out_vit_metric = -1;
    if (out_fail_reason) *out_fail_reason = "init";
    uint8_t eqbits48_a[48];
    uint8_t eqbits48_b[48];
    uint8_t deintl48_a[48];
    uint8_t deintl48_b[48];
    uint8_t enc96[96];
    // Phase 44: parallel soft-LLR buffers (only populated when use_soft_llr)
    float llr48_a[48];
    float llr48_b[48];
    float llr_inter_a[48];
    float llr_inter_b[48];
    float enc96_soft[96];

    // NOTE: rx52_a/b (d_early_eqsym) has already been phase-compensated in general_work.
    // Do NOT apply additional CPE compensation here.

    // Extract bits from HT-SIG0 (rx52_a)
    // Phase 44: when use_soft_llr, also compute LLR = sign(eq.imag()) * |H|/max|H|
    // Phase 46 AR5: when d_mmse_equalize, use MMSE equalization (conj(H)·rx/(|H|²+N0))
    //               to bypass Phase 38's 50× noise amplification at Hhdr52 nulls.
    // Parallel computation so the per-SC phase diagnostic dump keeps working.
    {
        float max_h_a = 0.0f;
        if (use_soft_llr) {
            for (int i = 0; i < 48; i++) {
                float hm = std::abs(H52_a[i]);
                if (hm > max_h_a) max_h_a = hm;
            }
            if (max_h_a < 1e-6f) max_h_a = 1e-6f;
        }
        // Phase 46 AR5: precompute MMSE-equalized HT-SIG0 if env var is ON.
        gr_complex eq_mmse_a[48];
        if (n0_percentile > 0) {
            mmse_equalize_htsig(rx52_a, H52_a, eq_mmse_a, n0_percentile);
        }
        for (int i = 0; i < 48; i++) {
            float h_mag = std::abs(H52_a[i]);
            gr_complex eq;
            if (h_mag < 0.001f) {
                eq = gr_complex(0.0f, 0.0f);
            } else if (n0_percentile > 0) {
                eq = eq_mmse_a[i];
            } else {
                eq = safe_div(rx52_a[i], H52_a[i]);
            }
            // QBPSK: HT-SIG is rotated by 90° (mult by j), so bits are on IMAG axis
            // bit 0 → -j (imag < 0), bit 1 → +j (imag >= 0)
            eqbits48_a[i] = (eq.imag() >= 0.0f) ? 1 : 0;
            if (use_soft_llr) {
                float conf = h_mag / max_h_a;
                float s = (eq.imag() >= 0.0f) ? 1.0f : -1.0f;
                llr48_a[i] = s * conf;
            }
        }
    }

    // Phase 20 Task 5: Per-SC phase diagnostic dump. Re-encode HT-SIG0
    // from eqbits48_a[48] (BPSK with QBPSK rotation: bit 1 -> +j, bit 0 -> -j),
    // then estimate per-SC phase from rx52_a / H52 vs re-encoded expected.
    // Opt-in via IEEE80211_HT_PER_SC_PHASE_DUMP=1.
    if (getenv("IEEE80211_HT_PER_SC_PHASE_DUMP")) {
        gr_complex expected_htsig0[52];
        for (int i = 0; i < 48; i++) {
            // QBPSK: bit on IMAG axis. Re-encode to a unit vector.
            expected_htsig0[i] = gr_complex(0.0f, (eqbits48_a[i] ? 1.0f : -1.0f));
        }
        // Pilot SCs (i=48..51): assume +j (placeholder; will refine if needed)
        for (int i = 48; i < 52; i++) {
            expected_htsig0[i] = gr_complex(0.0f, 1.0f);
        }
        // Compute equalized HT-SIG0 symbols (rx52_a / H52)
        gr_complex eq_htsig0[52];
        for (int i = 0; i < 52; i++) {
            float h_mag = std::abs(H52_a[i]);
            if (h_mag < 0.001f) {
                eq_htsig0[i] = gr_complex(0.0f, 0.0f);
            } else {
                eq_htsig0[i] = safe_div(rx52_a[i], H52_a[i]);
            }
        }
        // Estimate per-SC phase
        float phase_per_sc[52];
        int valid_sc = estimate_per_sc_phase_from_htsig0(
            eq_htsig0, expected_htsig0, phase_per_sc);
        // Atomic dump (single snprintf + USRP_LOG)
        char buf[1024];
        int n = snprintf(buf, sizeof(buf),
                         "[HT_PER_SC_PHASE] inv_a=%d inv_b=%d valid_sc=%d phase=[",
                         invert_a ? 1 : 0, invert_b ? 1 : 0, valid_sc);
        for (int i = 0; i < 52 && n < (int)sizeof(buf); i++) {
            n += snprintf(buf + n, sizeof(buf) - n, "%.3f,", phase_per_sc[i]);
        }
        snprintf(buf + n, sizeof(buf) - n, "]\n");
        USRP_LOG("%s", buf);
    }

    // Phase 19 Task 7: per-symbol CPE for HT-SIG1.
    // Hypothesis (from Phase 19 Task 6): CFO/SFO drift between HT-SIG0 (counter=2)
    // and HT-SIG1 (counter=3, 4 µs later) leaves HT-SIG1 with a residual phase
    // offset that breaks viterbi convergence. HT-SIG0 is stable across frames
    // (4 distinct values) but HT-SIG1 varies (4 distinct values), and all 128
    // frames share the same 8 distinct enc96 patterns — consistent with same
    // TX content but a consistent RX-side rotation per (inv_a, inv_b) trial.
    //
    // Fix: estimate residual phase from HT-SIG0's 4 pilots (indices 48-51 in
    // the 52-element rx52 layout, corresponding to subcarriers -21, -7, +7, +21
    // per the 802.11n spec), then apply the OPPOSITE rotation to HT-SIG1's
    // 48 data symbols before extracting bits. The QBPSK rotation is already
    // baked into the data (bits live on the IMAG axis), so we use only the
    // sign of the imag component as the per-pilot reference: normalize each
    // equalized pilot to +j (a unit vector along +imag axis) so the
    // argument of their average gives the residual phase estimate.
    //
    // rx52_b is const, so we store the rotation as a scalar cpe_rot_b and
    // multiply by it inside the eq = rx/H computation for HT-SIG1.
    //
    // Opt-in via IEEE80211_HT_PER_SYMBOL_CPE=1.
    gr_complex cpe_rot_b(1.0f, 0.0f);  // identity by default
    if (getenv("IEEE80211_HT_PER_SYMBOL_CPE")) {
        const int pilot_sc[4] = {48, 49, 50, 51};  // -21, -7, +7, +21
        gr_complex pilot_sum(0.0f, 0.0f);
        int n_pilots = 0;
        for (int p = 0; p < 4; p++) {
            int sc = pilot_sc[p];
            float h_mag = std::abs(H52_a[sc]);
            if (h_mag >= 0.001f) {
                gr_complex eq_p = safe_div(rx52_a[sc], H52_a[sc]);
                // QBPSK: pilots are on IMAG axis, so normalize to +j (sign of imag)
                gr_complex ref = gr_complex(0.0f, (eq_p.imag() >= 0.0f) ? 1.0f : -1.0f);
                pilot_sum += eq_p / ref;
                n_pilots++;
            }
        }
        if (n_pilots > 0) {
            // Average residual phase of HT-SIG0 pilots (relative to +j axis)
            float cpe_phase_htsig0 = std::arg(pilot_sum / float(n_pilots));
            // Apply OPPOSITE rotation to HT-SIG1 to compensate
            cpe_rot_b = std::polar(1.0f, -cpe_phase_htsig0);
        }
    }

    // Extract bits from HT-SIG1 (rx52_b)
    // Phase 44: also compute LLR for HT-SIG1 when use_soft_llr.
    // Phase 46 AR5: when d_mmse_equalize, use MMSE equalization (conj(H)·rx/(|H|²+N0))
    //               to bypass Phase 38's 50× noise amplification at Hhdr52 nulls.
    {
        float max_h_b = 0.0f;
        if (use_soft_llr) {
            for (int i = 0; i < 48; i++) {
                float hm = std::abs(H52_b[i]);
                if (hm > max_h_b) max_h_b = hm;
            }
            if (max_h_b < 1e-6f) max_h_b = 1e-6f;
        }
        // Phase 46 AR5: precompute MMSE-equalized HT-SIG1 if env var is ON.
        // NOTE: MMSE must be applied to rx52_b AFTER any upstream rotation
        // (e.g., cpe_rot_b is identity by default since general_work already
        // phase-compensates rx52_b). The current cpe_rot_b path is opt-in via
        // IEEE80211_HT_PER_SYMBOL_CPE and rotates the *equalized* symbol.
        // We follow the same structure: MMSE produces eq48_b, then we
        // post-multiply by cpe_rot_b to stay compatible with that path.
        gr_complex eq_mmse_b[48];
        if (n0_percentile > 0) {
            mmse_equalize_htsig(rx52_b, H52_b, eq_mmse_b, n0_percentile);
        }
        for (int i = 0; i < 48; i++) {
            float h_mag = std::abs(H52_b[i]);
            gr_complex eq;
            if (h_mag < 0.001f) {
                eq = gr_complex(0.0f, 0.0f);
            } else if (n0_percentile > 0) {
                eq = eq_mmse_b[i] * cpe_rot_b;
            } else {
                eq = safe_div(rx52_b[i], H52_b[i]) * cpe_rot_b;
            }
            // QBPSK: HT-SIG is rotated by 90° (mult by j), so bits are on IMAG axis
            eqbits48_b[i] = (eq.imag() >= 0.0f) ? 1 : 0;
            if (use_soft_llr) {
                float conf = h_mag / max_h_b;
                float s = (eq.imag() >= 0.0f) ? 1.0f : -1.0f;
                llr48_b[i] = s * conf;
            }
        }
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

    // Phase 44: if soft-LLR enabled, also deinterleave+invert the soft LLRs
    // and concatenate to enc96_soft (the float array the soft viterbi consumes).
    // Inversion flips the SIGN of the LLR (equivalent to flipping the bit).
    if (use_soft_llr) {
        const float sign_a = invert_a ? -1.0f : 1.0f;
        const float sign_b = invert_b ? -1.0f : 1.0f;
        for (int k = 0; k < 48; k++) {
            const int j = 3 * (k % 16) + k / 16;
            llr_inter_a[k] = llr48_a[j] * sign_a;
        }
        for (int k = 0; k < 48; k++) {
            const int j = 3 * (k % 16) + k / 16;
            llr_inter_b[k] = llr48_b[j] * sign_b;
        }
        for (int i = 0; i < 48; i++) {
            enc96_soft[i]       = llr_inter_a[i];
            enc96_soft[48 + i]  = llr_inter_b[i];
        }
    }

    // Phase 19 Task 1.5: HT-SIG input constellation dump in active decoder.
    if (getenv("IEEE80211_HTSIG_INPUT_DUMP")) {
        char buf[512];
        int n = snprintf(buf, sizeof(buf),
                         "[HTSIG_INPUT_DUMP] inv_a=%d inv_b=%d enc96=",
                         invert_a ? 1 : 0, invert_b ? 1 : 0);
        for (int i = 0; i < 96 && n < (int)sizeof(buf); i++)
            n += snprintf(buf + n, sizeof(buf) - n, "%d", enc96[i]);
        snprintf(buf + n, sizeof(buf) - n, "\n");
        USRP_LOG("%s", buf);
    }

    std::vector<uint8_t> dec48;
    int vit_metric = -1;
    int vit_metric_soft = 0;
    bool vit_ok = false;
    if (use_soft_llr) {
        // Phase 44: soft-LLR viterbi. Branch metric = squared error to
        // expected BPSK constellation; LLR magnitude weights confidence.
        vit_ok = viterbi_decode_133_171_soft(enc96_soft, 96, dec48, &vit_metric_soft);
        // Map Q8.8 metric back to an int (best-effort) for logging parity.
        vit_metric = vit_metric_soft;
    } else {
        vit_ok = viterbi_decode_133_171(enc96, 96, dec48, &vit_metric);
    }
    if (!vit_ok) {
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
            if (getenv("IEEE80211_HT_STRUCT_AUDIT")) {
                char buf[256];
                int n = snprintf(buf, sizeof(buf),
                                 "[HT_STRUCT_AUDIT] fail_reason=%s mcs=%d length=%d "
                                 "bw40=%d rsv=%d%d%d adv_coding=%d tail=0x%02X%02X%02X "
                                 "crc_rx=0x%02X crc_calc=0x%02X dec48=",
                                 "tail_nonzero", mcs, psdu_length, bw40, rsv0, rsv1, rsv2,
                                 adv_coding, decoded_bits[42], decoded_bits[43], decoded_bits[44],
                                 crc_rx, crc_calc);
                for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                    n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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
            for (int j = 0; j < 48 && n < (int)sizeof(buf); j++)
                n += snprintf(buf + n, sizeof(buf) - n, "%d", decoded_bits[j]);
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

// Phase 20: Per-subcarrier phase estimation from HT-SIG0.
// Given the received HT-SIG0 equalized symbols (rx_htsig0) and the
// expected symbols (re-encoded from HT-SIG0 hard-decision bits),
// estimate the per-SC phase error. Skips SCs with near-zero expected
// (DC null at i=0 and i=26) to avoid NaN/Inf.
static int estimate_per_sc_phase_from_htsig0(
    const gr_complex* rx_htsig0,
    const gr_complex* expected_htsig0,
    float* phase_per_sc)
{
    int valid = 0;
    for (int i = 0; i < 52; i++) {
        if (std::abs(expected_htsig0[i]) < 1e-6f) {
            phase_per_sc[i] = 0.0f;
            continue;
        }
        gr_complex ratio = rx_htsig0[i] / expected_htsig0[i];
        phase_per_sc[i] = std::arg(ratio);
        valid++;
    }
    return valid;
}

// Phase 20: Apply per-SC phase correction to HT-SIG1 equalized symbols.
// Rotates each SC by -phase[i] (opposite of the error estimated from HT-SIG0).
// Modifies rx_htsig1 in place.
static void apply_per_sc_phase_correction(
    gr_complex* rx_htsig1,
    const float* phase_per_sc)
{
    for (int i = 0; i < 52; i++) {
        gr_complex rotation = std::polar(1.0f, -phase_per_sc[i]);
        rx_htsig1[i] *= rotation;
    }
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

    // Phase 35: HT-SIG diagnostic dumps.
    // BIN_DUMP and PILOT_DUMP: two layers of the HT-SIG chain (raw FFT
    // bins post-extract/post-rotation; 4 pilot phases). EQ_INPUT_DUMP
    // removed per code review (was no-op duplicate of BIN_DUMP at the
    // counter=4 site). If a distinct layer dump is needed later, add
    // it back at the post-extract pre-rotation site.
    d_log_htsig_bin   = (std::getenv("IEEE80211_HTSIG_BIN_DUMP")   && std::getenv("IEEE80211_HTSIG_BIN_DUMP")[0]   == '1');
    d_log_htsig_pilot = (std::getenv("IEEE80211_HTSIG_PILOT_DUMP") && std::getenv("IEEE80211_HTSIG_PILOT_DUMP")[0] == '1');
    d_log_htsig_eq    = (std::getenv("IEEE80211_HTSIG_EQ_DUMP")    && std::getenv("IEEE80211_HTSIG_EQ_DUMP")[0]    == '1');
    if (d_log_htsig_bin)   std::cout << "[FRAME_EQ] IEEE80211_HTSIG_BIN_DUMP=1\n";
    if (d_log_htsig_pilot) std::cout << "[FRAME_EQ] IEEE80211_HTSIG_PILOT_DUMP=1\n";
    if (d_log_htsig_eq)    std::cout << "[FRAME_EQ] IEEE80211_HTSIG_EQ_DUMP=1 (equalized HT-SIG0/1 constellation ENABLED)\n";

    // Phase 35 Task 7c: per-symbol pilot CPE on HT-SIG0/HT-SIG1.
    // Pilots at bins {48,49,50,51} (SCs {-21,-7,7,21}). Per-symbol,
    // averages arg(pilot) over the 4 pilots and rotates all 52 bins of
    // that symbol by exp(-j*phi). Cancels per-symbol phase drift that
    // Phase 34 δ correction (constant per frame) cannot reach. Default OFF.
    // Enable via IEEE80211_HTSIG_PILOT_CPE=1.
    const char* env_hpc = std::getenv("IEEE80211_HTSIG_PILOT_CPE");
    d_apply_htsig_pilot_cpe = (env_hpc && env_hpc[0] == '1');
    if (d_apply_htsig_pilot_cpe) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_PILOT_CPE=1 (HT-SIG pilot-aided CPE ENABLED)\n";
    }

    // Phase 36: per-SC linear fit on HT-SIG pilots.
    const char* env_hpcps = std::getenv("IEEE80211_HTSIG_PILOT_PERSC");
    d_apply_htsig_pilot_persc = (env_hpcps && env_hpcps[0] == '1');
    if (d_apply_htsig_pilot_persc) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_PILOT_PERSC=1 (HT-SIG per-SC pilot CPE ENABLED)\n";
    }

    // Phase 39: HT-SIG pilot-based H re-estimation. Replaces Hhdr52
    // (L-LTF0-based) for HT-SIG equalization with H_htsig0/1 estimated
    // from each symbol's own 4 pilots. Bypasses L-LTF0 deep nulls.
    // L-SIG remains on Hhdr52 (Phase 34 fix). Default OFF.
    // Enable via IEEE80211_HTSIG_H_REESTIMATE=1.
    const char* env_hhr = std::getenv("IEEE80211_HTSIG_H_REESTIMATE");
    d_apply_htsig_h_reestimate = (env_hhr && env_hhr[0] == '1');
    if (d_apply_htsig_h_reestimate) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_H_REESTIMATE=1 (HT-SIG pilot-based H re-estimation ENABLED)\n";
    }

    // Phase 39: H_htsig dump. Flood-gated to 10 frames. Dumps
    // |H_htsig0|, |H_htsig1|, and ratio |H_htsig|/|Hhdr52| per SC
    // for offline verification. Enable via IEEE80211_HTSIG_H52_DUMP=1.
    const char* env_hhd = std::getenv("IEEE80211_HTSIG_H52_DUMP");
    d_log_htsig_h52 = (env_hhd && env_hhd[0] == '1');
    if (d_log_htsig_h52) {
        std::cout << "[FRAME_EQ] IEEE80211_HTSIG_H52_DUMP=1\n";
    }

    // Phase 31 Task 18 (RC-C L-SIG): L-SIG equalized constellation dump.
    // H52 over-equalization inflates |eq|^2 to ~12.91 and per-SC phase
    // error rotates BPSK constellation, causing viterbi_fail. Dump the
    // 48 raw equalized points (real, imag), |Hhdr52|, arg(Hhdr52), and
    // hard bits per frame to confirm. Atomic snprintf+USRP_LOG prevents
    // sync_short stdout shredding. Default OFF. Enable via
    // IEEE80211_LSIG_EQ_DUMP=1.
    static bool g_lsig_eq_dump_inited = false;
    if (!g_lsig_eq_dump_inited) {
        if (getenv("IEEE80211_LSIG_EQ_DUMP") && getenv("IEEE80211_LSIG_EQ_DUMP")[0] != '\0') {
            fprintf(stderr, "[FRAME_EQUALIZER] IEEE80211_LSIG_EQ_DUMP=1 (L-SIG constellation will be logged, first 10 calls only)\n");
        }
        g_lsig_eq_dump_inited = true;
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

    // Phase 31: receive-side L-LTF timing diagnostic (env-var gated, default OFF).
    // Mirrors the splitter's IEEE80211_LLTF_TIMING_DUMP. At the H52
    // computation site (just before compute_H52_tx_order), logs the
    // absolute FFT-block read position of the current HT-DATA symbol
    // (nread), the implied L-LTF0 and L-LTF1 absolute FFT-block positions
    // (i.e. nread - d_data_start_rel and nread - d_data_start_rel + 1),
    // and the d_sym_idx when H52 is computed. The splitter emits its
    // own [SPLITTER] LTS0 line with its own current_idx; comparing the
    // two values for the same frame reveals whether any timing offset
    // is in the upstream path (splitter) or the equalizer's H52 input.
    // Default OFF. Enable via IEEE80211_LLTF_TIMING_DUMP=1.
    static bool g_eq_lltf_timing_inited = false;
    if (!g_eq_lltf_timing_inited) {
        if (getenv("IEEE80211_LLTF_TIMING_DUMP") && getenv("IEEE80211_LLTF_TIMING_DUMP")[0] != '\0') {
            g_eq_lltf_timing_dump = true;
            fprintf(stderr, "[EQUALIZER] IEEE80211_LLTF_TIMING_DUMP=1 (received LTS0/LTS1 indices logged at H52 compute site)\n");
        }
        g_eq_lltf_timing_inited = true;
    }

    // Phase 34: per-frame sub-sample timing offset (δ) estimation+correction.
    // Discovered via Phase 33b USRP validation: argH[b] = -2π·kScIndex52[b]·δ/64
    // with δ per-frame in [0,1) at 1/64 quantization. Causes 64-PSK residual
    // that breaks L-SIG viterbi. δ is estimated from H52 via linear
    // regression of argH vs SC index, then applied as per-SC phase rotation
    // (retroactively for L-SIG/HT-SIG0, real-time for HT-SIG1+data). Default OFF.
    // Enable via IEEE80211_TIMING_OFFSET_APPLY=1.
    const char* env_toa = std::getenv("IEEE80211_TIMING_OFFSET_APPLY");
    d_apply_timing_offset = (env_toa && env_toa[0] == '1');
    d_log_timing_offset_dump = d_apply_timing_offset;  // coupled: dump only when applying
    if (d_apply_timing_offset) {
        std::cout << "[FRAME_EQ] IEEE80211_TIMING_OFFSET_APPLY=1 (δ estimation+correction ENABLED)\n";
    }

    // Phase 38 Step 2: per-symbol δ drift diagnostic. Independent flag — can
    // be enabled without IEEE80211_TIMING_OFFSET_APPLY to test on the raw
    // (pre-Phase-34) state. Most useful WITH the apply flag to compare
    // per-symbol δ after correction (should all be ~0 if Phase 34 worked).
    const char* env_dps = std::getenv("IEEE80211_DELTA_PER_SYMBOL_DUMP");
    d_log_delta_per_symbol = (env_dps && env_dps[0] == '1');
    if (d_log_delta_per_symbol) {
        std::cout << "[FRAME_EQ] IEEE80211_DELTA_PER_SYMBOL_DUMP=1 (per-symbol δ drift diagnostic ENABLED)\n";
    }

    // Phase 44: soft-LLR viterbi for HT-SIG unblock. When enabled, the
    // HTSIG decoder feeds soft LLRs (sign(eq.imag()) * |H[i]|/max(|H|))
    // to viterbi_decode_133_171_soft instead of hard bits. The branch
    // metric is squared-error distance, so channel-null SCs (|H[i]|~0)
    // contribute ~0 to the path metric. Hypothesis: bypasses Phase 41's
    // 50x noise amplification at Hhdr52 nulls. Default OFF.
    const char* env_sllr = std::getenv("IEEE80211_SOFT_LLR_VITERBI");
    d_use_soft_llr_viterbi = (env_sllr && env_sllr[0] == '1');
    if (d_use_soft_llr_viterbi) {
        std::cout << "[FRAME_EQ] IEEE80211_SOFT_LLR_VITERBI=1 (HT-SIG soft-LLR viterbi ENABLED)\n";
    }

    // Phase 46 AR5: MMSE equalization for HT-SIG. eq = conj(H)·rx / (|H|² + N0).
    // Bypasses Phase 38's 50× noise amplification at Hhdr52 channel nulls by
    // regularizing the denominator with a noise-floor estimate (25th percentile
    // of |H|²). Applied ONLY to HT-SIG0/HT-SIG1 bit extraction; L-SIG and data
    // symbols keep their existing safe_div path (Phase 34 δ correction already
    // unblocked L-SIG on USRP). Default OFF. Enable via
    // IEEE80211_MMSE_EQUALIZE=1.
    const char* env_mmse = std::getenv("IEEE80211_MMSE_EQUALIZE");
    d_mmse_equalize = (env_mmse && env_mmse[0] == '1');
    if (d_mmse_equalize) {
        std::cout << "[FRAME_EQ] IEEE80211_MMSE_EQUALIZE=1 (HT-SIG MMSE equalization ENABLED)\n";
    }
    // Phase 47: N0 percentile. Default 25. Range [1, 49]. 50+ behaves like
    // median and is fragile (Phase 42 REFUTED).
    const char* env_pct = std::getenv("IEEE80211_MMSE_N0_PERCENTILE");
    if (env_pct && env_pct[0] != '\0') {
        int p = std::atoi(env_pct);
        if (p >= 1 && p <= 49) d_mmse_n0_percentile = p;
    }
    if (d_mmse_equalize) {
        std::cout << "[FRAME_EQ] IEEE80211_MMSE_N0_PERCENTILE=" << d_mmse_n0_percentile
                  << " (N0 = " << d_mmse_n0_percentile << "th percentile)\n";
    }

    // Phase 59: H52 null detect + 邻域插值. All default OFF (Phase 33/34/38/41 baseline).
    const char* env_h52ni = std::getenv("IEEE80211_H52_NULL_INTERP");
    d_h52_null_interp_enabled = (env_h52ni && env_h52ni[0] == '1');
    const char* env_h52nt = std::getenv("IEEE80211_H52_NULL_THRESH");
    if (env_h52nt && env_h52nt[0] != '\0') {
        float t = std::atof(env_h52nt);
        if (t > 0.0f && t < 1.0f) d_h52_null_thresh = t;
    }
    const char* env_h52ir = std::getenv("IEEE80211_H52_INTERP_RADIUS");
    if (env_h52ir && env_h52ir[0] != '\0') {
        int r = std::atoi(env_h52ir);
        if (r >= 1 && r <= 5) d_h52_interp_radius = r;
    }
    const char* env_h52nd = std::getenv("IEEE80211_H52_NULL_DUMP");
    d_h52_null_dump_enabled = (env_h52nd && env_h52nd[0] == '1');
    if (d_h52_null_interp_enabled) {
        std::cout << "[FRAME_EQ] IEEE80211_H52_NULL_INTERP=1 (H52 null interp ENABLED, "
                  << "thresh=" << d_h52_null_thresh
                  << ", radius=" << d_h52_interp_radius
                  << ", dump=" << (d_h52_null_dump_enabled ? "ON" : "OFF") << ")\n";
    }

    // Phase 61: combo env var. Enables Phase 60 pre-clean with combo
    // parameters (thresh=0.10, radius=3) AND Phase 35 per-symbol pilot CPE.
    // Single opt-in knob that turns on the 3 sub-flags. Default OFF.
    // Enable via IEEE80211_H52_NULL_COMBO=1.
    const char* env_h52nc = std::getenv("IEEE80211_H52_NULL_COMBO");
    d_h52_null_combo_enabled = (env_h52nc && env_h52nc[0] == '1');
    if (d_h52_null_combo_enabled) {
        // Warn if user explicitly set individual knobs that combo will override.
        // Silent override would mask misconfig (e.g. user spent time tuning
        // IEEE80211_H52_NULL_THRESH only to find combo silently ignored it).
        // Use std::cerr to match project convention for env-var override warnings.
        if (std::getenv("IEEE80211_H52_NULL_THRESH") && std::getenv("IEEE80211_H52_NULL_THRESH")[0] != '\0') {
            std::cerr << "[FRAME_EQ] WARNING: IEEE80211_H52_NULL_COMBO overrides IEEE80211_H52_NULL_THRESH=0.10\n";
        }
        if (std::getenv("IEEE80211_H52_INTERP_RADIUS") && std::getenv("IEEE80211_H52_INTERP_RADIUS")[0] != '\0') {
            std::cerr << "[FRAME_EQ] WARNING: IEEE80211_H52_NULL_COMBO overrides IEEE80211_H52_INTERP_RADIUS=3\n";
        }
        if (std::getenv("IEEE80211_HTSIG_PILOT_CPE") && std::getenv("IEEE80211_HTSIG_PILOT_CPE")[0] == '1') {
            std::cerr << "[FRAME_EQ] WARNING: IEEE80211_H52_NULL_COMBO overrides IEEE80211_HTSIG_PILOT_CPE=1\n";
        }
        d_h52_null_interp_enabled = true;
        d_h52_null_thresh = 0.10f;   // tighter than default 0.15
        d_h52_interp_radius = 3;     // wider than default 2
        d_apply_htsig_pilot_cpe = true;
        std::cout << "[FRAME_EQ] IEEE80211_H52_NULL_COMBO=1 "
                  << "(H52 null interp ENABLED with thresh=0.10 radius=3, "
                  << "AND HT-SIG pilot CPE ENABLED)\n";
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

    // Phase 34: reset per-frame sub-sample timing offset state.
    d_timing_offset_per_frame = 0.0f;
    d_timing_offset_valid = false;

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

            // [LTF_WRITE_PER_FRAME] Phase 68 Task 1: capture d_early_eqsym[] at
            // the WRITE site (line 3736), immediately after extract_header52_from_sym64
            // fills it from sym64, BEFORE any CFO/SFO/delta compensation at lines
            // 3745-3766. Phase 67 T3 (LTF_SOURCE_PER_FRAME_DUMP, line 4286) showed
            // the READ site sees bit-identical d_early_eqsym[kLltf0Rel] and
            // d_early_eqsym[kLltf1Rel] across 8 USRP frames. If the WRITE site
            // also sees bit-identical values, the gating condition (this block)
            // is never re-entered with fresh sym64 — every frame writes the same
            // memory because (a) sym64 aliasing, (b) d_internal_symbol_counter
            // never advances, or (c) the wifi_start/d_in_frame path doesn't
            // reset state, causing the L-LTF extraction to re-fire on stale
            // buffers. If WRITE site VARIES but READ site is IDENTICAL, the bug
            // is downstream (extract_header52_from_sym64 writes to one buffer,
            // but the read uses a different/stale buffer). Sentinel SCs match
            // HHDR52_PER_FRAME / LTF_SOURCE_PER_FRAME formats for direct
            // cross-frame comparison. Opt-in via
            // IEEE80211_LTF_WRITE_PER_FRAME_DUMP=1. Thread-safe snprintf +
            // USRP_LOG per commit e90e3f5. Separate counter so this dump
            // cannot shadow LTF_SOURCE_PER_FRAME. Only emits for counter==0
            // (L-LTF0) and counter==1 (L-LTF1) to keep log size manageable;
            // the bug is specific to L-LTF extraction.
            if ((d_internal_symbol_counter == kLltf0Rel ||
                 d_internal_symbol_counter == kLltf1Rel) &&
                getenv("IEEE80211_LTF_WRITE_PER_FRAME_DUMP") &&
                getenv("IEEE80211_LTF_WRITE_PER_FRAME_DUMP")[0] == '1') {
                static int ltf_write_counter = 0;
                ltf_write_counter++;
                const int rel = d_internal_symbol_counter;
                const gr_complex* w = d_early_eqsym[rel];
                char lwbuf[1024];
                snprintf(lwbuf, sizeof(lwbuf),
                    "[LTF_WRITE_PER_FRAME] cnt=%d frame_sym=%d rel=%d "
                    "in_frame=%d d_have_header=%d "
                    "early[%d][0]=%.3f+%.3fj early[%d][10]=%.3f+%.3fj "
                    "early[%d][20]=%.3f+%.3fj early[%d][30]=%.3f+%.3fj "
                    "early[%d][40]=%.3f+%.3fj "
                    "early[%d][48]=%.3f+%.3fj early[%d][50]=%.3f+%.3fj "
                    "abs_in_off=%llu\n",
                    ltf_write_counter, d_internal_symbol_counter, rel,
                    d_in_frame ? 1 : 0, d_have_header ? 1 : 0,
                    rel, w[0].real(), w[0].imag(),
                    rel, w[10].real(), w[10].imag(),
                    rel, w[20].real(), w[20].imag(),
                    rel, w[30].real(), w[30].imag(),
                    rel, w[40].real(), w[40].imag(),
                    rel, w[48].real(), w[48].imag(),
                    rel, w[50].real(), w[50].imag(),
                    (unsigned long long)abs_in_off);
                USRP_LOG("%s", lwbuf);
            }

            // Apply CFO+SFO compensation to header symbols (L-SIG, HT-SIG0, HT-SIG1).
            // L-LTF0 (counter=0) is the H reference — do NOT compensate it.
            // L-LTF1 (counter=1) is used for CFO/SFO estimation — do NOT compensate it.
            // L-SIG (counter=2), HT-SIG0 (3), HT-SIG1 (4) need compensation.
            // Use d_phase_diff_per_sc[i] which contains CFO + SFO*sc for each subcarrier.
            // This is more accurate than d_cfo_phase_per_symbol alone (which lacks SFO).
            if (d_phase_diff_valid && d_internal_symbol_counter >= kLSigRel) {
                for (int i = 0; i < 52; i++) {
                    float total_phase = d_phase_diff_per_sc[i] * d_internal_symbol_counter;
                    // Phase 34: add δ correction for counter>=4 (HT-SIG1 and data symbols).
                    // δ is per-frame sub-sample timing offset (1/64 sample quantization on USRP).
                    // L-SIG (counter=2) and HT-SIG0 (counter=3) are corrected retroactively
                    // at Hhdr52 compute time (counter=4) by multiplying d_early_eqsym[2,3].
                    if (d_apply_timing_offset && d_timing_offset_valid &&
                        d_internal_symbol_counter >= 4) {
                        total_phase += -2.0f * (float)M_PI * kScIndex52[i] *
                                       d_timing_offset_per_frame / 64.0f *
                                       d_internal_symbol_counter;
                    }
                    gr_complex rot = std::exp(gr_complex(0.0f, -total_phase));
                    d_early_eqsym[d_internal_symbol_counter][i] *= rot;
                }
                USRP_LOG("[HDR_COMP] counter=%d phase[0]=%.4f phase[26]=%.4f delta=%.4f\n",
                         d_internal_symbol_counter,
                         d_phase_diff_per_sc[0] * d_internal_symbol_counter,
                         d_phase_diff_per_sc[26] * d_internal_symbol_counter,
                         d_timing_offset_per_frame);
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
                // (kScIndex52 is declared above for use by Phase 34 δ correction.)
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

                // Phase 47: stash H52 (still in scope here) for the
                // downstream data-symbol MMSE override, which runs after H52
                // leaves scope. Replaces gr::digital ZF output.
                if (d_mmse_equalize) {
                    std::memcpy(d_h52_stash, H52, sizeof(H52));
                    d_h52_stash_valid = true;
                }

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

        // Phase 47: MMSE override for data symbols. Replaces gr::digital
        // ZF equalizer output (raw_eq52) with conj(H)·rx/(|H|²+N0) when env
        // var is ON. H52 was stashed to d_h52_stash before this scope.
        // Approximate rx ≈ raw_eq52 * H52 (since raw_eq52 = rx / H52 from
        // gr::digital ZF). Bypasses the same 50× null-SC noise amplification.
        if (d_mmse_equalize && d_h52_stash_valid) {
            gr_complex rx52_from_eq[52];
            for (int k = 0; k < 52; k++) {
                if (std::abs(d_h52_stash[k]) > 1e-6f) {
                    rx52_from_eq[k] = raw_eq52[k] * d_h52_stash[k];
                } else {
                    rx52_from_eq[k] = gr_complex(0.0f, 0.0f);
                }
            }
            for (int k = 0; k < 48; k++) {
                double h_sq_k = std::norm(d_h52_stash[k]);
                gr_complex denom(h_sq_k + 1e-9, 0.0f);
                raw_eq52[k] = std::conj(d_h52_stash[k]) * rx52_from_eq[k] / denom;
            }
            // Pilots 48-51 untouched (channel tracking).
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
            // [LTF_SOURCE_PER_FRAME] Phase 67 Task 3: localize the Hhdr52
            // frozen-input bug to one of three layers. Phase 67 T2
            // (HHDR52_PER_FRAME_DUMP) showed Hhdr52 is bit-identical across
            // 8 USRP frames, so the freeze is upstream of the median filter
            // at line 4346-4348. This dump captures the 4 candidate L-LTF
            // source values AT THE MOMENT they are read in lines 4278-4284
            // (before estimate_header_channel_from_lltf52() is called at
            // line 4286). Sentinel SCs match the HHDR52_PER_FRAME format for
            // direct cross-frame comparison.
            //
            // Decision tree (Phase 67 T3):
            //   (A) L-LTF source IDENTICAL across frames -> bug is in
            //       sync_short / sync_long / H estimator feed (UPSTREAM of
            //       frame_equalizer; Phase 68 must attack sync_short L-LTF0
            //       FFT window alignment).
            //   (B) L-LTF source VARIES but Hhdr52 IDENTICAL -> bug is in
            //       estimate_header_channel_from_lltf52() (line 4286-4288;
            //       Phase 68 inspects accumulator / cached state).
            //   (C) Both vary -> Phase 67 T1/T2 measurements were
            //       artifacts (early-exit / short-circuit); pivot to LSIG
            //       viterbi candidate search (Phase 67 #1).
            //
            // Also reports d_ltf_compensated_valid[0/1] and
            // d_use_lltf1_for_h so we can detect (i) a stale latched
            // compensated buffer that never re-runs, or (ii) source branch
            // selection drift between frames. Opt-in via
            // IEEE80211_LTF_SOURCE_PER_FRAME_DUMP=1. Thread-safe snprintf +
            // USRP_LOG per commit e90e3f5. Counter separate from
            // HHDR52_PER_FRAME so the two dumps cannot shadow each other.
            if (getenv("IEEE80211_LTF_SOURCE_PER_FRAME_DUMP") &&
                getenv("IEEE80211_LTF_SOURCE_PER_FRAME_DUMP")[0] == '1') {
                static int ltf_src_counter = 0;
                ltf_src_counter++;
                const gr_complex* s0 = d_early_eqsym[kLltf0Rel];
                const gr_complex* s1 = d_early_eqsym[kLltf1Rel];
                const gr_complex* c0 = d_ltf_compensated[0];
                const gr_complex* c1 = d_ltf_compensated[1];
                char lsrcbuf[1024];
                snprintf(lsrcbuf, sizeof(lsrcbuf),
                    "[LTF_SOURCE_PER_FRAME] cnt=%d frame_sym=%d "
                    "use_lltf1=%d ltf_comp_valid=[%d,%d] "
                    "early[0][0]=%.3f+%.3fj early[0][10]=%.3f+%.3fj "
                    "early[0][20]=%.3f+%.3fj early[0][30]=%.3f+%.3fj early[0][40]=%.3f+%.3fj "
                    "early[1][0]=%.3f+%.3fj early[1][10]=%.3f+%.3fj "
                    "early[1][20]=%.3f+%.3fj early[1][30]=%.3f+%.3fj early[1][40]=%.3f+%.3fj "
                    "comp[0][0]=%.3f+%.3fj comp[0][10]=%.3f+%.3fj "
                    "comp[0][20]=%.3f+%.3fj comp[0][30]=%.3f+%.3fj comp[0][40]=%.3f+%.3fj "
                    "comp[1][0]=%.3f+%.3fj comp[1][10]=%.3f+%.3fj "
                    "comp[1][20]=%.3f+%.3fj comp[1][30]=%.3f+%.3fj comp[1][40]=%.3f+%.3fj\n",
                    ltf_src_counter, d_internal_symbol_counter,
                    d_use_lltf1_for_h ? 1 : 0,
                    d_ltf_compensated_valid[0] ? 1 : 0,
                    d_ltf_compensated_valid[1] ? 1 : 0,
                    s0[0].real(), s0[0].imag(),
                    s0[10].real(), s0[10].imag(),
                    s0[20].real(), s0[20].imag(),
                    s0[30].real(), s0[30].imag(),
                    s0[40].real(), s0[40].imag(),
                    s1[0].real(), s1[0].imag(),
                    s1[10].real(), s1[10].imag(),
                    s1[20].real(), s1[20].imag(),
                    s1[30].real(), s1[30].imag(),
                    s1[40].real(), s1[40].imag(),
                    c0[0].real(), c0[0].imag(),
                    c0[10].real(), c0[10].imag(),
                    c0[20].real(), c0[20].imag(),
                    c0[30].real(), c0[30].imag(),
                    c0[40].real(), c0[40].imag(),
                    c1[0].real(), c1[0].imag(),
                    c1[10].real(), c1[10].imag(),
                    c1[20].real(), c1[20].imag(),
                    c1[30].real(), c1[30].imag(),
                    c1[40].real(), c1[40].imag());
                USRP_LOG("%s", lsrcbuf);
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

            // [HHDR52_PER_FRAME] Phase 67 Task 2: trace Hhdr52 from allocation
            // (line 4275) to detect_h52_nulls input (line 4453). Phase 66
            // found n_nulls=24/52 frozen across 8 frames; H52_NULL_DUMP shows
            // bit-identical |H| and argH. Either (a) Hhdr52 is genuinely
            // frozen upstream, (b) detect_h52_nulls is frozen. This dump
            // distinguishes them by printing 5 sentinel SCs per frame. If
            // sentinel values vary frame-to-frame -> Hhdr52 healthy, bug is
            // in detect_h52_nulls. If sentinels are bit-identical -> bug is
            // upstream of the dump (extractor pathway / cached L-LTF / static
            // routing). Opt-in via IEEE80211_HHDR52_PER_FRAME_DUMP=1. Inserts
            // AFTER the median filter (line 4346-4348) so the printed Hhdr52
            // matches what detect_h52_nulls will see. Thread-safe snprintf
            // + USRP_LOG per commit e90e3f5.
            if (getenv("IEEE80211_HHDR52_PER_FRAME_DUMP") &&
                getenv("IEEE80211_HHDR52_PER_FRAME_DUMP")[0] == '1') {
                char hdrbuf[512];
                snprintf(hdrbuf, sizeof(hdrbuf),
                    "[HHDR52_PER_FRAME] frame_sym=%d "
                    "H[0]=%.3f+%.3fj H[10]=%.3f+%.3fj H[20]=%.3f+%.3fj "
                    "H[30]=%.3f+%.3fj H[40]=%.3f+%.3fj\n",
                    d_internal_symbol_counter,
                    Hhdr52[0].real(), Hhdr52[0].imag(),
                    Hhdr52[10].real(), Hhdr52[10].imag(),
                    Hhdr52[20].real(), Hhdr52[20].imag(),
                    Hhdr52[30].real(), Hhdr52[30].imag(),
                    Hhdr52[40].real(), Hhdr52[40].imag());
                USRP_LOG("%s", hdrbuf);
            }

            // Phase 34: estimate per-frame sub-sample timing offset δ from Hhdr52
            // (final H used for L-SIG/HT-SIG viterbi) and apply retroactive
            // correction to d_early_eqsym[c] for c = kLSigRel..kHtSig1Rel.
            // argH[b] ≈ -2π·kScIndex52[b]·δ/64 → linear regression gives δ.
            // Multiplies each SC's d_early_eqsym[c] by exp(+j·2π·δ·SC/64),
            // removing the δ_phase factor that d_early_eqsym[c] picked up at
            // its sample time. After this, eq = d_early_eqsym / Hhdr52 has the
            // δ factor cancelled in both numerator and denominator (assuming
            // constant per-frame δ).
            if (d_apply_timing_offset) {
                float delta = estimate_timing_offset_from_h52(Hhdr52);
                d_timing_offset_per_frame = delta;
                d_timing_offset_valid = true;

                // Retroactive correction to L-SIG (counter=2), HT-SIG0 (counter=3),
                // HT-SIG1 (counter=4). All three have already had CFO+SFO rotation
                // applied at line 3034-3039. We are removing the δ contribution.
                for (int sym = kLSigRel; sym <= kHtSig1Rel; sym++) {
                    if (!d_early_eqsym_valid[sym]) continue;
                    for (int i = 0; i < 52; i++) {
                        float delta_phase = (float)(2.0 * M_PI) *
                                            kScIndex52[i] * delta / 64.0f;
                        d_early_eqsym[sym][i] *= std::exp(gr_complex(0.0f, delta_phase));
                    }
                }

                // Diagnostic dump (flood-gated to first 10 frames, atomic snprintf).
                if (d_log_timing_offset_dump) {
                    static int g_delta_dump_counter = 0;
                    if (g_delta_dump_counter < 10) {
                        char delta_buf[256];
                        snprintf(delta_buf, sizeof(delta_buf),
                                 "[DELTA_DUMP] counter=%d delta=%.4f (k/64=%d) "
                                 "|H|mean=%.3f valid_lsig=%d valid_htsig0=%d valid_htsig1=%d\n",
                                 d_internal_symbol_counter, delta,
                                 (int)std::round(delta * 64.0f), std::abs(Hhdr52[26]),
                                 d_early_eqsym_valid[kLSigRel] ? 1 : 0,
                                 d_early_eqsym_valid[kHtSig0Rel] ? 1 : 0,
                                 d_early_eqsym_valid[kHtSig1Rel] ? 1 : 0);
                        USRP_LOG("%s", delta_buf);
                        g_delta_dump_counter++;
                    }
                }
            }

            // Phase 60: pre-clean Hhdr52 BEFORE HT-SIG equalization. Phase 59's call
            // site (line 5058) is gated by d_is_ht=true, which never sets on USRP
            // because HT-SIG viterbi fails first. This call site runs inside the
            // ht_parse_condition block (already validated), so it fires for all
            // frames reaching HT-SIG. Breaks the deadlock: previously HT-SIG needed
            // to succeed to run null detection; now null detection helps HT-SIG
            // succeed. Phase 34 δ correction has already run above, so δ estimation
            // is preserved.
            if (d_h52_null_interp_enabled) {
                auto nulls = detect_h52_nulls(Hhdr52, d_h52_null_thresh);
                if (d_h52_null_dump_enabled) {
                    // 2048-byte buffer (per Phase 59 fix) holds 52 entries + header
                    char buf[2048];
                    int off = snprintf(buf, sizeof(buf),
                        "[H60_NULL] n_nulls=%zu/%d thresh=%.3f radius=%d\n",
                        nulls.size(), 52, d_h52_null_thresh, d_h52_interp_radius);
                    for (size_t i = 0; i < nulls.size() && off < (int)sizeof(buf) - 32; i++) {
                        off += snprintf(buf + off, sizeof(buf) - off,
                            "  [%d] |H|=%.3f arg=%.3f\n",
                            nulls[i], std::abs(Hhdr52[nulls[i]]),
                            std::arg(Hhdr52[nulls[i]]));
                    }
                    USRP_LOG("%s", buf);
                }
                // Phase 67 Task 1: per-frame n_nulls disambiguation. Phase 66 found
                // n_nulls frozen at 24/52 across all 8 USRP frames — suspiciously
                // uniform. Two hypotheses: (a) channel static for 35s same-board
                // cable run, (b) frozen counter bug or detect_h52_nulls() returning
                // a constant dummy. This ONE-LINE dump per frame (independent of
                // H60_NULL above) lets us count distinct n_nulls values across
                // frames. If all identical -> (b) likely. If varied -> (a). Opt-in.
                if (getenv("IEEE80211_H60_NULL_PER_FRAME_DUMP") &&
                    getenv("IEEE80211_H60_NULL_PER_FRAME_DUMP")[0] == '1') {
                    char h60pbuf[256];
                    snprintf(h60pbuf, sizeof(h60pbuf),
                        "[H60_NULL_PER_FRAME] frame_sym=%d n_nulls=%zu/%d "
                        "thresh=%.3f radius=%d is_ht=%d\n",
                        d_internal_symbol_counter, nulls.size(), 52,
                        d_h52_null_thresh, d_h52_interp_radius,
                        d_is_ht ? 1 : 0);
                    USRP_LOG("%s", h60pbuf);
                }
                interp_h52_nulls(Hhdr52, nulls, d_h52_interp_radius);
            }

            // Phase 38 Step 2: per-symbol δ drift diagnostic. Estimate δ
            // independently from each symbol's 4 pilots (SCs {-21,-7,7,21},
            // bins {48,49,50,51}). argH_pilot[sc] ≈ -2π·sc·δ/64 + residual.
            // Linear regression gives per-symbol δ. Runs at counter=4 (HT-SIG1
            // time, after Phase 34 retroactive correction is applied to all
            // three header symbols).
            //
            // Interpretation:
            //   - If Phase 34 worked: per-symbol δ ≈ 0 for all three symbols,
            //     confirming constant-per-frame δ is the dominant model.
            //   - If per-symbol δ varies significantly across LSIG/HTSIG0/
            //     HTSIG1 (>0.1 = 1/10 of 1/64 grid), per-symbol drift exists
            //     and Phase 34's constant-per-frame model is insufficient.
            //
            // Atomic snprintf+USRP_LOG prevents sync_short stdout shredding
            // (Phase 9 lesson). Flood-gated to 10 frames to keep log small.
            if (d_log_delta_per_symbol) {
                static int g_delta_per_symbol_counter = 0;
                if (g_delta_per_symbol_counter < 10) {
                    const int pilot_scs[4]  = {-21, -7, 7, 21};
                    const int pilot_bins[4] = {48, 49, 50, 51};
                    const int syms[3]       = {kLSigRel, kHtSig0Rel, kHtSig1Rel};
                    const char* sym_n[3]    = {"LSIG", "HTSIG0", "HTSIG1"};

                    char dps_buf[512];
                    int off = snprintf(dps_buf, sizeof(dps_buf),
                                       "[DELTA_PER_SYMBOL] sym=%d H52_delta=%.4f",
                                       d_internal_symbol_counter,
                                       d_timing_offset_per_frame);

                    for (int s = 0; s < 3; s++) {
                        int sym = syms[s];
                        if (!d_early_eqsym_valid[sym]) {
                            off += snprintf(dps_buf + off, sizeof(dps_buf) - off,
                                            " %s_pilot=NA", sym_n[s]);
                            continue;
                        }
                        // Weighted linear regression of arg(d_early_eqsym[sym][pilot_bin])
                        // vs pilot SC index. Same algorithm as Phase 34 δ estimator.
                        double sum_sc = 0.0, sum_sc2 = 0.0, sum_arg = 0.0, sum_sc_arg = 0.0;
                        double sum_w = 0.0;
                        for (int i = 0; i < 4; i++) {
                            float a = std::arg(d_early_eqsym[sym][pilot_bins[i]]);
                            int sc = pilot_scs[i];
                            float w = std::abs(d_early_eqsym[sym][pilot_bins[i]]);
                            sum_sc     += (double)sc * w;
                            sum_sc2    += (double)sc * sc * w;
                            sum_arg    += a * w;
                            sum_sc_arg += (double)sc * a * w;
                            sum_w      += w;
                        }
                        if (sum_w < 1e-9) {
                            off += snprintf(dps_buf + off, sizeof(dps_buf) - off,
                                            " %s_pilot=NA", sym_n[s]);
                            continue;
                        }
                        double mean_sc = sum_sc / sum_w;
                        double mean_arg = sum_arg / sum_w;
                        double cov = 0.0, var = 0.0;
                        for (int i = 0; i < 4; i++) {
                            float a = std::arg(d_early_eqsym[sym][pilot_bins[i]]);
                            int sc = pilot_scs[i];
                            float w = std::abs(d_early_eqsym[sym][pilot_bins[i]]);
                            double dsc = sc - mean_sc;
                            cov += w * dsc * (a - mean_arg);
                            var += w * dsc * dsc;
                        }
                        if (var < 1e-9) {
                            off += snprintf(dps_buf + off, sizeof(dps_buf) - off,
                                            " %s_pilot=NA", sym_n[s]);
                            continue;
                        }
                        double b = cov / var;
                        float delta_pilot = (float)(-b * 64.0 / (2.0 * M_PI));
                        delta_pilot = delta_pilot - std::floor(delta_pilot);
                        // Also dump mean pilot arg (residual constant phase) and
                        // mean |bin| (signal quality).
                        double mean_arg_pilot = sum_arg / sum_w;
                        double mean_mag_pilot = sum_w / 4.0;
                        off += snprintf(dps_buf + off, sizeof(dps_buf) - off,
                                        " %s_delta=%.4f phi=%.3f |bin|=%.2f",
                                        sym_n[s], delta_pilot,
                                        mean_arg_pilot, mean_mag_pilot);
                    }
                    off += snprintf(dps_buf + off, sizeof(dps_buf) - off, "\n");
                    USRP_LOG("%s", dps_buf);
                    g_delta_per_symbol_counter++;
                }
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
            // Phase 66: per-candidate HT-SIG viterbi diagnostic (opt-in).
            // Track best (lowest) metric across all 16 candidates so a
            // summary line can show whether ALL candidates are saturated
            // (random-like, equalizer still the wall) vs only some fail.
            int  htsig_best_metric     = INT_MAX;
            int  htsig_best_rot        = -1;
            int  htsig_best_inv_a      = -1;
            int  htsig_best_inv_b      = -1;
            const char* htsig_best_fail = "none";

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

            // Phase 70: 8-candidate L-SIG viterbi search.
            // When IEEE80211_LSIG_VITERBI_CANDIDATE=1, try 4 phase rotations
            // × 2 inversions = 8 candidates. Pick the one with the lowest
            // structural-validity cost (HT_SIG_CAND pattern from Phase 66).
            // Default OFF: only the existing 2-attempt (inv=0,1) loop runs.
            const int n_rot = (getenv("IEEE80211_LSIG_VITERBI_CANDIDATE") &&
                               getenv("IEEE80211_LSIG_VITERBI_CANDIDATE")[0] != '\0') ? 4 : 1;
            int lsig_best_metric = INT_MAX;
            int lsig_best_rot = -1;
            int lsig_best_inv = -1;
            int lsig_best_enc = -1;
            int lsig_best_len = 0;
            int lsig_best_rate_field = -1;
            int lsig_best_parity_ok = -1;
            // Phase 70 T3: guard against the post-loop promotion block
            // re-firing after `goto lsig_body_entry;` returns control to
            // the body and control falls back through here. Without this
            // flag, the promotion block (and its goto) would repeat,
            // yielding an infinite loop. Set true BEFORE the goto and
            // checked as part of the condition below.
            bool lsig_promoted = false;

            // L-SIG invert brute-force (with optional rot candidate expansion)
            // Phase 70: declare local variables and loop indices in outer
            // scope so the goto (lsig_body_entry) after candidate promotion
            // can land in the body without jumping over their initialization.
            int lsig_enc = -1;
            int lsig_len = 0;
            int lsig_rate_field = -1;
            int lsig_parity_ok_int = -1;
            bool lsig_ok = false;
            int rot_lsig = 0;
            int inv_lsig = 0;
            for (rot_lsig = 0; rot_lsig < n_rot && !found; rot_lsig++) {
              for (inv_lsig = 0; inv_lsig <= 1 && !found; inv_lsig++) {

                lsig_ok = decode_lsig_direct_from_header52(d_early_eqsym[kLSigRel],
                                                                 Hhdr52,
                                                                 inv_lsig != 0,
                                                                 lsig_enc,
                                                                 lsig_len,
                                                                 &lsig_rate_field,
                                                                 nullptr,         // out_psdu_length: not needed (already in lsig_len)
                                                                 &lsig_parity_ok_int,
                                                                 nullptr,
                                                                 nullptr,
                                                                 /* rot_idx = */ rot_lsig);
                if (lsig_ok) {
                    lsig_decode_calls++;
                    lsig_last_inv       = inv_lsig;
                    lsig_last_rate      = lsig_rate_field;
                    lsig_last_len       = lsig_len;
                    lsig_last_parity_ok = lsig_parity_ok_int;
                    // Phase 70: in candidate-search mode, only accept the
                    // best (lowest-metric) candidate across all rot×inv combos.
                    if (n_rot > 1) {
                        // Estimate metric from decoded bits: structural validity cost
                        // (rate must be 0xD, length > 0, parity must match, tail must be 0).
                        // The viterbi's actual branch metric isn't exposed, so we use
                        // structural-validity cost as a proxy. Lower = better.
                        int approx_metric = 0;
                        if (lsig_rate_field != 0xD) approx_metric += 8;
                        if (lsig_len <= 0 || lsig_len > 4096) approx_metric += 4;
                        if (lsig_parity_ok_int != 1) approx_metric += 4;
                        if (approx_metric < lsig_best_metric) {
                            lsig_best_metric = approx_metric;
                            lsig_best_rot = rot_lsig;
                            lsig_best_inv = inv_lsig;
                            lsig_best_enc = lsig_enc;
                            lsig_best_len = lsig_len;
                            lsig_best_rate_field = lsig_rate_field;
                            lsig_best_parity_ok = lsig_parity_ok_int;
                        }
                        // Don't accept yet — wait for all candidates to be tried.
                        // Fall through to the !lsig_ok check below to skip to next candidate.
                    }
                } else {
                    // viterbi-decode failure means we never extracted rate/len/parity
                    // (we still want to distinguish viterbi fail from "rate/length wrong")
                    lsig_viterbi_fail_inv = inv_lsig;
                    lsig_saw_viterbi_fail = true;
                }

                if (!lsig_ok) {
                    continue;
                }

                // Phase 70: in candidate-search mode, don't run the HT-SIG
                // body on the current candidate. We have not yet tried all
                // (rot, inv) pairs, so we cannot pick the best L-SIG candidate
                // yet. The body is replayed once with the winning candidate
                // after the outer rot_lsig loop closes (see goto below).
                if (n_rot > 1) {
                    continue;
                }

            lsig_body_entry:  // Phase 70: target of goto after candidate promotion.
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

                // Phase 35 Task 7c: per-symbol pilot-aided CPE on HT-SIG0/HT-SIG1.
                // 4 pilots at bins {48,49,50,51} map to SCs {-21,-7,7,21}.
                // Average their phase per symbol, then rotate that symbol's
                // 52 bins by exp(-j*phi). Cancels per-symbol phase drift
                // that δ correction (constant per frame) cannot reach.
                // Gated on |bin| > 1e-3 to avoid NaN from arg(0). Applied
                // BEFORE the diagnostic dump and the rotation block so that
                // both downstream consumers see the corrected state.
                if (d_apply_htsig_pilot_cpe &&
                    d_early_eqsym_valid[kHtSig0Rel] && d_early_eqsym_valid[kHtSig1Rel]) {
                    static const int kPilotBins[4] = {48, 49, 50, 51};
                    auto apply_pilot_cpe = [](gr_complex* eqsym52, const int* pilot_bins) {
                        double sum_arg = 0.0;
                        int n_valid = 0;
                        for (int p = 0; p < 4; p++) {
                            const gr_complex& c = eqsym52[pilot_bins[p]];
                            if (std::abs(c) > 1e-3f) {
                                sum_arg += std::arg(c);
                                n_valid++;
                            }
                        }
                        if (n_valid == 0) return;  // skip if all pilots are null
                        float phi = (float)(sum_arg / n_valid);
                        gr_complex rot = std::exp(gr_complex(0.0f, -phi));
                        for (int i = 0; i < 52; i++) {
                            eqsym52[i] *= rot;
                        }
                    };
                    apply_pilot_cpe(d_early_eqsym[kHtSig0Rel], kPilotBins);
                    apply_pilot_cpe(d_early_eqsym[kHtSig1Rel], kPilotBins);
                    USRP_LOG("[HTSIG_PILOT_CPE] applied per-symbol rotation\n");
                }

                // Phase 36: per-SC linear fit on HT-SIG pilots. Captures per-SC
                // phase variation that the per-symbol MEAN (above) cannot reach.
                // For each symbol, recover (a, b) such that channel_phase(sc) ≈
                // a + b·sc, then apply exp(-j·(a + b·sc)) per bin. Uses the same
                // Hhdr52 channel estimate as the viterbi path. Inserted BEFORE
                // the diagnostic dump so dumps show post-CPE state.
                if (d_apply_htsig_pilot_persc &&
                    d_early_eqsym_valid[kHtSig0Rel] && d_early_eqsym_valid[kHtSig1Rel]) {
                    auto apply_persc = [&](gr_complex* eqsym52, int sym_idx) {
                        float a = 0.0f, b = 0.0f;
                        if (!estimate_htsig_pilot_persc(eqsym52, Hhdr52, sym_idx, a, b)) {
                            return;  // not enough valid pilots, skip
                        }
                        // Defensive: if helper returned true but a/b are not finite, skip
                        // (could happen if upstream arg() returns NaN despite magnitude gate).
                        if (!std::isfinite(a) || !std::isfinite(b)) {
                            return;
                        }
                        // Apply per-SC rotation: exp(-j·(a + b·sc_index)) for each bin.
                        for (int i = 0; i < 52; i++) {
                            const float ph = a + b * (float)kScIndex52[i];
                            eqsym52[i] *= std::exp(gr_complex(0.0f, -ph));
                        }
                        // Atomic diagnostic (single snprintf + USRP_LOG to avoid
                        // sync_short stdout shredding, per Phase 9 lesson).
                        char pcbuf[256];
                        snprintf(pcbuf, sizeof(pcbuf),
                                 "[HTSIG_PILOT_PERSC] sym=%d a=%.4f b=%.6f sc_range=[%d,%d]\n",
                                 sym_idx, a, b, kScIndex52[0], kScIndex52[51]);
                        USRP_LOG("%s", pcbuf);
                    };
                    apply_persc(d_early_eqsym[kHtSig0Rel], /*sym_idx*/ 0);
                    apply_persc(d_early_eqsym[kHtSig1Rel], /*sym_idx*/ 1);
                }

                // Phase 35: HT-SIG diagnostic dumps (flood-gated to first 10 frames).
                // Fires at counter=4 (HT-SIG1 captured) AFTER CFO+SFO+delta correction
                // has been applied to d_early_eqsym[3] and d_early_eqsym[4].
                // Dumps pre-rotation FFT bins and pilot phases for layer diagnosis.
                if (d_internal_symbol_counter == 4 && (d_log_htsig_bin || d_log_htsig_pilot || d_log_htsig_eq)) {
                    static int g_htsig_dump_counter = 0;
                    if (g_htsig_dump_counter < 10) {
                        if (d_log_htsig_bin && d_early_eqsym_valid[3] && d_early_eqsym_valid[4]) {
                            char htbuf[4096];
                            int n = 0;
                            n += snprintf(htbuf + n, sizeof(htbuf) - n,
                                "[HTSIG_BIN_DUMP] counter=4 frame=%d htsig0=[", g_htsig_dump_counter);
                            for (int i = 0; i < 52; i++) {
                                n += snprintf(htbuf + n, sizeof(htbuf) - n, "%.3f%+.3fi%c",
                                    d_early_eqsym[3][i].real(),
                                    d_early_eqsym[3][i].imag(),
                                    (i < 51) ? ',' : ']');
                            }
                            n += snprintf(htbuf + n, sizeof(htbuf) - n, " htsig1=[");
                            for (int i = 0; i < 52; i++) {
                                n += snprintf(htbuf + n, sizeof(htbuf) - n, "%.3f%+.3fi%c",
                                    d_early_eqsym[4][i].real(),
                                    d_early_eqsym[4][i].imag(),
                                    (i < 51) ? ',' : ']');
                            }
                            USRP_LOG("%s\n", htbuf);
                        }
                        if (d_log_htsig_pilot && d_early_eqsym_valid[3] && d_early_eqsym_valid[4]) {
                            // Phase 35 review fix (Issue 1): guard NaN risk on
                            // std::arg(0). On USRP, |H| at pilots can drop to
                            // 0.02-0.13 (Phase 31b); atan2(0,0) is
                            // implementation-defined and may yield NaN, which
                            // chokes the Python parser. Output NaN sentinel
                            // when |H| < 1e-3.
                            auto safe_arg = [](const gr_complex& c) -> float {
                                return (std::abs(c) > 1e-3f) ? std::arg(c)
                                                             : std::numeric_limits<float>::quiet_NaN();
                            };
                            char pbuf[256];
                            snprintf(pbuf, sizeof(pbuf),
                                "[HTSIG_PILOT_DUMP] counter=4 frame=%d "
                                "htsig0_pilots=arg[%.3f,%.3f,%.3f,%.3f] "
                                "htsig1_pilots=arg[%.3f,%.3f,%.3f,%.3f]\n",
                                g_htsig_dump_counter,
                                safe_arg(d_early_eqsym[3][48]), safe_arg(d_early_eqsym[3][49]),
                                safe_arg(d_early_eqsym[3][50]), safe_arg(d_early_eqsym[3][51]),
                                safe_arg(d_early_eqsym[4][48]), safe_arg(d_early_eqsym[4][49]),
                                safe_arg(d_early_eqsym[4][50]), safe_arg(d_early_eqsym[4][51]));
                            USRP_LOG("%s", pbuf);
                        }
                        // Phase 35 review fix (Issue 2): EQ_INPUT_DUMP removed.
                        // At counter=4, d_early_eqsym[3,4] already have
                        // CFO+SFO+delta applied, so dumping here would
                        // duplicate BIN_DUMP and waste the 10-frame
                        // flood-gate budget. If a distinct layer dump is
                        // needed later, add it at the post-extract
                        // pre-rotation site (different memory, distinct
                        // dump semantics).
                        // Phase 38 Step 7: NEW equalized-constellation dump.
                        // Computes eq = d_early_eqsym / Hhdr52 for all 52
                        // subcarriers of HT-SIG0 and HT-SIG1 and prints
                        // them. If equalization is correct, expect QBPSK
                        // clusters on the IMAG axis (±j) for data SCs and
                        // pilots {j,j,j,-j}. If scatter, equalization is
                        // broken. Counter still tied to 10-frame budget.
                        if (d_log_htsig_eq) {
                            char eqbuf[8192];
                            int n = 0;
                            // HT-SIG0 data SCs (0..47) and pilots (48..51)
                            n += snprintf(eqbuf + n, sizeof(eqbuf) - n,
                                "[HTSIG_EQ_DUMP] frame=%d htsig0_eq=[",
                                g_htsig_dump_counter);
                            for (int i = 0; i < 52; i++) {
                                gr_complex h = Hhdr52[i];
                                gr_complex eq;
                                if (std::abs(h) < 1e-3f) {
                                    eq = gr_complex(std::numeric_limits<float>::quiet_NaN(),
                                                     std::numeric_limits<float>::quiet_NaN());
                                } else {
                                    eq = d_early_eqsym[3][i] / h;
                                }
                                n += snprintf(eqbuf + n, sizeof(eqbuf) - n,
                                    "%.3f%+.3fi%c",
                                    eq.real(), eq.imag(),
                                    (i < 51) ? ',' : ']');
                            }
                            n += snprintf(eqbuf + n, sizeof(eqbuf) - n,
                                " htsig1_eq=[");
                            for (int i = 0; i < 52; i++) {
                                gr_complex h = Hhdr52[i];
                                gr_complex eq;
                                if (std::abs(h) < 1e-3f) {
                                    eq = gr_complex(std::numeric_limits<float>::quiet_NaN(),
                                                     std::numeric_limits<float>::quiet_NaN());
                                } else {
                                    eq = d_early_eqsym[4][i] / h;
                                }
                                n += snprintf(eqbuf + n, sizeof(eqbuf) - n,
                                    "%.3f%+.3fi%c",
                                    eq.real(), eq.imag(),
                                    (i < 51) ? ',' : ']');
                            }
                            // Summary: mean imag, std imag, |real|/|imag| ratio
                            // for the 48 data SCs of each symbol.
                            // QBPSK should give |real|/|imag| < 0.3.
                            double sum_im_a = 0, sum_im2_a = 0, sum_re_a = 0;
                            double sum_im_b = 0, sum_im2_b = 0, sum_re_b = 0;
                            int cnt = 0;
                            for (int i = 0; i < 48; i++) {
                                gr_complex h = Hhdr52[i];
                                if (std::abs(h) < 1e-3f) continue;
                                gr_complex ea = d_early_eqsym[3][i] / h;
                                gr_complex eb = d_early_eqsym[4][i] / h;
                                sum_re_a += std::abs(ea.real());
                                sum_im_a += ea.imag();
                                sum_im2_a += (double)ea.imag() * ea.imag();
                                sum_re_b += std::abs(eb.real());
                                sum_im_b += eb.imag();
                                sum_im2_b += (double)eb.imag() * eb.imag();
                                cnt++;
                            }
                            if (cnt > 0) {
                                double mean_re_a = sum_re_a / cnt;
                                double mean_im_a = sum_im_a / cnt;
                                double var_im_a = sum_im2_a / cnt - mean_im_a * mean_im_a;
                                double std_im_a = (var_im_a > 0) ? std::sqrt(var_im_a) : 0.0;
                                double mean_re_b = sum_re_b / cnt;
                                double mean_im_b = sum_im_b / cnt;
                                double var_im_b = sum_im2_b / cnt - mean_im_b * mean_im_b;
                                double std_im_b = (var_im_b > 0) ? std::sqrt(var_im_b) : 0.0;
                                n += snprintf(eqbuf + n, sizeof(eqbuf) - n,
                                    " htsig0 mean|re|=%.3f mean_im=%.3f std_im=%.3f"
                                    " htsig1 mean|re|=%.3f mean_im=%.3f std_im=%.3f\n",
                                    mean_re_a, mean_im_a, std_im_a,
                                    mean_re_b, mean_im_b, std_im_b);
                            } else {
                                n += snprintf(eqbuf + n, sizeof(eqbuf) - n, "\n");
                            }
                            USRP_LOG("%s", eqbuf);
                        }
                        g_htsig_dump_counter++;
                    }
                }

                // Phase 39: HT-SIG pilot-based H re-estimation.
                // Computes H_htsig0 and H_htsig1 from each symbol's own
                // 4 pilots, replacing Hhdr52 (L-LTF0-based) for HT-SIG
                // equalization. L-SIG remains on Hhdr52 (Phase 34 fix).
                // Both pointers default to Hhdr52 (preserves current
                // behavior when env var is OFF = no loopback regression).
                // Computed AFTER the diagnostic dump block (so dumps
                // show pre-replacement state) and BEFORE the viterbi
                // brute-force loop.
                gr_complex H_htsig0[52];
                gr_complex H_htsig1[52];
                const gr_complex* H_a_ptr = Hhdr52;
                const gr_complex* H_b_ptr = Hhdr52;
                if (d_apply_htsig_h_reestimate &&
                    d_early_eqsym_valid[kHtSig0Rel] &&
                    d_early_eqsym_valid[kHtSig1Rel]) {
                    bool h0_ok = estimate_H_from_htsig_pilots(
                        d_early_eqsym[kHtSig0Rel], Hhdr52, H_htsig0);
                    bool h1_ok = estimate_H_from_htsig_pilots(
                        d_early_eqsym[kHtSig1Rel], Hhdr52, H_htsig1);
                    H_a_ptr = h0_ok ? H_htsig0 : Hhdr52;
                    H_b_ptr = h1_ok ? H_htsig1 : Hhdr52;
                    if (h0_ok || h1_ok) {
                        USRP_LOG("[HTSIG_H_REESTIMATE] h0=%s h1=%s\n",
                                 h0_ok ? "ok" : "fallback",
                                 h1_ok ? "ok" : "fallback");
                    }
                    if (d_log_htsig_h52) {
                        static int hhtsig_dump_counter = 0;
                        if (hhtsig_dump_counter < 10) {
                            char hhbuf[4096];
                            int n = snprintf(hhbuf, sizeof(hhbuf),
                                "[HTSIG_H52_DUMP] frame=%d |H_htsig0|=[",
                                hhtsig_dump_counter);
                            for (int i = 0; i < 52; i++)
                                n += snprintf(hhbuf + n, sizeof(hhbuf) - n,
                                    "%.3f,", std::abs(H_htsig0[i]));
                            n += snprintf(hhbuf + n, sizeof(hhbuf) - n,
                                "] |H_htsig1|=[");
                            for (int i = 0; i < 52; i++)
                                n += snprintf(hhbuf + n, sizeof(hhbuf) - n,
                                    "%.3f,", std::abs(H_htsig1[i]));
                            n += snprintf(hhbuf + n, sizeof(hhbuf) - n,
                                "] ratio0=[");
                            for (int i = 0; i < 52; i++) {
                                float r = (std::abs(Hhdr52[i]) > 1e-3f)
                                    ? std::abs(H_htsig0[i]) / std::abs(Hhdr52[i])
                                    : 1.0f;
                                n += snprintf(hhbuf + n, sizeof(hhbuf) - n,
                                    "%.2f,", r);
                            }
                            n += snprintf(hhbuf + n, sizeof(hhbuf) - n, "]\n");
                            USRP_LOG("%s", hhbuf);
                            hhtsig_dump_counter++;
                        }
                    }
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
                                                           H_a_ptr,
                                                           H_b_ptr,
                                                           inv_a != 0,
                                                           inv_b != 0,
                                                           parsed_len,
                                                           parsed_mcs,
                                                           parsed_sgi,
                                                           parsed_agg,
                                                           parsed_use_ldpc,
                                                           rot,
                                                           &cand_metric,
                                                           &cand_fail,
                                                           d_use_soft_llr_viterbi,
                                                           d_mmse_equalize ? d_mmse_n0_percentile : 0);
                            // Per-rotation metric trace: log ALL 16 candidates so we can
                            // see which rotations produce a meaningful viterbi best-path
                            // metric, vs. metrics that are saturated (RANDOM-like).
                            USRP_LOG("[HT_SIG_CAND] sym=%d rot=%d inv_a=%d inv_b=%d "
                                     "metric=%d fail=%s\n",
                                     d_internal_symbol_counter,
                                     rot, inv_a, inv_b,
                                     cand_metric, cand_fail);
                            // Phase 66: track best (lowest) viterbi metric across
                            // all 16 candidates for the per-frame summary. Negative
                            // cand_metric means viterbi did not run (e.g. early
                            // sanity-check fail); skip those — we only care about
                            // metrics that actually came out of the decoder.
                            if (cand_metric >= 0 && cand_metric < htsig_best_metric) {
                                htsig_best_metric = cand_metric;
                                htsig_best_rot    = rot;
                                htsig_best_inv_a  = inv_a;
                                htsig_best_inv_b  = inv_b;
                                htsig_best_fail   = cand_fail;
                            }
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
            }  // close outer rot_lsig loop (Phase 70 4-rot candidate search)

            // When n_rot==1 (default), the inner inv_lsig loop above ran twice
            // (rot_lsig fixed at 0). When n_rot==4 (env var ON), it ran 8
            // times across all (rot, inv) pairs. The HT-SIG body was skipped
            // for every candidate in candidate mode (continue above), so we
            // promote the best candidate here and replay the body once.
            if (n_rot > 1 && lsig_best_metric < INT_MAX && !lsig_promoted) {
                // Promote the best candidate to the local variables
                // used by the rest of the function.
                lsig_enc = lsig_best_enc;
                lsig_len = lsig_best_len;
                lsig_rate_field = lsig_best_rate_field;
                lsig_parity_ok_int = lsig_best_parity_ok;
                lsig_ok = true;  // We found a valid L-SIG.
                // Log the candidate-search winner (thread-safe snprintf+USRP_LOG).
                char candbuf[256];
                snprintf(candbuf, sizeof(candbuf),
                    "[LSIG_CANDIDATE_WIN] rot=%d inv=%d approx_metric=%d "
                    "enc=%d len=%d rate_field=0x%X parity_ok=%d\n",
                    lsig_best_rot, lsig_best_inv, lsig_best_metric,
                    lsig_best_enc, lsig_best_len, lsig_best_rate_field,
                    lsig_best_parity_ok);
                USRP_LOG("%s", candbuf);
                // CRITICAL: Mark promoted BEFORE the goto. Otherwise, when
                // the body completes (found=true, continue, or natural exit)
                // and control falls back through the closing braces of the
                // loops into this block, the condition would still hold
                // (n_rot>1, best_metric<INT_MAX) and we'd re-enter the body
                // forever — infinite loop.
                lsig_promoted = true;
                // The HT-SIG body was skipped for every candidate while we
                // searched. Run it ONCE now with the best candidate's values.
                // The body uses the same `lsig_*` locals and `htsig_lsig_enc`
                // assignment, so a single manual entry is sufficient.
                goto lsig_body_entry;
            }

            // Phase 66: per-candidate HT-SIG viterbi summary. Opt-in via
            // IEEE80211_HTSIG_VITERBI_DIAG=1. Prints ONE line per frame
            // after the candidate search completes (found or not), with
            // the best viterbi metric across all 16 candidates and which
            // (rot, inv_a, inv_b) tuple produced it. Use to distinguish:
            //   - ALL candidates fail with metric ~INT_MAX/4 (saturation,
            //     equalizer is the wall — Phase 41 verdict still holds)
            //   - Best metric is moderate but CRC still fails (decoder
            //     sensitivity issue, not channel-physics)
            // Thread-safe: snprintf into local buf + USRP_LOG("%s", buf).
            // Default OFF; env var gate matches IEEE80211_H52_NULL_DUMP style.
            static const char* env_htsig_vit_diag =
                std::getenv("IEEE80211_HTSIG_VITERBI_DIAG");
            if (env_htsig_vit_diag && env_htsig_vit_diag[0] != '\0') {
                char vitdiag_buf[512];
                int n = 0;
                n += snprintf(vitdiag_buf + n, sizeof(vitdiag_buf) - n,
                              "[HTSIG_VITERBI_DIAG] frame_sym=%d n_candidates=%d "
                              "found=%d best_metric=%d best_rot=%d best_inv_a=%d "
                              "best_inv_b=%d best_fail=%s avg_snr_lsig=%.2f "
                              "avg_snr_htsig=%.2f is_ht_frame=%d\n",
                              d_internal_symbol_counter,
                              htsig_candidates_tried,
                              found ? 1 : 0,
                              (htsig_best_metric == INT_MAX) ? -1 : htsig_best_metric,
                              htsig_best_rot, htsig_best_inv_a, htsig_best_inv_b,
                              htsig_best_fail,
                              avg_snr_lsig, avg_snr_htsig,
                              d_is_ht_frame ? 1 : 0);
                USRP_LOG("%s", vitdiag_buf);
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

                    // Phase 31: receive-side L-LTF timing diagnostic. At the H52
                    // computation site (first HT-DATA symbol), log the equalizer's
                    // current FFT-block read position and the implied L-LTF0/L-LTF1
                    // absolute FFT-block positions so they can be diffed against the
                    // splitter's [SPLITTER] LTS0 line for the same frame. nread is
                    // the absolute FFT-block offset of the current symbol
                    // (abs_in_off); lts0_bin is the implied offset of the L-LTF0
                    // FFT block (= nread - d_data_start_rel); lts1_bin is the
                    // implied offset of the L-LTF1 FFT block (= nread - d_data_start_rel + 1).
                    // d_sym_idx is the relative symbol index within the frame.
                    // env-var-gated via IEEE80211_LLTF_TIMING_DUMP, default OFF.
                    if (g_eq_lltf_timing_dump) {
                        char buf[256];
                        snprintf(buf, sizeof(buf),
                                 "[EQUALIZER] H52 compute nread=%llu lts0_bin=%llu lts1_bin=%llu "
                                 "d_sym_idx=%d lts0_mag0=%.3f lts0_mag25=%.3f\n",
                                 (unsigned long long)abs_in_off,
                                 (unsigned long long)(abs_in_off - d_data_start_rel),
                                 (unsigned long long)(abs_in_off - d_data_start_rel + 1),
                                 d_sym_idx,
                                 std::abs(d_early_eqsym[kLltf0Rel][0]),
                                 std::abs(d_early_eqsym[kLltf0Rel][25]));
                        USRP_LOG("%s", buf);
                    }

                    compute_H52_tx_order(d_early_eqsym[kLltf0Rel], d_H52_tx_order);

                    // Phase 59: detect + interpolate H52 nulls (only when env var ON).
                    if (d_h52_null_interp_enabled) {
                        auto nulls = detect_h52_nulls(d_H52_tx_order, d_h52_null_thresh);
                        if (d_h52_null_dump_enabled) {
                            // 52 nulls × ~30 chars/entry + ~64 char header = ~1624 bytes.
                            // 2048 leaves headroom for safety.
                            char buf[2048];
                            int off = snprintf(buf, sizeof(buf),
                                "[H52_NULL] n_nulls=%zu/%d thresh=%.3f radius=%d\n",
                                nulls.size(), 52, d_h52_null_thresh, d_h52_interp_radius);
                            for (size_t i = 0; i < nulls.size() && off < (int)sizeof(buf) - 32; i++) {
                                off += snprintf(buf + off, sizeof(buf) - off,
                                    "  [%d] |H|=%.3f arg=%.3f\n",
                                    nulls[i], std::abs(d_H52_tx_order[nulls[i]]),
                                    std::arg(d_H52_tx_order[nulls[i]]));
                            }
                            USRP_LOG("%s", buf);
                        }
                        interp_h52_nulls(d_H52_tx_order, nulls, d_h52_interp_radius);
                    }

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


