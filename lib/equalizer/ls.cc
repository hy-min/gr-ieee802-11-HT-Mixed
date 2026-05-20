/*
 * Copyright (C) 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 */

#include "ls.h"
#include "ieee80211_constants.h"
#include <cstring>
#include <iostream>
#include <cmath>

using namespace gr::ieee802_11::equalizer;

void ls::equalize(gr_complex* in,
                  int n,
                  gr_complex* symbols,
                  uint8_t* bits,
                  std::shared_ptr<gr::digital::constellation> mod)
{
    fprintf(stderr, "[LS_EQ] equalize called with n=%d\n", n);
    if (n == 0) {
        for (int i = 0; i < 64; i++) {
            if (std::abs(kLltf64Binned[i]) > 1e-9f) {
                d_H[i] = in[i] / (kLltf64Binned[i] * kFftNormalize);
            } else {
                d_H[i] = gr_complex(0, 0);  // Guard bands, DC, pilots
            }
        }
        // Debug probe: show raw FFT and expected H at n=0
        fprintf(stderr, "[CHAN_EST] n=0 raw d_H[6-10] = ");
        for (int sc = 6; sc <= 10; sc++) {
            gr_complex expected_H = d_H[sc] / (kLltf64Binned[sc] * kFftNormalize);
            fprintf(stderr, "%.4f%+.4fi(%.4f%+.4fi) ",
                    d_H[sc].real(), d_H[sc].imag(),
                    expected_H.real(), expected_H.imag());
        }
        fprintf(stderr, "\n");
        // Debug: print first few d_H values
        fprintf(stderr, "[LS_EQ] n=0: d_H[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", d_H[i].real(), d_H[i].imag());
        }
        fprintf(stderr, "\n");
        // FFT shift verification: print d_H[6] (first positive freq) and d_H[58] (last negative freq)
        fprintf(stderr, "[FFT_SHIFT_CHECK] n=0: d_H[6]=%.4f%+.4fi d_H[58]=%.4f%+.4fi\n",
                d_H[6].real(), d_H[6].imag(),
                d_H[58].real(), d_H[58].imag());
        // Also print kLltf64Binned[6] and kLltf64Binned[58] for comparison
        fprintf(stderr, "[FFT_SHIFT_CHECK] n=0: kLltf64Binned[6]=%.4f%+.4fi kLltf64Binned[58]=%.4f%+.4fi\n",
                kLltf64Binned[6].real(), kLltf64Binned[6].imag(),
                kLltf64Binned[58].real(), kLltf64Binned[58].imag());
        float in_energy = 0;
        for (int k = 0; k < 64; k++) {
            in_energy += std::norm(in[k]);
        }
        float h_energy = 0;
        for (int k = 6; k <= 58; k++) {
            if (k != 32 && k != 11 && k != 25 && k != 39 && k != 53) {
                h_energy += std::norm(d_H[k]);
            }
        }
        fprintf(stderr, "[LS_EQ] n=0: in_energy=%.1f h_energy=%.1f h_mag_avg=%.3f\n",
                in_energy, h_energy, h_energy / 48.0f);

    } else if (n == 1) {
        double signal = 0;
        double noise = 0;

        // Debug: print raw FFT values before division
        fprintf(stderr, "[LS_EQ] n=1 raw FFT[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", in[i].real(), in[i].imag());
        }
        fprintf(stderr, "\n");
        fprintf(stderr, "[LS_EQ] n=1 kLltf64Binned[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", kLltf64Binned[i].real(), kLltf64Binned[i].imag());
        }
        fprintf(stderr, "\n");

        // Debug probe: show normalized H_i and d_H before averaging
        fprintf(stderr, "[CHAN_EST] n=1 H_i[6-10] = ");
        for (int sc = 6; sc <= 10; sc++) {
            gr_complex H_i = in[sc] / (kLltf64Binned[sc] * kFftNormalize);
            fprintf(stderr, "%.4f%+.4fi ", H_i.real(), H_i.imag());
        }
        fprintf(stderr, "\n");
        fprintf(stderr, "[CHAN_EST] n=1 d_H before avg[6-10] = ");
        for (int sc = 6; sc <= 10; sc++) {
            fprintf(stderr, "%.4f%+.4fi ", d_H[sc].real(), d_H[sc].imag());
        }
        fprintf(stderr, "\n");

        // L-LTF 只覆盖 legacy 的 52 occupied tones: 6..58 except DC=32
        for (int i = 0; i < 64; i++) {
            if ((i == 32) || (i < 6) || (i > 58)) {
                continue;
            }
            noise += std::pow(std::abs(d_H[i] - in[i]), 2);
            signal += std::pow(std::abs(d_H[i] + in[i]), 2);
            // Use kLltf64Binned for channel estimation
            // Guard bands, DC, and pilots have kLltf64Binned[i] == 0, so skip those
            // At n=1, only compute SNR, don't update d_H (in is L-SIG FFT, not L-LTF)
            if (n != 1 && std::abs(kLltf64Binned[i]) > 1e-9f) {
                gr_complex H_i = in[i] / (kLltf64Binned[i] * kFftNormalize);
                d_H[i] = (d_H[i] + H_i) * 0.5f;  // Average H estimates
            } else if (n != 1) {
                d_H[i] += in[i];  // For non-data bins, still accumulate
            }
        }

        // 为 HT 新增的边缘子载波做一个最小可用初始化
        d_H[4]  = d_H[6];
        d_H[5]  = d_H[6];
        d_H[59] = d_H[58];
        d_H[60] = d_H[58];

        d_snr = 10 * std::log10(signal / noise / 2);

        // Debug: print channel estimate
        fprintf(stderr, "[LS_EQ] n=1: channel estimate H[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", d_H[i].real(), d_H[i].imag());
        }
        fprintf(stderr, "\n");

    } else {
        // Debug: print input and d_H for n>=2
        fprintf(stderr, "[LS_EQ] n=%d: in[6-10] = ", n);
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", in[i].real(), in[i].imag());
        }
        fprintf(stderr, "\n");
        fprintf(stderr, "[LS_EQ] n=%d: d_H[6-10] = ", n);
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", d_H[i].real(), d_H[i].imag());
        }
        fprintf(stderr, "\n");

        int c = 0;
        for (int i = 0; i < 64; i++) {
            // HT data carriers: 4..60, 去掉 pilots 11/25/39/53 和 DC=32
            if ((i == 11) || (i == 25) || (i == 32) || (i == 39) || (i == 53) ||
                (i < 4) || (i > 60)) {
                continue;
            } else {
                symbols[c] = in[i] / d_H[i];
                bits[c] = mod->decision_maker(&symbols[c]);
                c++;
            }
        }
        fprintf(stderr, "[LS_EQ] n=%d: symbols[0-3] = ", n);
        for (int i = 0; i < 4 && i < c; i++) {
            fprintf(stderr, "%.3f+%.3fi ", symbols[i].real(), symbols[i].imag());
        }
        fprintf(stderr, "\n");
    }
}

double ls::get_snr() { return d_snr; }

void ls::reset()
{
    std::memset(d_H, 0, sizeof(d_H));
    d_snr = 0.0;
}