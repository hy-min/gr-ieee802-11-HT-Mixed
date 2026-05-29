#include "llr_demod.h"
#include <algorithm>
#include <cmath>

namespace gr {
namespace ieee802_11 {

// 16-QAM: level = sqrt(1/10) for normalized constellation
// Gray mapping matching TX constellation_16qam_impl:
//   b0=sign(real): 0=negative, 1=positive
//   b1=|real|<2*level: 0=outer(3), 1=inner(1)
//   b2=sign(imag): 0=negative, 1=positive
//   b3=|imag|<2*level: 0=outer(3), 1=inner(1)
void llr_16qam(const gr_complex& x, float llr[4], float noise_var, float level)
{
    const float kLevel = (level > 0.0f) ? level : std::sqrt(0.1f);
    const float scale = 1.0f / noise_var;
    const float r = x.real();
    const float i = x.imag();

    // b0: sign(real). 0 -> -1,-3 (negative) ; 1 -> +1,+3 (positive)
    float d0_0 = std::min(std::abs(r + 1.0f * kLevel), std::abs(r + 3.0f * kLevel));
    float d0_1 = std::min(std::abs(r - 1.0f * kLevel), std::abs(r - 3.0f * kLevel));
    llr[0] = (d0_1 * d0_1 - d0_0 * d0_0) * scale;

    // b1: 0 -> +/-3*kLevel (outer) ; 1 -> +/-1*kLevel (inner)
    float d1_0 = std::min(std::abs(r - 3.0f * kLevel), std::abs(r + 3.0f * kLevel));
    float d1_1 = std::min(std::abs(r - 1.0f * kLevel), std::abs(r + 1.0f * kLevel));
    llr[1] = (d1_1 * d1_1 - d1_0 * d1_0) * scale;

    // b2: sign(imag). 0 -> -1,-3 (negative) ; 1 -> +1,+3 (positive)
    float d2_0 = std::min(std::abs(i + 1.0f * kLevel), std::abs(i + 3.0f * kLevel));
    float d2_1 = std::min(std::abs(i - 1.0f * kLevel), std::abs(i - 3.0f * kLevel));
    llr[2] = (d2_1 * d2_1 - d2_0 * d2_0) * scale;

    // b3: 0 -> +/-3*kLevel (outer) ; 1 -> +/-1*kLevel (inner)
    float d3_0 = std::min(std::abs(i - 3.0f * kLevel), std::abs(i + 3.0f * kLevel));
    float d3_1 = std::min(std::abs(i - 1.0f * kLevel), std::abs(i + 1.0f * kLevel));
    llr[3] = (d3_1 * d3_1 - d3_0 * d3_0) * scale;
}

// 64-QAM: level = sqrt(1/42) for normalized constellation
// Points on each axis: +/-1*level, +/-3*level, +/-5*level, +/-7*level
// Gray mapping matching TX constellation_64qam_impl:
//   b0=sign(real): 0=negative, 1=positive
//   b1,b2=|real|: (0,0)=7, (1,0)=5, (1,1)=3, (0,1)=1
//   b3=sign(imag): 0=negative, 1=positive
//   b4,b5=|imag|: same pattern as real
void llr_64qam(const gr_complex& x, float llr[6], float noise_var, float level)
{
    const float kLevel = (level > 0.0f) ? level : std::sqrt(1.0f / 42.0f);
    const float scale = 1.0f / noise_var;
    const float r = x.real();
    const float i = x.imag();

    auto sq = [](float v) { return v * v; };

    // --- Real axis (bits 0, 1, 2) ---
    // b0: sign(real). 0 -> -1,-3,-5,-7 (negative) ; 1 -> +1,+3,+5,+7 (positive)
    float d0_0 = std::min({std::abs(r + 1.0f * kLevel),
                           std::abs(r + 3.0f * kLevel),
                           std::abs(r + 5.0f * kLevel),
                           std::abs(r + 7.0f * kLevel)});
    float d0_1 = std::min({std::abs(r - 1.0f * kLevel),
                           std::abs(r - 3.0f * kLevel),
                           std::abs(r - 5.0f * kLevel),
                           std::abs(r - 7.0f * kLevel)});
    llr[0] = (sq(d0_1) - sq(d0_0)) * scale;

    // b1: 0 -> +/-5, +/-7 (outer) ; 1 -> +/-1, +/-3 (inner)
    float d1_0 = std::min({std::abs(r - 5.0f * kLevel),
                           std::abs(r - 7.0f * kLevel),
                           std::abs(r + 5.0f * kLevel),
                           std::abs(r + 7.0f * kLevel)});
    float d1_1 = std::min({std::abs(r - 1.0f * kLevel),
                           std::abs(r - 3.0f * kLevel),
                           std::abs(r + 1.0f * kLevel),
                           std::abs(r + 3.0f * kLevel)});
    llr[1] = (sq(d1_1) - sq(d1_0)) * scale;

    // b2: 0 -> +/-1, +/-7 (edge) ; 1 -> +/-3, +/-5 (middle)
    float d2_0 = std::min({std::abs(r - 1.0f * kLevel),
                           std::abs(r - 7.0f * kLevel),
                           std::abs(r + 1.0f * kLevel),
                           std::abs(r + 7.0f * kLevel)});
    float d2_1 = std::min({std::abs(r - 3.0f * kLevel),
                           std::abs(r - 5.0f * kLevel),
                           std::abs(r + 3.0f * kLevel),
                           std::abs(r + 5.0f * kLevel)});
    llr[2] = (sq(d2_1) - sq(d2_0)) * scale;

    // --- Imaginary axis (bits 3, 4, 5) ---
    // b3: sign(imag). 0 -> -1,-3,-5,-7 (negative) ; 1 -> +1,+3,+5,+7 (positive)
    float d3_0 = std::min({std::abs(i + 1.0f * kLevel),
                           std::abs(i + 3.0f * kLevel),
                           std::abs(i + 5.0f * kLevel),
                           std::abs(i + 7.0f * kLevel)});
    float d3_1 = std::min({std::abs(i - 1.0f * kLevel),
                           std::abs(i - 3.0f * kLevel),
                           std::abs(i - 5.0f * kLevel),
                           std::abs(i - 7.0f * kLevel)});
    llr[3] = (sq(d3_1) - sq(d3_0)) * scale;

    float d4_0 = std::min({std::abs(i - 5.0f * kLevel),
                           std::abs(i - 7.0f * kLevel),
                           std::abs(i + 5.0f * kLevel),
                           std::abs(i + 7.0f * kLevel)});
    float d4_1 = std::min({std::abs(i - 1.0f * kLevel),
                           std::abs(i - 3.0f * kLevel),
                           std::abs(i + 1.0f * kLevel),
                           std::abs(i + 3.0f * kLevel)});
    llr[4] = (sq(d4_1) - sq(d4_0)) * scale;

    float d5_0 = std::min({std::abs(i - 1.0f * kLevel),
                           std::abs(i - 7.0f * kLevel),
                           std::abs(i + 1.0f * kLevel),
                           std::abs(i + 7.0f * kLevel)});
    float d5_1 = std::min({std::abs(i - 3.0f * kLevel),
                           std::abs(i - 5.0f * kLevel),
                           std::abs(i + 3.0f * kLevel),
                           std::abs(i + 5.0f * kLevel)});
    llr[5] = (sq(d5_1) - sq(d5_0)) * scale;
}

// Generic block processing
void compute_llr_block(const gr_complex* symbols, float* llr_out,
                       int n_symbols, int n_sc, int n_bpsc,
                       float noise_var)
{
    // Compute dynamic level for 16QAM/64QAM to handle TX/RX amplitude scaling
    float level_16qam = -1.0f;
    float level_64qam = -1.0f;
    if (n_bpsc == 4 || n_bpsc == 6) {
        float max_abs = 0.0f;
        for (int sym = 0; sym < n_symbols; ++sym) {
            for (int sc = 0; sc < n_sc; ++sc) {
                const gr_complex& x = symbols[sym * n_sc + sc];
                max_abs = std::max(max_abs, std::max(std::abs(x.real()), std::abs(x.imag())));
            }
        }
        if (max_abs > 0.1f) {
            if (n_bpsc == 4) {
                level_16qam = max_abs / 3.0f;
            } else if (n_bpsc == 6) {
                level_64qam = max_abs / 7.0f;
            }
        }
    }

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
                llr_16qam(x, &llr_out[idx], noise_var, level_16qam);
                idx += 4;
                break;
            case 6:
                llr_64qam(x, &llr_out[idx], noise_var, level_64qam);
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
