#include "llr_demod.h"
#include <algorithm>
#include <cmath>

namespace gr {
namespace ieee802_11 {

// 16-QAM: level = sqrt(1/10) for normalized constellation
// Gray mapping on each axis:
//   b0 (MSB real): sign(real) -> compare right vs left half
//   b1 (LSB real): |real| < 2*level -> compare inner vs outer
//   b2 (MSB imag): sign(imag)
//   b3 (LSB imag): |imag| < 2*level
void llr_16qam(const gr_complex& x, float llr[4], float noise_var)
{
    const float level = std::sqrt(0.1f);   // sqrt(1/10)
    const float scale = 1.0f / noise_var;
    const float r = x.real();
    const float i = x.imag();
    const float ar = std::abs(r);
    const float ai = std::abs(i);

    // Real axis: constellation points at +/-1*level, +/-3*level
    // b0 = sign(real): 0 -> +1*level, +3*level  ; 1 -> -1*level, -3*level
    float d0_0 = std::min(std::abs(r - 1.0f * level), std::abs(r - 3.0f * level));
    float d0_1 = std::min(std::abs(r + 1.0f * level), std::abs(r + 3.0f * level));
    llr[0] = (d0_1 * d0_1 - d0_0 * d0_0) * scale;

    // b1: 0 -> +/-3*level  ; 1 -> +/-1*level
    float d1_0 = std::min(std::abs(r - 3.0f * level), std::abs(r + 3.0f * level));
    float d1_1 = std::min(std::abs(r - 1.0f * level), std::abs(r + 1.0f * level));
    llr[1] = (d1_1 * d1_1 - d1_0 * d1_0) * scale;

    // Imaginary axis: same structure
    float d2_0 = std::min(std::abs(i - 1.0f * level), std::abs(i - 3.0f * level));
    float d2_1 = std::min(std::abs(i + 1.0f * level), std::abs(i + 3.0f * level));
    llr[2] = (d2_1 * d2_1 - d2_0 * d2_0) * scale;

    float d3_0 = std::min(std::abs(i - 3.0f * level), std::abs(i + 3.0f * level));
    float d3_1 = std::min(std::abs(i - 1.0f * level), std::abs(i + 1.0f * level));
    llr[3] = (d3_1 * d3_1 - d3_0 * d3_0) * scale;
}

// 64-QAM: level = sqrt(1/42) for normalized constellation
// Points on each axis: +/-1*level, +/-3*level, +/-5*level, +/-7*level
// Gray mapping per axis:
//   b0: sign -> right 4 vs left 4
//   b1: |val| < 4*level -> inner 2 vs outer 2
//   b2: 2*level < |val| < 6*level -> middle 2 vs edge 2
void llr_64qam(const gr_complex& x, float llr[6], float noise_var)
{
    const float level = std::sqrt(1.0f / 42.0f);
    const float scale = 1.0f / noise_var;
    const float r = x.real();
    const float i = x.imag();

    // Helper lambda: compute squared distance to a set of points
    auto sq = [](float v) { return v * v; };

    // --- Real axis (bits 0, 1, 2) ---
    // b0: sign(real). 0 -> +1,+3,+5,+7 ; 1 -> -1,-3,-5,-7
    float d0_0 = std::min({std::abs(r - 1.0f * level),
                           std::abs(r - 3.0f * level),
                           std::abs(r - 5.0f * level),
                           std::abs(r - 7.0f * level)});
    float d0_1 = std::min({std::abs(r + 1.0f * level),
                           std::abs(r + 3.0f * level),
                           std::abs(r + 5.0f * level),
                           std::abs(r + 7.0f * level)});
    llr[0] = (sq(d0_1) - sq(d0_0)) * scale;

    // b1: |real| < 4*level. 0 -> +/-5, +/-7 ; 1 -> +/-1, +/-3
    float d1_0 = std::min({std::abs(r - 5.0f * level),
                           std::abs(r - 7.0f * level),
                           std::abs(r + 5.0f * level),
                           std::abs(r + 7.0f * level)});
    float d1_1 = std::min({std::abs(r - 1.0f * level),
                           std::abs(r - 3.0f * level),
                           std::abs(r + 1.0f * level),
                           std::abs(r + 3.0f * level)});
    llr[1] = (sq(d1_1) - sq(d1_0)) * scale;

    // b2: 2*level < |real| < 6*level. 0 -> +/-1, +/-7 ; 1 -> +/-3, +/-5
    float d2_0 = std::min({std::abs(r - 1.0f * level),
                           std::abs(r - 7.0f * level),
                           std::abs(r + 1.0f * level),
                           std::abs(r + 7.0f * level)});
    float d2_1 = std::min({std::abs(r - 3.0f * level),
                           std::abs(r - 5.0f * level),
                           std::abs(r + 3.0f * level),
                           std::abs(r + 5.0f * level)});
    llr[2] = (sq(d2_1) - sq(d2_0)) * scale;

    // --- Imaginary axis (bits 3, 4, 5) ---
    float d3_0 = std::min({std::abs(i - 1.0f * level),
                           std::abs(i - 3.0f * level),
                           std::abs(i - 5.0f * level),
                           std::abs(i - 7.0f * level)});
    float d3_1 = std::min({std::abs(i + 1.0f * level),
                           std::abs(i + 3.0f * level),
                           std::abs(i + 5.0f * level),
                           std::abs(i + 7.0f * level)});
    llr[3] = (sq(d3_1) - sq(d3_0)) * scale;

    float d4_0 = std::min({std::abs(i - 5.0f * level),
                           std::abs(i - 7.0f * level),
                           std::abs(i + 5.0f * level),
                           std::abs(i + 7.0f * level)});
    float d4_1 = std::min({std::abs(i - 1.0f * level),
                           std::abs(i - 3.0f * level),
                           std::abs(i + 1.0f * level),
                           std::abs(i + 3.0f * level)});
    llr[4] = (sq(d4_1) - sq(d4_0)) * scale;

    float d5_0 = std::min({std::abs(i - 1.0f * level),
                           std::abs(i - 7.0f * level),
                           std::abs(i + 1.0f * level),
                           std::abs(i + 7.0f * level)});
    float d5_1 = std::min({std::abs(i - 3.0f * level),
                           std::abs(i - 5.0f * level),
                           std::abs(i + 3.0f * level),
                           std::abs(i + 5.0f * level)});
    llr[5] = (sq(d5_1) - sq(d5_0)) * scale;
}

// Generic block processing
void compute_llr_block(const gr_complex* symbols, float* llr_out,
                       int n_symbols, int n_sc, int n_bpsc,
                       float noise_var)
{
    int idx = 0;
    for (int sym = 0; sym < n_symbols; ++sym) {
        for (int sc = 0; sc < n_sc; ++sc) {
            const gr_complex& x = symbols[sym * n_sc + sc];
            switch (n_bpsc) {
            case 1:
                llr_out[idx++] = llr_bpsk(x, noise_var);
                break;
            case 2:
                llr_qpsk(x, &llr_out[idx], noise_var);
                idx += 2;
                break;
            case 4:
                llr_16qam(x, &llr_out[idx], noise_var);
                idx += 4;
                break;
            case 6:
                llr_64qam(x, &llr_out[idx], noise_var);
                idx += 6;
                break;
            default:
                // Unsupported modulation; fill with zeros
                for (int b = 0; b < n_bpsc; ++b) {
                    llr_out[idx++] = 0.0f;
                }
                break;
            }
        }
    }
}

} // namespace ieee802_11
} // namespace gr
