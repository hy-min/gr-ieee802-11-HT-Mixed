/*
 * Copyright (C) 2015 Bastian Bloessl <bloessl@ccs-labs.org>
 */

#include "sta.h"
#include <cstring>
#include <iostream>
#include <cmath>

using namespace gr::ieee802_11::equalizer;

void sta::equalize(gr_complex* in,
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
        gr_complex H_update[64];
        gr_complex H[64];

        gr_complex p = POLARITY[(n - 2) % 127];

        H[11] = in[11] * p;
        H[25] = in[25] * p;
        H[39] = in[39] * p;
        H[53] = in[53] * -p;

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
                H[i] = in[i] / point;
                c++;
            }
        }

        for (int i = 0; i < 64; i++) {
            int cnt = 0;
            gr_complex s = 0;
            for (int k = i - beta; k <= i + beta; k++) {
                if ((k == 32) || (k < 4) || (k > 60)) {
                    continue;
                }
                cnt++;
                s += H[k];
            }

            if (cnt > 0) {
                H_update[i] = s / gr_complex(cnt, 0);
            } else {
                H_update[i] = d_H[i];
            }
        }

        for (int i = 0; i < 64; i++) {
            if ((i < 4) || (i > 60) || (i == 32)) {
                continue;
            }
            d_H[i] =
                gr_complex(1 - alpha, 0) * d_H[i] + gr_complex(alpha, 0) * H_update[i];
        }
    }
}

double sta::get_snr() { return d_snr; }