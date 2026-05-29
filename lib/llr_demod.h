#ifndef INCLUDED_IEEE802_11_LLR_DEMOD_H
#define INCLUDED_IEEE802_11_LLR_DEMOD_H

#include <gnuradio/gr_complex.h>
#include <vector>

namespace gr {
namespace ieee802_11 {

// Compute LLR values from complex symbols for various modulations.
// All functions assume normalized constellation (unit average energy).
// noise_var: channel noise variance (sigma^2). If unknown, use 1.0.

// BPSK: 1 bit per symbol
// TX: bit0=0 -> (-1,0), bit0=1 -> (1,0)
// tavildar decoder: llr>0 -> bit=0
static inline float llr_bpsk(const gr_complex& x, float noise_var)
{
    return -2.0f * x.real() / noise_var;
}

// QPSK: 2 bits per symbol [bit0, bit1]
// TX: bit0=sign(real): 0=negative, 1=positive
//     bit1=sign(imag): 0=negative, 1=positive
// tavildar decoder: llr>0 -> bit=0
static inline void llr_qpsk(const gr_complex& x, float llr[2], float noise_var)
{
    llr[0] = -2.0f * x.real() / noise_var;
    llr[1] = -2.0f * x.imag() / noise_var;
}

// 16-QAM: 4 bits per symbol [b0,b1,b2,b3]
// Gray mapping matching TX constellation_16qam_impl:
//   b0=sign(real): 0=negative, 1=positive
//   b1=|real|<2*level: 0=outer(3), 1=inner(1)
//   b2=sign(imag): 0=negative, 1=positive
//   b3=|imag|<2*level: 0=outer(3), 1=inner(1)
// level = sqrt(1/10) for normalized 16-QAM. Pass level<0 to use default.
void llr_16qam(const gr_complex& x, float llr[4], float noise_var, float level = -1.0f);

// 64-QAM: 6 bits per symbol [b0,b1,b2,b3,b4,b5]
// Gray mapping matching TX constellation_64qam_impl:
//   b0=sign(real): 0=negative, 1=positive
//   b1,b2=|real|: (0,0)=7, (1,0)=5, (1,1)=3, (0,1)=1
//   b3=sign(imag): 0=negative, 1=positive
//   b4,b5=|imag|: same pattern as real
// level = sqrt(1/42) for normalized 64-QAM. Pass level<0 to use default.
void llr_64qam(const gr_complex& x, float llr[6], float noise_var, float level = -1.0f);

// Generic block processing
// Input: n_sym * n_sc complex symbols
// Output: n_sym * n_cbps LLR values
// n_bpsc: bits per subcarrier (1,2,4,6)
void compute_llr_block(const gr_complex* symbols, float* llr_out,
                       int n_symbols, int n_sc, int n_bpsc,
                       float noise_var);

} // namespace ieee802_11
} // namespace gr

#endif // INCLUDED_IEEE802_11_LLR_DEMOD_H
