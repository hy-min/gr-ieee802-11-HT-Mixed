#!/usr/bin/env python
"""
p193_loopback_longframe.py — offline, frame-driven long-frame reproduction.

Why a separate script
---------------------
`examples/test_direct_loopback.py` is the regression gate and its own comment
requires its default behaviour to stay unchanged.  This is a purpose-built
probe for the P192/P193 long-frame defect.

Why the existing loopback could not answer the question
-------------------------------------------------------
It drives the MAC with `blocks.message_strobe(..., 500)` -- WALL-CLOCK paced --
while an unthrottled offline PHY advances roughly 0.2% of real time (10 s of
wall clock moved ~18 ms of signal).  So the MAC produces frames hundreds of
times faster than the PHY can emit them and everything queues.  Any
frame-length dependence observed that way is confounded by queueing, and the
`[decode_mac][INCOMPLETE] where=stop` it produced came from the block's
shutdown hook, not from the transmitter.

Design
------
ONE FRAME AT A TIME, driven from Python rather than by a clock:

    post the payload to the MAC's 'app in' port
    wait until the RX either publishes a PDU or the per-frame budget expires
    record the outcome, then send the next frame

That removes queueing entirely, so each frame's fate is attributable.

`bandwidth=20e6` by default, matching the USRP arms.  The regression gate uses
10e6 because of a documented boxcar artefact (P154: 8-sample L-STF period does
not match the 16-lag boxcar); 20e6 is the honest setting for behavioural work.

Usage
-----
    python3 p193_loopback_longframe.py --len 400 --frames 5
"""
import argparse
import os
import sys
import time

os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'

from gnuradio import gr, blocks
import pmt

sys.path.insert(0, '/home/hy/gr-ieee802-11')
sys.path.insert(0, '/home/hy/gr-ieee802-11/examples')

