/*
 * Copyright (C) 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 */

#include "base.h"
#include <cstring>
#include <iostream>

using namespace gr::ieee802_11::equalizer;

const gr_complex base::LONG[] = { 0,  0,  0,  0,  0,  0,  1,  1,  -1, -1, 1,  1,  -1,
                                  1,  -1, 1,  1,  1,  1,  1,  1,  -1, -1, 1,  1,  -1,
                                  1,  -1, 1,  1,  1,  1,  0,  1,  -1, -1, 1,  1,  -1,
                                  1,  -1, 1,  -1, -1, -1, -1, -1, 1,  1,  -1, -1, 1,
                                  -1, 1,  -1, 1,  1,  1,  1,  0,  0,  0,  0,  0 };

const gr_complex base::POLARITY[127] = {
    1,  1,  1,  1,  -1, -1, -1, 1,  -1, -1, -1, -1, 1,  1,  -1, 1,  -1, -1, 1, 1,  -1, 1,
    1,  -1, 1,  1,  1,  1,  1,  1,  -1, 1,  1,  1,  -1, 1,  1,  -1, -1, 1,  1, 1,  -1, 1,
    -1, -1, -1, 1,  -1, 1,  -1, -1, 1,  -1, -1, 1,  1,  1,  1,  1,  -1, -1, 1, 1,  -1, -1,
    1,  -1, 1,  -1, 1,  1,  -1, -1, -1, 1,  1,  -1, -1, -1, -1, 1,  -1, -1, 1, -1, 1,  1,
    1,  1,  -1, 1,  -1, 1,  -1, 1,  -1, -1, -1, -1, -1, 1,  -1, 1,  1,  -1, 1, -1, 1,  1,
    1,  -1, -1, 1,  -1, -1, -1, 1,  1,  1,  -1, -1, -1, -1, -1, -1, -1
};

std::vector<gr_complex> base::get_csi()
{
    std::vector<gr_complex> csi;
    csi.reserve(56);

    // HT 20MHz occupied carriers: 4..60 except DC=32  => 56 tones
    for (int i = 0; i < 64; i++) {
        if ((i == 32) || (i < 4) || (i > 60)) {
            continue;
        }
        csi.push_back(d_H[i]);
    }

    return csi;
}