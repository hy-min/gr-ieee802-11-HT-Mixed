#!/usr/bin/env python3
"""
Unit test for sync_short_fused: compare output with original 8-block chain.
"""
import sys
import numpy as np

from gnuradio import gr, blocks, analog
from gnuradio.ieee802_11 import sync_short_fused


def test_sync_short_fused_vs_reference():
    """
    Build two parallel flowgraphs:
    - Reference: noise_source -> head -> [8 GR blocks] -> vector_sink
    - Fused:     noise_source -> head -> [sync_short_fused] -> vector_sink

    Compare 3 output streams sample-by-sample.
    """
    tb = gr.top_block()

    # Source: 20MHz complex noise, 10000 samples
    src = analog.noise_source_c(analog.GR_GAUSSIAN, 1.0, 42)
    head = blocks.head(gr.sizeof_gr_complex, 10000)
    tb.connect(src, head)

    # --- Reference 8-block chain (manually reconstructed) ---
    delay = blocks.delay(gr.sizeof_gr_complex, 16)
    conj = blocks.conjugate_cc()
    mult = blocks.multiply_vcc(1)
    ma_cc = blocks.moving_average_cc(48, 1, 4000, 1)
    mag = blocks.complex_to_mag(1)
    mag_sq = blocks.complex_to_mag_squared(1)
    ma_ff = blocks.moving_average_ff(64, 1, 4000, 1)
    div = blocks.divide_ff(1)

    ref_sink0 = blocks.vector_sink_c()  # delayed raw
    ref_sink1 = blocks.vector_sink_c()  # MA correlation
    ref_sink2 = blocks.vector_sink_f()  # normalized correlation

    tb.connect(head, delay)
    tb.connect(head, mult)
    tb.connect(delay, conj)
    tb.connect(conj, (mult, 1))
    tb.connect(mult, ma_cc)
    tb.connect(ma_cc, mag)
    tb.connect(mag, (div, 0))
    tb.connect(head, mag_sq)
    tb.connect(mag_sq, ma_ff)
    tb.connect(ma_ff, (div, 1))
    tb.connect(delay, ref_sink0)
    tb.connect(ma_cc, ref_sink1)
    tb.connect(div, ref_sink2)

    # --- Fused block (gating disabled for exact match) ---
    fused = sync_short_fused(0.5, 0.0, 1024)  # energy_gate_factor=0 disables gating
    fused.set_min_output_buffer(500000)

    fused_sink0 = blocks.vector_sink_c()
    fused_sink1 = blocks.vector_sink_c()
    fused_sink2 = blocks.vector_sink_f()

    tb.connect(head, fused)
    tb.connect((fused, 0), fused_sink0)
    tb.connect((fused, 1), fused_sink1)
    tb.connect((fused, 2), fused_sink2)

    # Run
    tb.start()
    tb.wait()

    # Compare
    ref0 = np.array(ref_sink0.data())
    ref1 = np.array(ref_sink1.data())
    ref2 = np.array(ref_sink2.data())

    fus0 = np.array(fused_sink0.data())
    fus1 = np.array(fused_sink1.data())
    fus2 = np.array(fused_sink2.data())

    # Skip first 64 samples (warm-up period for moving averages)
    skip = 64

    # Compare overlapping region (fused block has exactly 10000 samples,
    # reference chain may have extra due to delay block padding)
    n = min(len(fus0), len(ref0))
    diff0 = np.max(np.abs(ref0[skip:n] - fus0[skip:n]))
    diff1 = np.max(np.abs(ref1[skip:n] - fus1[skip:n]))
    diff2 = np.max(np.abs(ref2[skip:n] - fus2[skip:n]))

    print(f"Max diff out0 (delayed raw):      {diff0:.2e}")
    print(f"Max diff out1 (MA correlation):   {diff1:.2e}")
    print(f"Max diff out2 (normalized cor):   {diff2:.2e}")

    assert diff0 < 1e-5, f"out0 mismatch: {diff0}"
    assert diff1 < 1e-4, f"out1 mismatch: {diff1}"
    assert diff2 < 1e-5, f"out2 mismatch: {diff2}"
    print("PASS: All outputs match within tolerance")


def test_energy_gating():
    """
    Test that energy gating skips correlation computation for noise-only input.
    """
    tb = gr.top_block()

    # Very low power noise (energy should be below gate threshold)
    src = analog.noise_source_c(analog.GR_GAUSSIAN, 0.001, 42)
    head = blocks.head(gr.sizeof_gr_complex, 1000)
    tb.connect(src, head)

    fused = sync_short_fused(0.5, 3.0, 1024)
    fused.set_min_output_buffer(500000)
    sink0 = blocks.vector_sink_c()
    sink1 = blocks.vector_sink_c()
    sink2 = blocks.vector_sink_f()

    tb.connect(head, fused)
    tb.connect((fused, 0), sink0)
    tb.connect((fused, 1), sink1)
    tb.connect((fused, 2), sink2)

    tb.start()
    tb.wait()

    out1 = np.array(sink1.data())
    out2 = np.array(sink2.data())

    # After warm-up, correlation outputs should be near zero (gated)
    skip = 128
    max_cor = np.max(np.abs(out1[skip:]))
    max_norm = np.max(np.abs(out2[skip:]))

    print(f"Max gated correlation magnitude:  {max_cor:.2e}")
    print(f"Max gated normalized correlation: {max_norm:.2e}")

    assert max_cor < 1e-6, f"Correlation not gated: {max_cor}"
    assert max_norm < 1e-6, f"Normalized cor not gated: {max_norm}"
    print("PASS: Energy gating works correctly")


if __name__ == '__main__':
    test_sync_short_fused_vs_reference()
    test_energy_gating()
    print("\nAll tests passed!")
