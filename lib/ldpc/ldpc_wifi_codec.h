#ifndef INCLUDED_LDPC_WIFI_CODEC_H
#define INCLUDED_LDPC_WIFI_CODEC_H

#include <vector>
#include <cstdint>
#include <memory>

namespace gr {
namespace ieee802_11 {

class ldpc_wifi_codec {
public:
    ldpc_wifi_codec();
    ~ldpc_wifi_codec();

    bool init(unsigned block_length, unsigned rate_index);

    void encode(const uint8_t* info_bits, int info_len, uint8_t* coded_bits, int coded_len);

    void decode(const float* llr_in, int llr_len, uint8_t* decoded_bits, int decoded_len,
                int max_iter = 50, bool min_sum = true);

    int get_n() const { return d_n; }
    int get_k() const { return d_k; }
    bool is_initialized() const { return d_initialized; }

private:
    class impl;
    std::unique_ptr<impl> d_pimpl;
    bool d_initialized;
    int d_n;
    int d_k;
};

} // namespace ieee802_11
} // namespace gr

#endif // INCLUDED_LDPC_WIFI_CODEC_H
