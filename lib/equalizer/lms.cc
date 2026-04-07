/*
 * Copyright (C) 2015 Bastian Bloessl <bloessl@ccs-labs.org>
 */

#include "lms.h"
#include <cstring>
#include <iostream>
#include <cmath>

using namespace gr::ieee802_11::equalizer;

void lms::equalize(gr_complex* in,
                   int n,
                   gr_complex* symbols,
                   uint8_t* bits,
                   std::shared_ptr<gr::digital::constellation> mod)
{
    if (n == 0) {
        std::memcpy(d_H, in, 64 * sizeof(gr_complex));

    } else if (n == 1) {
        double signal = 0;
        double noise = 0;
        for (int i = 0; i < 64; i++) {
            if ((i == 32) || (i < 6) || (i > 58)) {
                continue;
            }
            noise += std::pow(std::abs(d_H[i] - in[i]), 2);
            signal += std::pow(std::abs(d_H[i] + in[i]), 2);
            d_H[i] += in[i];
            d_H[i] /= LONG[i] * gr_complex(2, 0);
        }

        d_H[4]  = d_H[6];
        d_H[5]  = d_H[6];
        d_H[59] = d_H[58];
        d_H[60] = d_H[58];

        d_snr = 10 * std::log10(signal / noise / 2);

    } else {
        int c = 0;
        for (int i = 0; i < 64; i++) {
            if ((i == 11) || (i == 25) || (i == 32) || (i == 39) || (i == 53) ||
                (i < 4) || (i > 60)) {
                continue;
            } else {
                symbols[c] = in[i] / d_H[i];
                bits[c] = mod->decision_maker(&symbols[c]);
                gr_complex point;
                mod->map_to_points(bits[c], &point);
                d_H[i] = gr_complex(1 - alpha, 0) * d_H[i] +
                         gr_complex(alpha, 0) * in[i] / point;
                c++;
            }
        }
    }
}

double lms::get_snr() { return d_snr; }