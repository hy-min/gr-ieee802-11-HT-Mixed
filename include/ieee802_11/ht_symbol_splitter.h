/*
 * Copyright (C) 2013, 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */
#ifndef INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_H
#define INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_H

#include <gnuradio/block.h>
#include <ieee802_11/api.h>

namespace gr {
namespace ieee802_11 {

/*!
 * HT Symbol Splitter: Converts 80-sample HT-Mixed OFDM symbols to 64-sample FFT blocks
 *
 * HT-Mixed mode uses 80-sample OFDM symbols (16 CP + 64 data), but FFT blocks
 * are 64 samples. This block removes the 16-sample CP and outputs only the
 * 64-sample data portion, properly aligned for FFT processing.
 *
 * The block expects:
 * - Input: continuous stream from sync_long with CP still present
 * - Output: 64-sample blocks aligned to OFDM symbol boundaries
 *
 * CP removal logic:
 * - Within each 80-sample block, skip first 16 samples (CP)
 * - Output next 64 samples (OFDM data)
 */
class IEEE802_11_API ht_symbol_splitter : virtual public block
{
public:
    typedef std::shared_ptr<ht_symbol_splitter> sptr;

    /*!
     * Create HT Symbol Splitter block
     *
     * @param fft_size FFT size (typically 64 for 20MHz)
     * @param symbol_size OFDM symbol size (80 for HT-Mixed, 64 for Legacy)
     * @param cp_size Cyclic prefix size (16 for HT-Mixed, 0 for data after L-SIG)
     */
    static sptr make(int fft_size = 64, int symbol_size = 80, int cp_size = 16);

    /*!
     * Set the current HT-Mixed mode
     * @param ht_mixed true for HT-Mixed mode, false for Legacy
     */
    virtual void set_ht_mixed(bool ht_mixed) = 0;
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_HT_SYMBOL_SPLITTER_H */
