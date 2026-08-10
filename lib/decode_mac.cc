// USRP debug log control - uncomment to enable verbose logs
#define USRP_DEBUG_LOGS
#ifdef USRP_DEBUG_LOGS
#define USRP_LOG(...) do { fprintf(stderr,  __VA_ARGS__); } while(0)
#define USRP_LOG_STD(...) do { std::USRP_LOG( __VA_ARGS__); } while(0)
#else
#define USRP_LOG(...) ((void)0)
#define USRP_LOG_STD(...) ((void)0)
#endif

#include <ieee802_11/decode_mac.h>
#include "utils.h"
#include "viterbi_decoder/viterbi_decoder.h"
#include "llr_demod.h"
#include "ldpc/ldpc_wifi_codec.h"

#include <gnuradio/io_signature.h>
#include <gnuradio/gr_complex.h>
#include <pmt/pmt.h>

#include <boost/crc.hpp>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <fstream>
#include <cstdio>
#include <sstream>
#include <string>
#include <vector>
#include <cmath>

using namespace gr::ieee802_11;

#define LINKTYPE_IEEE802_11 105

namespace {

static inline uint8_t hard_bpsk_bit(const gr_complex& x)
{
    return (x.real() >= 0.0f) ? 1 : 0;
}

static int ht_n_bpsc_from_mcs(int mcs)
{
    switch (mcs) {
    case 0: return 1;
    case 1: return 2;
    case 2: return 2;
    case 3: return 4;
    case 4: return 4;
    case 5: return 6;
    case 6: return 6;
    case 7: return 6;
    default: return 1;
    }
}

static int ht_n_cbps_from_mcs(int mcs)
{
    switch (mcs) {
    case 0: return 52;
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
    case 0: return 26;
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

static int ht_n_sym_from_mcs_len(int mcs, int len_bytes)
{
    const int n_dbps = ht_n_dbps_from_mcs(mcs);
    return (16 + 8 * len_bytes + 6 + n_dbps - 1) / n_dbps;
}

static int ht_n_sym_mcs0_only(int len_bytes)
{
    const int n_dbps = 26; // HT MCS0
    return (16 + 8 * len_bytes + 6 + n_dbps - 1) / n_dbps;
}

// HT 20MHz / 1SS / BPSK(MCS0) 去交织逆变换
static void ht_bpsk_deinterleave_52(const uint8_t* in52, uint8_t* out52)
{
    const int n_cbps = 52;
    const int n_bpsc = 1;
    const int s = std::max(n_bpsc / 2, 1);
    const int n_col = 13;
    const int n_row = n_cbps / n_col; // = 4

    std::memset(out52, 0, 52);

    for (int j = 0; j < n_cbps; j++) {
        const int first = (s * (j / s)) + ((j + ((n_col * j) / n_cbps)) % s);
        const int k = n_col * first - (n_cbps - 1) * (first / n_row);
        out52[k] = in52[j];
    }
}

// Generic HT deinterleaving for 52 subcarriers
static void ht_deinterleave(const uint8_t* in, uint8_t* out, int n_sym, int mcs)
{
    const int n_bpsc = ht_n_bpsc_from_mcs(mcs);
    const int n_cbps = ht_n_cbps_from_mcs(mcs);
    const int s = std::max(n_bpsc / 2, 1);
    const int n_col = 13;  // HT 20MHz: 13 columns
    const int n_row = n_cbps / n_col;  // 4 * n_bpsc

    // Verify dimensions
    if (n_row * n_col != n_cbps) {
        // Should not happen for valid HT MCS
        std::memset(out, 0, n_sym * n_cbps);
        return;
    }

    for (int sym = 0; sym < n_sym; sym++) {
        const uint8_t* in_sym = in + sym * n_cbps;
        uint8_t* out_sym = out + sym * n_cbps;

        // Deinterleaving (reverse operation of interleaving)
        // Based on utils.cc interleave() function with reverse=true
        for (int j = 0; j < n_cbps; j++) {
            const int i = s * (j / s) + ((j + (n_col * j) / n_cbps) % s);
            const int k = n_col * i - (n_cbps - 1) * (i / n_row);
            out_sym[k] = in_sym[j];
        }
    }
}

// Phase 162: float variant of ht_deinterleave for soft LLRs. Identical index
// math; kept separate from the uint8_t version to avoid touching the hot
// hard-decision path.
static void ht_deinterleave_f32(const float* in, float* out, int n_sym, int mcs)
{
    const int n_bpsc = ht_n_bpsc_from_mcs(mcs);
    const int n_cbps = ht_n_cbps_from_mcs(mcs);
    const int s = std::max(n_bpsc / 2, 1);
    const int n_col = 13;  // HT 20MHz: 13 columns
    const int n_row = n_cbps / n_col;  // 4 * n_bpsc

    if (n_row * n_col != n_cbps) {
        std::memset(out, 0, sizeof(float) * (size_t)n_sym * (size_t)n_cbps);
        return;
    }

    for (int sym = 0; sym < n_sym; sym++) {
        const float* in_sym = in + sym * n_cbps;
        float* out_sym = out + sym * n_cbps;

        for (int j = 0; j < n_cbps; j++) {
            const int i = s * (j / s) + ((j + (n_col * j) / n_cbps) % s);
            const int k = n_col * i - (n_cbps - 1) * (i / n_row);
            out_sym[k] = in_sym[j];
        }
    }
}

static std::string bits_to_string(const uint8_t* bits, int n)
{
    std::string s;
    s.reserve((size_t)n);
    for (int i = 0; i < n; i++) {
        s.push_back(bits[i] ? '1' : '0');
    }
    return s;
}

static std::string bytes_to_hex(const uint8_t* buf, int n)
{
    std::ostringstream oss;
    oss << std::hex << std::setfill('0');
    for (int i = 0; i < n; i++) {
        if (i) {
            oss << ' ';
        }
        oss << std::setw(2) << int(buf[i]);
    }
    return oss.str();
}

static inline uint32_t read_le_u32(const uint8_t* p)
{
    return (uint32_t(p[0])      ) |
           (uint32_t(p[1]) <<  8) |
           (uint32_t(p[2]) << 16) |
           (uint32_t(p[3]) << 24);
}

static inline void write_le_u32(uint32_t v, uint8_t out[4])
{
    out[0] = uint8_t(v & 0xFF);
    out[1] = uint8_t((v >> 8) & 0xFF);
    out[2] = uint8_t((v >> 16) & 0xFF);
    out[3] = uint8_t((v >> 24) & 0xFF);
}


static void dump_bits_to_file(const char* path, const uint8_t* bits, int n)
{
    std::ofstream ofs(path, std::ios::out | std::ios::trunc | std::ios::binary);
    if (!ofs) {
        return;
    }
    for (int i = 0; i < n; i++) {
        ofs.put(bits[i] ? '1' : '0');
    }
    ofs.put('\n');
}

static bool read_bits_from_file(const char* path, std::vector<uint8_t>& bits)
{
    std::ifstream ifs(path, std::ios::in | std::ios::binary);
    if (!ifs) {
        return false;
    }

    bits.clear();
    char ch = 0;
    while (ifs.get(ch)) {
        if (ch == '0' || ch == '1') {
            bits.push_back(uint8_t(ch - '0'));
        }
    }
    return !bits.empty();
}


static bool read_bits_prefer_last(const char* last_path,
                                  const char* fallback_path,
                                  std::vector<uint8_t>& bits,
                                  std::string& used_path)
{
    if (read_bits_from_file(last_path, bits)) {
        used_path = last_path;
        return true;
    }
    if (read_bits_from_file(fallback_path, bits)) {
        used_path = fallback_path;
        return true;
    }
    used_path.clear();
    return false;
}

// Map HT MCS to Encoding
static Encoding mcs_to_encoding(int mcs)
{
    switch (mcs) {
    case 0: return BPSK_1_2;
    case 1: return QPSK_1_2;
    case 2: return QPSK_3_4;
    case 3: return QAM16_1_2;
    case 4: return QAM16_3_4;
    case 5: return QAM64_2_3;
    case 6: return QAM64_3_4;
    case 7: return QAM64_5_6;
    default: return BPSK_1_2;
    }
}

// Hard demodulation for QPSK (2 bits per subcarrier)
// Returns two bits: [bit0, bit1] where bit0 is LSB (real>0), bit1 is MSB (imag>0)
static void hard_qpsk_bits(const gr_complex& x, uint8_t bits[2])
{
    bits[0] = (x.real() >= 0.0f) ? 1 : 0;
    bits[1] = (x.imag() >= 0.0f) ? 1 : 0;
}

// Hard demodulation for 16-QAM (4 bits per subcarrier)
// Based on constellation_16qam_impl::decision_maker
static void hard_16qam_bits(const gr_complex& x, uint8_t bits[4])
{
    const float level = sqrtf(0.1f);
    float re = x.real();
    float im = x.imag();

    // Bit 0: real > 0
    bits[0] = (re > 0) ? 1 : 0;
    // Bit 1: |real| < 2*level
    bits[1] = (std::abs(re) < (2 * level)) ? 1 : 0;
    // Bit 2: imag > 0
    bits[2] = (im > 0) ? 1 : 0;
    // Bit 3: |imag| < 2*level
    bits[3] = (std::abs(im) < (2 * level)) ? 1 : 0;
}

// Hard demodulation for 64-QAM (6 bits per subcarrier)
// Maps to constellation_64qam_impl::d_constellation Gray-code ordering.
// |I|/|Q| level -> (bit2,bit1) lookup: 7->(0,0), 5->(1,0), 3->(0,1), 1->(1,1)
static void hard_64qam_bits(const gr_complex& x, uint8_t bits[6], float level)
{
    // 64QAM constellation I/Q levels: 1, 3, 5, 7 * level (absolute values)
    static const float kIqLevels[4] = { 1.0f, 3.0f, 5.0f, 7.0f };
    // Gray-code lookup: index 0=|I|=1, 1=|I|=3, 2=|I|=5, 3=|I|=7
    // Each pair is (bit2, bit1)
    static const uint8_t kLevelToBits[4][2] = {
        {0, 1}, // |level|=1 -> bits[high]=0, bits[low]=1
        {1, 1}, // |level|=3 -> bits[high]=1, bits[low]=1
        {1, 0}, // |level|=5 -> bits[high]=1, bits[low]=0
        {0, 0}, // |level|=7 -> bits[high]=0, bits[low]=0
    };

    auto map_component = [&](float v, int bit0_idx, int bit1_idx, int bit2_idx) {
        float av = std::abs(v);
        // Find nearest level by absolute distance
        int best_idx = 0;
        float best_dist = std::abs(av - kIqLevels[0] * level);
        for (int i = 1; i < 4; i++) {
            float d = std::abs(av - kIqLevels[i] * level);
            if (d < best_dist) {
                best_dist = d;
                best_idx = i;
            }
        }
        bits[bit0_idx] = (v >= 0.0f) ? 1 : 0;
        bits[bit1_idx] = kLevelToBits[best_idx][1];
        bits[bit2_idx] = kLevelToBits[best_idx][0];
    };

    map_component(x.real(), 0, 1, 2);
    map_component(x.imag(), 3, 4, 5);

    // Diagnostic: verify fix is active (unique string)
    static int hard64_call_count = 0;
    hard64_call_count++;
    if (hard64_call_count == 1) {
        USRP_LOG( "[HARD64QAM_FIX] hard_64qam_bits Gray-code lookup fix active\n");
    }
}

} // anonymous namespace


class decode_mac_impl : public decode_mac
{
public:
    decode_mac_impl(bool log, bool debug)
        : block("decode_mac",
                gr::io_signature::make(1, 1, sizeof(gr_complex)),
                gr::io_signature::make(0, 0, 0)),
          d_log(log),
          d_debug(debug),
          d_in_frame(false),
          d_items_copied(0),
          d_items_expected(0),
          d_frame_seq(0),
          d_ht_mcs(-1),
          d_ht_len(0),
          d_ht_n_sym(0),
          d_ht_n_cbps(52),
          d_use_ldpc(false),
          d_scrambler_seed(1),
          d_ldpc_block_length(648),
          d_ofdm(BPSK_1_2),
          d_frame(d_ofdm, 0)
    {
        message_port_register_out(pmt::mp("out"));

        // Phase 162: data-path soft-decision viterbi (|H|^2-weighted LLR).
        // Opt-in, default OFF. When ON, the Conv path uses per-bit soft LLRs
        // (Re(eq) * |H_sc|^2 from the frame_equalizer "soft_h2" tag) with a
        // float deinterleave + soft-metric viterbi, instead of hard bits.
        // BPSK only (n_bpsc==1); other MCS fall back to the hard path.
        {
            const char* env_soft = std::getenv("IEEE80211_DATA_SOFT_VITERBI");
            d_data_soft_viterbi = (env_soft && env_soft[0] == '1');
            if (d_data_soft_viterbi) {
                USRP_LOG( "[DECODE_SOFT] IEEE80211_DATA_SOFT_VITERBI=1 "
                          "(|H|^2-weighted soft viterbi on data path)\n");
            }
        }
    }

    ~decode_mac_impl() override { log_incomplete("destructor"); }

    bool stop() override
    {
        log_incomplete("stop");
        return block::stop();
    }

    void forecast(int noutput_items, gr_vector_int& ninput_items_required) override
    {
        // This is a sink block (0 output ports). Force the scheduler to
        // call us whenever input is available by requesting at least 1 item.
        ninput_items_required[0] = 1;
    }

    int general_work(int,
                     gr_vector_int& ninput_items,
                     gr_vector_const_void_star& input_items,
                     gr_vector_void_star&)
    {
        static int gw_call_count = 0;
        gw_call_count++;
        const gr_complex* in = (const gr_complex*)input_items[0];
        int i = 0;
        const uint64_t nread = nitems_read(0);
        USRP_LOG( "[DECODE_MAC_GW] call=%d ninput=%d nread=%llu in_frame=%d copied=%llu/%llu\n",
                gw_call_count, ninput_items[0], (unsigned long long)nread,
                d_in_frame ? 1 : 0,
                (unsigned long long)d_items_copied, (unsigned long long)d_items_expected);

        while (i < ninput_items[0]) {
            std::vector<gr::tag_t> tags;
            get_tags_in_range(tags, 0, nread + i, nread + i + 1);

            if (!tags.empty()) {
                USRP_LOG( "[DECODE_TAG] nread=%llu i=%d n_tags=%zu",
                        (unsigned long long)nread, i, tags.size());
                for (const auto& t : tags) {
                    USRP_LOG( " key=%s", pmt::symbol_to_string(t.key).c_str());
                }
                USRP_LOG( "\n");
            }

            // Also search for LDPC tags in a wider window (they may be offset)
            std::vector<gr::tag_t> all_tags;
            get_tags_in_range(all_tags, 0, nread, nread + ninput_items[0]);

            const pmt::pmt_t k_frame_bytes_sp = pmt::mp("frame bytes");
            const pmt::pmt_t k_frame_bytes_us = pmt::mp("frame_bytes");
            const pmt::pmt_t k_mcs            = pmt::mp("mcs");

            for (auto& t : tags) {
                if (pmt::eq(t.key, k_frame_bytes_sp) || pmt::eq(t.key, k_frame_bytes_us)) {

                    if (d_in_frame && d_items_copied < d_items_expected) {
                        // Safety net: if we're already >50% into a frame, ignore
                        // spurious duplicate tags rather than restarting.
                        // This protects against upstream false detections.
                        const bool well_into_frame =
                            (d_items_expected > 0) &&
                            (d_items_copied > d_items_expected / 2);
                        if (well_into_frame) {
                            USRP_LOG("[DECODE_TAG] IGNORE duplicate frame_bytes tag "
                                     "at copied=%llu/%llu (already >50%%)\n",
                                     (unsigned long long)d_items_copied,
                                     (unsigned long long)d_items_expected);
                            break;  // Skip this tag, keep capturing current frame
                        }
                        log_incomplete("new_frame_tag_before_complete");
                    }

                    d_meta = pmt::make_dict();
                    for (auto& tt : tags) {
                        d_meta = pmt::dict_add(d_meta, tt.key, tt.value);
                    }

                    const bool has_mcs = pmt::dict_has_key(d_meta, k_mcs);
                    if (!has_mcs) {
                        if (d_debug) {
                            dout << "[decode_mac] skip non-HT frame (MCS 0-7 HT mode required)"
                                 << std::endl;
                        }
                        d_in_frame = false;
                        break;
                    }

                    d_ht_mcs = (int)pmt::to_uint64(
                        pmt::dict_ref(d_meta, k_mcs, pmt::from_uint64(0)));

                    uint64_t len = 0;
                    if (pmt::dict_has_key(d_meta, k_frame_bytes_us)) {
                        len = pmt::to_uint64(
                            pmt::dict_ref(d_meta, k_frame_bytes_us, pmt::from_uint64(0)));
                    } else {
                        len = pmt::to_uint64(
                            pmt::dict_ref(d_meta, k_frame_bytes_sp, pmt::from_uint64(0)));
                    }
                    d_ht_len = (int)len;

                    d_use_ldpc = false;
                    for (auto& tt : all_tags) {
                        if (pmt::eq(tt.key, pmt::mp("use_ldpc"))) {
                            d_use_ldpc = pmt::to_bool(tt.value);
                        }
                    }
                    d_scrambler_seed = 1;
                    for (auto& tt : all_tags) {
                        if (pmt::eq(tt.key, pmt::mp("scrambler_seed"))) {
                            d_scrambler_seed = (int)pmt::to_long(tt.value);
                        }
                    }
                    d_ldpc_block_length = 648; // default
                    for (auto& tt : all_tags) {
                        if (pmt::eq(tt.key, pmt::mp("ldpc_block_length"))) {
                            d_ldpc_block_length = (int)pmt::to_long(tt.value);
                        }
                    }

                    // Phase 162: per-SC |H|^2 reliability weights (soft_h2 tag,
                    // emitted by frame_equalizer at the same offset as frame_bytes
                    // when its IEEE80211_DATA_SOFT_VITERBI is also ON).
                    d_soft_h2_valid = false;
                    if (d_data_soft_viterbi) {
                        for (auto& tt : tags) {
                            if (pmt::eq(tt.key, pmt::mp("soft_h2")) &&
                                pmt::is_f32vector(tt.value)) {
                                const std::vector<float> w = pmt::f32vector_elements(tt.value);
                                if (w.size() == 52) {
                                    for (int k = 0; k < 52; k++) {
                                        d_soft_h2[k] = w[k];
                                    }
                                    d_soft_h2_valid = true;
                                }
                            }
                        }
                    }

                    // 检查MCS范围是否有效
                    if (d_ht_mcs < 0 || d_ht_mcs > 7) {
                        if (d_debug) {
                            dout << "[decode_mac] skip HT frame: invalid MCS=" << d_ht_mcs << std::endl;
                        }
                        d_in_frame = false;
                        break;
                    }

                    d_ht_n_sym = ht_n_sym_from_mcs_len(d_ht_mcs, d_ht_len);
                    // For LDPC, TX may use more symbols than convolutional
                    // Search in all_tags (wider window) since ldpc_n_sym may be at same offset
                    for (auto& tt : all_tags) {
                        if (pmt::eq(tt.key, pmt::mp("ldpc_n_sym"))) {
                            int tx_n_sym = (int)pmt::to_long(tt.value);
                            USRP_LOG( "[DECODE_TAG] ldpc_n_sym=%d current=%d use_ldpc=%d\n",
                                    tx_n_sym, d_ht_n_sym, d_use_ldpc ? 1 : 0);
                            if (tx_n_sym > d_ht_n_sym) {
                                d_ht_n_sym = tx_n_sym;
                            }
                        }
                    }
                    if (d_ht_n_sym <= 0) {
                        if (d_debug) {
                            dout << "[decode_mac] invalid HT n_sym, mcs=" << d_ht_mcs << " len=" << d_ht_len << std::endl;
                        }
                        d_in_frame = false;
                        break;
                    }

                    d_ht_n_cbps = ht_n_cbps_from_mcs(d_ht_mcs);
                    // Each OFDM symbol has 52 subcarriers for HT mode
                    const int n_sc = 52;
                    d_items_expected = (uint64_t)d_ht_n_sym * (uint64_t)n_sc;
                    d_items_copied   = 0;
                    d_in_frame       = true;
                    d_frame_seq++;

                    d_rx_eq.assign((size_t)d_items_expected, gr_complex(0.0f, 0.0f));

                    dout << "[decode_mac] capture HT frame"
                         << " mcs=" << d_ht_mcs
                         << " frame_seq=" << d_frame_seq
                         << " len=" << d_ht_len
                         << " n_sym=" << d_ht_n_sym
                         << " items_expected=" << d_items_expected
                         << std::endl;
                    break;
                }
            }

            if (!d_in_frame) {
                ++i;
                continue;
            }

            if (d_items_copied < d_items_expected) {
                d_rx_eq[(size_t)d_items_copied] = in[i];
                d_items_copied++;

                if ((d_items_copied % (uint64_t)d_ht_n_cbps) == 0ULL || d_items_copied == d_items_expected) {
                    dout << "[decode_mac][CAPTURE] frame_seq=" << d_frame_seq
                         << " copied=" << d_items_copied
                         << "/" << d_items_expected
                         << " syms=" << (d_items_copied / (uint64_t)d_ht_n_cbps)
                         << "/" << d_ht_n_sym
                         << std::endl;
                }

                if (d_items_copied == d_items_expected) {
                    dout << "[decode_mac][COMPLETE] frame_seq=" << d_frame_seq
                         << " got_full_frame=1" << std::endl;
                    decode_and_publish();
                    d_in_frame = false;
                }
            }

            ++i;
        }

        consume(0, i);
        return 0;
    }

private:
    void log_incomplete(const char* where)
    {
        if (!d_in_frame || d_items_expected == 0) {
            return;
        }

        const uint64_t full_syms = d_items_copied / 52ULL;
        const uint64_t rem_items = d_items_copied % 52ULL;
        const uint64_t missing_items = (d_items_expected > d_items_copied) ?
                                       (d_items_expected - d_items_copied) : 0ULL;
        const uint64_t missing_syms_floor = missing_items / 52ULL;
        const uint64_t missing_items_tail = missing_items % 52ULL;

        dout << "[decode_mac][INCOMPLETE] where=" << where
             << " frame_seq=" << d_frame_seq
             << " len=" << d_ht_len
             << " n_sym=" << d_ht_n_sym
             << " expected=" << d_items_expected
             << " got=" << d_items_copied
             << " full_syms=" << full_syms
             << " rem_items=" << rem_items
             << " missing_items=" << missing_items
             << " missing_syms_floor=" << missing_syms_floor
             << " missing_items_tail=" << missing_items_tail
             << std::endl;
    }

    void decode_and_publish()
    {
        // Fallback: check d_meta for use_ldpc if not set from tags
        if (!d_use_ldpc && d_meta) {
            if (pmt::dict_has_key(d_meta, pmt::mp("use_ldpc"))) {
                d_use_ldpc = pmt::to_bool(pmt::dict_ref(d_meta, pmt::mp("use_ldpc"), pmt::PMT_F));
            }
        }

        USRP_LOG( "[DECODE_AND_PUBLISH] called n_sym=%d n_cbps=%d n_dbps=%d d_ht_len=%d rx_eq_size=%zu use_ldpc=%d\n",
                d_ht_n_sym, d_ht_n_cbps, ht_n_dbps_from_mcs(d_ht_mcs), d_ht_len, d_rx_eq.size(), d_use_ldpc ? 1 : 0);
        if (d_rx_eq.empty() || d_ht_n_sym <= 0) {
            USRP_LOG( "[DECODE_AND_PUBLISH] no data captured\n");
            return;
        }

        // Use actual received data size to determine n_sym for LDPC
        int n_sym = d_ht_n_sym;
        if (d_use_ldpc) {
            int n_sc = 52; // HT 20MHz data carriers
            n_sym = (int)(d_rx_eq.size() / n_sc);
            if ((int)d_rx_eq.size() % n_sc != 0) {
                USRP_LOG( "[DECODE_LDPC] rx_eq_size=%zu not multiple of %d, using n_sym=%d\n",
                        d_rx_eq.size(), n_sc, n_sym);
            }
            USRP_LOG( "[DECODE_LDPC] using n_sym=%d (from rx_eq_size=%zu)\n",
                    n_sym, d_rx_eq.size());
        }
        const int n_cbps = d_ht_n_cbps;
        const int n_dbps = ht_n_dbps_from_mcs(d_ht_mcs);

        // Phase 161: full per-SC eq dump for selected data symbols (opt-in).
        // Used to root-cause the last-symbol bit-corruption defect (reproduces
        // on clean 20 MHz loopback: sym19-21 hard bits wrong while the 4
        // sentinel SCs in [EQ_HTDATA] look clean — need all 52 to tell
        // rotation vs mapping vs window). IEEE80211_SYM52_DUMP=last or =all.
        {
            static const char* env_sym52 = std::getenv("IEEE80211_SYM52_DUMP");
            if (env_sym52 && env_sym52[0] != '\0') {
                const bool all = (std::string(env_sym52) == "all");
                for (int s = 0; s < n_sym; s++) {
                    if (!all && s < n_sym - 2 && s != 5 && s != 7) continue;
                    char sbuf[1400];
                    int sn = snprintf(sbuf, sizeof(sbuf), "[SYM52] sym=%d eq=", s);
                    for (int k = 0; k < n_cbps && sn < (int)sizeof(sbuf) - 24; k++) {
                        sn += snprintf(sbuf + sn, sizeof(sbuf) - sn, "%.2f,%.2f,",
                                       d_rx_eq[(size_t)s * n_cbps + k].real(),
                                       d_rx_eq[(size_t)s * n_cbps + k].imag());
                    }
                    snprintf(sbuf + sn, sizeof(sbuf) - sn, "\n");
                    USRP_LOG("%s", sbuf);
                }
            }
        }

        d_rx_bits.assign((size_t)(n_sym * n_cbps), 0);
        d_deintl_bits.assign((size_t)(n_sym * n_cbps), 0);

        // 1) hard demap
        const int n_bpsc = ht_n_bpsc_from_mcs(d_ht_mcs);
        // Compute dynamic level for 64QAM to handle TX/RX amplitude mismatch.
        // frame_equalizer output amplitude is ~1.3x the standard value due to
        // TX/RX scaling.  Use max(|re|,|im|)/7 for best outer-point accuracy.
        float qam64_level = sqrtf(1.0f / 42.0f);
        if (n_bpsc == 6) {
            double re_min = 1e9, re_max = -1e9, im_min = 1e9, im_max = -1e9;
            float max_abs = 0.0f;
            for (size_t k = 0; k < d_rx_eq.size(); k++) {
                float re = d_rx_eq[k].real();
                float im = d_rx_eq[k].imag();
                re_min = std::min(re_min, (double)re);
                re_max = std::max(re_max, (double)re);
                im_min = std::min(im_min, (double)im);
                im_max = std::max(im_max, (double)im);
                max_abs = std::max(max_abs, std::max(std::abs(re), std::abs(im)));
            }
            if (max_abs > 0.1f) {
                qam64_level = max_abs / 7.0f;
            }
            USRP_LOG( "[DECODE_MAC] 64QAM sym stats: re=[%.3f,%.3f] im=[%.3f,%.3f] n=%zu max_abs=%.3f level=%.4f\n",
                    re_min, re_max, im_min, im_max, d_rx_eq.size(), max_abs, qam64_level);
        }
        size_t bit_idx = 0;
        for (size_t k = 0; k < d_rx_eq.size(); k++) {
            const gr_complex& sym = d_rx_eq[k];
            switch (n_bpsc) {
            case 1: // BPSK
                d_rx_bits[bit_idx++] = hard_bpsk_bit(sym);
                break;
            case 2: // QPSK
                {
                    uint8_t bits[2];
                    hard_qpsk_bits(sym, bits);
                    d_rx_bits[bit_idx++] = bits[0];
                    d_rx_bits[bit_idx++] = bits[1];
                }
                break;
            case 4: // 16-QAM
                {
                    uint8_t bits[4];
                    hard_16qam_bits(sym, bits);
                    for (int i = 0; i < 4; i++) {
                        d_rx_bits[bit_idx++] = bits[i];
                    }
                }
                break;
            case 6: // 64-QAM
                {
                    uint8_t bits[6];
                    hard_64qam_bits(sym, bits, qam64_level);
                    for (int i = 0; i < 6; i++) {
                        d_rx_bits[bit_idx++] = bits[i];
                    }
                }
                break;
            default:
                // fallback to BPSK
                d_rx_bits[bit_idx++] = hard_bpsk_bit(sym);
                break;
            }
        }
        // Sanity check
        if (bit_idx != (size_t)(n_sym * n_cbps)) {
            dout << "[decode_mac] demodulation bit count mismatch: expected "
                 << (n_sym * n_cbps) << " got " << bit_idx << std::endl;
        }

        // ============================================================
        // LDPC decode path
        // ============================================================
        if (d_use_ldpc) {
            // Diagnostic: analyze received symbol quality
            {
                double re_sum = 0, re_sq = 0;
                int pos_re = 0, neg_re = 0;
                for (size_t i = 0; i < d_rx_eq.size(); i++) {
                    float re = d_rx_eq[i].real();
                    re_sum += re;
                    re_sq += re * re;
                    if (re >= 0) pos_re++; else neg_re++;
                }
                double mean_re = re_sum / d_rx_eq.size();
                double var_re = re_sq / d_rx_eq.size() - mean_re * mean_re;
                USRP_LOG( "[LDPC_DIAG] RX symbols: n=%zu mean_re=%.3f var_re=%.3f pos=%d neg=%d\n",
                        d_rx_eq.size(), mean_re, var_re, pos_re, neg_re);
                // Print first 64 hard bits
                USRP_LOG( "[LDPC_DIAG] first64=");
                for (int i = 0; i < 64 && i < (int)d_rx_eq.size(); i++) {
                    USRP_LOG( "%d", hard_bpsk_bit(d_rx_eq[i]));
                }
                USRP_LOG( "\n");
            }

            // DEBUG: Compare RX hard bits with TX reference file
            {
                std::vector<uint8_t> tx_bits;
                std::ifstream tx_ifs("/tmp/wifi_tx_punctured_all_bits.last.txt", std::ios::in | std::ios::binary);
                if (tx_ifs) {
                    char ch;
                    while (tx_ifs.get(ch)) {
                        if (ch == '0' || ch == '1') {
                            tx_bits.push_back(ch - '0');
                        }
                    }
                }
                int cmp_len = std::min((int)tx_bits.size(), (int)d_rx_bits.size());
                int mism = 0;
                int first_mism = -1;
                for (int i = 0; i < cmp_len; i++) {
                    if (tx_bits[i] != d_rx_bits[i]) {
                        mism++;
                        if (first_mism < 0) first_mism = i;
                    }
                }
                USRP_LOG( "[LDPC_DIAG] TX-vs-RX hard bits: cmp=%d tx=%zu rx=%zu mism=%d first_mism=%d\n",
                        cmp_len, tx_bits.size(), d_rx_bits.size(), mism, first_mism);
                if (first_mism >= 0 && first_mism < cmp_len) {
                    USRP_LOG( "[LDPC_DIAG] TX around first_mism: ");
                    for (int i = first_mism; i < std::min(first_mism + 16, cmp_len); i++)
                        USRP_LOG( "%d", tx_bits[i]);
                    USRP_LOG( "\n");
                    USRP_LOG( "[LDPC_DIAG] RX around first_mism: ");
                    for (int i = first_mism; i < std::min(first_mism + 16, cmp_len); i++)
                        USRP_LOG( "%d", d_rx_bits[i]);
                    USRP_LOG( "\n");
                }
                // Per-symbol BER analysis
                USRP_LOG( "[LDPC_DIAG] Per-symbol mismatches (n_cbps=%d):\n", n_cbps);
                int sym_with_errors = 0;
                for (int sym = 0; sym < n_sym && sym * n_cbps < cmp_len; sym++) {
                    int sym_mism = 0;
                    for (int j = 0; j < n_cbps && sym * n_cbps + j < cmp_len; j++) {
                        if (tx_bits[sym * n_cbps + j] != d_rx_bits[sym * n_cbps + j]) {
                            sym_mism++;
                        }
                    }
                    if (sym_mism > 0) {
                        sym_with_errors++;
                        USRP_LOG( "[LDPC_DIAG]   sym=%d mism=%d/%d\n", sym, sym_mism, n_cbps);
                    }
                }
                USRP_LOG( "[LDPC_DIAG] Symbols with errors: %d/%d\n", sym_with_errors, n_sym);
            }

            const float noise_var = 1.0f;
            d_rx_llr.assign((size_t)(n_sym * n_cbps), 0.0f);
            compute_llr_block(d_rx_eq.data(), d_rx_llr.data(),
                              n_sym, 52, n_bpsc, noise_var);

            // 802.11n standard LDPC decode with shortening + puncturing/repetition
            int n_dbps = ht_n_dbps_from_mcs(d_ht_mcs);
            int data_bits = (16 + 8 * d_ht_len + 6 + n_dbps - 1) / n_dbps * n_dbps;
            unsigned block_length = (data_bits <= 324) ? 648 :
                                    (data_bits <= 648) ? 1296 : 1944;
            unsigned rate_index;
            switch (d_ht_mcs) {
            case 0: case 1: case 3: rate_index = 0; break;
            case 5: rate_index = 1; break;
            case 2: case 4: case 6: rate_index = 2; break;
            case 7: rate_index = 3; break;
            default: rate_index = 0; break;
            }

            if (!d_ldpc_codec.init(block_length, rate_index)) {
                USRP_LOG( "[DECODE_FAIL] LDPC init failed\n");
                return;
            }

            int n = d_ldpc_codec.get_n();  // block_length
            int k = d_ldpc_codec.get_k();  // info bits per block
            int m = n - k;                  // parity bits per block
            int n_blocks = (data_bits + k - 1) / k;
            if (n_blocks < 1) n_blocks = 1;

            // Compute puncture / repetition
            int total_output = data_bits + n_blocks * m;
            int received_bits = n_sym * n_cbps;
            int n_puncture = (total_output > received_bits) ? total_output - received_bits : 0;
            int n_repeat   = (total_output < received_bits) ? received_bits - total_output : 0;

            USRP_LOG( "[LDPC_STD] data_bits=%d blocks=%d k=%d m=%d n=%d total_out=%d recv=%d puncture=%d repeat=%d\n",
                    data_bits, n_blocks, k, m, n, total_output, received_bits, n_puncture, n_repeat);

            // Separate info LLR and parity LLR from received stream
            // TX order: [info block0][parity block0][info block1][parity block1]...[repetition]
            std::vector<float> info_llr(data_bits);
            std::vector<float> parity_llr(n_blocks * m, 0.0f);

            for (int i = 0; i < data_bits && i < received_bits; i++) {
                info_llr[i] = d_rx_llr[i];
            }

            int parity_received = received_bits - data_bits;
            if (parity_received > 0) {
                for (int i = 0; i < parity_received && i < n_blocks * m; i++) {
                    parity_llr[i] = d_rx_llr[data_bits + i];
                }
            }

            // Handle repetition: merge repeated parity LLRs
            if (n_repeat > 0 && parity_received > 0) {
                for (int i = 0; i < n_repeat; i++) {
                    int src_idx = parity_received - n_repeat + i;
                    int dst_idx = i % (n_blocks * m);
                    parity_llr[dst_idx] += d_rx_llr[data_bits + src_idx];
                }
            }

            // Decode each block
            std::vector<uint8_t> decoded_all(data_bits);
            int info_offset = 0;
            int parity_offset = 0;

            for (int b = 0; b < n_blocks; b++) {
                int npld = std::min(k, data_bits - b * k);
                int nsh = k - npld;

                // Reconstruct full codeword LLR: info + shortening(0) + parity
                std::vector<float> block_llr(n, 0.0f);
                for (int i = 0; i < npld; i++) {
                    block_llr[i] = info_llr[info_offset + i];
                }
                // shortening bits: LLR = 0 (already zero)
                for (int i = 0; i < m; i++) {
                    block_llr[k + i] = parity_llr[parity_offset + i];
                }

                std::vector<uint8_t> decoded_cw(k);
                d_ldpc_codec.decode(block_llr.data(), n,
                                    decoded_cw.data(), k, 50, true);

                // Copy only actual info bits (skip shortening)
                for (int i = 0; i < npld; i++) {
                    decoded_all[info_offset + i] = decoded_cw[i];
                }

                info_offset += npld;
                parity_offset += m;
            }

            // Descramble and check FCS
            int best_seed = d_scrambler_seed;
            bool fcs_ok = false;
            std::vector<int> seeds_to_try;
            seeds_to_try.push_back(d_scrambler_seed);
            seeds_to_try.push_back(1);

            for (int trial_seed : seeds_to_try) {
                std::vector<uint8_t> descrambled = decoded_all;
                int state = trial_seed;
                for (int i = 0; i < (int)descrambled.size(); i++) {
                    int feedback = ((state & 64) != 0) ^ ((state & 8) != 0);
                    descrambled[i] ^= feedback;
                    state = ((state << 1) & 0x7e) | feedback;
                }

                d_out_bytes.assign((size_t)d_ht_len + 2, 0);
                for (int i = 0; i < 16 && i < (int)descrambled.size(); i++) {
                    d_out_bytes[i / 8] |= (descrambled[i] << (i % 8));
                }
                for (int i = 0; i < d_ht_len * 8 && (i + 16) < (int)descrambled.size(); i++) {
                    d_out_bytes[2 + i / 8] |= (descrambled[i + 16] << (i % 8));
                }

                const uint8_t* psdu = d_out_bytes.data() + 2;
                if (d_ht_len >= 4) {
                    const uint32_t rx_fcs = read_le_u32(psdu + d_ht_len - 4);
                    boost::crc_32_type crc;
                    crc.process_bytes(psdu, d_ht_len - 4);
                    if (crc.checksum() == rx_fcs) {
                        fcs_ok = true;
                        best_seed = trial_seed;
                        break;
                    }
                }
            }

            if (!fcs_ok) {
                USRP_LOG( "[DECODE_FAIL] LDPC FCS error after seed search len=%d\n", d_ht_len);
                return;
            }

            USRP_LOG( "[DECODE_SUCCESS] LDPC FCS OK seed=%d len=%d\n", best_seed, d_ht_len);
            pmt::pmt_t blob = pmt::make_blob(d_out_bytes.data() + 2, d_ht_len);
            d_meta = pmt::dict_add(d_meta, pmt::mp("dlt"), pmt::from_long(105));
            d_meta = pmt::dict_add(d_meta, pmt::mp("crc"), pmt::from_long(1));
            message_port_pub(pmt::mp("out"), pmt::cons(d_meta, blob));
            return;
        }

        // ============================================================
        // Convolutional decode path (original code continues below)
        // ============================================================

        if (d_debug) {
            const int first_n = std::min<int>(64, (int)d_rx_bits.size());
            const int last_n  = std::min<int>(64, (int)d_rx_bits.size());

            dout << "[decode_mac] hard bits first64: "
                 << bits_to_string(d_rx_bits.data(), first_n)
                 << std::endl;

            dout << "[decode_mac] hard bits last64:  "
                 << bits_to_string(d_rx_bits.data() + ((int)d_rx_bits.size() - last_n), last_n)
                 << std::endl;
        }

        // 2) 每个 OFDM symbol 做 HT 去交织
        ht_deinterleave(d_rx_bits.data(), d_deintl_bits.data(), n_sym, d_ht_mcs);

        if (d_debug) {
            const int first_n = std::min<int>(64, (int)d_deintl_bits.size());
            const int last_n  = std::min<int>(64, (int)d_deintl_bits.size());

            dout << "[decode_mac] deintl bits first64: "
                 << bits_to_string(d_deintl_bits.data(), first_n)
                 << std::endl;

            dout << "[decode_mac] deintl bits last64:  "
                 << bits_to_string(d_deintl_bits.data() + ((int)d_deintl_bits.size() - last_n), last_n)
                 << std::endl;
        }


        dump_bits_to_file("/tmp/wifi_rx_hard_all_bits.last.txt",
                          d_rx_bits.data(),
                          (int)d_rx_bits.size());
        dump_bits_to_file("/tmp/wifi_rx_deintl_all_bits.last.txt",
                          d_deintl_bits.data(),
                          (int)d_deintl_bits.size());

        static bool wrote_hard_ref = false;
        if (!wrote_hard_ref) {
            dump_bits_to_file("/tmp/wifi_rx_hard_all_bits.txt",
                              d_rx_bits.data(),
                              (int)d_rx_bits.size());
            wrote_hard_ref = true;
        }

        static bool wrote_deintl_ref = false;
        if (!wrote_deintl_ref) {
            dump_bits_to_file("/tmp/wifi_rx_deintl_all_bits.txt",
                              d_deintl_bits.data(),
                              (int)d_deintl_bits.size());
            wrote_deintl_ref = true;
        }

        dout << "[decode_mac][HARD-ALL] nbits=" << d_rx_bits.size()
             << " first64="
             << bits_to_string(d_rx_bits.data(), std::min<int>(64, (int)d_rx_bits.size()))
             << " last64="
             << bits_to_string(d_rx_bits.data() + ((int)d_rx_bits.size() - std::min<int>(64, (int)d_rx_bits.size())),
                               std::min<int>(64, (int)d_rx_bits.size()))
             << std::endl;

        dout << "[decode_mac][DEINTL-ALL] nbits=" << d_deintl_bits.size()
             << " first64="
             << bits_to_string(d_deintl_bits.data(), std::min<int>(64, (int)d_deintl_bits.size()))
             << " last64="
             << bits_to_string(d_deintl_bits.data() + ((int)d_deintl_bits.size() - std::min<int>(64, (int)d_deintl_bits.size())),
                               std::min<int>(64, (int)d_deintl_bits.size()))
             << std::endl;

        {
            std::vector<uint8_t> tx_interleaved_all;
            std::string used_interleaved_path;
            const bool have_tx_interleaved =
                read_bits_prefer_last("/tmp/wifi_tx_interleaved_all_bits.last.txt",
                                      "/tmp/wifi_tx_interleaved_all_bits.txt",
                                      tx_interleaved_all,
                                      used_interleaved_path);

            if (have_tx_interleaved && (int)tx_interleaved_all.size() >= n_sym * n_cbps) {
                int first_bad_sym = -1;
                int total_mism = 0;
                for (int sym = 0; sym < n_sym; sym++) {
                    int mism = 0;
                    const uint8_t* tx52 = tx_interleaved_all.data() + sym * n_cbps;
                    const uint8_t* rx52 = d_rx_bits.data() + sym * n_cbps;
                    for (int k = 0; k < n_cbps; k++) {
                        if (tx52[k] != rx52[k]) {
                            mism++;
                        }
                    }
                    total_mism += mism;
                    dout << "[decode_mac][SYM " << std::setw(2) << std::setfill('0') << sym
                         << "] hard-vs-TX-interleaved mismatches=" << std::setfill(' ') << mism
                         << std::endl;
                    if (mism > 0 && first_bad_sym < 0) {
                        first_bad_sym = sym;
                        dout << "[decode_mac][FIRST-BAD-HARD-SYM] sym=" << sym
                             << " tx52=" << bits_to_string(tx52, n_cbps)
                             << std::endl;
                        dout << "[decode_mac][FIRST-BAD-HARD-SYM] sym=" << sym
                             << " rx52=" << bits_to_string(rx52, n_cbps)
                             << std::endl;
                    }
                }
                dout << "[decode_mac][HARD-vs-TX-INTERLEAVED] total_mismatches=" << total_mism
                     << " first_bad_sym=" << first_bad_sym
                     << " ref=" << used_interleaved_path
                     << std::endl;
            } else {
                dout << "[decode_mac][HARD-vs-TX-INTERLEAVED] TX reference unavailable or too short"
                     << " ref_bits=" << tx_interleaved_all.size()
                     << " need_bits=" << (n_sym * n_cbps)
                     << std::endl;
            }
        }

        {
            std::vector<uint8_t> tx_punctured_all;
            std::string used_punctured_path;
            const bool have_tx_ref =
                read_bits_prefer_last("/tmp/wifi_tx_punctured_all_bits.last.txt",
                                      "/tmp/wifi_tx_punctured_all_bits.txt",
                                      tx_punctured_all,
                                      used_punctured_path);
            if (have_tx_ref && (int)tx_punctured_all.size() >= n_sym * n_cbps) {
                int first_bad_sym = -1;
                int total_mism = 0;
                for (int sym = 0; sym < n_sym; sym++) {
                    int mism = 0;
                    const uint8_t* tx52 = tx_punctured_all.data() + sym * n_cbps;
                    const uint8_t* rx52 = d_deintl_bits.data() + sym * n_cbps;
                    for (int k = 0; k < n_cbps; k++) {
                        if (tx52[k] != rx52[k]) {
                            mism++;
                        }
                    }
                    total_mism += mism;
                    dout << "[decode_mac][SYM " << std::setw(2) << std::setfill('0') << sym
                         << "] deintl-vs-TX-punctured mismatches=" << std::setfill(' ') << mism
                         << std::endl;
                    if (mism > 0 && first_bad_sym < 0) {
                        first_bad_sym = sym;
                        dout << "[decode_mac][FIRST-BAD-DEINTL-SYM] sym=" << sym
                             << " tx52=" << bits_to_string(tx52, n_cbps)
                             << std::endl;
                        dout << "[decode_mac][FIRST-BAD-DEINTL-SYM] sym=" << sym
                             << " rx52=" << bits_to_string(rx52, n_cbps)
                             << std::endl;
                    }
                }
                dout << "[decode_mac][DEINTL-vs-TX-PUNCTURED] total_mismatches=" << total_mism
                     << " first_bad_sym=" << first_bad_sym
                     << " ref=" << used_punctured_path
                     << std::endl;
            } else {
                dout << "[decode_mac][DEINTL-vs-TX-PUNCTURED] TX reference unavailable or too short"
                     << " ref_bits=" << tx_punctured_all.size()
                     << " need_bits=" << (n_sym * n_cbps)
                     << std::endl;
            }
        }

        // 3) 准备 decoder 参数
        d_ofdm = ofdm_param(mcs_to_encoding(d_ht_mcs));
        // Override with HT 52-carrier parameters; ofdm_param constructor uses
        // legacy 48-carrier values which mismatch HT mode (e.g., 288 vs 312 for 64QAM)
        d_ofdm.n_bpsc = ht_n_bpsc_from_mcs(d_ht_mcs);
        d_ofdm.n_cbps = ht_n_cbps_from_mcs(d_ht_mcs);
        d_ofdm.n_dbps = ht_n_dbps_from_mcs(d_ht_mcs);
        d_frame = frame_param(d_ofdm, d_ht_len);

        d_frame.psdu_size      = d_ht_len;
        d_frame.n_sym          = n_sym;
        d_frame.n_data_bits    = n_sym * d_ofdm.n_dbps;
        d_frame.n_encoded_bits = n_sym * d_ofdm.n_cbps;
        d_frame.n_pad          = d_frame.n_data_bits - (16 + 8 * d_ht_len + 6);

        if (d_frame.n_pad < 0) {
            USRP_LOG( "[DECODE_FAIL] invalid n_pad=%d\n", d_frame.n_pad);
            return;
        }

        if (d_debug) {
            dout << "[decode_mac] decoder params"
                 << " psdu_size=" << d_frame.psdu_size
                 << " n_sym=" << d_frame.n_sym
                 << " n_data_bits=" << d_frame.n_data_bits
                 << " n_encoded_bits=" << d_frame.n_encoded_bits
                 << " n_pad=" << d_frame.n_pad
                 << std::endl;
        }

        // 4) Viterbi
        // Phase 162: soft-decision path (opt-in). Requires the soft_h2 tag
        // (per-SC |H|^2 weights from frame_equalizer) and BPSK (n_bpsc==1).
        // LLR_i = Re(eq_i) * |H_i|^2 is proportional to the true LLR; the
        // max-log viterbi is scale-invariant so no noise estimate is needed.
        uint8_t* decoded = nullptr;
        const bool soft_path =
            d_data_soft_viterbi && d_soft_h2_valid && (n_bpsc == 1);
        if (soft_path) {
            const int total = n_sym * n_cbps;
            d_rx_llr_soft.assign((size_t)total, 0.0f);
            d_deintl_llr.assign((size_t)total, 0.0f);
            for (int k = 0; k < total; k++) {
                d_rx_llr_soft[(size_t)k] = d_rx_eq[(size_t)k].real() * d_soft_h2[k % 52];
            }
            ht_deinterleave_f32(d_rx_llr_soft.data(), d_deintl_llr.data(), n_sym, d_ht_mcs);
            decoded = d_decoder.decode_soft(&d_ofdm, &d_frame, d_deintl_llr.data());
        } else {
            if (d_data_soft_viterbi && !d_soft_h2_valid) {
                static bool warned_no_tag = false;
                if (!warned_no_tag) {
                    warned_no_tag = true;
                    USRP_LOG( "[DECODE_SOFT] WARNING: IEEE80211_DATA_SOFT_VITERBI=1 but no "
                              "soft_h2 tag on frame — falling back to hard viterbi "
                              "(is frame_equalizer's env also ON?)\n");
                }
            }
            decoded = d_decoder.decode(&d_ofdm, &d_frame, d_deintl_bits.data());
        }
        if (!decoded) {
            USRP_LOG( "[DECODE_FAIL] Viterbi decoder returned null\n");
            return;
        }

        if (d_debug) {
            const int first_n = std::min(64, d_frame.n_data_bits);
            const int last_n  = std::min(64, d_frame.n_data_bits);

            dout << "[decode_mac] viterbi bits first64: "
                 << bits_to_string(decoded, first_n)
                 << std::endl;

            dout << "[decode_mac] viterbi bits last64:  "
                 << bits_to_string(decoded + (d_frame.n_data_bits - last_n), last_n)
                 << std::endl;
        }

        // 5) descramble
        descramble(decoded);

        const uint8_t* psdu = d_out_bytes.data() + 2; // 跳过 16-bit SERVICE
        if (d_ht_len < 4) {
            dout << "[decode_mac] invalid psdu len for FCS: " << d_ht_len << std::endl;
            return;
        }

        // 6) strict FCS check
        const uint32_t rx_fcs = read_le_u32(psdu + d_ht_len - 4);

        boost::crc_32_type crc;
        crc.process_bytes(psdu, d_ht_len - 4);
        const uint32_t calc_fcs = crc.checksum();

        uint8_t calc_fcs_le[4];
        write_le_u32(calc_fcs, calc_fcs_le);

        if (d_debug) {
            const int head_n = std::min(16, d_ht_len);
            const int tail_n = std::min(8,  d_ht_len);

            dout << "[decode_mac] descramble bytes first16: "
                 << bytes_to_hex(psdu, head_n)
                 << std::endl;

            dout << "[decode_mac] descramble bytes last" << tail_n << ":  "
                 << bytes_to_hex(psdu + d_ht_len - tail_n, tail_n)
                 << std::endl;

            dout << "[decode_mac] body last4:            "
                 << bytes_to_hex(psdu + d_ht_len - 8, 4)
                 << std::endl;

            dout << "[decode_mac] calc_fcs bytes(le):    "
                 << bytes_to_hex(calc_fcs_le, 4)
                 << std::endl;

            dout << "[decode_mac] rx_fcs bytes(le):      "
                 << bytes_to_hex(psdu + d_ht_len - 4, 4)
                 << std::endl;

            dout << "[decode_mac] tail compare:          "
                 << "body4=[" << bytes_to_hex(psdu + d_ht_len - 8, 4) << "] "
                 << "calc=[" << bytes_to_hex(calc_fcs_le, 4) << "] "
                 << "rx=["   << bytes_to_hex(psdu + d_ht_len - 4, 4) << "]"
                 << std::endl;
        }

        if (calc_fcs == rx_fcs) {
            if (d_debug) {
                dout << "[decode_mac] FCS OK"
                     << " calc=0x" << std::hex << calc_fcs
                     << " rx=0x"   << rx_fcs
                     << std::dec << std::endl;
            }
            USRP_LOG( "[DECODE_SUCCESS] Conv FCS OK, publishing message len=%d\n", d_ht_len);
            // Phase 163b: per-frame MAC sequence-number log (opt-in) for
            // per-frame fate forensics — missing seqs = lost frames, joinable
            // to the 100ms TX lattice to see exactly what the chain was doing
            // when a frame was lost. seq_ctrl at PSDU bytes 22-23 (12-bit seq
            // in bits 4-15; mac.cc: header.seq_nr = d_seq_nr << 4).
            {
                static const char* env_seq = std::getenv("IEEE80211_DECODE_SEQ");
                if (env_seq && env_seq[0] == '1' && d_ht_len >= 24) {
                    const uint16_t seq_ctrl =
                        (uint16_t)psdu[22] | ((uint16_t)psdu[23] << 8);
                    USRP_LOG( "[DECODE_SEQ] seq=%d\n", (seq_ctrl >> 4) & 0xFFF);
                }
            }
            pmt::pmt_t blob = pmt::make_blob(psdu, d_ht_len);
            d_meta = pmt::dict_add(d_meta, pmt::mp("dlt"), pmt::from_long(LINKTYPE_IEEE802_11));
            d_meta = pmt::dict_add(d_meta, pmt::mp("crc"), pmt::from_long(1));
            message_port_pub(pmt::mp("out"), pmt::cons(d_meta, blob));
            USRP_LOG( "[DECODE_AND_PUBLISH] message published: len=%d bytes\n", d_ht_len);
            return;
        }

        // Phase 161: dump decoded PSDU head on Conv-FCS failure (opt-in).
        // Discriminates the terminal-fail population: our frames show the
        // mac header pattern (0x42 addr1 / 0x23 addr2 / 0xff addr3 + 'x'
        // payload); foreign/garbage events show random bytes. This decides
        // whether LDPC-terminal losses are decoder errors or non-our frames.
        {
            static const char* env_fail_dump = std::getenv("IEEE80211_FAIL_PSDU_DUMP");
            if (env_fail_dump && env_fail_dump[0] == '1') {
                char fbuf[256];
                int fn = snprintf(fbuf, sizeof(fbuf), "[FAIL_PSDU] len=%d head=", d_ht_len);
                for (int i = 0; i < d_ht_len && i < 66 && fn < (int)sizeof(fbuf) - 8; i++) {
                    fn += snprintf(fbuf + fn, sizeof(fbuf) - fn, "%02x", ((const uint8_t*)psdu)[i]);
                }
                snprintf(fbuf + fn, sizeof(fbuf) - fn, "\n");
                USRP_LOG("%s", fbuf);
            }
        }

        USRP_LOG( "[DECODE_FAIL] Conv FCS error calc=0x%x rx=0x%x len=%d, trying LDPC fallback\n",
                calc_fcs, rx_fcs, d_ht_len);

        // ============================================================
        // LDPC fallback
        // ============================================================
        {
            const float noise_var = 1.0f;
            d_rx_llr.assign((size_t)(n_sym * n_cbps), 0.0f);
            compute_llr_block(d_rx_eq.data(), d_rx_llr.data(),
                              n_sym, 52, n_bpsc, noise_var);
            descramble_llr(d_rx_llr.data(), (int)d_rx_llr.size(), d_scrambler_seed);

            // Compute block length using same logic as TX (mapper_impl.cc)
            int n_dbps_fb = ht_n_dbps_from_mcs(d_ht_mcs);
            int n_data_bits_fb = (16 + 8 * d_ht_len + 6 + n_dbps_fb - 1) / n_dbps_fb * n_dbps_fb;
            unsigned block_length = (n_data_bits_fb <= 324) ? 648 :
                                    (n_data_bits_fb <= 648) ? 1296 : 1944;
            USRP_LOG( "[LDPC_DEBUG] fallback n_data_bits=%d block_length=%u\n",
                    n_data_bits_fb, block_length);
            unsigned rate_index;
            switch (d_ht_mcs) {
            case 0: case 1: case 3: rate_index = 0; break;
            case 5: rate_index = 1; break;
            case 2: case 4: case 6: rate_index = 2; break;
            case 7: rate_index = 3; break;
            default: rate_index = 0; break;
            }

            if (!d_ldpc_codec.init(block_length, rate_index)) {
                USRP_LOG( "[DECODE_FAIL] LDPC init failed\n");
                return;
            }

            int n = d_ldpc_codec.get_n();
            int k = d_ldpc_codec.get_k();

            if ((int)d_rx_llr.size() < n) {
                d_rx_llr.resize(n, 0.0f);
            }

            std::vector<uint8_t> decoded_cw(k);
            d_ldpc_codec.decode(d_rx_llr.data(), n,
                                decoded_cw.data(), k, 50, true);

            d_out_bytes.assign((size_t)d_ht_len + 2, 0);
            for (int i = 0; i < 16 && i < k; i++) {
                d_out_bytes[i / 8] |= (decoded_cw[i] << (i % 8));
            }
            for (int i = 0; i < d_ht_len * 8 && (i + 16) < k; i++) {
                d_out_bytes[2 + i / 8] |= (decoded_cw[i + 16] << (i % 8));
            }

            const uint8_t* psdu_ldpc = d_out_bytes.data() + 2;
            const uint32_t rx_fcs_ldpc = read_le_u32(psdu_ldpc + d_ht_len - 4);
            boost::crc_32_type crc_ldpc;
            crc_ldpc.process_bytes(psdu_ldpc, d_ht_len - 4);
            const uint32_t calc_fcs_ldpc = crc_ldpc.checksum();

            if (calc_fcs_ldpc != rx_fcs_ldpc) {
                USRP_LOG( "[DECODE_FAIL] LDPC FCS error calc=0x%x rx=0x%x len=%d\n",
                        calc_fcs_ldpc, rx_fcs_ldpc, d_ht_len);
                return;
            }

            USRP_LOG( "[DECODE_SUCCESS] LDPC FCS OK (fallback), publishing message len=%d\n", d_ht_len);
            pmt::pmt_t blob = pmt::make_blob(psdu_ldpc, d_ht_len);
            d_meta = pmt::dict_add(d_meta, pmt::mp("dlt"), pmt::from_long(LINKTYPE_IEEE802_11));
            d_meta = pmt::dict_add(d_meta, pmt::mp("crc"), pmt::from_long(1));
            message_port_pub(pmt::mp("out"), pmt::cons(d_meta, blob));
        }
    }

    void descramble(uint8_t* decoded)
    {
        const int psdu_size = d_ht_len;

        d_out_bytes.assign((size_t)psdu_size + 2U, 0);

        int state = 0;
        for (int i = 0; i < 7; i++) {
            if (decoded[i]) {
                state |= 1 << (6 - i);
            }
        }

        d_out_bytes[0] = (uint8_t)state;

        for (int i = 7; i < psdu_size * 8 + 16; i++) {
            const int feedback = ((state & 64) != 0) ^ ((state & 8) != 0);
            const int bit = feedback ^ decoded[i];
            d_out_bytes[(size_t)i / 8] |= (uint8_t)((bit & 1) << (i % 8));
            state = ((state << 1) & 0x7e) | feedback;
        }
    }

    void descramble_llr(float* llr, int n_bits, int scrambler_seed)
    {
        int state = scrambler_seed;
        for (int i = 0; i < n_bits; i++) {
            int feedback = ((state & 64) != 0) ^ ((state & 8) != 0);
            if (feedback) {
                llr[i] = -llr[i];
            }
            state = ((state << 1) & 0x7e) | feedback;
        }
    }

private:
    bool d_log;
    bool d_debug;

    bool d_in_frame;
    uint64_t d_items_copied;
    uint64_t d_items_expected;
    uint64_t d_frame_seq;

    pmt::pmt_t d_meta;

    int d_ht_mcs;
    int d_ht_len;
    int d_ht_n_sym;
    int d_ht_n_cbps;

    ofdm_param d_ofdm;
    frame_param d_frame;
    viterbi_decoder d_decoder;

    std::vector<gr_complex> d_rx_eq;
    std::vector<uint8_t> d_rx_bits;
    std::vector<uint8_t> d_deintl_bits;
    std::vector<uint8_t> d_out_bytes;

    // Phase 162: data-path soft viterbi state
    bool d_data_soft_viterbi = false;   // env IEEE80211_DATA_SOFT_VITERBI
    bool d_soft_h2_valid = false;       // soft_h2 tag seen for current frame
    float d_soft_h2[52] = { 0.0f };     // per-SC |H|^2 weights (52-array order)
    std::vector<float> d_rx_llr_soft;   // weighted LLRs, SC order
    std::vector<float> d_deintl_llr;    // weighted LLRs, encoder order

    bool d_use_ldpc;
    ldpc_wifi_codec d_ldpc_codec;
    std::vector<float> d_rx_llr;
    int d_scrambler_seed;
    int d_ldpc_block_length;
};


decode_mac::sptr decode_mac::make(bool log, bool debug)
{
    return gnuradio::get_initial_sptr(new decode_mac_impl(log, debug));
}