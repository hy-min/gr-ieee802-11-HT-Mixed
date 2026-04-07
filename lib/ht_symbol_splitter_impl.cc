/*
 * HT Symbol Splitter: Converts 80-sample HT-Mixed OFDM symbols to 64-sample FFT blocks
 *
 * Key principle: "吃进 80，吐出 64"
 *
 * In 802.11n HT-Mixed mode, ALL OFDM symbols (L-SIG, HT-SIG, HT-Data)
 * follow the same structure: 80 samples = 16 CP + 64 Data
 * There is NO "gap" between symbols - it's purely 16 CP + 64 Data.
 *
 * This block uses wifi_start tag to get frame timing,
 * then calculates relative offsets for proper symbol alignment.
 */

#include "ht_symbol_splitter_impl.h"
#include <gnuradio/io_signature.h>
#include <stdio.h>
#include <string.h>

namespace gr {
namespace ieee802_11 {

ht_symbol_splitter::sptr
ht_symbol_splitter::make(int fft_size, int symbol_size, int cp_size)
{
    return gnuradio::get_initial_sptr(
        new ht_symbol_splitter_impl(fft_size, symbol_size, cp_size));
}

ht_symbol_splitter_impl::ht_symbol_splitter_impl(int fft_size, int symbol_size, int cp_size)
    : block("ht_symbol_splitter",
            io_signature::make(1, 1, sizeof(gr_complex)),
            io_signature::make(1, 1, sizeof(gr_complex))),
      d_fft_size(fft_size),
      d_symbol_size(symbol_size),
      d_cp_size(cp_size),
      d_ht_mixed(true),
      d_debug(true),
      d_debug_count(0),
      d_frame_start_abs(0),
      d_frame_start_known(false),
      d_items_processed(0)
{
    // Circular buffer for FFT-sized blocks
    d_buffer.resize(d_fft_size);
    d_buffer_count = 0;

    // We output in multiples of fft_size
    set_output_multiple(d_fft_size);

    fprintf(stderr, "[HT_SPLITTER] Created: fft_size=%d, symbol_size=%d, cp_size=%d\n",
            d_fft_size, d_symbol_size, d_cp_size);
}

ht_symbol_splitter_impl::~ht_symbol_splitter_impl() {}

void ht_symbol_splitter_impl::set_ht_mixed(bool ht_mixed)
{
    d_ht_mixed = ht_mixed;
    fprintf(stderr, "[HT_SPLITTER] Mode changed: ht_mixed=%d\n", ht_mixed);
}

void ht_symbol_splitter_impl::forecast(int noutput_items,
                                        gr_vector_int& ninput_items_required)
{
    // For each 64 output samples, we need up to 80 input samples due to CP skipping
    int n_blocks = (noutput_items + d_fft_size - 1) / d_fft_size;
    ninput_items_required[0] = n_blocks * d_symbol_size;
}

int ht_symbol_splitter_impl::general_work(int noutput_items,
                                           gr_vector_int& ninput_items,
                                           gr_vector_const_void_star& input_items,
                                           gr_vector_void_star& output_items)
{
    const gr_complex* in = (const gr_complex*)input_items[0];
    gr_complex* out = (gr_complex*)output_items[0];

    int produced = 0;
    int consumed = 0;

    if (d_debug && d_debug_count < 5) {
        fprintf(stderr, "\n[HT_SPLITTER] === START general_work: noutput_items=%d, frame_known=%d ===\n",
                noutput_items, d_frame_start_known);
    }

    // Get absolute position of first input item
    uint64_t start_abs_idx = nitems_read(0);

    // Look for wifi_start tag
    if (!d_frame_start_known) {
        std::vector<gr::tag_t> tags;
        get_tags_in_range(tags, 0, start_abs_idx, start_abs_idx + ninput_items[0]);

        for (const auto& tag : tags) {
            if (pmt::symbol_to_string(tag.key) == "wifi_start") {
                // wifi_start tag is written at sync_long output position 0 (when rel=0)
                // This corresponds to the start of L-LTF DATA
                // For HT-Mixed 20MHz, L-LTF DATA starts at sync_long input ~176
                // But we need to find the offset to L-LTF DATA within the stream

                // The tag appears at position start_abs_idx in our input
                // wifi_start was written at output position 0 of sync_long
                // Since sync_long output position 0 = sync_long input position d_frame_start,
                // and d_frame_start = 176 for HT-Mixed, we need to find d_frame_start

                // For now, assume wifi_start at position X means L-LTF DATA starts at X
                // This assumes 1:1 input-output mapping in the connected stream
                d_frame_start_abs = start_abs_idx;
                d_frame_start_known = true;
                fprintf(stderr, "[HT_SPLITTER] wifi_start tag found at start_abs_idx=%llu\n",
                        (unsigned long long)start_abs_idx);
                // Propagate wifi_start tag to output for downstream blocks (e.g., frame_equalizer)
                add_item_tag(0,  // output port 0
                             nitems_written(0),  // current output position
                             pmt::string_to_symbol("wifi_start"),
                             pmt::from_double(pmt::to_double(tag.value)),  // preserve freq offset
                             pmt::string_to_symbol(name()));
                fprintf(stderr, "[HT_SPLITTER] wifi_start tag propagated at output pos %llu\n",
                        (unsigned long long)nitems_written(0));
                break;
            }
        }
    }

    int i = 0;
    while (i < ninput_items[0]) {
        uint64_t current_idx = start_abs_idx + i;

        // Only process after frame start is known
        if (d_frame_start_known && current_idx >= d_frame_start_abs) {
            uint64_t rel_idx = current_idx - d_frame_start_abs;
            bool should_buffer = false;

            // HT-Mixed 20MHz preamble structure with explicit boundaries:
            // sync_long output starts at d_frame_start, which is L-LTF0 DATA start (176).
            // So rel_idx=0 in sync_long output = input 176 (L-LTF0 DATA).
            //
            // Original frame structure (input positions):
            // L-STF: 0-159 (NOT in sync_long output)
            // L-LTF0: 160-239 (CP=160-175, DATA=176-239)
            // L-LTF1: 240-319 (CP=240-255, DATA=256-319)
            // L-SIG: 320-399 (CP=320-335, DATA=336-399)
            // HT-SIG: 400-479 and 480-559 (HT-SIG0 and HT-SIG1, each 80 samples)
            // HT-STF: 560-639
            // HT-DATA: 640+
            //
            // sync_long output rel_idx mapping:
            // rel_idx 0-63: L-LTF0 DATA (input 176-239)
            // rel_idx 64-127: L-LTF1 DATA (input 240-303)
            // rel_idx 128-191: L-SIG DATA (input 304-367)
            // rel_idx 192-255: HT-SIG0 (input 368-431) - but this is WRONG
            //
            // Actually for HT-SIG: each symbol is 80 samples (16 CP + 64 data)
            // HT-SIG0: CP at input 368-383, data at 384-447
            // HT-SIG1: CP at input 448-463, data at 464-527
            //
            // sync_long output rel_idx:
            // rel_idx 0-63: L-LTF0 DATA (input 176-239)
            // rel_idx 64-127: L-LTF1 DATA (input 240-303)
            // rel_idx 128-191: L-SIG DATA (input 304-367)
            // rel_idx 192-255: HT-SIG0 CP (input 368-383)
            // rel_idx 192-255: HT-SIG0 DATA (input 384-447)? NO! That's 64 samples
            //
            // Let me recalculate:
            // HT-SIG0: input 368-447 (80 samples)
            //   CP: 368-383 (16 samples) = rel_idx 192-207
            //   Data: 384-447 (64 samples) = rel_idx 208-271
            // HT-SIG1: input 448-527 (80 samples)
            //   CP: 448-463 (16 samples) = rel_idx 272-287
            //   Data: 464-527 (64 samples) = rel_idx 288-351
            //
            // L-LTF0: input 160-239 (80 samples) - but sync starts at 176 (data)
            //   L-LTF0 DATA: 176-239 (64 samples) = rel_idx 0-63
            // L-LTF1: input 240-319 (80 samples)
            //   L-LTF1 DATA: 256-319 (64 samples) = rel_idx 80-143? NO!
            //   Wait, L-LTF1 CP is 240-255, LTF1 DATA is 256-319
            //   rel_idx = input - 176:
            //     L-LTF1 DATA start = 256 - 176 = 80
            //     L-LTF1 DATA end = 319 - 176 = 143
            //   So L-LTF1 DATA is rel_idx 80-143
            //
            // Let me verify:
            // L-LTF0 DATA: input 176-239 = rel_idx 0-63 (64 samples) ✓
            // L-LTF1 DATA: input 256-319 = rel_idx 80-143 (64 samples) ✓
            //
            // L-SIG: input 336-399 = DATA (not CP since sync starts at 176)
            //   But wait, L-SIG CP is input 320-335, which is < 176, so not in output
            //   L-SIG DATA: input 336-399 = rel_idx 160-223 (64 samples)
            //
            // HT-SIG0: input 384-447 = DATA
            //   HT-SIG0 CP (input 368-383) is < 176? 368 > 176, so YES, it's in output
            //   368 - 176 = 192, so HT-SIG0 CP is rel_idx 192-207
            //   HT-SIG0 DATA is rel_idx 208-271
            //
            // HT-SIG1: input 464-527 = DATA
            //   HT-SIG1 CP (input 448-463) is > 176, so in output: 448-176=272 to 463-176=287
            //   HT-SIG1 DATA is rel_idx 288-351
            //
            // Correct HT-Mixed 20MHz preamble boundaries:
            // rel_idx 0-63: L-LTF0 DATA (input 176-239)
            // rel_idx 64-79: L-LTF1 CP (input 240-255) - SKIP
            // rel_idx 80-143: L-LTF1 DATA (input 256-319)
            // rel_idx 144-159: L-SIG CP (input 320-335) - SKIP
            // rel_idx 160-223: L-SIG DATA (input 336-399)
            // rel_idx 224-239: HT-SIG0 CP (input 400-415) - SKIP
            // rel_idx 240-303: HT-SIG0 DATA (input 416-479)
            // rel_idx 304-319: HT-SIG1 CP (input 480-495) - SKIP
            // rel_idx 320-383: HT-SIG1 DATA (input 496-559)
            // rel_idx 384-399: HT-STF CP (input 560-575) - SKIP
            // rel_idx 400-463: HT-STF DATA (input 576-639)
            // rel_idx 464+: HT-DATA (each symbol 80 samples: 16 CP + 64 data)
            if (rel_idx >= 0 && rel_idx < 64) {
                // L-LTF0 DATA: rel_idx 0-63 (input 176-239)
                should_buffer = true;
            } else if (rel_idx >= 80 && rel_idx < 144) {
                // L-LTF1 DATA: rel_idx 80-143 (input 256-319)
                // Skip rel_idx 64-79 (L-LTF1 CP: input 240-255)
                should_buffer = true;
            } else if (rel_idx >= 160 && rel_idx < 224) {
                // L-SIG DATA: rel_idx 160-223 (input 336-399)
                // Skip rel_idx 144-159 (L-SIG CP: input 320-335)
                should_buffer = true;
            } else if (rel_idx >= 240 && rel_idx < 304) {
                // HT-SIG0 DATA: rel_idx 240-303 (input 416-479)
                // Skip rel_idx 224-239 (HT-SIG0 CP: input 400-415)
                should_buffer = true;
            } else if (rel_idx >= 320 && rel_idx < 384) {
                // HT-SIG1 DATA: rel_idx 320-383 (input 496-559)
                // Skip rel_idx 304-319 (HT-SIG1 CP: input 480-495)
                should_buffer = true;
            } else if (rel_idx >= 384) {
                // HT-STF and HT-DATA: each 80-sample symbol
                uint64_t sym_offset = (rel_idx - 384) % 80;
                if (sym_offset >= 16) {
                    should_buffer = true;  // Skip CP, buffer data
                }
            }

            if (should_buffer) {
                d_buffer[d_buffer_count++] = in[i];

                // When buffer is full, output FFT block
                if (d_buffer_count == d_fft_size) {
                    uint64_t out_rel_idx = current_idx - d_frame_start_abs;
                    // Debug: check if this is HT-SIG region (HT-SIG0 data: 240-303, HT-SIG1 data: 320-383)
                    if ((out_rel_idx >= 240 && out_rel_idx < 304) || (out_rel_idx >= 320 && out_rel_idx < 384)) {
                        fprintf(stderr, "[HT_SPLITTER] HT-SIG OUTPUT at rel_idx=%llu, sample[0]=%.4f+%.4fi\n",
                                (unsigned long long)out_rel_idx,
                                d_buffer[0].real(), d_buffer[0].imag());
                    }
                    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
                    produced += d_fft_size;
                    d_buffer_count = 0;

                    if (d_debug && d_debug_count < 10) {
                        fprintf(stderr, "[HT_SPLITTER] OUTPUT FFT at rel_idx=%llu\n",
                                (unsigned long long)out_rel_idx);
                    }
                }
            }
        }

        i++;
        consumed++;
    }

    d_items_processed += consumed;

    if (d_debug && d_debug_count < 5) {
        fprintf(stderr, "[HT_SPLITTER] === END: consumed=%d, produced=%d ===\n\n",
                consumed, produced);
        d_debug_count++;
    }

    consume(0, consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr
