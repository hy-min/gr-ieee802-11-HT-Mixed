#include "ldpc_wifi_codec.h"
#include "LdpcCode.h"
#include <cstring>
#include <stdexcept>

namespace gr {
namespace ieee802_11 {

class ldpc_wifi_codec::impl {
public:
    std::unique_ptr<LdpcCode> code;
};

ldpc_wifi_codec::ldpc_wifi_codec()
    : d_pimpl(std::make_unique<impl>()),
      d_initialized(false),
      d_n(0),
      d_k(0)
{
}

ldpc_wifi_codec::~ldpc_wifi_codec() = default;

bool ldpc_wifi_codec::init(unsigned block_length, unsigned rate_index)
{
    unsigned info_length = 0;
    switch (rate_index) {
        case 0: info_length = block_length / 2; break;
        case 1: info_length = block_length * 2 / 3; break;
        case 2: info_length = block_length * 3 / 4; break;
        case 3: info_length = block_length * 5 / 6; break;
        default: return false;
    }

    d_pimpl->code = std::make_unique<LdpcCode>(block_length, info_length);
    d_pimpl->code->load_wifi_ldpc(block_length, rate_index);

    d_n = block_length;
    d_k = info_length;
    d_initialized = true;
    return true;
}

void ldpc_wifi_codec::encode(const uint8_t* info_bits, int info_len,
                             uint8_t* coded_bits, int coded_len)
{
    if (!d_initialized) {
        throw std::runtime_error("LDPC codec not initialized");
    }
    if (info_len != d_k || coded_len != d_n) {
        throw std::runtime_error("LDPC encode: buffer size mismatch");
    }

    std::vector<uint8_t> info(info_bits, info_bits + info_len);
    std::vector<uint8_t> codeword = d_pimpl->code->encode(info);
    std::memcpy(coded_bits, codeword.data(), coded_len);
}

void ldpc_wifi_codec::decode(const float* llr_in, int llr_len,
                             uint8_t* decoded_bits, int decoded_len,
                             int max_iter, bool min_sum)
{
    if (!d_initialized) {
        throw std::runtime_error("LDPC codec not initialized");
    }
    if (llr_len != d_n || decoded_len != d_k) {
        throw std::runtime_error("LDPC decode: buffer size mismatch");
    }

    std::vector<double> llr(llr_in, llr_in + llr_len);
    std::vector<uint8_t> codeword = d_pimpl->code->decode(llr, max_iter, min_sum);
    std::memcpy(decoded_bits, codeword.data(), decoded_len);
}

} // namespace ieee802_11
} // namespace gr
