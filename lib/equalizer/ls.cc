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
    if (n == 0) {
        for (int i = 0; i < 64; i++) {
            if (std::abs(kLltf64Binned[i]) > 1e-9f) {
                d_H[i] = in[i] / (kLltf64Binned[i] * kFftNormalize);
            } else {
                d_H[i] = gr_complex(0, 0);  // Guard bands, DC, pilots
            }
        }

    } else if (n == 1) {
        double signal = 0;
        double noise = 0;

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

    } else {
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
    }
}

double ls::get_snr() { return d_snr; }

void ls::reset()
{
    std::memset(d_H, 0, sizeof(d_H));
    d_snr = 0.0;
}