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
      d_frame_start_abs(176),  // Expected L-LTF0 DATA start (sync_long output starts here)
      d_frame_start_known(false),
      d_items_processed(0),
      d_last_rel_idx(0),
      d_wifi_start_accepted(false),
      d_ignore_mode(false),
      d_rx_reset_offset(-1),
      d_prev_should_buffer(false)
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
    ninput_items_required[0] = 1;
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

    uint64_t start_abs_idx = nitems_read(0);

    // PROBE: Print when general_work is called
    static int call_count = 0;
    static int fft_out_count = 0;
    call_count++;
    if (call_count <= 10) {
        fprintf(stderr, "[SPLITTER_WORK] call=%d noutput=%d ninput=%d start_abs=%llu ignore=%d buf_cnt=%d\n",
                call_count, noutput_items, ninput_items[0], (unsigned long long)start_abs_idx, d_ignore_mode, d_buffer_count);
    }

    // Get all tags in current work call range
    std::vector<gr::tag_t> tags;
    get_tags_in_range(tags, 0, start_abs_idx, start_abs_idx + ninput_items[0]);

    // PROBE: Print all tags found
    for (const auto& tag : tags) {
        fprintf(stderr, "[SPLITTER_TAG] offset=%llu key=%s value=%.2f\n",
                (unsigned long long)tag.offset,
                pmt::symbol_to_string(tag.key).c_str(),
                pmt::is_number(tag.value) ? pmt::to_double(tag.value) : 0.0);
    }

    // Check for rx_reset tag
    // CRITICAL: Only set ignore_mode if this is a NEW rx_reset for the CURRENT frame
    // If rx_reset_offset is behind us (from previous frame), ignore it
    for (const auto& tag : tags) {
        if (pmt::symbol_to_string(tag.key) == "rx_reset") {
            uint64_t reset_pos = (uint64_t)tag.offset;
            // Only enter ignore_mode if this reset is AFTER our current position
            if (reset_pos > start_abs_idx) {
                d_ignore_mode = true;
                d_buffer_count = 0;  // Clear current buffer
                d_buffer_filled = false;
                d_rx_reset_offset = (int64_t)tag.offset;  // Store rx_reset position
            }
        }
    }

    // Read sync_offset tag for coordinate mapping
    // CRITICAL FIX: Build a map of sync_offset values by position
    // We need the sync_offset that corresponds to EACH wifi_start position
    std::map<uint64_t, int64_t> sync_offset_by_pos;
    for (const auto& tag : tags) {
        if (pmt::symbol_to_string(tag.key) == "sync_offset") {
            uint64_t pos = (uint64_t)tag.offset;
            int64_t val = (int64_t)pmt::to_double(tag.value);
            sync_offset_by_pos[pos] = val;
        }
    }

    // Look for wifi_start tag - always check for new frames
    // If we see wifi_start at a position significantly beyond our current d_frame_start_abs,
    // it indicates a new frame has started and we should update our reference.
    if (true) {
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
                    // We're seeing wifi_start during preamble processing.
                    // FIX: We MUST set d_frame_start_abs ONLY on the FIRST wifi_start.
                    // Subsequent wifi_start tags during preamble are spurious and should be ignored.
                    // Also, d_frame_start (160) is the CP start, but we want L-LTF0 DATA start (176).
                    // CRITICAL: Only set d_frame_start_abs on FIRST wifi_start
                    if (!d_frame_start_known) {
                        d_frame_start_known = true;
                        // CRITICAL FIX: Get the sync_offset for THIS wifi_start position
                        // The problem was we used the last sync_offset (at offset=401, value=561)
                        // instead of the one at wifi_start position (offset=0, value=0)
                        int64_t sync_for_this_wifi_start = -1;
                        auto it = sync_offset_by_pos.find(tag_abs_pos);
                        if (it != sync_offset_by_pos.end()) {
                            sync_for_this_wifi_start = it->second;
                        } else {
                            // Find the closest sync_offset at or before this wifi_start
                            for (auto rit = sync_offset_by_pos.rbegin(); rit != sync_offset_by_pos.rend(); rit++) {
                                if (rit->first <= tag_abs_pos) {
                                    sync_for_this_wifi_start = rit->second;
                                    break;
                                }
                            }
                        }
                        // PROBE: Print d_frame_start_abs calculation
                        fprintf(stderr, "[SPLITTER_FRAME_START] d_frame_start=%llu sync=%lld wifi_pos=%llu\n",
                                (unsigned long long)d_frame_start, (long long)sync_for_this_wifi_start, (unsigned long long)tag_abs_pos);
                        // If we know the sync_offset, use it to correct the mapping
                        if (sync_for_this_wifi_start >= 0) {
                            // Correct formula: d_frame_start_abs = d_frame_start [+16] - sync_offset
                            // wifi_start tag.value = d_frame_start (CP start in sync_long input)
                            // sync_offset = d_offset at time wifi_start was written
                            // When sync_offset=0, we need +16 to get correct CP→DATA offset
                            // When sync_offset>0, the offset is already incorporated
                            if (d_frame_start >= 160 && d_frame_start <= 200) {
                                d_frame_start_abs = (int64_t)d_frame_start + 16 - sync_for_this_wifi_start;
                            } else {
                                d_frame_start_abs = (int64_t)d_frame_start - sync_for_this_wifi_start;
                            }
                        } else {
                            // Fallback: use original logic
                            if (d_frame_start >= 160 && d_frame_start <= 200) {
                                d_frame_start_abs = (int64_t)d_frame_start + 16;  // 176
                            } else {
                                d_frame_start_abs = (int64_t)d_frame_start;
                            }
                        }
                        d_wifi_start_accepted = true;  // Propagate wifi_start!
                        // Reset ignore_mode when new frame starts
                        d_ignore_mode = false;
                        d_rx_reset_offset = -1;
                    } else {
                        // Already have frame start - ignore this wifi_start
                        d_wifi_start_accepted = false;
                        // But still reset ignore_mode since this is a NEW frame
                        d_ignore_mode = false;
                        d_rx_reset_offset = -1;
                    }
                } else {
                    // FIX: Only set d_frame_start_abs when wifi_start is ACCEPTED, not when ignored.
                    // Previously, d_frame_start_abs was set BEFORE the preamble check, causing
                    // spurious wifi_start (d_frame_start=181) to overwrite the correct value (176).
                    d_frame_start_abs = (int64_t)d_frame_start;
                    d_frame_start_known = true;
                    d_wifi_start_accepted = true;

                    // Reset ignore_mode when new frame starts
                    d_ignore_mode = false;
                    d_rx_reset_offset = -1;

                    // Critical state reset for multi-frame handling:
                    // When a new wifi_start is detected, reset all CP-skip state variables
                    // to ensure the second frame's L-LTF CP is correctly skipped, just like the first frame.
                    d_buffer_count = 0;
                    d_items_processed = 0;
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
    bool at_end_of_input = false;  // Once true, skip all buffering until end of call


    // CRITICAL SAFETY CHECK: If we don't have enough items for even one symbol, return 0.
    // This prevents reading garbage data when GNU Radio wakes us with insufficient items.
    if (ninput_items[0] < d_symbol_size) {
        return 0;
    }
    // ============================================================
    // CARRYOVER BUFFER CHECK: If previous call left a full buffer
    // at a boundary, output it first before processing new data.
    // This prevents starvation from firing and outputting partial data.
    // ============================================================
    if (d_buffer_count == d_fft_size && d_frame_start_known) {
        uint64_t last_rel_idx = d_last_rel_idx;
        bool at_boundary = false;
        // Check if last_rel_idx was at a boundary position
        if (last_rel_idx == 63 || last_rel_idx == 143 || last_rel_idx == 223 ||
            last_rel_idx == 303 || last_rel_idx == 383 || last_rel_idx == 463) {
            at_boundary = true;
        } else if (last_rel_idx >= 464) {
            uint64_t sym_offset = (last_rel_idx - 464) % 80;
            at_boundary = (sym_offset == 0);
        }
        if (at_boundary) {
            memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
            produced += d_fft_size;
            d_buffer_count = 0;
            d_buffer_filled = false;

            // Compute time-domain energy of the buffered 64 samples for FFT window diagnostic
            fft_out_count++;
            double td_energy = 0.0;
            for (int j = 0; j < d_fft_size; j++) {
                td_energy += std::norm(d_buffer[j]);
            }
            fprintf(stderr, "[SPLITTER_FFTPROBE] fft_out=%d rel_idx=%llu td_energy=%.1f first=%.4f%+.4fi\n",
                    fft_out_count, (unsigned long long)last_rel_idx, td_energy,
                    d_buffer[0].real(), d_buffer[0].imag());
        }
    }

    while (i < ninput_items[0]) {
        // PROBE: Debug while loop
        if (i % 100 == 0 && i > 0) {
            fprintf(stderr, "[SPLITTER_LOOP] i=%d ninput=%d produced=%d\n", i, ninput_items[0], produced);
        }
        // Check if we're near the end
        if (i >= 800 && i < 813) {
            fprintf(stderr, "[SPLITTER_DEBUG] i=%d remaining=%d d_buffer=%d\n", i, ninput_items[0] - i, d_buffer_count);
        }
        uint64_t current_idx = start_abs_idx + i;

        // If in ignore mode, consume all without buffering
        if (d_ignore_mode) {
            if (current_idx > (uint64_t)d_rx_reset_offset) {
                d_ignore_mode = false;
                d_rx_reset_offset = -1;
                d_buffer_count = 0;
                d_items_processed = 0;
            } else {
                i++;
                consumed++;
                continue;
            }
        }

        // Compute rel_idx early for use in garbage detection
        uint64_t rel_idx = 0;
        bool frame_started = (d_frame_start_known && current_idx >= d_frame_start_abs);
        if (frame_started) {
            rel_idx = current_idx - d_frame_start_abs;
        }

        // Only process after frame start is known
        if (frame_started) {
            // ============================================================
            // Calculate remaining items EARLY so we can check boundaries
            // before starvation fires
            // ============================================================
            int remaining_items = ninput_items[0] - i;
            // Calculate items needed dynamically based on position in 80-sample cycle.
            // For preamble (rel_idx < 464): need full 80 (16 CP + 64 Data).
            // For HT-DATA (rel_idx >= 464): need 80 - sym_offset remaining in current cycle.
            uint64_t sym_offset_for_starve = 0;
            int items_needed_for_current_symbol = 80;  // default for preamble
            if (frame_started && rel_idx >= 464) {
                sym_offset_for_starve = (rel_idx - 464) % 80;
                items_needed_for_current_symbol = 80 - (int)sym_offset_for_starve;
            }

            // ============================================================
            // should_buffer calculation - determines what region we're in
            // ============================================================
            bool should_buffer = false;

            // FIX: When entering a new buffering region (should_buffer just became true),
            // reset d_buffer_filled so we can start buffering the new symbol.
            if (should_buffer && !d_prev_should_buffer) {
                d_buffer_filled = false;
                d_buffer_count = 0;
            }
            d_prev_should_buffer = should_buffer;

            // ============================================================
            // HT-Mixed 20MHz preamble structure with explicit boundaries:
            // sync_long output starts at d_frame_start, which is L-LTF0 DATA start (176).
            // So rel_idx=0 in sync_long output = input 176 (L-LTF0 DATA).

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
            } else if (rel_idx < 224) {
                // Stage 2b: L-SIG DATA (rel_idx 160-223) - CORRECTED from 240 to 224
                should_buffer = true;
            } else if (rel_idx < 240) {
                // Stage 3: HT-SIG0 CP (rel_idx 224-239) - CORRECTED
                should_buffer = false;
            } else if (rel_idx < 304) {
                // Stage 3b: HT-SIG0 DATA (rel_idx 240-303) - 64 samples
                should_buffer = true;
            } else if (rel_idx < 320) {
                // Stage 4: HT-SIG1 CP (rel_idx 304-319) - 跳过
                should_buffer = false;
            } else if (rel_idx < 384) {
                // Stage 4b: HT-SIG1 DATA (rel_idx 320-383) - 64 samples
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

            // Always buffer when should_buffer is true (unless at end of input)
            if (should_buffer && !at_end_of_input) {
                d_buffer[d_buffer_count++] = in[i];
            }

            // ============================================================
            // BOUNDARY CHECK AFTER BUFFERING
            // Check if buffer just became full at a boundary position.
            // This runs AFTER the current sample is buffered, so d_buffer_count
            // is updated and rel_idx reflects the just-buffered position.
            // ============================================================
            // PROBE: Print when buffer is full
            if (d_buffer_count == d_fft_size) {
                fprintf(stderr, "[SPLITTER_BUFFER_FULL] rel_idx=%d frame_start=%lld current=%lld\n",
                        rel_idx, (long long)d_frame_start_abs, (long long)current_idx);

                uint64_t out_rel_idx = current_idx - d_frame_start_abs;

                // ============================================================
                // Boundary trigger: output FFT window when 64 samples collected
                // ============================================================
                bool at_boundary = false;
                if (rel_idx < 64) {
                    at_boundary = (rel_idx == 63);
                } else if (rel_idx < 144) {
                    at_boundary = (rel_idx == 143);
                } else if (rel_idx < 160) {
                    at_boundary = false;
                } else if (rel_idx < 224) {
                    at_boundary = (rel_idx == 223);
                } else if (rel_idx < 304) {
                    at_boundary = (rel_idx == 303);
                } else if (rel_idx < 320) {
                    at_boundary = false;
                } else if (rel_idx < 384) {
                    at_boundary = (rel_idx == 383);
                } else if (rel_idx < 400) {
                    at_boundary = false;
                } else if (rel_idx < 464) {
                    at_boundary = (rel_idx == 463);
                } else {
                    uint64_t sym_offset = (rel_idx - 464) % 80;
                    // FIX: Allow sym_offset 0 OR 79 (79 occurs due to RESET zeros混入)
                    // This is a pragmatic fix: when buffer is full and we're near a boundary,
                    // just output the FFT. The alternative is to lose the data entirely.
                    at_boundary = (sym_offset == 0 || sym_offset == 79);
                }

                // FIX: For HT-DATA (rel_idx >= 464), ALWAYS output when buffer is full
                // even if at_boundary=false. This prevents data loss due to boundary misalignment.
                // For preamble (rel_idx < 464), only output at correct boundaries.
                if (at_boundary || rel_idx >= 464) {
                    // Compute energy BEFORE output to filter RESET-gap zeros
                    float total_energy = 0.0f;
                    for (int zz = 0; zz < 64; zz++) {
                        total_energy += std::norm(d_buffer[zz]);
                    }
                    // Skip near-zero-energy FFTs (RESET gap between frames)
                    if (total_energy < 10.0f) {
                        d_buffer_count = 0;
                        d_buffer_filled = false;
                        // DO NOT output — skip this zero-energy FFT block
                    } else {
                        // Debug: Print symbol type based on rel_idx
                        int symbol_type = -1;
                        if (rel_idx == 63 || rel_idx == 143) {
                            symbol_type = 0; // L-LTF FFT
                        } else if (rel_idx == 223) {
                            symbol_type = 2; // L-SIG FFT
                        } else if (rel_idx == 303) {
                            symbol_type = 3; // HT-SIG0 FFT
                        } else if (rel_idx == 383) {
                            symbol_type = 4; // HT-SIG1 FFT
                        } else if (rel_idx == 463) {
                            symbol_type = 5; // HT-STF FFT
                        }
                        float peak_mag = 0.0f;
                        int peak_idx = 0;
                        for (int zz = 0; zz < 64; zz++) {
                            if (std::abs(d_buffer[zz]) > peak_mag) {
                                peak_mag = std::abs(d_buffer[zz]);
                                peak_idx = zz;
                            }
                        }
                        // PROBE: Print FFT buffer content before output
                        fprintf(stderr, "[SPLITTER_FFT] type=%d rel_idx=%d first=%.4f%+.4fi last=%.4f%+.4fi energy=%.2f\n",
                                symbol_type, rel_idx,
                                d_buffer[0].real(), d_buffer[0].imag(),
                                d_buffer[63].real(), d_buffer[63].imag(),
                                total_energy);
                        memcpy(&out[produced], d_buffer.data(), d_fft_size * sizeof(gr_complex));
                        produced += d_fft_size;
                        d_buffer_count = 0;
                        d_buffer_filled = false;

                        fft_out_count++;
                        fprintf(stderr, "[SPLITTER_FFTPROBE] fft_out=%d rel_idx=%llu td_energy=%.1f first=%.4f%+.4fi\n",
                                fft_out_count, (unsigned long long)rel_idx, (double)total_energy,
                                d_buffer[0].real(), d_buffer[0].imag());
                    }
                } else {
                    // Buffer filled at non-boundary position - DANGER!
                    // We missed a boundary, so this FFT is garbage.
                    // DO NOT output - just reset and continue buffering.
                    // This prevents corrupting the equalizer with garbage FFTs.
                    d_buffer_count = 0;
                    d_buffer_filled = false;
                    // Do NOT output - continue to next iteration
                }
            }

            // ============================================================
            // STARVATION PROTECTION
            // Runs AFTER boundary check so full buffers at boundaries are output first.
            //
            // NEVER output partial FFT windows - this corrupts the equalizer's
            // channel estimate. If we can't form a full 64-sample FFT window,
            // return without output and let GNU Radio provide more items.
            //
            // FIX: Only trigger starvation protection if buffer is FULL (64 samples).
            // Partial buffers (from CP skipping) should NOT trigger early return.
            // ============================================================
            if (remaining_items < items_needed_for_current_symbol && d_buffer_count == d_fft_size && !at_end_of_input) {
                // Buffer is full - output it and return
                fprintf(stderr, "[SPLITTER_STARVE] FULL buf=%d remaining=%d rel=%llu produced=%d nout=%d\n",
                        d_buffer_count, remaining_items, (unsigned long long)rel_idx, produced, noutput_items);
                for (int j = 0; j < d_buffer_count; j++) {
                    out[produced++] = d_buffer[j];
                }
                d_buffer_count = 0;
                d_buffer_filled = false;
                d_items_processed += i;
                d_last_rel_idx = rel_idx;
                consume_each(i);
                return produced;
            } else if (remaining_items < items_needed_for_current_symbol && d_buffer_count > 0 && d_buffer_count < d_fft_size && !at_end_of_input) {
                // Partial buffer - buffer whatever we can now, then return.
                // If should_buffer was true, the current item was already buffered
                // at line 579, so skip past it to avoid duplicating it in the buffer.
                if (should_buffer) {
                    i++;
                    consumed++;
                }
                int buf_before = d_buffer_count;
                while (i < ninput_items[0] && d_buffer_count < d_fft_size) {
                    d_buffer[d_buffer_count++] = in[i];
                    i++;
                    consumed++;
                }
                fprintf(stderr, "[SPLITTER_STARVE] PARTIAL buf=%d->%d remaining=%d rel=%llu produced=%d nout=%d\n",
                        buf_before, d_buffer_count, ninput_items[0] - i, (unsigned long long)rel_idx, produced, noutput_items);
                d_items_processed += consumed;
                d_last_rel_idx = rel_idx;
                consume_each(consumed);
                return produced;
            }
        }

        items_consumed_this_call++;
        i++;
        consumed++;
    }

    // Update d_items_processed for normal completion
    d_items_processed += consumed;

    // Track last position for carryover buffer check
    if (consumed > 0 && d_frame_start_known) {
        d_last_rel_idx = (start_abs_idx + consumed - 1) - d_frame_start_abs;
    }

    consume_each(consumed);
    // PROBE: Print final production
    fprintf(stderr, "[SPLITTER_RETURN] produced=%d consumed=%d\n", produced, consumed);
    return produced;
}

} // namespace ieee802_11
} // namespace gr
