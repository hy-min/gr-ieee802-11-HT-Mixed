/*
 * Copyright (C) 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 */

#include "ls.h"
#include <cstring>
#include <iostream>
#include <cmath>

using namespace gr::ieee802_11::equalizer;

// Pre-computed FFT of the L-LTF time-domain sequence (LONG from sync_long.cc)
// This is the expected frequency response of the L-LTF at each FFT bin
// Computed as: FFT(LONG_td) where LONG_td is the 64-sample L-LTF time-domain waveform
static const gr_complex FFT_LONG[64] = {
    gr_complex(-0.0002, 0.0000),  // bin 0 (SC -32)
    gr_complex(8.8326, 0.8699),   // bin 1 (SC -31)
    gr_complex(-8.7047, -1.7315), // bin 2 (SC -30)
    gr_complex(-8.4932, -2.5764), // bin 3 (SC -29)
    gr_complex(8.1998, 3.3965),   // bin 4 (SC -28)
    gr_complex(7.8277, 4.1840),  // bin 5 (SC -27)
    gr_complex(-7.3798, -4.9310), // bin 6 (SC -26)
    gr_complex(6.8608, 5.6305),   // bin 7 (SC -25)
    gr_complex(-6.2757, -6.2757), // bin 8 (SC -24)
    gr_complex(5.6303, 6.8605),   // bin 9 (SC -23)
    gr_complex(-4.9308, -7.3795), // bin 10 (SC -22)
    gr_complex(-4.1839, -7.8275), // bin 11 (SC -21) - pilot
    gr_complex(-3.3964, -8.1997), // bin 12 (SC -20)
    gr_complex(-2.5763, -8.4929), // bin 13 (SC -19)
    gr_complex(-1.7313, -8.7038), // bin 14 (SC -18)
    gr_complex(0.8700, 8.8329),   // bin 15 (SC -17)
    gr_complex(0.0000, 8.8750),  // bin 16 (SC -16)
    gr_complex(0.8699, -8.8325),  // bin 17 (SC -15)
    gr_complex(1.7314, -8.7045),  // bin 18 (SC -14)
    gr_complex(-2.5765, 8.4934), // bin 19 (SC -13)
    gr_complex(3.3965, -8.1998), // bin 20 (SC -12)
    gr_complex(-4.1837, 7.8272),  // bin 21 (SC -11) - pilot
    gr_complex(4.9307, -7.3793),  // bin 22 (SC -10)
    gr_complex(-5.6305, 6.8608),  // bin 23 (SC -9)
    gr_complex(-6.2757, 6.2757),  // bin 24 (SC -8)
    gr_complex(-6.8603, 5.6301),  // bin 25 (SC -7) - pilot
    gr_complex(-7.3795, 4.9308),  // bin 26 (SC -6)
    gr_complex(0.0003, -0.0001), // bin 27 (SC -5)
    gr_complex(0.0006, -0.0002), // bin 28 (SC -4)
    gr_complex(-0.0004, 0.0001), // bin 29 (SC -3)
    gr_complex(-0.0001, 0.0000), // bin 30 (SC -2)
    gr_complex(-0.0004, 0.0000), // bin 31 (SC -1)
    gr_complex(-0.0006, 0.0000), // bin 32 (DC)
    gr_complex(-0.0002, -0.0000), // bin 33 (SC +1)
    gr_complex(-0.0005, -0.0001), // bin 34 (SC +2)
    gr_complex(-0.0001, -0.0000), // bin 35 (SC +3)
    gr_complex(-0.0002, -0.0001), // bin 36 (SC +4)
    gr_complex(-0.0004, -0.0002), // bin 37 (SC +5)
    gr_complex(-7.3794, -4.9308), // bin 38 (SC +6)
    gr_complex(-6.8609, -5.6306), // bin 39 (SC +7) - pilot
    gr_complex(6.2757, 6.2757),  // bin 40 (SC +8)
    gr_complex(5.6305, 6.8608),  // bin 41 (SC +9)
    gr_complex(-4.9311, -7.3799), // bin 42 (SC +10)
    gr_complex(-4.1838, -7.8273), // bin 43 (SC +11)
    gr_complex(3.3963, 8.1993),   // bin 44 (SC +12)
    gr_complex(-2.5764, -8.4933), // bin 45 (SC +13)
    gr_complex(1.7314, 8.7045),   // bin 46 (SC +14)
    gr_complex(-0.8699, -8.8323), // bin 47 (SC +15)
    gr_complex(0.0000, -8.8750), // bin 48 (SC +16)
    gr_complex(0.8699, -8.8324), // bin 49 (SC +17)
    gr_complex(1.7314, -8.7041), // bin 50 (SC +18)
    gr_complex(2.5764, -8.4933), // bin 51 (SC +19)
    gr_complex(3.3963, -8.1994), // bin 52 (SC +20)
    gr_complex(-4.1838, 7.8273),  // bin 53 (SC +21) - pilot
    gr_complex(-4.9311, 7.3799),  // bin 54 (SC +22)
    gr_complex(5.6305, -6.8607), // bin 55 (SC +23)
    gr_complex(6.2757, -6.2757), // bin 56 (SC +24)
    gr_complex(-6.8602, 5.6301),  // bin 57 (SC +25)
    gr_complex(7.3790, -4.9305), // bin 58 (SC +26)
    gr_complex(-7.8273, 4.1838),  // bin 59 (SC +27)
    gr_complex(8.2000, -3.3965), // bin 60 (SC +28)
    gr_complex(8.4929, -2.5763), // bin 61 (SC +29)
    gr_complex(8.7044, -1.7314), // bin 62 (SC +30)
    gr_complex(8.8326, -0.8699), // bin 63 (SC +31)
};

void ls::equalize(gr_complex* in,
                  int n,
                  gr_complex* symbols,
                  uint8_t* bits,
                  std::shared_ptr<gr::digital::constellation> mod)
{
    fprintf(stderr, "[LS_EQ] equalize called with n=%d, this=%p d_H_addr=%p\n",
            n, (void*)this, (void*)d_H);
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
        fprintf(stderr, "[LS_EQ] n=1 FFT_LONG[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", FFT_LONG[i].real(), FFT_LONG[i].imag());
        }
        fprintf(stderr, "\n");
        fprintf(stderr, "[LS_EQ] n=1 d_H[6-10] = ");
        for (int i = 6; i < 10; i++) {
            fprintf(stderr, "%.3f+%.3fi ", d_H[i].real(), d_H[i].imag());
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
            // Use FFT_LONG for proper channel estimation
            // The expected L-LTF FFT has magnitude ~8.875 at data subcarriers
            d_H[i] /= FFT_LONG[i];
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