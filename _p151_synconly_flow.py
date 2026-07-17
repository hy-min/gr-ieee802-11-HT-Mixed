
import os, sys, time
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ.setdefault('IEEE80211_LSIG_RATE_FORCE', '0xD')
os.environ.setdefault('IEEE80211_TIMING_OFFSET_APPLY', '1')
os.environ.setdefault('IEEE80211_HDR_COMP_DISABLE', '1')
os.environ.setdefault('IEEE80211_H52_2WAY_DEFAULT', '0')
os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')
from gnuradio import gr, blocks
import ieee802_11

class Top(gr.top_block):
    def __init__(self, iq, nsamp):
        super().__init__("sync-only determinism")
        self.src = blocks.file_source(gr.sizeof_gr_complex, iq, False)
        self.head = blocks.head(gr.sizeof_gr_complex, int(nsamp))
        self.fused = ieee802_11.sync_short_fused(0.01, 3.0, 1024)
        self.short = ieee802_11.sync_short(0.01, 2, True, True)
        self.delay = blocks.delay(gr.sizeof_gr_complex, 320)
        self.long = ieee802_11.sync_long(320, True, True)
        self.sink = blocks.null_sink(gr.sizeof_gr_complex)
        self.connect((self.src, 0), (self.head, 0))
        self.connect((self.head, 0), (self.fused, 0))
        self.connect((self.fused, 0), (self.short, 0))
        self.connect((self.fused, 1), (self.short, 1))
        self.connect((self.fused, 2), (self.short, 2))
        self.connect((self.short, 0), (self.delay, 0))
        self.connect((self.delay, 0), (self.long, 1))
        self.connect((self.short, 0), (self.long, 0))
        self.connect((self.long, 0), (self.sink, 0))

tb = Top(sys.argv[1], float(sys.argv[2]))
tb.start(); tb.wait()
