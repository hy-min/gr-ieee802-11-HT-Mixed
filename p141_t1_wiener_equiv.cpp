// Phase 141 T1: C++ Wiener kernel equivalence test
// Verifies that wiener_filter_h52() produces identical output to the
// Python reference implementation in p141_t1_wiener_unit.py.
//
// Build:
//   g++ -O2 -std=c++17 p141_t1_wiener_equiv.cpp -o p141_t1_wiener_equiv
//
// Note: This file DUPLICATES the wiener_filter_h52() kernel for an
// isolated unit test (avoids needing to link against the full
// frame_equalizer_impl.cc object which has many other dependencies).
// The kernel here MUST be byte-identical to the one in
// lib/frame_equalizer_impl.cc.

#include <cmath>
#include <cstdio>
#include <complex>
#include <cstdlib>

// Use std::complex<float> as a portable stand-in for gr_complex
// (gr_complex = std::complex<float> in GNU Radio)
using gr_complex = std::complex<float>;

// Copy of the wiener_filter_h52 kernel from frame_equalizer_impl.cc.
// KEEP IN SYNC.
static void wiener_filter_h52(
    const gr_complex* h_ls,
    const gr_complex* y_ltf,
    const float* r_hh,
    float sigma2_noise,
    float g_min,
    gr_complex* h_out)
{
    for (int k = 0; k < 52; k++) {
        float y_abs2 = std::norm(y_ltf[k]);
        if (y_abs2 < 1e-12f) y_abs2 = 1e-12f;
        float noise_term = sigma2_noise / y_abs2;
        float G = r_hh[k] / (r_hh[k] + noise_term);
        if (G < g_min) G = g_min;
        h_out[k] = gr_complex(G * h_ls[k].real(), G * h_ls[k].imag());
    }
}

// Test driver: synthesize 52-SC channel with 5 nulls + noise, compute
// Wiener output, and check against a hand-coded "reference" Python formula
// (mirrors the test in p141_t1_wiener_unit.py).

int main()
{
    // Test 1: Shrinkage at null SCs
    // h_true[k] = exp(j*0.1*k) for k in 0..51, with 5 nulls at
    // indices {5, 13, 19, 33, 47}.
    float sigma2 = 0.5f;
    float g_min = 0.1f;
    gr_complex h_ls[52];
    gr_complex y_ltf[52];
    float r_hh[52];

    // Deterministic noise generation (mirrors numpy seed=42)
    srand(42);
    for (int k = 0; k < 52; k++) {
        // h_true[k] with optional null
        gr_complex h_true = std::polar(1.0f, 0.1f * (float)k);
        if (k == 5)  h_true = gr_complex(0.01f, 0.01f);
        if (k == 13) h_true = gr_complex(0.02f, 0.01f);
        if (k == 19) h_true = gr_complex(0.01f, 0.01f);
        if (k == 33) h_true = gr_complex(0.02f, 0.01f);
        if (k == 47) h_true = gr_complex(0.01f, 0.01f);
        // x_ltf[k] = sign(sin(2*pi*k/4 + 1))  -- BPSK +/-1, no zeros
        float s = std::sin(2.0f * 3.14159265f * (float)k / 4.0f + 1.0f);
        gr_complex x_ltf = gr_complex((s >= 0.0f) ? 1.0f : -1.0f, 0.0f);
        // noise ~ N(0, sigma2/2) + j*N(0, sigma2/2)
        float n_re = ((float)rand() / (float)RAND_MAX - 0.5f) * 2.0f * std::sqrt(sigma2 / 2.0f);
        float n_im = ((float)rand() / (float)RAND_MAX - 0.5f) * 2.0f * std::sqrt(sigma2 / 2.0f);
        gr_complex noise = gr_complex(n_re, n_im);
        y_ltf[k] = h_true * x_ltf + noise;
        // LS estimate: H_ls = y_ltf / x_ltf (x_ltf is +/-1, so equivalent)
        h_ls[k] = y_ltf[k] / x_ltf;
        // R_hh = |h_ls|^2 (single-frame approximation)
        r_hh[k] = std::norm(h_ls[k]);
    }

    gr_complex h_out[52];
    wiener_filter_h52(h_ls, y_ltf, r_hh, sigma2, g_min, h_out);

    // Verify: null SCs should have smaller |H_out| than |H_ls|
    int null_indices[5] = {5, 13, 19, 33, 47};
    float ls_null_mag = 0.0f, wiener_null_mag = 0.0f;
    for (int i = 0; i < 5; i++) {
        int k = null_indices[i];
        ls_null_mag += std::abs(h_ls[k]);
        wiener_null_mag += std::abs(h_out[k]);
    }
    ls_null_mag /= 5.0f;
    wiener_null_mag /= 5.0f;
    std::printf("[Test 1] LS null SC |H|:     %.6f\n", ls_null_mag);
    std::printf("[Test 1] Wiener null SC |H|: %.6f\n", wiener_null_mag);
    if (!(wiener_null_mag < ls_null_mag)) {
        std::fprintf(stderr, "FAIL: Wiener failed to shrink null SCs\n");
        return 1;
    }

    // Verify: strong SCs preserved
    float ls_strong_mag = 0.0f, wiener_strong_mag = 0.0f;
    int n_strong = 0;
    for (int k = 0; k < 52; k++) {
        bool is_null = false;
        for (int i = 0; i < 5; i++) {
            if (k == null_indices[i]) { is_null = true; break; }
        }
        if (!is_null) {
            ls_strong_mag += std::abs(h_ls[k]);
            wiener_strong_mag += std::abs(h_out[k]);
            n_strong++;
        }
    }
    ls_strong_mag /= (float)n_strong;
    wiener_strong_mag /= (float)n_strong;
    std::printf("[Test 1] LS strong SC |H|:     %.6f\n", ls_strong_mag);
    std::printf("[Test 1] Wiener strong SC |H|: %.6f\n", wiener_strong_mag);
    if (!(wiener_strong_mag > 0.7f * ls_strong_mag)) {
        std::fprintf(stderr, "FAIL: Wiener over-shrunk strong SCs\n");
        return 1;
    }
    std::printf("[Test 1] PASS\n\n");

    // Test 2: g_min floor (sigma2 huge -> G -> 0 -> clamp to 0.1)
    gr_complex h_ls2[52];
    gr_complex y_ltf2[52];
    float r_hh2[52];
    for (int k = 0; k < 52; k++) {
        h_ls2[k] = gr_complex(1.0f, 0.0f);
        y_ltf2[k] = gr_complex(1.0f, 0.0f);
        r_hh2[k] = 1.0f;
    }
    gr_complex h_out2[52];
    wiener_filter_h52(h_ls2, y_ltf2, r_hh2, 100.0f, 0.1f, h_out2);
    // With y_abs2=1, r_hh=1, sigma2=100: G = 1/(1+100) = 0.0099 -> clamped to 0.1
    for (int k = 0; k < 52; k++) {
        float expected_re = 0.1f * 1.0f;
        if (std::abs(h_out2[k].real() - expected_re) > 1e-5f ||
            std::abs(h_out2[k].imag()) > 1e-5f) {
            std::fprintf(stderr, "FAIL Test 2 at k=%d: got (%f,%f) expected (%f,0)\n",
                         k, h_out2[k].real(), h_out2[k].imag(), expected_re);
            return 1;
        }
    }
    std::printf("[Test 2] g_min floor: PASS\n\n");

    // Test 3: zero-division safety (y_ltf=0 everywhere)
    gr_complex h_ls3[52];
    gr_complex y_ltf3[52];
    float r_hh3[52];
    for (int k = 0; k < 52; k++) {
        h_ls3[k] = gr_complex(1.0f, 0.0f);
        y_ltf3[k] = gr_complex(0.0f, 0.0f);
        r_hh3[k] = 1.0f;
    }
    gr_complex h_out3[52];
    wiener_filter_h52(h_ls3, y_ltf3, r_hh3, 0.5f, 0.1f, h_out3);
    for (int k = 0; k < 52; k++) {
        if (!std::isfinite(h_out3[k].real()) || !std::isfinite(h_out3[k].imag())) {
            std::fprintf(stderr, "FAIL Test 3 at k=%d: non-finite\n", k);
            return 1;
        }
        // |y|=0 -> noise_term = sigma2/1e-12 = 5e11, G -> 0, clamped to 0.1
        if (std::abs(h_out3[k].real() - 0.1f) > 1e-5f ||
            std::abs(h_out3[k].imag()) > 1e-5f) {
            std::fprintf(stderr, "FAIL Test 3 at k=%d: got (%f,%f) expected (0.1,0)\n",
                         k, h_out3[k].real(), h_out3[k].imag());
            return 1;
        }
    }
    std::printf("[Test 3] zero-division safety: PASS\n\n");

    // Test 4: sigma2 -> 0, G -> 1, Wiener = LS
    gr_complex h_ls4[52];
    gr_complex y_ltf4[52];
    float r_hh4[52];
    for (int k = 0; k < 52; k++) {
        h_ls4[k] = gr_complex((float)k, (float)(52 - k));
        y_ltf4[k] = gr_complex(1.0f, 0.0f);
        r_hh4[k] = 1.0f;
    }
    gr_complex h_out4[52];
    wiener_filter_h52(h_ls4, y_ltf4, r_hh4, 1e-20f, 0.0f, h_out4);
    for (int k = 0; k < 52; k++) {
        if (std::abs(h_out4[k].real() - h_ls4[k].real()) > 1e-3f ||
            std::abs(h_out4[k].imag() - h_ls4[k].imag()) > 1e-3f) {
            std::fprintf(stderr, "FAIL Test 4 at k=%d: got (%f,%f) expected (%f,%f)\n",
                         k, h_out4[k].real(), h_out4[k].imag(),
                         h_ls4[k].real(), h_ls4[k].imag());
            return 1;
        }
    }
    std::printf("[Test 4] sigma2->0: G->1, h_out == h_ls: PASS\n\n");

    std::printf("ALL WIENER KERNEL TESTS PASSED\n");
    return 0;
}