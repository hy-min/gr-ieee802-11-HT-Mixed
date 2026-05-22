/*
 * Copyright (C) 2013 ...
 * (same as upstream)
 */
#ifndef INCLUDED_IEEE802_11_UTILS_H
#define INCLUDED_IEEE802_11_UTILS_H

#include <gnuradio/config.h>
#include <ieee802_11/api.h>
#include <ieee802_11/mapper.h>
#include <cinttypes>
#include <iostream>

using gr::ieee802_11::Encoding;

#define MAX_PAYLOAD_SIZE 1500
#define MAX_PSDU_SIZE (MAX_PAYLOAD_SIZE + 28) // MAC, CRC
#define MAX_SYM (((16 + 8 * MAX_PSDU_SIZE + 6) / 24) + 1)
#define MAX_BITS_PER_SYM 288
#define MAX_ENCODED_BITS ((16 + 8 * MAX_PSDU_SIZE + 6) * 2 + MAX_BITS_PER_SYM)

#define dout d_debug && std::cout
#define mylog(...)                       \
    do {                                 \
        if (d_log) {                     \
            d_logger->info(__VA_ARGS__); \
        }                                \
    } while (0);

#pragma pack(push, 1)
struct mac_header {
    uint16_t frame_control;
    uint16_t duration;
    uint8_t addr1[6];
    uint8_t addr2[6];
    uint8_t addr3[6];
    uint16_t seq_nr;
};
#pragma pack(pop)

class ofdm_param
{
public:
    ofdm_param(Encoding e);

    Encoding encoding;
    char rate_field;
    int n_bpsc;
    int n_cbps;
    int n_dbps;

    void print();
};

class frame_param
{
public:
    frame_param(ofdm_param& ofdm, int psdu_length);

    int psdu_size;
    int n_sym;
    int n_pad;
    int n_encoded_bits;
    int n_data_bits;

    void print();
};

void generate_mac_data_frame(
    const char* msdu, int msdu_size, char** psdu, int* psdu_size, char seq);

void scramble(const char* input, char* out, frame_param& frame, char initial_state);
void reset_tail_bits(char* scrambled_data, frame_param& frame);
void convolutional_encoding(const char* input, char* out, frame_param& frame);
void puncturing(const char* input, char* out, frame_param& frame, ofdm_param& ofdm);

void interleave(const char* input,
                char* out,
                frame_param& frame,
                ofdm_param& ofdm,
                bool reverse = false);

// LDPC encoding for 802.11n
// Input: scrambled bits (length = frame.n_data_bits)
// Output: LDPC coded bits (length = frame.n_encoded_bits)
// Returns false if encoding fails.
bool ldpc_encode(const char* scrambled_data, char* out, frame_param& frame, ofdm_param& ofdm);

void split_symbols(const char* input, char* out, frame_param& frame, ofdm_param& ofdm);
void generate_bits(const char* psdu, char* data_bits, frame_param& frame);

#endif /* INCLUDED_IEEE802_11_UTILS_H */