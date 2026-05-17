// SPDX-License-Identifier: GPL-3.0-or-later
#include "insert_ht_training_impl.h"

#include <gnuradio/io_signature.h>
#include <pmt/pmt.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <vector>

namespace gr {
namespace ieee802_11 {

namespace {

// 路线 B 下，mixed_mode_carrier_allocator 输出：
//   4 个 legacy sync words
// + 3 个 header OFDM symbols (L-SIG + HT-SIG1 + HT-SIG2)
// 然后插入：HT-STF + HT-LTF
static constexpr int kInsertAtSym = 7;

// 你前面给出的 64-bin HT-STF / HT-LTF 表，直接固化进来。
// 这里按 fftshift 后的 64-bin 频域向量使用。

static const std::array<gr_complex, 64> kHtStf64 = {
    gr_complex( 0.f,  0.f), gr_complex( 0.f,  0.f),
    gr_complex( 0.f,  0.f), gr_complex( 0.f,  0.f),
    gr_complex( 0.f,  0.f), gr_complex( 0.f,  0.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 0.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f,  1.f),
    gr_complex( 1.f,  0.f), gr_complex( 0.f, -1.f),
    gr_complex(-1.f,  0.f), gr_complex( 0.f,  0.f),
    gr_complex( 0.f,  0.f), gr_complex( 0.f,  0.f),
    gr_complex( 0.f,  0.f), gr_complex( 0.f,  0.f)
};

static const std::array<gr_complex, 64> kHtLtf64 = {
    gr_complex( 0.f, 0.f), gr_complex( 0.f, 0.f),
    gr_complex( 0.f, 0.f), gr_complex( 0.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 0.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex(-1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex(-1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 1.f, 0.f),
    gr_complex( 1.f, 0.f), gr_complex( 0.f, 0.f),
    gr_complex( 0.f, 0.f), gr_complex( 0.f, 0.f)
};

} // anonymous namespace

// =====================================================================
// 关键修复：补上 insert_ht_training 的 3 参数构造函数“库内符号定义”
// =====================================================================
insert_ht_training::insert_ht_training(const std::string& name,
                                       gr::io_signature::sptr input_signature,
                                       gr::io_signature::sptr output_signature)
    : gr::block(name, input_signature, output_signature)
{
}

// ---------------------------------------------------------------------
// factory
// ---------------------------------------------------------------------
insert_ht_training::sptr insert_ht_training::make(const std::string& tag_key)
{
    return gnuradio::get_initial_sptr(new insert_ht_training_impl(tag_key));
}

// ---------------------------------------------------------------------
// ctor
// ---------------------------------------------------------------------
insert_ht_training_impl::insert_ht_training_impl(const std::string& tag_key)
    : insert_ht_training("insert_ht_training",
                         gr::io_signature::make(1, 1, VLEN * sizeof(gr_complex)),
                         gr::io_signature::make(1, 1, VLEN * sizeof(gr_complex))),
      d_tag_key(tag_key),
      d_tag_key_pmt(pmt::intern(tag_key)),
      d_state(ST_IDLE),
      d_in_pkt_remaining(0),
      d_sym_idx_in_pkt(0),
      d_inserted(false),
      d_in_pkt_len(0)
{
    set_tag_propagation_policy(TPP_DONT);
    build_training_symbols();
}

// ---------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------
bool insert_ht_training_impl::is_used_bin(int bin) const
{
    // 这里保留实现，虽然当前 build_training_symbols() 已经直接写死 64-bin 表
    // fftshift 后:
    //   DC = 32
    //   HT20 occupied = [4..60] except 32
    if (bin == 32) {
        return false;
    }
    if (bin < 4 || bin > 60) {
        return false;
    }
    return true;
}

void insert_ht_training_impl::build_training_symbols()
{
    // kHtStf64 and kHtLtf64 are defined in fftshift order (index = SC + 32,
    // i.e. negative frequencies first, then DC, then positive frequencies).
    // But the TX IFFT and RX FFT in this flowgraph both use shift=False
    // (natural order: DC at bin 0, positive freq at bins 1-31, negative
    // freq at bins 32-63).  Convert fftshift → natural to avoid a 50 %
    // sign-flip rate in the HT-DATA channel estimate computed from HT-LTF.
    for (int k = 0; k < 64; k++) {
        d_ht_stf_64[k] = kHtStf64[(k + 32) % 64];
        d_ht_ltf_64[k] = kHtLtf64[(k + 32) % 64];
    }
}

void insert_ht_training_impl::forward_tags_at_input_item(uint64_t abs_in_item,
                                                         uint64_t abs_out_item)
{
    std::vector<tag_t> tags;
    get_tags_in_range(tags, 0, abs_in_item, abs_in_item + 1);

    for (auto& t : tags) {
        if (pmt::equal(t.key, d_tag_key_pmt)) {
            continue;
        }
        add_item_tag(0, abs_out_item, t.key, t.value, t.srcid);
    }
}

// ---------------------------------------------------------------------
// forecast
// ---------------------------------------------------------------------
void insert_ht_training_impl::forecast(int noutput_items,
                                       gr_vector_int& ninput_items_required)
{
    ninput_items_required[0] = std::max(1, noutput_items - N_TRAIN);
}

// ---------------------------------------------------------------------
// general_work
// ---------------------------------------------------------------------
int insert_ht_training_impl::general_work(int noutput_items,
                                          gr_vector_int& ninput_items,
                                          gr_vector_const_void_star& input_items,
                                          gr_vector_void_star& output_items)
{
    const gr_complex* in = (const gr_complex*)input_items[0];
    gr_complex* out = (gr_complex*)output_items[0];

    const int n_in = ninput_items[0];
    int produced = 0;
    int consumed = 0;

    while (produced < noutput_items) {

        const bool have_in = (consumed < n_in);

        // -------------------------
        // IDLE: wait for packet start (length tag at current input item)
        // -------------------------
        if (d_state == ST_IDLE) {
            if (!have_in) {
                break;
            }

            const uint64_t abs_in = nitems_read(0) + consumed;

            std::vector<tag_t> tags;
            get_tags_in_range(tags, 0, abs_in, abs_in + 1, d_tag_key_pmt);

            if (tags.empty()) {
                if (produced + 1 > noutput_items) {
                    break;
                }

                const uint64_t abs_out = nitems_written(0) + produced;
                forward_tags_at_input_item(abs_in, abs_out);

                std::memcpy(out + (produced * VLEN),
                            in + (consumed * VLEN),
                            VLEN * sizeof(gr_complex));

                produced += 1;
                consumed += 1;
                continue;
            }

            // packet start
            d_in_pkt_len = (uint64_t)pmt::to_long(tags[0].value);
            d_in_pkt_remaining = (int)d_in_pkt_len;
            d_sym_idx_in_pkt = 0;
            d_inserted = false;
            d_state = ST_IN_PKT;

            const uint64_t abs_out = nitems_written(0) + produced;
            add_item_tag(0,
                         abs_out,
                         d_tag_key_pmt,
                         pmt::from_long((long)(d_in_pkt_len + N_TRAIN)),
                         pmt::intern(name()));

            forward_tags_at_input_item(abs_in, abs_out);
            continue;
        }

        // -------------------------
        // IN_PKT
        // -------------------------
        if (d_state == ST_IN_PKT) {

            // 4 legacy sync + 3 header 之后，插入 HT-STF + HT-LTF
            if (!d_inserted && d_sym_idx_in_pkt == kInsertAtSym) {
                if (produced + N_TRAIN > noutput_items) {
                    break;
                }

                std::memcpy(out + (produced * VLEN),
                            d_ht_stf_64,
                            VLEN * sizeof(gr_complex));
                produced += 1;

                std::memcpy(out + (produced * VLEN),
                            d_ht_ltf_64,
                            VLEN * sizeof(gr_complex));
                produced += 1;

                d_inserted = true;
                continue;
            }

            if (d_in_pkt_remaining <= 0) {
                d_state = ST_IDLE;
                continue;
            }

            if (!have_in) {
                break;
            }
            if (produced + 1 > noutput_items) {
                break;
            }

            const uint64_t abs_in = nitems_read(0) + consumed;
            const uint64_t abs_out = nitems_written(0) + produced;

            forward_tags_at_input_item(abs_in, abs_out);

            std::memcpy(out + (produced * VLEN),
                        in + (consumed * VLEN),
                        VLEN * sizeof(gr_complex));

            produced += 1;
            consumed += 1;

            d_in_pkt_remaining -= 1;
            d_sym_idx_in_pkt += 1;

            if (d_in_pkt_remaining == 0) {
                d_state = ST_IDLE;
            }
            continue;
        }
    }

    consume(0, consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr