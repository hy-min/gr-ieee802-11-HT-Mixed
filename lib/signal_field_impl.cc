/*
 * Copyright (C) 2013, 2016 Bastian Bloessl
 *
 * GPLv3+
 */

#include "signal_field_impl.h"
#include "utils.h"

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

using namespace gr::ieee802_11;

namespace {

// -------------------- bit helpers --------------------
inline int get_bit_local(int b, int i) { return (b & (1 << i)) ? 1 : 0; }

inline int parity8(uint8_t x)
{
    x ^= x >> 4;
    x ^= x >> 2;
    x ^= x >> 1;
    return x & 0x1;
}

// HT-SIG CRC8: G(x)=x^8 + x^2 + x + 1
// init all-ones, final invert. Input bits[0..33] (34 bits).
static uint8_t ht_sig_crc8(const char* bits0_33)
{
    uint8_t c = 0xFF;

    for (int i = 0; i < 34; i++) {
        uint8_t m = bits0_33[i] & 0x1;
        uint8_t c7 = (c >> 7) & 0x1;
        uint8_t feedback = c7 ^ m;

        uint8_t new_c7 = (c >> 6) & 0x1;
        uint8_t new_c6 = (c >> 5) & 0x1;
        uint8_t new_c5 = (c >> 4) & 0x1;
        uint8_t new_c4 = (c >> 3) & 0x1;
        uint8_t new_c3 = (c >> 2) & 0x1;
        uint8_t new_c2 = ((c >> 1) & 0x1) ^ feedback; // x^2 tap
        uint8_t new_c1 = (c & 0x1) ^ feedback;        // x^1 tap
        uint8_t new_c0 = feedback;                    // x^0 tap

        c = (uint8_t)((new_c7 << 7) | (new_c6 << 6) | (new_c5 << 5) |
                      (new_c4 << 4) | (new_c3 << 3) | (new_c2 << 2) |
                      (new_c1 << 1) | new_c0);
    }

    return (uint8_t)~c;
}

// Map our legacy Encoding enum to HT MCS (single-stream, 20 MHz, no 256QAM)
static int encoding_to_ht_mcs(Encoding enc)
{
    switch (enc) {
    case BPSK_1_2:
        return 0; // MCS0
    case QPSK_1_2:
        return 1; // MCS1
    case QPSK_3_4:
        return 2; // MCS2
    case QAM16_1_2:
        return 3; // MCS3
    case QAM16_3_4:
        return 4; // MCS4
    case QAM64_2_3:
        return 5; // MCS5
    case QAM64_3_4:
        return 6; // MCS6
    default:
        // BPSK_3_4 (legacy-only) or anything unknown -> fallback MCS0
        return 0;
    }
}

// Compute HT-mixed L-SIG LENGTH (legacy LENGTH field) to cover HT portion duration.
// In this codebase, "HT header" is only HT-SIG (2 symbols) inserted before DATA.
// So we cover: N_target = 2 + frame.n_sym
// A practical mapping at 6 Mbps is: LENGTH ≈ 3 * (N_target - 1) bytes, clamped to 12-bit.
static int compute_lsig_length_for_ht(const frame_param& frame)
{
    const int N_target = 2 + frame.n_sym;
    int L = 3 * std::max(0, N_target - 1);
    L = std::min(L, 4095);
    return L;
}

// Generate 48 coded+interleaved bits for L-SIG (legacy SIGNAL).
// For HT-mixed: rate is fixed 6 Mbps (rate_field=0xD), length is computed to cover HT duration.
static void generate_l_sig_header(char* out,
                                  const frame_param& data_frame,
                                  const ofdm_param& data_ofdm)
{
    (void)data_ofdm;

    char signal_header[24];
    std::memset(signal_header, 0, sizeof(signal_header));

    // HT-mixed: L-SIG RATE fixed 6 Mbps => 0xD (1101)
    const int rate_field = 0x0D;

    // HT-mixed: LENGTH is not psdu bytes; it's a duration-covering length.
    const int length = compute_lsig_length_for_ht(data_frame);

    // rate bits [0..3] transmitted MSB->LSB as in original code
    signal_header[0] = get_bit_local(rate_field, 3);
    signal_header[1] = get_bit_local(rate_field, 2);
    signal_header[2] = get_bit_local(rate_field, 1);
    signal_header[3] = get_bit_local(rate_field, 0);

    // reserved
    signal_header[4] = 0;

    // 12-bit length, LSB first
    for (int i = 0; i < 12; i++) {
        signal_header[5 + i] = get_bit_local(length, i);
    }

    // parity over first 17 bits
    int sum = 0;
    for (int i = 0; i < 17; i++) {
        if (signal_header[i]) {
            sum++;
        }
    }
    signal_header[17] = sum % 2;

    // tail 6 zeros
    for (int i = 0; i < 6; i++) {
        signal_header[18 + i] = 0;
    }

    // L-SIG always BPSK 1/2
    ofdm_param sig_ofdm(BPSK_1_2);
    frame_param sig_frame(sig_ofdm, 0); // n_data_bits=24

    char encoded[48];
    convolutional_encoding(signal_header, encoded, sig_frame);

    // Debug: print TX L-SIG encoded bits after convolutional encoding (before interleave)
    fprintf(stderr, "[TX_LSIG_Coded] encoded[0:24] = ");
    for (int i = 0; i < 24; i++) fprintf(stderr, "%d", encoded[i] & 0x1);
    fprintf(stderr, "\n");
    fprintf(stderr, "[TX_LSIG_Coded] encoded[24:48] = ");
    for (int i = 24; i < 48; i++) fprintf(stderr, "%d", encoded[i] & 0x1);
    fprintf(stderr, "\n");
    fflush(stderr);

    interleave(encoded, out, sig_frame, sig_ofdm);
}

// Generate 96 coded+interleaved bits for HT-SIG (2 OFDM symbols, each 48 coded bits).
// This is a simplified HT-SIG suitable for single-stream 20 MHz, long GI, no STBC/LDPC/aggregation.
// Bits layout we use (LSB-first for multi-bit fields), matching the standard packing conceptually:
//  bits  0.. 6 : MCS (0..6)
//  bit      7 : CBW (0=20 MHz)
//  bits  8..23: HT-LENGTH (16 bits) = PSDU length in bytes
//  bits 24..33: other controls (10 bits) = 0
//  bits 34..41: CRC8 over bits[0..33]
//  bits 42..47: tail = 0
static void generate_ht_sig_header(char* out,
                                   const frame_param& data_frame,
                                   const ofdm_param& data_ofdm)
{
    char ht_bits[48];
    std::memset(ht_bits, 0, sizeof(ht_bits));

    const int mcs = encoding_to_ht_mcs((Encoding)data_ofdm.encoding);
    const int ht_len = std::min(std::max(0, data_frame.psdu_size), 65535);

    // MCS 7 bits
    for (int i = 0; i < 7; i++) {
        ht_bits[i] = (mcs >> i) & 0x1;
    }

    // CBW=20MHz
    ht_bits[7] = 0;

    // LENGTH 16 bits LSB first
    for (int i = 0; i < 16; i++) {
        ht_bits[8 + i] = (ht_len >> i) & 0x1;
    }

    // bits 24..33 reserved/controls -> 0
    for (int i = 0; i < 10; i++) {
        ht_bits[24 + i] = 0;
    }

    // CRC8 over bits[0..33]
    const uint8_t crc = ht_sig_crc8(ht_bits);
    for (int i = 0; i < 8; i++) {
        ht_bits[34 + i] = (crc >> i) & 0x1;
    }

    // tail 6 zeros
    for (int i = 0; i < 6; i++) {
        ht_bits[42 + i] = 0;
    }

    // Now encode+interleave exactly like data, but BPSK 1/2 and 2 symbols.
    ofdm_param sig_ofdm(BPSK_1_2);

    // Trick: choose psdu_length=1 so frame_param yields n_sym=2 for BPSK1/2:
    // n_sym = ceil((16 + 8*1 + 6)/24) = ceil(30/24)=2  -> n_data_bits = 48
    frame_param ht_frame(sig_ofdm, 1);

    char encoded[96];
    convolutional_encoding(ht_bits, encoded, ht_frame);
    interleave(encoded, out, ht_frame, sig_ofdm);

    // Debug: print TX HT-SIG encoded bits before interleave
    // These are what RX should see after deinterleaving
    fprintf(stderr, "[TX_HT_SIG] encoded[0:48] = ");
    for (int i = 0; i < 48; i++) fprintf(stderr, "%d", encoded[i] & 0x1);
    fprintf(stderr, "\n");
    fprintf(stderr, "[TX_HT_SIG] encoded[48:96] = ");
    for (int i = 48; i < 96; i++) fprintf(stderr, "%d", encoded[i] & 0x1);
    fprintf(stderr, "\n");
    fprintf(stderr, "[TX_HT_SIG] ht_bits[0:24] = ");
    for (int i = 0; i < 24; i++) fprintf(stderr, "%d", ht_bits[i] & 0x1);
    fprintf(stderr, "\n");
    fflush(stderr);
}

} // namespace

// ======================================================================

signal_field::sptr signal_field::make()
{
    return signal_field::sptr(new signal_field_impl());
}

// Header size: 144 bits = L-SIG(48) + HT-SIG(96)
signal_field::signal_field() : packet_header_default(144, "packet_len") {}

signal_field_impl::signal_field_impl()
    : packet_header_default(144, "packet_len")
{
}

signal_field_impl::~signal_field_impl() {}

int signal_field_impl::get_bit(int b, int i)
{
    return (b & (1 << i)) ? 1 : 0;
}

// Generate full header: [0..47]=L-SIG, [48..143]=HT-SIG
void signal_field_impl::generate_signal_field(char* out,
                                              frame_param& frame,
                                              ofdm_param& ofdm)
{
    // L-SIG for HT-mixed
    generate_l_sig_header(out, frame, ofdm);

    // HT-SIG (2 symbols)
    generate_ht_sig_header(out + 48, frame, ofdm);
}

bool signal_field_impl::header_formatter(long packet_len,
                                         unsigned char* out,
                                         const std::vector<tag_t>& tags)
{
    (void)packet_len;

    bool encoding_found = false;
    bool len_found = false;
    int encoding = 0;
    int len = 0;

    std::fprintf(stderr, "[SIGNAL_FORMATTER] called with %zu tags\n", tags.size());
    for (size_t i = 0; i < tags.size(); i++) {
        std::fprintf(stderr, "[SIGNAL_FORMATTER] tag[%zu]: key=%s\n", i, pmt::symbol_to_string(tags[i].key).c_str());
        if (pmt::eq(tags[i].key, pmt::mp("encoding"))) {
            encoding_found = true;
            encoding = pmt::to_long(tags[i].value);
            std::fprintf(stderr, "[SIGNAL_FORMATTER] found encoding=%d\n", encoding);
        } else if (pmt::eq(tags[i].key, pmt::mp("psdu_len"))) {
            len_found = true;
            len = pmt::to_long(tags[i].value);
            std::fprintf(stderr, "[SIGNAL_FORMATTER] found len=%d\n", len);
        }
    }

    if ((!encoding_found) || (!len_found)) {
        std::fprintf(stderr, "[SIGNAL_FORMATTER] missing: encoding_found=%d len_found=%d\n", encoding_found, len_found);
        return false;
    }

    std::fprintf(stderr, "[SIGNAL_FORMATTER] encoding=%d len=%d\n", encoding, len);

    ofdm_param ofdm((Encoding)encoding);
    frame_param frame(ofdm, len);

    generate_signal_field((char*)out, frame, ofdm);
    return true;
}

bool signal_field_impl::header_parser(const unsigned char* in,
                                      std::vector<tag_t>& tags)
{
    (void)in;
    (void)tags;
    throw std::runtime_error("not implemented yet");
    return false;
}
