#include "utils.h"

#include <math.h>
#include <cassert>
#include <cstring>
#include <algorithm>
#include <iostream>

using gr::ieee802_11::BPSK_1_2;
using gr::ieee802_11::BPSK_3_4;
using gr::ieee802_11::QPSK_1_2;
using gr::ieee802_11::QPSK_3_4;
using gr::ieee802_11::QAM16_1_2;
using gr::ieee802_11::QAM16_3_4;
using gr::ieee802_11::QAM64_2_3;
using gr::ieee802_11::QAM64_3_4;
using gr::ieee802_11::QAM64_5_6;

ofdm_param::ofdm_param(Encoding e)
{
    encoding = e;

    // 这里保持 legacy OFDM 参数（48 data carriers）
    // HT 路径需要在 mapper/decode_mac 中显式覆盖 n_bpsc/n_cbps/n_dbps
    switch (e) {
    case BPSK_1_2:
        n_bpsc = 1;
        n_cbps = 48;
        n_dbps = 24;
        rate_field = 0x0D;
        break;

    case BPSK_3_4:
        n_bpsc = 1;
        n_cbps = 48;
        n_dbps = 36;
        rate_field = 0x0F;
        break;

    case QPSK_1_2:
        n_bpsc = 2;
        n_cbps = 96;
        n_dbps = 48;
        rate_field = 0x05;
        break;

    case QPSK_3_4:
        n_bpsc = 2;
        n_cbps = 96;
        n_dbps = 72;
        rate_field = 0x07;
        break;

    case QAM16_1_2:
        n_bpsc = 4;
        n_cbps = 192;
        n_dbps = 96;
        rate_field = 0x09;
        break;

    case QAM16_3_4:
        n_bpsc = 4;
        n_cbps = 192;
        n_dbps = 144;
        rate_field = 0x0B;
        break;

    case QAM64_2_3:
        n_bpsc = 6;
        n_cbps = 288;
        n_dbps = 192;
        rate_field = 0x01;
        break;

    case QAM64_3_4:
        n_bpsc = 6;
        n_cbps = 288;
        n_dbps = 216;
        rate_field = 0x03;
        break;

    case QAM64_5_6:
        // 工程里给 HT MCS7 借壳用
        // legacy SIGNAL 本身没有 5/6，这里只给占位值
        n_bpsc = 6;
        n_cbps = 288;
        n_dbps = 240;
        rate_field = 0x00;
        break;

    default:
        assert(false);
        break;
    }
}

void ofdm_param::print()
{
    std::cout << "OFDM Parameters:" << std::endl;
    std::cout << "encoding :" << encoding << std::endl;
    std::cout << "rate_field :" << (int)rate_field << std::endl;
    std::cout << "n_bpsc :" << n_bpsc << std::endl;
    std::cout << "n_cbps :" << n_cbps << std::endl;
    std::cout << "n_dbps :" << n_dbps << std::endl;
}

frame_param::frame_param(ofdm_param& ofdm, int psdu_length)
{
    psdu_size = psdu_length;
    n_sym = (int)ceil((16 + 8 * psdu_size + 6) / (double)ofdm.n_dbps);
    n_data_bits = n_sym * ofdm.n_dbps;
    n_pad = n_data_bits - (16 + 8 * psdu_size + 6);
    n_encoded_bits = n_sym * ofdm.n_cbps;
}

void frame_param::print()
{
    std::cout << "FRAME Parameters:" << std::endl;
    std::cout << "psdu_size: " << psdu_size << std::endl;
    std::cout << "n_sym: " << n_sym << std::endl;
    std::cout << "n_pad: " << n_pad << std::endl;
    std::cout << "n_encoded_bits: " << n_encoded_bits << std::endl;
    std::cout << "n_data_bits: " << n_data_bits << std::endl;
}

void scramble(const char* in, char* out, frame_param& frame, char initial_state)
{
    int state = initial_state;
    int feedback;

    for (int i = 0; i < frame.n_data_bits; i++) {
        feedback = (!!(state & 64)) ^ (!!(state & 8));
        out[i] = feedback ^ in[i];
        state = ((state << 1) & 0x7e) | feedback;
    }
}

void reset_tail_bits(char* scrambled_data, frame_param& frame)
{
    memset(scrambled_data + frame.n_data_bits - frame.n_pad - 6, 0, 6 * sizeof(char));
}

int ones(int n)
{
    int sum = 0;
    for (int i = 0; i < 8; i++) {
        if (n & (1 << i)) {
            sum++;
        }
    }
    return sum;
}

void convolutional_encoding(const char* in, char* out, frame_param& frame)
{
    int state = 0;

    for (int i = 0; i < frame.n_data_bits; i++) {
        assert(in[i] == 0 || in[i] == 1);
        state = ((state << 1) & 0x7e) | in[i];
        out[i * 2]     = ones(state & 0133) % 2;
        out[i * 2 + 1] = ones(state & 0171) % 2;
    }
}

void puncturing(const char* in, char* out, frame_param& frame, ofdm_param& ofdm)
{
    int mod;

    for (int i = 0; i < frame.n_data_bits * 2; i++) {
        switch (ofdm.encoding) {
        case BPSK_1_2:
        case QPSK_1_2:
        case QAM16_1_2:
            *out = in[i];
            out++;
            break;

        case QAM64_2_3:
            if (i % 4 != 3) {
                *out = in[i];
                out++;
            }
            break;

        case BPSK_3_4:
        case QPSK_3_4:
        case QAM16_3_4:
        case QAM64_3_4:
            mod = i % 6;
            if (!(mod == 3 || mod == 4)) {
                *out = in[i];
                out++;
            }
            break;

        case QAM64_5_6: {
            int m = i % 10;
            if (!(m == 3 || m == 4 || m == 7 || m == 8)) {
                *out = in[i];
                out++;
            }
            break;
        }

        default:
            assert(false);
            break;
        }
    }
}

void interleave(const char* in, char* out, frame_param& frame, ofdm_param& ofdm, bool reverse)
{
    const int n_cbps = ofdm.n_cbps;
    const int s = std::max(ofdm.n_bpsc / 2, 1);

    int n_col = 0;
    int n_row = 0;

    // legacy 48-carrier
    if (n_cbps == 48 || n_cbps == 96 || n_cbps == 192 || n_cbps == 288) {
        n_col = 16;
        n_row = 3 * ofdm.n_bpsc;
    }
    // HT 20MHz 52-carrier
    else if (n_cbps == 52 || n_cbps == 104 || n_cbps == 208 || n_cbps == 312) {
        n_col = 13;
        n_row = 4 * ofdm.n_bpsc;
    } else {
        assert(false);
    }

    assert(n_row * n_col == n_cbps);

    for (int sym = 0; sym < frame.n_sym; sym++) {
        const char* in_sym = in + sym * n_cbps;
        char* out_sym = out + sym * n_cbps;

        if (!reverse) {
            // interleaving
            for (int k = 0; k < n_cbps; k++) {
                const int i = n_row * (k % n_col) + (k / n_col);
                const int j =
                    s * (i / s) +
                    ((i + n_cbps - ((n_col * i) / n_cbps)) % s);
                out_sym[j] = in_sym[k];
            }
        } else {
            // deinterleaving
            for (int j = 0; j < n_cbps; j++) {
                const int i =
                    s * (j / s) +
                    ((j + (n_col * j) / n_cbps) % s);
                const int k =
                    n_col * i - (n_cbps - 1) * (i / n_row);
                out_sym[k] = in_sym[j];
            }
        }
    }
}

void split_symbols(const char* in, char* out, frame_param& frame, ofdm_param& ofdm)
{
    // 动态计算每个 OFDM symbol 的 data carriers 数
    // legacy: 48, HT20: 52
    const int data_carriers = ofdm.n_cbps / ofdm.n_bpsc;
    const int total_symbols = frame.n_sym * data_carriers;

    for (int i = 0; i < total_symbols; i++) {
        unsigned char sym = 0;

        for (int k = 0; k < ofdm.n_bpsc; k++) {
            assert(*in == 0 || *in == 1);
            sym |= ((*in) & 0x1) << k;
            in++;
        }

        out[i] = sym;
    }
}

void generate_bits(const char* psdu, char* data_bits, frame_param& frame)
{
    memset(data_bits, 0, 16);
    data_bits += 16;

    for (int i = 0; i < frame.psdu_size; i++) {
        for (int b = 0; b < 8; b++) {
            data_bits[i * 8 + b] = !!(psdu[i] & (1 << b));
        }
    }
}