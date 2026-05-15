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
      d_items_processed(0),
      d_wifi_start_accepted(false)
{
    // Circular buffer for FFT-sized blocks
    d_buffer.resize(d_fft_size);
    d_buffer_count = 0;
    d_buffer_filled = false;

    // We output in multiples of fft_size
    set_output_multiple(d_fft_size);

    // Disable automatic tag propagation - we manually control which tags are forwarded
    set_tag_propagation_policy(TPP_DONT);

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

    // PROBE: Print work call info at start of each work
    static int work_call = 0;
    int this_call = work_call++;
    uint64_t start_abs_idx = nitems_read(0);
    fprintf(stderr, "[SPLITTER_WORK] call=%d ninput_items[0]=%d start_abs_idx=%llu d_frame_start_abs=%lld\n",
            this_call, ninput_items[0], (unsigned long long)start_abs_idx, (long long)d_frame_start_abs);

    // PROBE: Check what sync_long actually produced in its output buffer
    // This reads directly from the input buffer at key positions
    // [SPLITTER_INPUT_CHECK] - REMOVED: excessive debug spam
    // if (this_call == 0 && ninput_items[0] >= 448) {
    //     fprintf(stderr, "[SPLITTER_INPUT_CHECK] in[0]=%.6f%+.6fi in[5]=%.6f%+.6fi\n",
    //             in[0].real(), in[0].imag(), in[5].real(), in[5].imag());
    //     fprintf(stderr, "[SPLITTER_INPUT_CHECK] in[383]=%.6f%+.6fi in[384]=%.6f%+.6fi in[385]=%.6f%+.6fi\n",
    //             in[383].real(), in[383].imag(), in[384].real(), in[384].imag(),
    //             in[385].real(), in[385].imag());
    //     fprintf(stderr, "[SPLITTER_INPUT_CHECK] in[415]=%.6f%+.6fi in[416]=%.6f%+.6fi in[417]=%.6f%+.6fi\n",
    //             in[415].real(), in[415].imag(), in[416].real(), in[416].imag(),
    //             in[417].real(), in[417].imag());
    // }

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
                // d_frame_start_abs should be 0 because:
                // - sync_long output[0] = sync_long input[d_frame_start] = input[176]
                // - ht_symbol_splitter input[0] = sync_long output[0] = input[176]
                // - So L-LTF0 DATA starts at ht_symbol_splitter input position 0
                // - We want rel_idx=0 to correspond to L-LTF0 DATA start
                // - Therefore d_frame_start_abs = 0

                // Check if this is a NEW frame
                // If d_frame_start_known is already true and we see another wifi_start,
                // it means a new frame has started (we don't re-use wifi_start within a frame)
                //
                // CRITICAL FIX: Ignore wifi_start tags while still processing preamble symbols.
                // HT-Mixed preamble has ~7 symbols (2 L-LTF + 1 L-SIG + 2 HT-SIG + 1 HT-STF) = 448 samples before HT-DATA.
                // If d_items_processed < 500, we're still in preamble - ignore this wifi_start.
                //
                // The previous condition "!is_new_frame && d_frame_start_known" was wrong:
                // it only ignored if d_frame_start_known was already true. But on the FIRST
                // wifi_start, d_frame_start_known is false, so the tag was always accepted,
                // causing d_frame_start_abs to change from 0 to 176 MID-PREAMBLE, corrupting
                // the rel_idx calculation for buffered symbols.
                //
                // FIX: Ignore wifi_start if d_items_processed < 500 (still in preamble).
                // This applies regardless of whether d_frame_start_known is true.
                // However, we still need to set d_frame_start_known=true so that buffering is enabled.
                bool is_in_preamble = (d_items_processed < 500);

                if (is_in_preamble) {
                    // We're seeing wifi_start during preamble processing - ignore the new value
                    // but KEEP the current d_frame_start_abs to avoid corrupting buffered symbols
                    fprintf(stderr, "[SPLITTER_TAG] Ignoring wifi_start during preamble: d_items_processed=%llu d_frame_start=%llu\n",
                            (unsigned long long)d_items_processed, (unsigned long long)d_frame_start);
                    // CRITICAL: Still enable buffering by setting d_frame_start_known=true
                    // if this is the first wifi_start we received
                    if (!d_frame_start_known) {
                        d_frame_start_known = true;
                        // d_frame_start_abs stays at initial value (0) during preamble
                        // We'll compute rel_idx correctly because input[0] = L-LTF0 DATA
                    }
                    // CRITICAL: Still propagate wifi_start so downstream (FFT/equalizer) knows
                    // where the frame starts. Use d_frame_start_abs=0 since that's our
                    // internal coordinate during preamble.
                    d_wifi_start_accepted = true;  // Propagate wifi_start!
                } else {
                    // FIX: Only set d_frame_start_abs when wifi_start is ACCEPTED, not when ignored.
                    // Previously, d_frame_start_abs was set BEFORE the preamble check, causing
                    // spurious wifi_start (d_frame_start=181) to overwrite the correct value (176).
                    d_frame_start_abs = (int64_t)d_frame_start;
                    fprintf(stderr, "[SPLITTER_TAG] d_frame_start=%llu -> d_frame_start_abs=%lld\n",
                            (unsigned long long)d_frame_start,
                            (long long)d_frame_start_abs);
                    d_frame_start_known = true;
                    d_wifi_start_accepted = true;

                    // Critical state reset for multi-frame handling:
                    // When a new wifi_start is detected, reset all CP-skip state variables
                    // to ensure the second frame's L-LTF CP is correctly skipped, just like the first frame.
                    d_buffer_count = 0;
                    d_items_processed = 0;
                    fprintf(stderr, "[HT_SPLITTER] wifi_start detected! Reset buffer_count=0, items_processed=0.\n");
                }

                // Only propagate wifi_start if SPLITTER accepted it (not ignored)
                if (d_wifi_start_accepted) {
                    add_item_tag(0,  // output port 0
                                 nitems_written(0),  // current output position
                                 pmt::string_to_symbol("wifi_start"),
                                 pmt::from_double(d_frame_start_abs),
                                 pmt::string_to_symbol(name()));
                    d_wifi_start_accepted = false;  // Reset after propagation
                }

                break;
            }
        }
    }

    int i = 0;
    int items_consumed_this_call = 0;  // Track consumed for starvation protection

    bool prev_should_buffer = false;  // Track previous should_buffer for region transition detection

    // CRITICAL SAFETY CHECK: If we don't have enough items for even one symbol, return 0.
    // This prevents reading garbage data when GNU Radio wakes us with insufficient items.
    if (ninput_items[0] < d_symbol_size) {
        fprintf(stderr, "[SPLITTER_SAFETY] ninput_items[0]=%d < d_symbol_size=%d, returning 0\n",
                ninput_items[0], d_symbol_size);
        return 0;
    }
    while (i < ninput_items[0]) {
        uint64_t current_idx = start_abs_idx + i;

        // Compute rel_idx early for use in garbage detection
        uint64_t rel_idx = 0;
        bool frame_started = (d_frame_start_known && current_idx >= d_frame_start_abs);
        if (frame_started) {
            rel_idx = current_idx - d_frame_start_abs;
        }

        // Only process after frame start is known
        if (frame_started) {
            // Calculate how many items we need to complete the current symbol
            // HT-Mixed 20MHz: each OFDM symbol is 80 samples (16 CP + 64 Data)
            // We need to know if we have enough remaining items to finish current symbol
            int remaining_items = ninput_items[0] - i;
            int items_needed_for_current_symbol = 80;  // 16 CP + 64 Data

            // STARVATION PROTECTION: If not enough items remain to complete current symbol,
            // AND we're in a DATA region (should_buffer=true), AND we're not mid-buffer
            // (d_buffer_count == 0), consume what we've processed and return.
            // NOTE: We only check this when in_data_region=true because in CP/gap regions
            // (should_buffer=false), we don't need a full symbol - we're just skipping.
            if (remaining_items < items_needed_for_current_symbol && d_buffer_count == 0) {
                // Only trigger starvation if we're in a DATA region
                // HT-Mixed preamble DATA regions: rel_idx 0-63 (LTF0), 80-143 (LTF1), 160-223 (L-SIG),
                // 240-303 (HT-SIG0), 320-383 (HT-SIG1), 400-463 (HT-STF), 464+ (HT-DATA)
                bool in_data_region = (rel_idx < 64) || (rel_idx >= 80 && rel_idx < 144) ||
                                     (rel_idx >= 160 && rel_idx < 224) || (rel_idx >= 240 && rel_idx < 304) ||
                                     (rel_idx >= 320 && rel_idx < 384) || (rel_idx >= 400 && rel_idx < 464) ||
                                     (rel_idx >= 464);
                if (in_data_region) {
                    fprintf(stderr, "[SPLITTER_STARVATION] remaining=%d < needed=%d, returning early\n",
                            remaining_items, items_needed_for_current_symbol);
                    d_items_processed += items_consumed_this_call;
                    d_buffer_filled = false;
                    d_buffer_count = 0;
                    consume(0, items_consumed_this_call);
                    return produced;
                }
            }

            bool should_buffer = false;

            // FIX: When entering a new buffering region (should_buffer just became true),
            // reset d_buffer_filled so we can start buffering the new symbol.
            if (should_buffer && !prev_should_buffer) {
                d_buffer_filled = false;
                d_buffer_count = 0;
            }
            prev_should_buffer = should_buffer;

            // [SPLITTER_START, SPLITTER_IN_AMP, SPLITTER_AMPLITUDE, SPLITTER_IDX_xxx] - REMOVED: excessive debug probes

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
            // HT-Mixed 20MHz Preamble Structure (IEEE 802.11n):
            //
            // sync_long outputs from input 176 (L-LTF0 DATA start)
            // rel_idx = input_pos - 176
            //
            // L-LTF0 DATA: rel_idx 0-63 (64 samples) -> BUFFER
            // L-LTF1 CP: rel_idx 64-79 (16 samples) -> SKIP
            // L-LTF1 DATA: rel_idx 80-143 (64 samples) -> BUFFER
            // L-SIG CP: rel_idx 144-159 (16 samples) -> SKIP
            // L-SIG DATA: rel_idx 160-223 (64 samples) -> BUFFER
            // HT-SIG0 CP: rel_idx 224-239 (16 samples) -> SKIP
            // HT-SIG0 DATA: rel_idx 240-303 (64 samples) -> BUFFER
            // HT-SIG1 CP: rel_idx 304-319 (16 samples) -> SKIP
            // HT-SIG1 DATA: rel_idx 320-383 (64 samples) -> BUFFER
            // HT-STF CP: rel_idx 384-399 (16 samples) -> SKIP
            // HT-STF DATA: rel_idx 400-463 (64 samples) -> BUFFER
            // HT-DATA: rel_idx 464+ (80-sample period: 16 CP + 64 Data)
            // ============================================================
            // ============================================================
            // HT-Mixed 20MHz Preamble Structure (IEEE 802.11n)
            // L-LTF: T1 (0-63) + T2 (64-127) 无缝连接，无 CP
            // 后续符号: 16 CP + 64 DATA = 80 点
            // ============================================================
            if (rel_idx < 64) {
                // Stage 1: L-LTF0 DATA (rel_idx 0-63)
                should_buffer = true;
            } else if (rel_idx < 80) {
                // Stage 1b: L-LTF1 CP (rel_idx 64-79) - 跳过！
                should_buffer = false;
            } else if (rel_idx < 144) {
                // Stage 1c: L-LTF1 DATA (rel_idx 80-143)
                should_buffer = true;
            } else if (rel_idx < 160) {
                // Stage 2: L-SIG CP (rel_idx 144-159) - 跳过
                should_buffer = false;
            } else if (rel_idx < 240) {
                // Stage 2b: L-SIG DATA (rel_idx 160-223)
                should_buffer = true;
            } else if (rel_idx < 304) {
                // Stage 3: HT-SIG0 CP (rel_idx 240-303) - 跳过
                should_buffer = false;
            } else if (rel_idx < 368) {
                // Stage 3b: HT-SIG0 DATA (rel_idx 304-367) - 64 samples
                should_buffer = true;
            } else if (rel_idx < 384) {
                // Stage 4: HT-SIG1 CP (rel_idx 368-383) - 跳过
                should_buffer = false;
            } else if (rel_idx < 448) {
                // Stage 4b: HT-SIG1 DATA (rel_idx 384-447)
                should_buffer = true;
            } else if (rel_idx < 400) {
                // Stage 5: HT-STF CP (rel_idx 384-399) - 跳过
                should_buffer = false;
            } else if (rel_idx < 464) {
                // Stage 5b: HT-STF DATA (rel_idx 400-463)
                should_buffer = true;
            } else {
                // Stage 6: HT-DATA and beyond (80-sample period: 16 CP + 64 Data)
                uint64_t sym_rel_idx = rel_idx - 464;
                uint64_t sym_offset = sym_rel_idx % 80;
                if (sym_offset >= 16) {
                    should_buffer = true;  // Skip CP, buffer Data
                }
            }

            // [SPLITTER_REL] - REMOVED: debug probe
            // DEBUG PROBE: Track rel_idx and buffer position for key indices
            // static int debug_rel_idx = 0;
            // if (debug_rel_idx < 20 && (rel_idx == 0 || rel_idx == 64 || rel_idx == 80 || rel_idx == 144 || rel_idx == 160)) {
            //     fprintf(stderr, "[SPLITTER_REL] rel_idx=%llu current_idx=%llu should_buffer=%d d_buffer_count=%d\n",
            //             (unsigned long long)rel_idx,
            //             (unsigned long long)current_idx,
            //             should_buffer ? 1 : 0,
            //             d_buffer_count);
            //     debug_rel_idx++;
            // }

            // Always buffer when should_buffer is true
            if (should_buffer) {
                d_buffer[d_buffer_count++] = in[i];
                // [SPLITTER_LTF0_PROBE, SPLITTER_LTF1_PROBE] - REMOVED: debug probes
            }

            // [SPLITTER_LSIG_BUF] - REMOVED: debug probe
            // Probe: Check buffer state at L-SIG boundary rel_idx
            // static int lsig_boundary_probe = 0;
            // if (lsig_boundary_probe < 3 && (rel_idx == 160 || rel_idx == 223)) {
            //     fprintf(stderr, "[SPLITTER_LSIG_BUF] rel_idx=%llu d_buffer_count=%d d_buffer_filled=%d should_buffer=%d\n",
            //             (unsigned long long)rel_idx, d_buffer_count, d_buffer_filled, should_buffer);
            //     lsig_boundary_probe++;
            // }

            // Check boundary conditions when buffer is full
            if (d_buffer_count == d_fft_size) {
                uint64_t out_rel_idx = current_idx - d_frame_start_abs;

                // [DEBUG_BCKT] - REMOVED: boundary check debug (excessive spam)
                // fprintf(stderr, "[DEBUG_BCKT] rel_idx=%llu d_buffer_count=%d d_buffer_filled=%d out_rel_idx=%llu\n",
                //         (unsigned long long)rel_idx, d_buffer_count, d_buffer_filled, (unsigned long long)out_rel_idx);

                // [SPLITTER_LTF1_END] - REMOVED: boundary debug probe
                // PROBE: Print LTF1 buffer at boundary (out_rel_idx == 143)
                // static int ltf1_boundary_probe = 0;
                // if (ltf1_boundary_probe < 2 && out_rel_idx == 143) {
                //     fprintf(stderr, "[SPLITTER_LTF1_END] LTF1 buffer complete at boundary, first 5 samples:\n");
                //     for (int dbg_i = 0; dbg_i < 5; dbg_i++) {
                //         fprintf(stderr, "  buf[%d]=%.6f%+.6fi\n", dbg_i, d_buffer[dbg_i].real(), d_buffer[dbg_i].imag());
                //     }
                //     ltf1_boundary_probe++;
                // }

                // ============================================================
                // Boundary trigger: output FFT window when 64 samples collected
                // Single Source of Truth: rel_idx (not out_rel_idx)
                // ============================================================
                bool at_boundary = false;
                if (rel_idx < 64) {
                    // LTF0 boundary: output at rel_idx=63
                    at_boundary = (rel_idx == 63);
                } else if (rel_idx < 144) {
                    // LTF1 DATA boundary: output at rel_idx=143 (end of LTF1 DATA)
                    at_boundary = (rel_idx == 143);
                } else if (rel_idx < 160) {
                    // L-SIG CP: no output
                    at_boundary = false;
                } else if (rel_idx < 224) {
                    // L-SIG boundary: output at rel_idx=223
                    at_boundary = (rel_idx == 223);
                } else if (rel_idx < 304) {
                    // HT-SIG0 CP: no output
                    at_boundary = false;
                } else if (rel_idx < 368) {
                    // HT-SIG0 boundary: output at rel_idx=303
                    at_boundary = (rel_idx == 303);
                } else if (rel_idx < 384) {
                    // HT-SIG1 CP: no output
                    at_boundary = false;
                } else if (rel_idx < 448) {
                    // HT-SIG1 boundary: output at rel_idx=383
                    at_boundary = (rel_idx == 383);
                } else if (rel_idx < 464) {
                    // HT-STF boundary: output at rel_idx=463
                    at_boundary = (rel_idx == 463);
                } else {
                    // HT-DATA and beyond: 80-sample periodicity
                    uint64_t sym_offset = (rel_idx - 464) % 80;
                    at_boundary = (sym_offset == 0);
                }

                // [DEBUG_SPLITTER_REL] - REMOVED: rel_idx debug (excessive spam)
                // if (out_rel_idx >= 130 && out_rel_idx <= 240) {
                //     fprintf(stderr, "[DEBUG_SPLITTER_REL] out_rel_idx=%llu d_buffer_count=%d d_buffer_filled=%d at_boundary=%d\n",
                //             (unsigned long long)out_rel_idx, d_buffer_count, d_buffer_filled, at_boundary);
                // }

                if (at_boundary) {
                    // Debug: Print symbol type based on rel_idx
                    // The SPLITTER outputs FFT at the boundary where the previous symbol ends.
                    // rel_idx=223: output is L-SIG FFT (L-SIG DATA ends at 223)
                    // rel_idx=303: output is HT-SIG0 FFT (HT-SIG0 DATA ends at 303)
                    // rel_idx=447: output is HT-SIG1 FFT (HT-SIG1 DATA ends at 447)
                    int symbol_type = -1;
                    if (rel_idx == 63 || rel_idx == 143) {
                        symbol_type = 0; // L-LTF FFT
                    } else if (rel_idx == 223) {
                        symbol_type = 2; // L-SIG FFT
                    } else if (rel_idx == 303) {
                        symbol_type = 3; // HT-SIG0 FFT
                    } else if (rel_idx == 447) {
                        symbol_type = 4; // HT-SIG1 FFT
                    } else if (rel_idx == 463) {
                        symbol_type = 5; // HT-STF FFT
                    }
                    // [SPLITTER] output - REMOVED: excessive debug spam
                    // fprintf(stderr, "[SPLITTER] Output symbol type=%d at rel_idx=%llu\n",
                    //         symbol_type, (unsigned long long)out_rel_idx);
                    // Time-domain energy probe using norm (magnitude squared)
                    // [SPLITTER_FFTPROBE] - KEPT: useful for FFT verification
                    float total_energy = 0.0f;
                    float peak_mag = 0.0f;
                    int peak_idx = 0;
                    for (int zz = 0; zz < 64; zz++) {
                        float n = std::norm(d_buffer[zz]);  // magnitude squared
                        total_energy += n;
                        if (std::abs(d_buffer[zz]) > peak_mag) {
                            peak_mag = std::abs(d_buffer[zz]);
                            peak_idx = zz;
                        }
                    }
                    fprintf(stderr, "[SPLITTER_FFTPROBE] type=%d rel_idx=%llu td_energy=%.4f peak_mag=%.4f@%d first=%.4f%+.4fi last=%.4f%+.4fi buf_filled=%d\n",
                            symbol_type, (unsigned long long)out_rel_idx, total_energy,
                            peak_mag, peak_idx,
                            d_buffer[0].real(), d_buffer[0].imag(),
                            d_buffer[63].real(), d_buffer[63].imag(),
                            d_buffer_filled);
                    fflush(stderr);
                    // PROBE: Print time-domain buffer at boundary (LTF0 vs LTF1 comparison)
                    // If LTF1 is negated version of LTF0, we'll see d_buffer[i] ≈ -first_ltf0_sample
                    static gr_complex saved_first_ltf0[8] = {gr_complex(0,0)};
                    static bool have_ltf0 = false;
                    if (out_rel_idx == 63 && !have_ltf0) {
                        // This is LTF0 - save first 8 samples
                        for (int dbg_i = 0; dbg_i < 8; dbg_i++) {
                            saved_first_ltf0[dbg_i] = d_buffer[dbg_i];
                        }
                        have_ltf0 = true;
                        fprintf(stderr, "\n[SPLITTER_TD_PROBE] LTF0 (rel_idx=63) first 8 TD samples:\n");
                        for (int dbg_i = 0; dbg_i < 8; dbg_i++) {
                            fprintf(stderr, "  TD[%d] = %.6f%+.6fi\n",
                                    dbg_i, d_buffer[dbg_i].real(), d_buffer[dbg_i].imag());
                        }
                    } else if (out_rel_idx == 127 && have_ltf0) {
                        // This is LTF1 - compare with saved LTF0
                        fprintf(stderr, "\n[SPLITTER_TD_PROBE] LTF1 (rel_idx=127) first 8 TD samples:\n");
                        for (int dbg_i = 0; dbg_i < 8; dbg_i++) {
                            fprintf(stderr, "  TD[%d] = %.6f%+.6fi  (LTF0[0]=%.6f%+.6fi diff=%.6f%+.6fi)\n",
                                    dbg_i, d_buffer[dbg_i].real(), d_buffer[dbg_i].imag(),
                                    saved_first_ltf0[dbg_i].real(), saved_first_ltf0[dbg_i].imag(),
                                    (d_buffer[dbg_i] + saved_first_ltf0[dbg_i]).real(),
                                    (d_buffer[dbg_i] + saved_first_ltf0[dbg_i]).imag());
                        }
                        fprintf(stderr, "\n[SPLITTER_TD_PROBE] LTF1 vs LTF0 negation check:\n");
                        for (int dbg_i = 0; dbg_i < 8; dbg_i++) {
                            gr_complex diff = d_buffer[dbg_i] + saved_first_ltf0[dbg_i];  // Should be ~0 if LTF1 = -LTF0
                            fprintf(stderr, "  TD[%d]: LTF1 + LTF0 = %.6f%+.6fi (should be ~0 if negated)\n",
                                    dbg_i, diff.real(), diff.imag());
                        }
                        have_ltf0 = false;  // Reset for next frame
                    }
                    // Output at boundary
                    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
                    // [SPLITTER_DUMP] - REMOVED: debug probe
                    // [SPLITTER_LLTF_VERIFY] - REMOVED: debug probe
                    produced += d_fft_size;
                    d_buffer_count = 0;
                    d_buffer_filled = false;
                } else {
                    // Buffer filled at non-boundary position
                    // Output the buffered data and reset state so next symbol can be buffered
                    memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
                    produced += d_fft_size;
                    d_buffer_count = 0;
                    d_buffer_filled = false;
                }
            }
        }

        items_consumed_this_call++;
        i++;
        consumed++;
    }

    // Update d_items_processed for normal completion (starvation return already updates it)
    d_items_processed += consumed;

    consume(0, consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr
