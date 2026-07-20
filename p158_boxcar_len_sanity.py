#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 158-W32 sanity: boxcar window 16 vs 32 through sync_short_fused.

Synthetic period-16 BPSK L-STF (10 repeats = 160 samples, |x|=1) in CN(0,s^2)
noise. Verifies the ring/mask change is correct before touching hardware:
  - plateau height scales ~linearly with W (raw sum: 16 -> ~16, 32 -> ~32)
  - noise mean scales ~linearly, noise std ~sqrt(W)
  - plateau/noise-std margin improves ~sqrt(2) at W=32

Run from repo root:
  PYTHONPATH=build/python/bindings:python:examples \
    /home/hy/conda/envs/gnuradio/bin/python p158_boxcar_len_sanity.py
"""
import os
import sys

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR'] = '1'
os.environ['IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE'] = '1'

import numpy as np
from gnuradio import gr, blocks
import ieee802_11

N_PRE, N_POST = 2000, 2000
SIGMA = 0.05


def run(boxcar_len):
    if boxcar_len == 16:
        os.environ.pop('IEEE80211_SYNC_SHORT_FUSED_BOXCAR_LEN', None)
    else:
        os.environ['IEEE80211_SYNC_SHORT_FUSED_BOXCAR_LEN'] = str(boxcar_len)
    rng = np.random.default_rng(7)
    seq16 = rng.choice([-1.0, 1.0], 16).astype(np.complex64)
    lstf = np.tile(seq16, 10)
    noise = lambda n: (SIGMA / np.sqrt(2)) * (
        rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    stream = np.concatenate([noise(N_PRE), lstf, noise(N_POST)])

    src = blocks.vector_source_c(stream.tolist(), False)
    fused = ieee802_11.sync_short_fused(0.01, 3.0, 1024)  # reads env NOW
    s0, s1, s2 = blocks.vector_sink_c(), blocks.vector_sink_c(), blocks.vector_sink_f()
    tb = gr.top_block()
    tb.connect(src, fused)
    tb.connect((fused, 0), s0)
    tb.connect((fused, 1), s1)
    tb.connect((fused, 2), s2)
    tb.run()
    out2 = np.array(s2.data())
    plateau = out2[N_PRE + 2 * boxcar_len: N_PRE + 140]  # full-height region only
    noise_r = out2[100:1800]
    return plateau.mean(), noise_r.mean(), noise_r.std()


def main():
    p16, m16, s16 = run(16)
    p32, m32, s32 = run(32)
    print(f"W=16: plateau={p16:.3f} noise_mean={m16:.4f} noise_std={s16:.4f} "
          f"margin={(p16 - m16) / s16:.1f}")
    print(f"W=32: plateau={p32:.3f} noise_mean={m32:.4f} noise_std={s32:.4f} "
          f"margin={(p32 - m32) / s32:.1f}")

    fails = []
    if not (1.8 < p32 / p16 < 2.2):
        fails.append(f"plateau ratio {p32/p16:.2f} not ~2 (ring/mask bug?)")
    if not (1.8 < m32 / m16 < 2.2):
        fails.append(f"noise mean ratio {m32/m16:.2f} not ~2")
    if not (1.2 < s32 / s16 < 1.7):
        fails.append(f"noise std ratio {s32/s16:.2f} not ~sqrt(2)")
    if (p32 - m32) / s32 <= (p16 - m16) / s16:
        fails.append("margin did not improve at W=32")
    if fails:
        print("FAIL:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("PASS: boxcar W=32 behaves as theory predicts")


if __name__ == '__main__':
    main()
