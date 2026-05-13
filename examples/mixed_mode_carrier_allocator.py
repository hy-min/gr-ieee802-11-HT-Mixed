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
        print(f"[MM-CA] __init__ called, tag_key={tag_key}", flush=True)
        self._debug_call_count = 0
        self._first_htdata_printed = False  # Flag to print only first HT-DATA

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
    def _bits01_to_str(vals):
        """
        只用于 header bit-domain 调试：
        real > 0.5 -> '1'
        否则 -> '0'
        """
        return ''.join('1' if np.real(v) > 0.5 else '0' for v in vals)

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

    @staticmethod
    def _bpsk_syms_to_bits_str(vals):
        """
        只用于 MCS0/BPSK 调试：
          real >= 0 -> '1'
          real <  0 -> '0'
        """
        return ''.join('1' if np.real(v) >= 0.0 else '0' for v in vals)

    @staticmethod
    def _write_debug_bits52_file(bits52_str: str):
        path = "/tmp/wifi_tx_data0_bits52.txt"
        try:
            # 只写首帧，后续帧不覆盖
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(bits52_str + "\n")
                print(f"[TX][HTDATA0] saved first-frame reference to {path}", flush=True)
            else:
                print(f"[TX][HTDATA0] keep existing first-frame reference: {path}", flush=True)
        except Exception as e:
            print(f"[TX][HTDATA0] write debug file failed: {e}", flush=True)

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

        # ULTIMATE DEBUG: Print TX IFFT input physical bins (before FFT shift)
        # This is the raw 64-bin array that goes into IFFT
        # Only print FIRST HT-DATA of FIRST frame (once)
        if len(carriers) == 52 and not self._first_htdata_printed:
            print(f"[TX_HTDATA] FIRST HT-DATA (IFFT input before FFT shift)")
            print(f"[TX_HTDATA] First 4 kTxOrder52 subcarriers: -28,-27,-26,-25")
            for sc in [-28, -27, -26, -25]:
                bin_idx = self._sc_to_fft_bin_idx(sc)
                print(f"[TX_HTDATA]   sc={sc:3d} -> bin[{bin_idx:2d}] = {out_vec[bin_idx].real:.4f}+{out_vec[bin_idx].imag:.4f}i -> bit={1 if out_vec[bin_idx].real >= 0 else 0}")
            self._first_htdata_printed = True

        # DEBUG: Print pilot subcarrier mapping for first call
        static_debug_count = getattr(self, '_debug_fill_count', 0)
        if static_debug_count < 3:
            print(f"[TX_MM-CA] _fill_symbol called #{static_debug_count}")
            print(f"[TX_MM-CA]   carriers[:8] = {carriers[:8]}")
            print(f"[TX_MM-CA]   pilot_carriers = {self._pilot_carriers}")
            for sc, pv in zip(self._pilot_carriers, pilot_values):
                fft_bin = self._sc_to_fft_bin_idx(sc)
                phase_deg = np.angle(pv) * 180 / np.pi
                print(f"[TX_MM-CA]   PILOT sc={sc:3d} -> bin={fft_bin:2d} val={pv:.4f} phase={phase_deg:.1f}deg")
        self._debug_fill_count = static_debug_count + 1

    def forecast(self, noutput_items, ninputs):
        return [max(self._hdr_len, 1)] * ninputs

    def general_work(self, input_items, output_items):
        inp = input_items[0]
        out = output_items[0]

        n_in = len(inp)
        n_out = len(out)

        self._debug_call_count += 1
        if self._debug_call_count <= 3:
            print(f"[MM-CA] general_work called #{self._debug_call_count}: n_in={n_in} n_out={n_out}", flush=True)
            if n_in > 0:
                print(f"[MM-CA] inp[0:8]={inp[0:8]}", flush=True)
            if n_out > 0:
                print(f"[MM-CA] First output symbol (64 bins): {output_items[0][0][:8]}", flush=True)

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

            print(f"[TX][MM-CA] lsig_data48={self._bits01_to_str(lsig_bits48)}", flush=True)
            print(f"[TX][MM-CA] htsig1_data48={self._bits01_to_str(htsig1_bits48)}", flush=True)
            print(f"[TX][MM-CA] htsig2_data48={self._bits01_to_str(htsig2_bits48)}", flush=True)

            # header bits -> BPSK ±1
            lsig_bpsk48   = self._map_header_bits_to_bpsk(lsig_bits48)
            htsig1_bpsk48 = self._map_header_bits_to_bpsk(htsig1_bits48)
            htsig2_bpsk48 = self._map_header_bits_to_bpsk(htsig2_bits48)

            # HT-SIG uses QBPSK (90° rotation on Q-axis)
            # Rotate HT-SIG data symbols by multiplying by j
            htsig1_bpsk48 = htsig1_bpsk48 * 1j
            htsig2_bpsk48 = htsig2_bpsk48 * 1j

            # DEBUG: Verify QBPSK rotation - should be pure imaginary (±j)
            print(f"[TX][QBPSK_CHECK] htsig1_bpsk48[0:8] = {htsig1_bpsk48[0:8]}", flush=True)
            print(f"[TX][QBPSK_CHECK] htsig2_bpsk48[0:8] = {htsig2_bpsk48[0:8]}", flush=True)
            for i in range(8):
                phase_deg = np.angle(htsig1_bpsk48[i]) * 180 / np.pi
                print(f"[TX][QBPSK_CHECK] htsig1[{i}]={htsig1_bpsk48[i]} phase={phase_deg:.1f}deg is_pure_imag={np.abs(htsig1_bpsk48[i].real) < 0.01}", flush=True)

            # HT-SIG pilots also need 90° rotation
            ht_sig_pilot_values = [pv * 1j for pv in self._legacy_pilot_values]

            # 1) legacy preamble
            for s, sw in enumerate(self._sync_words):
                out[produced + s][:] = sw

            # DEBUG: Print first 4 preamble symbols (L-STF, L-STF, L-LTF, L-LTF)
            if self._debug_call_count <= 3:
                print(f"[MM-CA][PREAMBLE] Output symbols 0-3 (should be L-STF, L-STF, L-LTF, L-LTF):")
                for s in range(4):
                    # L-STF has energy at bins 8, 12, 16, 20, 24, 28 (see LEGACY_STF)
                    # L-LTF has energy at bins 1, 33, 35, 39, 41, 45, 47, 51, 53, 57, 59, 63
                    val8 = out[produced + s][8]
                    val12 = out[produced + s][12]
                    val1 = out[produced + s][1]
                    val63 = out[produced + s][63]
                    print(f"[MM-CA][PREAMBLE] sym{s}: bin[1]={val1}, bin[8]={val8}, bin[12]={val12}, bin[63]={val63}")

            # Additional debug: Print L-LTF pilot values at bins for SC{-21,-7,+7,+21}
            # With shift=False IFFT input order: SC{-N} -> bin (64-N), SC{+N} -> bin N
            # So SC{-21} -> bin 43, SC{-7} -> bin 57, SC{+7} -> bin 7, SC{+21} -> bin 21
            for s in [2, 3]:  # L-LTF symbols
                if s == 2 or s == 3:
                    bin_21 = 43   # SC -21
                    bin_m7 = 57   # SC -7
                    bin_p7 = 7    # SC +7
                    bin_p21 = 21  # SC +21
                    v21 = out[produced + s][bin_21]
                    vm7 = out[produced + s][bin_m7]
                    vp7 = out[produced + s][bin_p7]
                    vp21 = out[produced + s][bin_p21]
                    print(f"[TX_LTF_PILOT] sym{s}: SC{{-21}}->bin{bin_21}={v21:.4f}, SC{{-7}}->bin{bin_m7}={vm7:.4f}, SC{{+7}}->bin{bin_p7}={vp7:.4f}, SC{{+21}}->bin{bin_p21}={vp21:.4f}")
                    # Also print magnitude of each
                    print(f"[TX_LTF_PILOT] sym{s} mag: |SC{{-21}}|={np.abs(v21):.4f}, |SC{{-7}}|={np.abs(vm7):.4f}, |SC{{+7}}|={np.abs(vp7):.4f}, |SC{{+21}}|={np.abs(vp21):.4f}")

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

                # 仅用于 MCS0/BPSK 对拍：
                # 这里的 data52 就是"首个 HT DATA OFDM symbol 的 52 个 mapper 输出，
                # 且顺序正是 TX 当前喂给 carrier allocator 的顺序"
                if s == 0:
                    bits52 = self._bpsk_syms_to_bits_str(data52)
                    print(f"[TX][HTDATA0] carriers52={self._data_carriers}", flush=True)
                    print(f"[TX][HTDATA0] bits52={bits52}", flush=True)
                    self._write_debug_bits52_file(bits52)

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
