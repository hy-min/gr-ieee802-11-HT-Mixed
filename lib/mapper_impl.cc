/*
 * Copyright (C) 2013, 2016 Bastian Bloessl <bloessl@ccs-labs.org>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation either version 3, or (at your option)
 * any later version.
 */

#include "utils.h"
#include <gnuradio/io_signature.h>
#include <ieee802_11/mapper.h>

#include <pmt/pmt.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace gr::ieee802_11;

class mapper_impl : public mapper
{
public:
    mapper_impl(Encoding e, bool debug)
        : block("mapper",
                gr::io_signature::make(0, 0, 0),
                gr::io_signature::make(1, 1, sizeof(char))),
          d_symbols_offset(0),
          d_symbols(nullptr),
          d_symbols_len(0),
          d_debug(debug),
          d_scrambler(1),
          d_ofdm(e),
          d_use_ldpc(false)
    {
        message_port_register_in(pmt::mp("in"));
        set_msg_handler(pmt::mp("in"),
                        [this](pmt::pmt_t msg) { this->handle_msg(msg); });

        std::cout << "[MAPPER] constructor: encoding=" << (int)e << ", debug=" << (debug ? "true" : "false") << std::endl;
        set_encoding(e);
    }

    ~mapper_impl() override
    {
        if (d_symbols) {
            free(d_symbols);
            d_symbols = nullptr;
        }
    }

    void set_encoding(Encoding encoding) override
    {
        std::cout << "MAPPER: encoding: " << (int)encoding << std::endl;
        gr::thread::scoped_lock lock(d_mutex);
        d_ofdm = ofdm_param(encoding);
    }

    void set_use_ldpc(bool use_ldpc) override { d_use_ldpc = use_ldpc; }

    int general_work(int noutput,
                     gr_vector_int&,
                     gr_vector_const_void_star&,
                     gr_vector_void_star& output_items) override
    {
        unsigned char* out = (unsigned char*)output_items[0];

        if (!d_symbols || d_symbols_offset >= d_symbols_len) {
            return 0;
        }

        const int n = std::min(noutput, d_symbols_len - d_symbols_offset);
        std::memcpy(out, d_symbols + d_symbols_offset, n);
        d_symbols_offset += n;

        if (d_symbols_offset == d_symbols_len) {
            d_symbols_offset = 0;
            free(d_symbols);
            d_symbols = nullptr;
            d_symbols_len = 0;
        }

        return n;
    }

private:
    static long dict_get_long_default(pmt::pmt_t dict, const char* key, long defv)
    {
        if (!pmt::is_dict(dict)) {
            return defv;
        }

        const pmt::pmt_t k = pmt::intern(key);
        const pmt::pmt_t v = pmt::dict_ref(dict, k, pmt::PMT_NIL);

        if (pmt::eq(v, pmt::PMT_NIL)) {
            return defv;
        }
        if (!pmt::is_integer(v)) {
            return defv;
        }

        return pmt::to_long(v);
    }

    static int meta_get_mcs(pmt::pmt_t meta)
    {
        long mcs = dict_get_long_default(meta, "mcs", -1);
        if (mcs >= 0) {
            return (int)mcs;
        }

        long enc = dict_get_long_default(meta, "encoding", -1);
        if (enc >= 0) {
            return (int)enc;
        }

        return -1;
    }

    static bool extract_psdu_bytes(pmt::pmt_t p, std::vector<uint8_t>& out)
    {
        out.clear();

        if (pmt::is_blob(p)) {
            const size_t n = pmt::blob_length(p);
            const uint8_t* d = static_cast<const uint8_t*>(pmt::blob_data(p));
            out.assign(d, d + n);
            return true;
        }

        if (pmt::is_u8vector(p)) {
            const size_t n = pmt::length(p);
            out.resize(n);
            for (size_t i = 0; i < n; i++) {
                out[i] = (uint8_t)pmt::u8vector_ref(p, i);
            }
            return true;
        }

        return false;
    }

    static bool is_ht_mcs(int mcs)
    {
        return (mcs >= 0 && mcs <= 7);
    }

    static int ht_n_bpsc_from_mcs(int mcs)
    {
        switch (mcs) {
        case 0: return 1;   // BPSK 1/2
        case 1: return 2;   // QPSK 1/2
        case 2: return 2;   // QPSK 3/4
        case 3: return 4;   // 16QAM 1/2
        case 4: return 4;   // 16QAM 3/4
        case 5: return 6;   // 64QAM 2/3
        case 6: return 6;   // 64QAM 3/4
        case 7: return 6;   // 64QAM 5/6
        default: return 1;
        }
    }

    static int ht_n_cbps_from_mcs(int mcs)
    {
        switch (mcs) {
        case 0: return 52;  // HT-Data 20MHz: 52 subcarriers (48 data + 4 pilots)
        case 1: return 104;
        case 2: return 104;
        case 3: return 208;
        case 4: return 208;
        case 5: return 312;
        case 6: return 312;
        case 7: return 312;
        default: return 52;
        }
    }

    static int ht_n_dbps_from_mcs(int mcs)
    {
        switch (mcs) {
        case 0: return 26;  // HT-Data 20MHz MCS0: 48 data subcarriers * 1 bit / 2 = 24, but we use 52/2 = 26
        case 1: return 52;
        case 2: return 78;
        case 3: return 104;
        case 4: return 156;
        case 5: return 208;
        case 6: return 234;
        case 7: return 260;
        default: return 26;
        }
    }

    static std::string bits_to_string(const char* bits, int n)
    {
        std::string s;
        s.reserve((size_t)std::max(n, 0));
        for (int i = 0; i < n; i++) {
            s.push_back((bits[i] & 0x1) ? '1' : '0');
        }
        return s;
    }

    static bool write_bits_file_if_absent(const std::string& path, const char* bits, int n)
    {
        std::ifstream fin(path.c_str(), std::ios::in);
        if (fin.good()) {
            return false;
        }

        std::ofstream fout(path.c_str(), std::ios::out | std::ios::trunc);
        if (!fout.good()) {
            return false;
        }

        for (int i = 0; i < n; i++) {
            fout << ((bits[i] & 0x1) ? '1' : '0');
        }
        fout << '\n';
        return true;
    }

    static bool write_bits_file_overwrite(const std::string& path, const char* bits, int n)
    {
        std::ofstream fout(path.c_str(), std::ios::out | std::ios::trunc);
        if (!fout.good()) {
            return false;
        }

        for (int i = 0; i < n; i++) {
            fout << ((bits[i] & 0x1) ? '1' : '0');
        }
        fout << '\n';
        return true;
    }

    void maybe_dump_ht_mcs0_debug(int mcs,
                                  bool ht_mode,
                                  const frame_param& frame,
                                  int data_carriers,
                                  const char* punctured_data,
                                  const char* interleaved_data,
                                  const char* symbols)
    {
        if (!ht_mode || mcs != 0) {
            return;
        }
        if (frame.n_sym <= 0 || data_carriers < 52 || frame.n_encoded_bits < 52) {
            return;
        }

        const int first52 = 52;
        const int first64 = std::min(64, frame.n_encoded_bits);
        const int last64  = std::min(64, frame.n_encoded_bits);

        const std::string punctured_bits   = bits_to_string(punctured_data, first52);
        const std::string interleaved_bits = bits_to_string(interleaved_data, first52);
        const std::string symbols_bits     = bits_to_string(symbols, first52);

        std::cout << "[TX][HTDATA0][PUNCTURED] bits52=" << punctured_bits << std::endl;
        std::cout << "[TX][HTDATA0][INTERLEAVED] bits52=" << interleaved_bits << std::endl;
        std::cout << "[TX][HTDATA0] bits52=" << symbols_bits << std::endl;

        std::cout << "[TX][PUNCTURED-ALL] nbits=" << frame.n_encoded_bits
                  << " first64=" << bits_to_string(punctured_data, first64)
                  << " last64="  << bits_to_string(punctured_data + (frame.n_encoded_bits - last64), last64)
                  << std::endl;

        std::cout << "[TX][INTERLEAVED-ALL] nbits=" << frame.n_encoded_bits
                  << " first64=" << bits_to_string(interleaved_data, first64)
                  << " last64="  << bits_to_string(interleaved_data + (frame.n_encoded_bits - last64), last64)
                  << std::endl;

        const bool wrote_punctured0 =
            write_bits_file_if_absent("/tmp/wifi_tx_punctured0_bits52.txt",
                                      punctured_data,
                                      first52);
        const bool wrote_interleaved0 =
            write_bits_file_if_absent("/tmp/wifi_tx_interleaved0_bits52.txt",
                                      interleaved_data,
                                      first52);
        const bool wrote_symbols0 =
            write_bits_file_if_absent("/tmp/wifi_tx_data0_bits52.txt",
                                      symbols,
                                      first52);

        const bool wrote_punctured_all =
            write_bits_file_if_absent("/tmp/wifi_tx_punctured_all_bits.txt",
                                      punctured_data,
                                      frame.n_encoded_bits);
        const bool wrote_interleaved_all =
            write_bits_file_if_absent("/tmp/wifi_tx_interleaved_all_bits.txt",
                                      interleaved_data,
                                      frame.n_encoded_bits);

        write_bits_file_overwrite("/tmp/wifi_tx_punctured_all_bits.last.txt",
                                  punctured_data,
                                  frame.n_encoded_bits);
        write_bits_file_overwrite("/tmp/wifi_tx_interleaved_all_bits.last.txt",
                                  interleaved_data,
                                  frame.n_encoded_bits);

        std::cout << "[TX][HTDATA0][PUNCTURED] "
                  << (wrote_punctured0 ? "created" : "keep existing first-frame reference")
                  << " /tmp/wifi_tx_punctured0_bits52.txt"
                  << std::endl;

        std::cout << "[TX][HTDATA0][INTERLEAVED] "
                  << (wrote_interleaved0 ? "created" : "keep existing first-frame reference")
                  << " /tmp/wifi_tx_interleaved0_bits52.txt"
                  << std::endl;

        std::cout << "[TX][HTDATA0] "
                  << (wrote_symbols0 ? "created" : "keep existing first-frame reference")
                  << " /tmp/wifi_tx_data0_bits52.txt"
                  << std::endl;

        std::cout << "[TX][PUNCTURED-ALL] "
                  << (wrote_punctured_all ? "created" : "keep existing first-frame reference")
                  << " /tmp/wifi_tx_punctured_all_bits.txt"
                  << std::endl;

        std::cout << "[TX][INTERLEAVED-ALL] "
                  << (wrote_interleaved_all ? "created" : "keep existing first-frame reference")
                  << " /tmp/wifi_tx_interleaved_all_bits.txt"
                  << std::endl;

        std::cout << "[TX][PUNCTURED-ALL] updated /tmp/wifi_tx_punctured_all_bits.last.txt" << std::endl;
        std::cout << "[TX][INTERLEAVED-ALL] updated /tmp/wifi_tx_interleaved_all_bits.last.txt" << std::endl;
    }

    void setup_ht_params(int mcs, int psdu_length, frame_param& frame)
    {
        const int ht_n_bpsc = ht_n_bpsc_from_mcs(mcs);
        const int ht_n_cbps = ht_n_cbps_from_mcs(mcs);
        const int ht_n_dbps = ht_n_dbps_from_mcs(mcs);

        std::cout << "[MAPPER][DEBUG] setup_ht_params called: mcs=" << mcs
                  << " ht_n_cbps=" << ht_n_cbps << " ht_n_dbps=" << ht_n_dbps << std::endl;

        d_ofdm.n_bpsc = ht_n_bpsc;
        d_ofdm.n_cbps = ht_n_cbps;
        d_ofdm.n_dbps = ht_n_dbps;

        frame.psdu_size = psdu_length;
        frame.n_sym = (16 + 8 * psdu_length + 6 + ht_n_dbps - 1) / ht_n_dbps;
        frame.n_data_bits = frame.n_sym * ht_n_dbps;
        frame.n_pad = frame.n_data_bits - (16 + 8 * psdu_length + 6);
        frame.n_encoded_bits = frame.n_sym * ht_n_cbps;
    }

    void handle_msg(pmt::pmt_t msg)
    {
        std::cout << "[MAPPER] handle_msg called" << std::endl;
        if (!pmt::is_pair(msg)) {
            std::cout << "[MAPPER] msg is not a pair" << std::endl;
            return;
        }

        gr::thread::scoped_lock lock(d_mutex);

        if (d_symbols) {
            free(d_symbols);
            d_symbols = nullptr;
            d_symbols_len = 0;
            d_symbols_offset = 0;
        }

        const pmt::pmt_t meta = pmt::car(msg);
        const pmt::pmt_t data = pmt::cdr(msg);

        int mcs = meta_get_mcs(meta);
        const long len_meta = dict_get_long_default(meta, "len", -1);
        const long psdu_len_meta = dict_get_long_default(meta, "psdu_len", -1);

        if (d_debug) {
            std::cout << "[MAPPER] meta=" << pmt::write_string(meta) << std::endl;
            std::cout << "[MAPPER] meta fields: mcs=" << mcs
                      << " len=" << len_meta
                      << " psdu_len=" << psdu_len_meta
                      << std::endl;
        }

        if (mcs >= 0) {
            if (mcs > 7) {
                mcs = 7;
            }
            d_ofdm = ofdm_param((Encoding)mcs);
            if (d_debug) {
                std::cout << "[MAPPER] using MCS=" << mcs << std::endl;
            }
        }

        std::vector<uint8_t> psdu_bytes_vec;
        if (!extract_psdu_bytes(data, psdu_bytes_vec)) {
            std::cout << "[MAPPER] ERROR: cdr is neither blob nor u8vector" << std::endl;
            return;
        }

        const int psdu_length = (int)psdu_bytes_vec.size();
        const char* psdu = (const char*)psdu_bytes_vec.data();

        frame_param frame(d_ofdm, psdu_length);

        // 确定有效的 MCS：优先使用 meta 中的 mcs，否则使用 d_ofdm.encoding
        int effective_mcs = mcs;
        if (effective_mcs < 0) {
            // 检查 d_ofdm.encoding 是否是 HT MCS (0-7)
            if (d_ofdm.encoding >= 0 && d_ofdm.encoding <= 7) {
                effective_mcs = (int)d_ofdm.encoding;
                if (d_debug) {
                    std::cout << "[MAPPER] using d_ofdm.encoding as effective MCS: " << effective_mcs << std::endl;
                }
            }
        }

        const bool ht_mode = is_ht_mcs(effective_mcs);
        std::cout << "[MAPPER][DEBUG] effective_mcs=" << effective_mcs << " ht_mode=" << ht_mode << std::endl;
        if (ht_mode) {
            if (d_debug) {
                std::cout << "[MAPPER] HT mode enabled, effective MCS: " << effective_mcs << std::endl;
            }
            setup_ht_params(effective_mcs, psdu_length, frame);
        } else {
            if (d_debug) {
                std::cout << "[MAPPER] Non-HT mode, effective MCS: " << effective_mcs << std::endl;
            }
        }

        const int data_carriers = d_ofdm.n_cbps / d_ofdm.n_bpsc;

        if (frame.n_sym > MAX_SYM) {
            std::cout << "[MAPPER] packet too large, maximum number of symbols is "
                      << MAX_SYM << std::endl;
            return;
        }

        char* data_bits        = (char*)calloc(frame.n_data_bits, sizeof(char));
        char* scrambled_data   = (char*)calloc(frame.n_data_bits, sizeof(char));
        char* encoded_data     = (char*)calloc(frame.n_data_bits * 2, sizeof(char));
        char* punctured_data   = (char*)calloc(frame.n_encoded_bits, sizeof(char));
        char* interleaved_data = (char*)calloc(frame.n_encoded_bits, sizeof(char));
        char* symbols          = (char*)calloc(frame.n_sym * data_carriers, sizeof(char));

        generate_bits(psdu, data_bits, frame);
        fprintf(stderr, "[TX_SCRAMBLER] seed=%d, first16bits before scramble: ", d_scrambler);
        for (int i = 0; i < 16; i++) fprintf(stderr, "%d", data_bits[i]);
        fprintf(stderr, "\n");
        scramble(data_bits, scrambled_data, frame, d_scrambler++);
        fprintf(stderr, "[TX_SCRAMBLER] first16bits after scramble: ");
        for (int i = 0; i < 16; i++) fprintf(stderr, "%d", scrambled_data[i]);
        fprintf(stderr, "\n");
        if (d_scrambler > 127) {
            d_scrambler = 1;
        }

        reset_tail_bits(scrambled_data, frame);

        if (d_use_ldpc && ht_mode) {
            bool ok = ldpc_encode(scrambled_data, interleaved_data, frame, d_ofdm);
            if (!ok) {
                std::cerr << "[MAPPER] LDPC encode failed, falling back to convolutional" << std::endl;
                d_use_ldpc = false;
            }
            // Copy for debug output compatibility
            std::memcpy(punctured_data, interleaved_data, frame.n_encoded_bits);
            split_symbols(interleaved_data, symbols, frame, d_ofdm);
        }

        if (!d_use_ldpc || !ht_mode) {
            convolutional_encoding(scrambled_data, encoded_data, frame);
            puncturing(encoded_data, punctured_data, frame, d_ofdm);
            interleave(punctured_data, interleaved_data, frame, d_ofdm);
            split_symbols(interleaved_data, symbols, frame, d_ofdm);
        }

        maybe_dump_ht_mcs0_debug(mcs,
                                 ht_mode,
                                 frame,
                                 data_carriers,
                                 punctured_data,
                                 interleaved_data,
                                 symbols);

        d_symbols_len = frame.n_sym * data_carriers;
        d_symbols = (char*)calloc(d_symbols_len, 1);
        std::memcpy(d_symbols, symbols, d_symbols_len);
        d_symbols_offset = 0;

        const pmt::pmt_t srcid = pmt::string_to_symbol(alias());

        add_item_tag(0,
                     nitems_written(0),
                     pmt::string_to_symbol("packet_len"),
                     pmt::from_long(d_symbols_len),
                     srcid);

        add_item_tag(0,
                     nitems_written(0),
                     pmt::mp("psdu_len"),
                     pmt::from_long(psdu_length),
                     srcid);

        add_item_tag(0,
                     nitems_written(0),
                     pmt::mp("encoding"),
                     pmt::from_long((mcs >= 0) ? mcs : (int)d_ofdm.encoding),
                     srcid);

        // 添加 mcs 标签供 HT-SIG 使用
        add_item_tag(0,
                     nitems_written(0),
                     pmt::mp("mcs"),
                     pmt::from_long((mcs >= 0) ? mcs : (int)d_ofdm.encoding),
                     srcid);

        free(data_bits);
        free(scrambled_data);
        free(encoded_data);
        free(punctured_data);
        free(interleaved_data);
        free(symbols);

        if (d_debug) {
            std::cout << "[MAPPER] frame:"
                      << " ht_mode=" << (ht_mode ? 1 : 0)
                      << " psdu=" << psdu_length
                      << " n_sym=" << frame.n_sym
                      << " n_data_bits=" << frame.n_data_bits
                      << " n_encoded_bits=" << frame.n_encoded_bits
                      << " data_carriers=" << data_carriers
                      << " out_symbols=" << d_symbols_len
                      << std::endl;
        }
    }

private:
    int d_symbols_offset;
    char* d_symbols;
    int d_symbols_len;
    bool d_debug;
    uint8_t d_scrambler;
    ofdm_param d_ofdm;
    bool d_use_ldpc;
    gr::thread::mutex d_mutex;
};

mapper::sptr mapper::make(Encoding mcs, bool debug)
{
    return gnuradio::get_initial_sptr(new mapper_impl(mcs, debug));
}
