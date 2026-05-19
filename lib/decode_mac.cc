#include <ieee802_11/decode_mac.h>
#include "utils.h"
#include "viterbi_decoder/viterbi_decoder.h"

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
// Based on constellation_64qam_impl::decision_maker
static void hard_64qam_bits(const gr_complex& x, uint8_t bits[6])
{
    const float level = sqrtf(1.0f / 42.0f);
    float re = x.real();
    float im = x.imag();

    // Bit 0: real > 0
    bits[0] = (re > 0) ? 1 : 0;
    // Bit 1: |real| < 4*level
    bits[1] = (std::abs(re) < (4 * level)) ? 1 : 0;
    // Bit 2: |real| < 6*level && |real| > 2*level
    bits[2] = (std::abs(re) < (6 * level) && std::abs(re) > (2 * level)) ? 1 : 0;
    // Bit 3: imag > 0
    bits[3] = (im > 0) ? 1 : 0;
    // Bit 4: |imag| < 4*level
    bits[4] = (std::abs(im) < (4 * level)) ? 1 : 0;
    // Bit 5: |imag| < 6*level && |imag| > 2*level
    bits[5] = (std::abs(im) < (6 * level) && std::abs(im) > (2 * level)) ? 1 : 0;
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
          d_ofdm(BPSK_1_2),
          d_frame(d_ofdm, 0)
    {
        message_port_register_out(pmt::mp("out"));
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
        fprintf(stderr, "[DECODE_MAC_GW] call=%d ninput=%d nread=%llu in_frame=%d copied=%llu/%llu\n",
                gw_call_count, ninput_items[0], (unsigned long long)nread,
                d_in_frame ? 1 : 0,
                (unsigned long long)d_items_copied, (unsigned long long)d_items_expected);

        while (i < ninput_items[0]) {
            std::vector<gr::tag_t> tags;
            get_tags_in_range(tags, 0, nread + i, nread + i + 1);

            if (!tags.empty()) {
                fprintf(stderr, "[DECODE_TAG] nread=%llu i=%d n_tags=%zu",
                        (unsigned long long)nread, i, tags.size());
                for (const auto& t : tags) {
                    fprintf(stderr, " key=%s", pmt::symbol_to_string(t.key).c_str());
                }
                fprintf(stderr, "\n");
            }

            const pmt::pmt_t k_frame_bytes_sp = pmt::mp("frame bytes");
            const pmt::pmt_t k_frame_bytes_us = pmt::mp("frame_bytes");
            const pmt::pmt_t k_mcs            = pmt::mp("mcs");

            for (auto& t : tags) {
                if (pmt::eq(t.key, k_frame_bytes_sp) || pmt::eq(t.key, k_frame_bytes_us)) {

                    if (d_in_frame && d_items_copied < d_items_expected) {
                        log_incomplete("new_frame_tag_before_complete");
                    }

                    d_meta = pmt::make_dict();
                    for (auto& tt : tags) {
                        d_meta = pmt::dict_add(d_meta, tt.key, tt.value);
                    }

                    const bool has_mcs = pmt::dict_has_key(d_meta, k_mcs);
                    if (!has_mcs) {
                        if (d_debug) {
                            dout << "[decode_mac] skip non-HT frame (this build only restores strict HT MCS0)"
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

                    // 检查MCS范围是否有效
                    if (d_ht_mcs < 0 || d_ht_mcs > 7) {
                        if (d_debug) {
                            dout << "[decode_mac] skip HT frame: invalid MCS=" << d_ht_mcs << std::endl;
                        }
                        d_in_frame = false;
                        break;
                    }

                    d_ht_n_sym = ht_n_sym_from_mcs_len(d_ht_mcs, d_ht_len);
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
        fprintf(stderr, "[DECODE_AND_PUBLISH] called n_sym=%d n_cbps=%d n_dbps=%d d_ht_len=%d rx_eq_size=%zu\n",
                d_ht_n_sym, d_ht_n_cbps, ht_n_dbps_from_mcs(d_ht_mcs), d_ht_len, d_rx_eq.size());
        if (d_rx_eq.empty() || d_ht_n_sym <= 0) {
            fprintf(stderr, "[DECODE_AND_PUBLISH] no data captured\n");
            return;
        }

        const int n_sym  = d_ht_n_sym;
        const int n_cbps = d_ht_n_cbps;
        const int n_dbps = ht_n_dbps_from_mcs(d_ht_mcs);

        d_rx_bits.assign((size_t)(n_sym * n_cbps), 0);
        d_deintl_bits.assign((size_t)(n_sym * n_cbps), 0);

        // 1) hard demap
        const int n_bpsc = ht_n_bpsc_from_mcs(d_ht_mcs);
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
                    hard_64qam_bits(sym, bits);
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
        d_frame = frame_param(d_ofdm, d_ht_len);

        d_frame.psdu_size      = d_ht_len;
        d_frame.n_sym          = n_sym;
        d_frame.n_data_bits    = n_sym * n_dbps;
        d_frame.n_encoded_bits = n_sym * n_cbps;
        d_frame.n_pad          = d_frame.n_data_bits - (16 + 8 * d_ht_len + 6);

        if (d_frame.n_pad < 0) {
            fprintf(stderr, "[DECODE_FAIL] invalid n_pad=%d\n", d_frame.n_pad);
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
        uint8_t* decoded = d_decoder.decode(&d_ofdm, &d_frame, d_deintl_bits.data());
        if (!decoded) {
            fprintf(stderr, "[DECODE_FAIL] Viterbi decoder returned null\n");
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

        if (calc_fcs != rx_fcs) {
            fprintf(stderr, "[DECODE_FAIL] FCS error calc=0x%x rx=0x%x len=%d\n", calc_fcs, rx_fcs, d_ht_len);
            return;
        }

        if (d_debug) {
            dout << "[decode_mac] FCS OK"
                 << " calc=0x" << std::hex << calc_fcs
                 << " rx=0x"   << rx_fcs
                 << std::dec << std::endl;
        }

        // 7) 发消息
        pmt::pmt_t blob = pmt::make_blob(psdu, d_ht_len);

        d_meta = pmt::dict_add(d_meta,
                               pmt::mp("dlt"),
                               pmt::from_long(LINKTYPE_IEEE802_11));

        fprintf(stderr, "[DECODE_SUCCESS] FCS OK, publishing message len=%d\n", d_ht_len);
        message_port_pub(pmt::mp("out"), pmt::cons(d_meta, blob));
        fprintf(stderr, "[DECODE_AND_PUBLISH] message published: len=%d bytes\n", d_ht_len);
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
};


decode_mac::sptr decode_mac::make(bool log, bool debug)
{
    return gnuradio::get_initial_sptr(new decode_mac_impl(log, debug));
}