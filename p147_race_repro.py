#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 147 TDD repro: two concurrent sync_short instances racing on the shared
`static float sorted_buf[4096]` (sync_short.cc:124) -> std::sort segfault.

Before fix: two instances, both fed continuous correlated data so both enter the
adaptive-threshold sort concurrently -> race -> intermittent SIGSEGV.
After fix (stack-private buffer): no shared state -> no crash.

Run repeatedly; a single segfault in any run = BUG PRESENT.
"""
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

from gnuradio import gr, blocks, analog
import ieee802_11


class TwoSyncShort(gr.top_block):
    """Reproduce the realtime topology: TWO sync_short instances on independent
    fast sources, both driven hard so both do the adaptive-threshold std::sort
    on the SHARED static sorted_buf concurrently."""
    def __init__(self):
        gr.top_block.__init__(self, "two-sync_short-race")
        # Two independent noise sources -> two sync_short instances (like
        # TX-hier RX path + RX-only chain in the realtime test).
        self.src0 = analog.fastnoise_source_c(analog.GR_GAUSSIAN, 1.0, 0, 8192)
        self.src1 = analog.fastnoise_source_c(analog.GR_GAUSSIAN, 1.0, 0, 8192)
        # head to bound the run
        self.head0 = blocks.head(gr.sizeof_gr_complex, 200_000_000)
        self.head1 = blocks.head(gr.sizeof_gr_complex, 200_000_000)
        self.ss0 = ieee802_11.sync_short(0.01, 2, True, True)
        self.ss1 = ieee802_11.sync_short(0.01, 2, True, True)
        self.ss0.set_min_output_buffer(1000000)
        self.ss1.set_min_output_buffer(1000000)
        self.sink0 = blocks.null_sink(gr.sizeof_gr_complex)
        self.sink1 = blocks.null_sink(gr.sizeof_gr_complex)
        # sync_short needs 3 inputs; feed noise into all (correlated input can
        # be derived but noise is enough to drive the adaptive window fill).
        for src, head, ss, sink in ((self.src0, self.head0, self.ss0, self.sink0),
                                     (self.src1, self.head1, self.ss1, self.sink1)):
            self.connect(src, head)
            # input 0: complex, input 1: complex (abs), input 2: float corr
            self.connect(head, (ss, 0))
            self.connect(head, (ss, 1))
            # corr input (float): use a constant-ish float stream via complex->mag
            self.connect(head, blocks.complex_to_mag(1), (ss, 2))
            self.connect(ss, sink)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for trial in range(n):
        tb = TwoSyncShort()
        tb.start()
        # let it run; if the race triggers, the process SIGSEGVs (no clean return)
        time.sleep(6)
        tb.stop()
        tb.wait()
        print(f"[repro] trial {trial+1}/{n} completed WITHOUT crash", flush=True)
    print("[repro] ALL trials completed — no crash (bug NOT triggered / FIXED)", flush=True)


if __name__ == '__main__':
    main()
