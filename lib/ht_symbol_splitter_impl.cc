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
    d_buffer_filled = false;

    // We output in multiples of fft_size
    set_output_multiple(d_fft_size);


}

ht_symbol_splitter_impl::~ht_symbol_splitter_impl() {}

void ht_symbol_splitter_impl::set_ht_mixed(bool ht_mixed)
{
    d_ht_mixed = ht_mixed;

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



    // Get absolute position of first input item
    uint64_t start_abs_idx = nitems_read(0);

    // Look for wifi_start tag - always check for new frames
    // If we see wifi_start at a position significantly beyond our current d_frame_start_abs,
    // it indicates a new frame has started and we should update our reference.
    if (true) {
        std::vector<gr::tag_t> tags;
        get_tags_in_range(tags, 0, start_abs_idx, start_abs_idx + ninput_items[0]);

        for (const auto& tag : tags) {
            if (pmt::symbol_to_string(tag.key) == "wifi_start") {
                // sync_long writes wifi_start at nitems_written(0) when rel=0.
                // In COPY state, rel=0 means d_offset = d_frame_start.
                // sync_long outputs: out[o] = in[d_offset + o] = in[d_frame_start + o]
                // So output position 0 corresponds to input position d_frame_start.
                //
                // The wifi_start tag offset is the OUTPUT position in sync_long's stream.
                // Since sync_long output[tag.offset] = sync_long input[d_frame_start],
                // and sync_long output maps 1:1 to ht_symbol_splitter input:
                //   ht_symbol_splitter input[tag.offset] = sync_long input[d_frame_start]
                //
                // But we want ht_symbol_splitter input position 0 to correspond to L-LTF DATA start.
                // L-LTF DATA starts at input position d_frame_start in sync_long.
                // So we need: d_frame_start_abs = tag.offset - d_frame_start
                //
                // Actually, simpler interpretation:
                // - The tag.offset is where wifi_start appears in the input stream
                // - At that position, we're reading sync_long input[d_frame_start]
                // - We want d_frame_start_abs such that rel_idx = current_idx - d_frame_start_abs = 0
                //   when current_idx = tag.offset
                // - So d_frame_start_abs = tag.offset
                //
                // But tag.offset is the position in sync_long's OUTPUT, and we want the position
                // in ht_symbol_splitter's INPUT where L-LTF DATA starts.
                //
                // Since sync_long output = ht_symbol_splitter input (1:1), and
                // sync_long output[tag.offset] = input[d_frame_start],
                // the L-LTF DATA starts at ht_symbol_splitter input position d_frame_start.
                // But tag.offset is where wifi_start was written, which is at sync_long output position 0.
                // Since sync_long output position 0 = sync_long input position d_frame_start,
                // and this maps 1:1 to ht_symbol_splitter input, wifi_start appears at
                // ht_symbol_splitter input position d_frame_start.
                //
                // So d_frame_start_abs = d_frame_start (the tag.value)!
                uint64_t d_frame_start = (uint64_t)pmt::to_double(tag.value);
                uint64_t tag_abs_pos = (uint64_t)tag.offset;

                // d_frame_start_abs should be the ABSOLUTE position where L-LTF DATA starts.
                // wifi_start tag.offset = absolute position in input stream where tag was written
                // d_frame_start (176) = offset within sync_long where L-LTF DATA starts
                // sync_long output[0] = sync_long input[d_frame_start]
                // Since sync_long output IS ht_symbol_splitter input (1:1 mapping):
                //   ht_symbol_splitter input position 0 = sync_long input[d_frame_start]
                // So L-LTF DATA starts at ht_symbol_splitter input position 0.
                // But we use tag.offset to find where we are in the stream.
                // The first sample we consume (i=0) has current_idx = tag.offset.
                // This corresponds to ht_symbol_splitter input position 0.
                // So d_frame_start_abs = 0 (meaning L-LTF DATA is at rel_idx 0).
                //
                // But wait - we want to buffer DATA, not CP. L-LTF DATA starts at
                // sync_long input position d_frame_start = 176.
                // In ht_symbol_splitter's coordinate: first sample (position 0) IS L-LTF DATA.
                // So d_frame_start_abs should be 0, not d_frame_start.
                //
                // Actually, the issue is that d_frame_start_abs is used to compute rel_idx,
                // and rel_idx=0 should correspond to L-LTF DATA. Since our input position 0
                // IS L-LTF DATA, we need d_frame_start_abs = 0.
                //
                // But the original code uses d_frame_start = 176, which would make
                // rel_idx = current_idx - 176. At current_idx = 1166976 (where tag was found),
                // rel_idx = 1166800, which is way off.
                //
                // The fix: d_frame_start_abs should be d_frame_start, not tag.offset.
                // sync_long sets d_frame_start=176 (L-LTF0 DATA start in original input).
                // We want rel_idx=0 to correspond to L-LTF0 DATA start.
                // d_frame_start_abs = d_frame_start;

                // Check if this is a NEW frame
                // If d_frame_start_known is already true and we see another wifi_start,
                // it means a new frame has started (we don't re-use wifi_start within a frame)
                bool is_new_frame = d_frame_start_known;

                d_frame_start_abs = d_frame_start;  // tag.offset = position in stream where wifi_start appears

                d_frame_start_known = true;

                // Propagate wifi_start tag to output for downstream blocks (e.g., frame_equalizer)
                add_item_tag(0,  // output port 0
                             nitems_written(0),  // current output position
                             pmt::string_to_symbol("wifi_start"),
                             pmt::from_double(d_frame_start_abs),
                             pmt::string_to_symbol(name()));

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
            // ============================================================
            // HT-Mixed 20MHz Preamble Structure (CORRECTED):
            //
            // L-LTF: 160 samples = 32 GI2 + 64 LTF0 + 64 LTF1
            //   - rel_idx 0-63:   LTF0 DATA (input d_frame_start to d_frame_start+63)
            //   - rel_idx 64-79:  LTF1 CP (16 samples) -> SKIP
            //   - rel_idx 80-143: LTF1 DATA (64 samples) -> BUFFER
            //   - rel_idx 128-143: L-SIG CP (16 samples) -> SKIP
            //   - rel_idx 144-207: L-SIG DATA (64 samples) -> BUFFER
            //   - rel_idx 208-223: HT-SIG0 CP (16 samples) -> SKIP
            //   - rel_idx 224-287: HT-SIG0 DATA (64 samples) -> BUFFER
            //   - rel_idx 288-303: HT-SIG1 CP (16 samples) -> SKIP
            //   - rel_idx 304-367: HT-SIG1 DATA (64 samples) -> BUFFER
            //   - rel_idx 368-383: HT-STF CP (16 samples) -> SKIP
            //   - rel_idx 384-447: HT-STF DATA (64 samples) -> BUFFER
            //   - rel_idx 448+: HT-DATA (80-sample period: 16 CP + 64 Data)
            // ============================================================
            if (rel_idx < 64) {
                // Stage 1: L-LTF0 DATA (rel_idx 0-63)
                should_buffer = true;
            } else if (rel_idx < 80) {
                // Stage 1b: L-LTF1 CP (rel_idx 64-79) - SKIP!
                should_buffer = false;
            } else if (rel_idx < 144) {
                // Stage 1c: L-LTF1 DATA (rel_idx 80-143)
                should_buffer = true;
            } else if (rel_idx < 208) {
                // Stage 2: L-SIG (rel_idx 128-143 CP, 144-207 DATA)
                should_buffer = (rel_idx >= 144);
            } else if (rel_idx < 288) {
                // Stage 3: HT-SIG0 (rel_idx 208-223 CP, 224-287 DATA)
                should_buffer = (rel_idx >= 224);
            } else if (rel_idx < 368) {
                // Stage 4: HT-SIG1 (rel_idx 288-303 CP, 304-367 DATA)
                should_buffer = (rel_idx >= 304);
            } else if (rel_idx < 448) {
                // Stage 5: HT-STF (rel_idx 368-383 CP, 384-447 DATA)
                should_buffer = (rel_idx >= 384);
            } else {
                // Stage 6: HT-DATA and beyond (80-sample period)
                uint64_t sym_rel_idx = rel_idx - 448;
                uint64_t sym_offset = sym_rel_idx % 80;
                if (sym_offset >= 16) {
                    should_buffer = true;  // Skip CP, buffer Data
                }
            }

            if (should_buffer && !d_buffer_filled) {
                d_buffer[d_buffer_count++] = in[i];
            }

            // Check boundary conditions when buffer is full
            if (d_buffer_count == d_fft_size) {
                uint64_t out_rel_idx = current_idx - d_frame_start_abs;

                // Boundary check: out_rel_idx % 80 == 63
                // This gives boundaries at: 63, 143, 223, 303, 383, 463, 543, 623, 703...
                // All boundaries satisfy out_rel_idx % 80 == 63
                // For HT-DATA (rel_idx >= 448): exclude out_rel_idx < 480 to skip false boundary at 463
                bool at_boundary = (out_rel_idx % 80 == 63);
                if (rel_idx >= 448 && out_rel_idx < 480) {
                    at_boundary = false;
                }

                if (at_boundary) {
                    // Output at boundary
                    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
                    produced += d_fft_size;
                    d_buffer_count = 0;
                    d_buffer_filled = false;
                } else {
                    // Buffer filled at non-boundary - hold for next boundary
                    d_buffer_filled = true;
                    // Don't reset d_buffer_count - keep at 64
                }
            }
        }

        i++;
        consumed++;
    }

    d_items_processed += consumed;


    consume(0, consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr
