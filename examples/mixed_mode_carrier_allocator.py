#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import numpy as np
import pmt
from gnuradio import gr


# ----------------------------------------
# Legacy preamble sync words (fftshift order)
# 这里只放 legacy:
#   L-STF, L-STF, L-LTF, L-LTF
# HT-STF / HT-LTF 由 insert_ht_training 负责插入
# ----------------------------------------

LEGACY_STF = (
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    (-1.4719601443879746 - 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    (-1.4719601443879746 - 1.4719601443879746j), 0.0, 0.0, 0.0,
    (-1.4719601443879746 - 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    (-1.4719601443879746 - 1.4719601443879746j), 0.0, 0.0, 0.0,
    (-1.4719601443879746 - 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    (1.4719601443879746 + 1.4719601443879746j), 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0
)

# CORRECTED: IEEE 802.11a L-LTF frequency-domain sequence for IFFT input
# STRICT IFFT natural memory order (no shift assumed in input array):
#   Bin 0:       DC (SC 0) = 0
#   Bin 1-26:    Positive frequencies (SC +1 to +26)
#   Bin 27-37:   Guard band (0)
#   Bin 38-63:   Negative frequencies (SC -26 to -1)
#
# IFFT with shift=True will internally handle the frequency reordering.
#
LEGACY_LTF = (
    0,
    1, -1, -1, 1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1, 1, -1, -1, 1, -1, 1, -1, 1, 1, 1, 1,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1
)

DEFAULT_SYNC_WORDS = (
    LEGACY_STF,
    LEGACY_STF,
    LEGACY_LTF,
    LEGACY_LTF,
)

POLARITY_127 = (
    1, 1, 1, 1, -1, -1, -1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1, -1, 1, 1,
    -1, 1, 1, -1, 1, 1, 1, 1, 1, 1, -1, 1, 1, 1, -1, 1, 1, -1, -1, 1, 1, 1,
    -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1, -1, -1, 1, 1, 1, 1, 1, -1, -1,
    1, 1, -1, -1, 1, -1, 1, -1, 1, 1, -1, -1, -1, 1, 1, -1, -1, -1, -1, 1,
    -1, -1, 1, -1, 1, 1, 1, 1, -1, 1, -1, 1, -1, 1, -1, -1, -1, -1, -1, 1,
    -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, 1, -1, -1,
    -1, -1, -1, -1, -1
)


class mixed_mode_carrier_allocator(gr.basic_block):
    """
    输入：scalar complex stream
      - 前 144 个样本：L-SIG + HT-SIG，共 3 个 OFDM symbol，每个 48 data carriers
      - 后续：HT DATA，每个 OFDM symbol 52 data carriers

    输出：vec64 frequency-domain symbols
      - 先输出 4 个 legacy sync words
          L-STF, L-STF, L-LTF, L-LTF
      - 再输出 3 个 header OFDM symbols
          L-SIG, HT-SIG1, HT-SIG2
      - 再输出 n_data_sym 个 HT data OFDM symbols

    最终输出 packet_len = 4 + 3 + n_data_sym
    """

    def __init__(self, tag_key="packet_len", sync_words=None):
        gr.basic_block.__init__(
            self,
            name="mixed_mode_carrier_allocator",
            in_sig=[np.complex64],
            out_sig=[np.dtype((np.complex64, 64))],
        )
        self._tag_key_str = str(tag_key)
        self._tag_key = pmt.intern(self._tag_key_str)
        self._srcid = pmt.intern(self.name())

        if sync_words is None:
            sync_words = DEFAULT_SYNC_WORDS

        self._sync_words = [np.asarray(w, dtype=np.complex64) for w in sync_words]
        for i, w in enumerate(self._sync_words):
            if len(w) != 64:
                raise RuntimeError(f"sync_words[{i}] length={len(w)} != 64")

        self._n_sync = len(self._sync_words)

        # Phase 143: BPSK-HT-SIG fallback (non-standard, TX/RX coordinated).
        self._htsig_bpsk_fallback = (
            os.environ.get('IEEE80211_HTSIG_BPSK_FALLBACK') == '1'
        )

        # header: 48 data subcarriers (legacy 20MHz OFDM)
        self._hdr_carriers = [
            -26, -25, -24, -23, -22,
            -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8,
            -6, -5, -4, -3, -2, -1,
             1,  2,  3,  4,  5,  6,
             8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
            22, 23, 24, 25, 26
        ]

        # HT data: 52 data subcarriers
        self._data_carriers = [
            -28, -27, -26, -25, -24, -23, -22,
            -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8,
            -6, -5, -4, -3, -2, -1,
             1,  2,  3,  4,  5,  6,
             8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
            22, 23, 24, 25, 26, 27, 28
        ]

        self._pilot_carriers = [-21, -7, 7, 21]

        # legacy SIGNAL / HT-SIG 前两符号沿用 legacy pilot 极性
        self._legacy_pilot_values = [
            np.complex64(1 + 0j),
            np.complex64(1 + 0j),
            np.complex64(1 + 0j),
            np.complex64(-1 + 0j),
        ]

        self._hdr_len = 144
        self._hdr_nsym = 3
        self._n_data = 52

        self.set_tag_propagation_policy(gr.TPP_DONT)

    @staticmethod
    def _sc_to_fft_bin_idx(sc: int) -> int:
        # For TX IFFT with shift=False (DC at bin 0):
        # SC{-26}→bin 38, SC{+7}→bin 7, SC{0}→bin 0
        # Formula: (sc + 64) % 64 maps subcarrier to unshifted bin
        idx = (sc + 64) % 64
        if idx < 0 or idx >= 64:
            raise RuntimeError(f"subcarrier {sc} out of range")
        return idx

    @staticmethod
    def _map_header_bits_to_bpsk(vals):
        """
        把 header 的 0/1 bit 映射成 BPSK:
          0 -> -1+0j
          1 -> +1+0j
        """
        out = np.empty(len(vals), dtype=np.complex64)
        for i, v in enumerate(vals):
            out[i] = np.complex64((1.0 + 0.0j) if np.real(v) > 0.5 else (-1.0 + 0.0j))
        return out

    def _ht_pilot_values(self, data_sym_idx: int):
        p = np.complex64(POLARITY_127[data_sym_idx % 127] + 0j)
        return [p, p, p, -p]

    def _fill_symbol(self, out_vec, carriers, in_syms, pilot_values):
        out_vec[:] = 0.0 + 0.0j

        if len(carriers) != len(in_syms):
            raise RuntimeError(
                f"carrier/data length mismatch: {len(carriers)} vs {len(in_syms)}"
            )

        for sc, x in zip(carriers, in_syms):
            fft_bin = self._sc_to_fft_bin_idx(sc)
            out_vec[fft_bin] = np.complex64(x)

        # Write pilot values to pilot carrier positions
        for sc, pv in zip(self._pilot_carriers, pilot_values):
            fft_bin = self._sc_to_fft_bin_idx(sc)
            out_vec[fft_bin] = np.complex64(pv)

    def forecast(self, noutput_items, ninputs):
        return [max(self._hdr_len, 1)] * ninputs

    def general_work(self, input_items, output_items):
        inp = input_items[0]
        out = output_items[0]

        n_in = len(inp)
        n_out = len(out)

        produced = 0
        consumed = 0

        while consumed < n_in and produced < n_out:
            abs_in = int(self.nitems_read(0) + consumed)

            tags = self.get_tags_in_range(0, abs_in, abs_in + 1)

            pkt_len = None
            passthrough_tags = []

            for t in tags:
                if pmt.eq(t.key, self._tag_key):
                    pkt_len = int(pmt.to_long(t.value))
                else:
                    passthrough_tags.append(t)

            if pkt_len is None:
                consumed += 1
                continue

            if pkt_len < self._hdr_len:
                raise RuntimeError(
                    f"mixed allocator input too short: pkt_len={pkt_len} < hdr_len={self._hdr_len}"
                )

            rem = pkt_len - self._hdr_len
            if rem % self._n_data != 0:
                raise RuntimeError(
                    f"mixed allocator input length invalid: pkt_len={pkt_len}, "
                    f"(pkt_len-{self._hdr_len}) % {self._n_data} = {rem % self._n_data}"
                )

            n_data_sym = rem // self._n_data
            out_pkt_len = self._n_sync + self._hdr_nsym + n_data_sym

            if (n_in - consumed) < pkt_len:
                break
            if (n_out - produced) < out_pkt_len:
                break

            abs_out = int(self.nitems_written(0) + produced)

            self.add_item_tag(
                0,
                abs_out,
                self._tag_key,
                pmt.from_long(out_pkt_len),
                self._srcid,
            )

            for t in passthrough_tags:
                self.add_item_tag(0, abs_out, t.key, t.value, t.srcid)

            # --------------------------------------------------
            # 取出 3 个 header 的 48-bit 输入
            # --------------------------------------------------
            lsig_bits48   = np.asarray(inp[consumed +   0 : consumed +  48], dtype=np.complex64)
            htsig1_bits48 = np.asarray(inp[consumed +  48 : consumed +  96], dtype=np.complex64)
            htsig2_bits48 = np.asarray(inp[consumed +  96 : consumed + 144], dtype=np.complex64)

            # header bits -> BPSK ±1
            lsig_bpsk48   = self._map_header_bits_to_bpsk(lsig_bits48)
            htsig1_bpsk48 = self._map_header_bits_to_bpsk(htsig1_bits48)
            htsig2_bpsk48 = self._map_header_bits_to_bpsk(htsig2_bits48)

            if not self._htsig_bpsk_fallback:
                # Standard QBPSK HT-SIG (90° rotation on Q-axis)
                htsig1_bpsk48 = htsig1_bpsk48 * 1j
                htsig2_bpsk48 = htsig2_bpsk48 * 1j
                ht_sig_pilot_values = [pv * 1j for pv in self._legacy_pilot_values]
            else:
                # Phase 143 fallback: keep HT-SIG as BPSK on real axis
                ht_sig_pilot_values = self._legacy_pilot_values

            # 1) legacy preamble
            for s, sw in enumerate(self._sync_words):
                out[produced + s][:] = sw

            base_out = produced + self._n_sync

            # 2) L-SIG / HT-SIG1 / HT-SIG2
            self._fill_symbol(
                out[base_out + 0],
                self._hdr_carriers,
                lsig_bpsk48,
                self._legacy_pilot_values,
            )
            self._fill_symbol(
                out[base_out + 1],
                self._hdr_carriers,
                htsig1_bpsk48,
                ht_sig_pilot_values,
            )
            self._fill_symbol(
                out[base_out + 2],
                self._hdr_carriers,
                htsig2_bpsk48,
                ht_sig_pilot_values,
            )

            # 3) HT DATA
            base_in = consumed + self._hdr_len
            for s in range(n_data_sym):
                a = base_in + s * self._n_data
                b = a + self._n_data

                data52 = np.asarray(inp[a:b], dtype=np.complex64)

                pilot_values = self._ht_pilot_values(s)
                self._fill_symbol(
                    out[base_out + self._hdr_nsym + s],
                    self._data_carriers,
                    data52,
                    pilot_values,
                )

            consumed += pkt_len
            produced += out_pkt_len

        self.consume(0, consumed)
        return produced
