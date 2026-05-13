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
        std::memcpy(d_H, in, 64 * sizeof(gr_complex));
        // Debug: print first few d_H values
        fprintf(stderr, "[LS_EQ] n=0: d_H[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", d_H[i].real(), d_H[i].imag());
        }
        fprintf(stderr, "\n");

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

        // L-LTF 只覆盖 legacy 的 52 occupied tones: 6..58 except DC=32
        for (int i = 0; i < 64; i++) {
            if ((i == 32) || (i < 6) || (i > 58)) {
                continue;
            }
            noise += std::pow(std::abs(d_H[i] - in[i]), 2);
            signal += std::pow(std::abs(d_H[i] + in[i]), 2);
            d_H[i] += in[i];
            // Use kLltf64Binned for channel estimation
            // Guard bands, DC, and pilots have kLltf64Binned[i] == 0, so skip those
            if (std::abs(kLltf64Binned[i]) > 1e-9f) {
                d_H[i] /= (kLltf64Binned[i] * kFftNormalize);
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