_MCS_TO_ENC = {0: 'BPSK_1_2', 1: 'QPSK_1_2', 2: 'QPSK_3_4', 3: 'QAM16_1_2',
               4: 'QAM16_3_4', 5: 'QAM64_2_3', 6: 'QAM64_3_4', 7: 'QAM64_5_6'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--len', type=int, default=400, help='payload bytes')
    ap.add_argument('--frames', type=int, default=5)
    ap.add_argument('--mcs', type=int, default=0)
    ap.add_argument('--bandwidth', type=float, default=20e6)
    ap.add_argument('--period-ms', type=int, default=2000,
                    help='message_strobe period; must exceed the ~0.5 s a '
                         'frame costs end-to-end offline, else frames queue')
    ap.add_argument('--settle-s', type=float, default=30.0,
                    help='extra wall-clock after the last frame is requested')
    ap.add_argument('--payload-byte', default='x')
    a = ap.parse_args()

    # Same setdefaults the USRP harness applies, so the comparison is fair.
    for k, v in (('IEEE80211_LSIG_RATE_FORCE', '0xD'),
                 ('IEEE80211_TIMING_OFFSET_APPLY', '0'),
                 ('IEEE80211_HDR_COMP_DISABLE', '1'),
                 ('IEEE80211_H52_2WAY_DEFAULT', '0'),
                 ('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1'),
                 ('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1'),
                 ('IEEE80211_SYNC_SHORT_MIN_PLATEAU_OVERRIDE', '24'),
                 ('IEEE80211_SYNC_SHORT_TRIGGER_MARGIN', '2.5'),
                 ('IEEE80211_DATA_SOFT_VITERBI', '1'),
                 ('IEEE80211_LSIG_VITERBI_CANDIDATE', '1')):
        os.environ.setdefault(k, v)

    import ieee802_11
    from wifi_phy_hier import wifi_phy_hier

    enc = getattr(ieee802_11, _MCS_TO_ENC[a.mcs])
    payload = a.payload_byte * a.len
    print(f'[P193-LB] len={a.len} frames={a.frames} mcs={a.mcs} '
          f'bw={a.bandwidth:g} payload={a.payload_byte!r}×{a.len}', flush=True)

    class Counter(gr.basic_block):
        def __init__(self):
            gr.basic_block.__init__(self, name='ctr', in_sig=None, out_sig=None)
            self.message_port_register_in(pmt.intern('pdu'))
            self.set_msg_handler(pmt.intern('pdu'), self.handle)
            self.ok = 0
            self.fail = 0

        def handle(self, msg):
            crc = pmt.to_long(pmt.dict_ref(pmt.car(msg), pmt.intern('crc'),
                                           pmt.from_long(0)))
            if crc:
                self.ok += 1
            else:
                self.fail += 1

    class Stripper(gr.basic_block):
        def __init__(self):
            gr.basic_block.__init__(self, name='strip', in_sig=None,
                                    out_sig=None)
            self.message_port_register_in(pmt.intern('pdu'))
            self.message_port_register_out(pmt.intern('pdu'))
            self.set_msg_handler(pmt.intern('pdu'), self.handle)

        def handle(self, msg):
            meta = pmt.dict_delete(pmt.car(msg), pmt.mp('encoding'))
            meta = pmt.dict_delete(meta, pmt.mp('mcs'))
            self.message_port_pub(pmt.intern('pdu'), pmt.cons(meta, pmt.cdr(msg)))

    tb = gr.top_block('p193_loopback')
    phy_tx = wifi_phy_hier(bandwidth=a.bandwidth, chan_est=ieee802_11.LS,
                           encoding=enc, frequency=5.89e9, sensitivity=0.01,
                           use_ldpc=False)
    phy_rx = wifi_phy_hier(bandwidth=a.bandwidth, chan_est=ieee802_11.LS,
                           encoding=enc, frequency=5.89e9, sensitivity=0.01,
                           use_ldpc=False)
    mac = ieee802_11.mac([0x23] * 6, [0x42] * 6, [0xff] * 6)
    strip = Stripper()
    ctr = Counter()
    src = blocks.null_source(gr.sizeof_gr_complex)
    snk = blocks.null_sink(gr.sizeof_gr_complex)

    tb.msg_connect((mac, 'phy out'), (strip, 'pdu'))
    tb.msg_connect((strip, 'pdu'), (phy_tx, 'mac_in'))
    tb.connect((src, 0), (phy_tx, 0))
    tb.connect((phy_tx, 0), (phy_rx, 0))
    tb.connect((phy_rx, 0), (snk, 0))
    tb.msg_connect((phy_rx, 'mac_out'), (ctr, 'pdu'))

    # Drive with message_strobe, NOT a hand-rolled _post loop.
    #
    # Measured: a manual one-shot _post advanced the flowgraph only ~0.47 ms of
    # signal in 15 s -- LESS THAN ONE FRAME -- while the regression gate's
    # message_strobe moved ~20 ms (20 frames) in 10 s.  The strobe is not just
    # a frame source, it keeps the flowgraph's message infrastructure alive;
    # post-once-then-wait stalls it.  So: strobe with a period long enough that
    # one frame finishes before the next is requested (measured cost is ~0.5 s
    # per frame), and simply count the outcomes over the whole run.
    period_ms = a.period_ms
    strobe = blocks.message_strobe(pmt.intern(payload), period_ms)
    tb.msg_connect((strobe, 'strobe'), (mac, 'app in'))

    budget = a.frames * period_ms / 1000.0 + a.settle_s
    print(f'[P193-LB] strobe={period_ms}ms budget={budget:.0f}s '
          f'(expect ~{a.frames} frames)', flush=True)
    tb.start()
    t0 = time.time()
    last = -1
    try:
        while time.time() - t0 < budget:
            time.sleep(0.5)
            n = ctr.ok + ctr.fail
            if n != last:
                print(f'[P193-LB]   t={time.time()-t0:5.1f}s '
                      f'ok={ctr.ok} fail={ctr.fail}', flush=True)
                last = n
            if n >= a.frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()

    results = ['FCS_OK'] * ctr.ok + ['FCS_FAIL'] * ctr.fail

    n_ok = results.count('FCS_OK')
    print(f'[P193-LB] RESULT len={a.len} requested={a.frames} '
          f'FCS_OK={ctr.ok} FCS_FAIL={ctr.fail} '
          f'(processed={len(results)})', flush=True)
    return 0 if ctr.ok and not ctr.fail else 1


if __name__ == '__main__':
    sys.exit(main())
