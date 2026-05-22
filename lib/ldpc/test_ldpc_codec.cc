#include "ldpc_wifi_codec.h"
#include <cstdint>
#include <cstdlib>
#include <cmath>
#include <cstring>
#include <iostream>
#include <vector>
#include <random>

using namespace gr::ieee802_11;

static int g_tests_passed = 0;
static int g_tests_failed = 0;

static void report_test(const char* name, bool passed)
{
    if (passed) {
        std::cout << "[PASS] " << name << std::endl;
        ++g_tests_passed;
    } else {
        std::cout << "[FAIL] " << name << std::endl;
        ++g_tests_failed;
    }
}

// ------------------------------------------------------------------
// Test 1: clean encode -> decode for all 12 (N, rate) combinations
// ------------------------------------------------------------------
static bool test_clean_encode_decode(unsigned block_length, unsigned rate_index)
{
    ldpc_wifi_codec codec;
    if (!codec.init(block_length, rate_index)) {
        std::cerr << "  init failed for N=" << block_length
                  << " rate=" << rate_index << std::endl;
        return false;
    }

    int N = codec.get_n();
    int K = codec.get_k();

    std::vector<uint8_t> info_bits(K);
    for (int i = 0; i < K; ++i) {
        info_bits[i] = static_cast<uint8_t>(std::rand() & 1);
    }

    std::vector<uint8_t> coded_bits(N);
    codec.encode(info_bits.data(), K, coded_bits.data(), N);

    // Perfect LLRs: +20 for bit 0, -20 for bit 1
    std::vector<float> llr(N);
    for (int i = 0; i < N; ++i) {
        llr[i] = (coded_bits[i] == 0) ? 20.0f : -20.0f;
    }

    std::vector<uint8_t> decoded_bits(K);
    codec.decode(llr.data(), N, decoded_bits.data(), K, 50, true);

    bool match = true;
    for (int i = 0; i < K; ++i) {
        if (decoded_bits[i] != info_bits[i]) {
            match = false;
            break;
        }
    }
    return match;
}

// ------------------------------------------------------------------
// Test 2: noisy BPSK + AWGN for N=648, rate=0
// ------------------------------------------------------------------
static void test_noisy(unsigned block_length,
                       unsigned rate_index,
                       double sigma,
                       int num_trials)
{
    ldpc_wifi_codec codec;
    if (!codec.init(block_length, rate_index)) {
        std::cerr << "  init failed" << std::endl;
        return;
    }

    int N = codec.get_n();
    int K = codec.get_k();

    std::mt19937 rng(42);
    std::normal_distribution<double> noise(0.0, sigma);

    long long total_info_bits = 0;
    long long total_bit_errors = 0;
    int trials_passed = 0;

    for (int trial = 0; trial < num_trials; ++trial) {
        std::vector<uint8_t> info_bits(K);
        for (int i = 0; i < K; ++i) {
            info_bits[i] = static_cast<uint8_t>(std::rand() & 1);
        }

        std::vector<uint8_t> coded_bits(N);
        codec.encode(info_bits.data(), K, coded_bits.data(), N);

        // BPSK: 0 -> +1, 1 -> -1, then add AWGN
        std::vector<double> y(N);
        for (int i = 0; i < N; ++i) {
            double s = (coded_bits[i] == 0) ? 1.0 : -1.0;
            y[i] = s + noise(rng);
        }

        // LLR = 2*y / sigma^2
        double sigma2 = sigma * sigma;
        std::vector<float> llr(N);
        for (int i = 0; i < N; ++i) {
            llr[i] = static_cast<float>(2.0 * y[i] / sigma2);
        }

        std::vector<uint8_t> decoded_bits(K);
        codec.decode(llr.data(), N, decoded_bits.data(), K, 50, true);

        bool match = true;
        for (int i = 0; i < K; ++i) {
            if (decoded_bits[i] != info_bits[i]) {
                match = false;
                ++total_bit_errors;
            }
        }
        if (match) {
            ++trials_passed;
        }
        total_info_bits += K;
    }

    double ber = static_cast<double>(total_bit_errors) / static_cast<double>(total_info_bits);
    std::cout << "  sigma=" << sigma
              << "  trials=" << num_trials
              << "  passed=" << trials_passed
              << "  BER=" << ber
              << "  (info_bits=" << total_info_bits
              << "  errors=" << total_bit_errors << ")"
              << std::endl;
}

// ------------------------------------------------------------------
// main
// ------------------------------------------------------------------
int main(int argc, char* argv[])
{
    (void)argc;
    (void)argv;

    std::srand(42);

    std::cout << "========================================" << std::endl;
    std::cout << "LDPC WiFi Codec Unit Test" << std::endl;
    std::cout << "========================================" << std::endl;

    // ---- Clean encode/decode tests (all 12 combos) ----
    std::cout << std::endl;
    std::cout << "--- Clean encode->decode (all 12 combos) ---" << std::endl;

    unsigned blocks[] = {648, 1296, 1944};
    unsigned rates[]  = {0, 1, 2, 3};

    for (size_t bi = 0; bi < 3; ++bi) {
        for (size_t ri = 0; ri < 4; ++ri) {
            unsigned N = blocks[bi];
            unsigned r = rates[ri];
            char name[64];
            std::snprintf(name, sizeof(name), "N=%u rate=%u", N, r);
            bool ok = test_clean_encode_decode(N, r);
            report_test(name, ok);
        }
    }

    // ---- Noisy tests (N=648, rate=0) ----
    std::cout << std::endl;
    std::cout << "--- Noisy BPSK + AWGN (N=648, rate=0) ---" << std::endl;

    test_noisy(648, 0, 0.3, 10);
    test_noisy(648, 0, 0.5, 10);
    test_noisy(648, 0, 0.7, 10);

    // ---- Summary ----
    std::cout << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << "Summary: " << g_tests_passed << " passed, "
              << g_tests_failed << " failed" << std::endl;
    std::cout << "========================================" << std::endl;

    return (g_tests_failed > 0) ? 1 : 0;
}
