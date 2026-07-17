#!/usr/bin/env python3
"""Phase 151: isolate whether residual non-determinism is in the SYNC chain
(sync_short_fused -> sync_short -> sync_long) or DOWNSTREAM (equalizer/decode).

Runs the chain ONLY up to sync_long (no splitter/equalizer/decode_mac), N times
on the same capture, and counts sync_long 'LONG: frame start' detections from
stdout. If the count is constant across runs -> sync chain is deterministic
(residual is downstream). If it varies -> sync chain still chunk-variant.

Usage: python3 p151_synconly_determinism.py --runs 4 [--fix-env K=V ...]
Exit 0 = sync chain deterministic, 1 = sync chain non-deterministic.
"""
import argparse, os, subprocess, sys

REPO = os.path.dirname(os.path.abspath(__file__))

FLOW = r'''
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
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iq', default='/home/hy/captures/p150_ant_g15.fc32')
    ap.add_argument('--nsamp', type=float, default=0)
    ap.add_argument('--runs', type=int, default=4)
    ap.add_argument('--fix-env', action='append', default=[])
    args = ap.parse_args()
    nsamp = args.nsamp or (os.path.getsize(args.iq) // 8)

    runner = os.path.join(REPO, '_p151_synconly_flow.py')
    with open(runner, 'w') as f:
        f.write(FLOW)

    counts = []
    for r in range(args.runs):
        env = os.environ.copy()
        env.pop('LD_LIBRARY_PATH', None)
        for kv in args.fix_env:
            k, v = kv.split('=', 1)
            env[k] = v
        env['LD_PRELOAD'] = './wrap_rpc2.so'
        env['PYTHONPATH'] = 'build/python/bindings:python:examples'
        out = f'/tmp/p151_sync_run{r}.out'
        with open(out, 'w') as of:
            subprocess.run(['/home/hy/conda/envs/gnuradio/bin/python', runner,
                            args.iq, str(nsamp)], cwd=REPO, env=env,
                           stdout=of, stderr=subprocess.DEVNULL, check=True)
        n = sum(1 for line in open(out, errors='replace') if 'LONG: frame start' in line)
        counts.append(n)
        print(f'[run {r}] sync_long detections={n}', flush=True)

    constant = len(set(counts)) == 1
    print(f'\ndetections {min(counts)}..{max(counts)}  constant={constant}')
    print('VERDICT:', 'sync chain DETERMINISTIC (residual is downstream)' if constant
          else 'sync chain STILL NON-DETERMINISTIC (residual is in sync)')
    sys.exit(0 if constant else 1)


if __name__ == '__main__':
    main()
