#!/usr/bin/env python3
"""
p187_coupling_measure.py — measure the two coupling paths at the VICTIM's RX.

Why
---
Every Co-SR conclusion in this project rests on an unmeasured number: how much
stronger is box A's own transmitter than box B's, at box A's receiver?  P180
inferred ">31.5 dB" from the fact that turning A's TX down 31.5 dB did not let
B's interference show up -- but at that operating point A's own link was only
52% (SNR-limited), so B's contribution could simply have been buried in the
noise floor.  An invisible effect is not an absent effect.

This measures both paths directly, in absolute received power, from the
victim's own IQ:

  run 1: A transmits normally,  B silent        -> P_A + N
  run 2: A silent (tx-scale~0), B transmits     -> P_B + N
  run 3: both silent                            -> N

  SIR = 10*log10( (P_A - N) / (P_B - N) )

Both transmitters use the SAME frame config (len38 / 100 ms) so the duty cycles
match and the ratio is duty-independent.

Usage
-----
  python3 p187_coupling_measure.py
"""
import argparse
import datetime
import os
import subprocess
import time
from pathlib import Path

import numpy as np

REPO = Path('/home/hy/gr-ieee802-11')
HARNESS = REPO / 'test_usrp_rxonly_instrumented.py'
PY = '/home/hy/conda/envs/gnuradio/bin/python'
CAP_ROOT = Path('/home/hy/captures')

# A's digital scale when we want it effectively silent (its own signal -100 dB)
SILENT_SCALE = 1e-5


def base_env():
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = str(REPO / 'wrap_rpc2.so')
    env['PYTHONPATH'] = f'{REPO}/build/python/bindings:{REPO}/python:{REPO}/examples'
    env['IEEE80211_HDR_COMP_DISABLE'] = '0'
    env['IEEE80211_SYNC_LONG_XCORR_FS'] = '1'
    return env


def run(a, addr, tx_gain, tx_scale, capfile, label):
    """One capture run on the victim box (a.a_addr).  Transmitter is `addr`."""
    cmd = [PY, str(HARNESS),
           '--mcs', '0', '--freq', str(a.freq),
           '--tx-addr', addr, '--rx-addr', a.a_addr,
           '--rate', '20',
           '--tx-gain', str(tx_gain), '--tx-scale', str(tx_scale),
           '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
           '--rx-subdev', a.rx_subdev,
           '--interval', str(a.interval), '--len', str(a.len),
           '--warmup', str(a.warmup), '--run', str(a.run),
           '--scales', str(a.rx_scale),
           '--capture', str(capfile)]
    print(f'[P187] {label}: TX={addr} tx_gain={tx_gain} tx_scale={tx_scale:g} '
          f'-> capture {capfile.name}', flush=True)
    with open(capfile.with_suffix('.log'), 'w') as lf:
        p = subprocess.run(cmd, cwd=str(REPO), stdout=lf, stderr=subprocess.STDOUT,
                           timeout=a.warmup + a.run + 180, env=base_env())
    if p.returncode != 0:
        print(f'[P187]   WARNING rc={p.returncode}', flush=True)
    return capfile.stat().st_size


def power_of(path, skip_s=0.5):
    """Mean |x|^2 over the file, skipping the first `skip_s` seconds."""
    x = np.fromfile(path, dtype=np.complex64)
    if skip_s > 0:
        x = x[int(skip_s * 20e6):]
    if x.size == 0:
        return 0.0
    return float(np.mean(np.abs(x).astype(np.float64) ** 2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--a-addr', default='192.168.10.2', help='victim box (RX)')
    p.add_argument('--b-addr', default='192.168.20.3', help='neighbour box')
    p.add_argument('--tx-gain', type=float, default=31.5)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--warmup', type=float, default=2)
    p.add_argument('--run', type=float, default=3)
    p.add_argument('--tag', default='')
    a = p.parse_args()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = CAP_ROOT / f'p187_coupling_{ts}{a.tag and "_"+a.tag or ""}'
    outdir.mkdir(parents=True, exist_ok=True)
    print('=== P187 COUPLING MEASUREMENT (victim RX input) ===', flush=True)
    print(f'[P187] victim RX = box {a.a_addr} ({a.rx_subdev}); '
          f'tx-gain {a.tx_gain}, rx-gain {a.rx_gain}, rx-scale {a.rx_scale}',
          flush=True)
    print(f'[P187] both transmitters use MCS0 len{a.len} / {a.interval:g}ms '
          f'(same duty -> duty cancels in the ratio)', flush=True)

    caps = {}
    # 1: A transmits (wanted path), B silent
    caps['A'] = outdir / 'cap_A_wanted.bin'
    run(a, a.a_addr, a.tx_gain, 1.0, caps['A'], 'run1  A transmits')
    time.sleep(3)
    # 2: B transmits (interferer path).  A is silent BY CONSTRUCTION here: the
    # harness's TX is box B, so box A only receives -- do NOT pass
    # SILENT_SCALE, which would attenuate B itself (-100 dB) and measure noise.
    caps['B'] = outdir / 'cap_B_interferer.bin'
    run(a, a.b_addr, a.tx_gain, 1.0, caps['B'], 'run2  B transmits')
    time.sleep(3)
    # 3: noise floor
    caps['N'] = outdir / 'cap_N_floor.bin'
    run(a, a.a_addr, a.tx_gain, SILENT_SCALE, caps['N'], 'run3  both silent')

    print('\n========== P187 RESULT ==========', flush=True)
    pw = {k: power_of(v) for k, v in caps.items()}
    for k in ('A', 'B', 'N'):
        print(f'[P187] mean |x|^2  {k:1s} = {pw[k]:.6e}', flush=True)
    n = pw['N']
    pa = max(pw['A'] - n, 0.0)
    pb = max(pw['B'] - n, 0.0)
    print(f'[P187] wanted  path power (A->A) = {pa:.6e}', flush=True)
    print(f'[P187] neighbor path power (B->A) = {pb:.6e}', flush=True)
    if pa > 0 and pb > 0:
        sir = 10 * np.log10(pa / pb)
        print(f'\n[P187] SIR at victim RX = {sir:.2f} dB   '
              f'(capture threshold in P178 offline: ~+3 dB)', flush=True)
        print(f'[P187] => headroom above threshold = {sir - 3:.2f} dB', flush=True)
    else:
        print('[P187] could not resolve both paths (one below the floor)',
              flush=True)
    print(f'[P187] logs: {outdir}', flush=True)
    print(f'[P187] NOTE: capture files are ~{caps["A"].stat().st_size/1e6:.0f} MB '
          f'each; delete them if space is needed.', flush=True)


if __name__ == '__main__':
    main()
