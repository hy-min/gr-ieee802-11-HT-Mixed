#!/usr/bin/env python3
"""
p192_selflink_config.py — why does a box's OWN self-link fail at len1000/10ms?

Context
-------
Every SIR-sweep arm in P188-P191 used an interferer configured len1000 / 10 ms
(a 1305 us frame, 13.05% duty).  That interferer decoded ZERO frames in six
consecutive arms, while the SAME box's self-link at len38 / 100 ms runs ~75%
(P186: 113/104/119).

Nobody asked why.  It matters, because Co-SR means "two APs transmitting at
the same time" -- both links need a HIGH duty cycle, and if a single link
cannot sustain its own duty, two-link coexistence in this geometry is
impossible for a reason that has nothing to do with interference.

Two candidate causes, and they have different fixes
--------------------------------------------------
  (1) FRAME LENGTH   a 318-symbol frame accumulates residual CFO across its
                     span.  The self-link is A:0 TX -> B:0 RX: two different
                     UBX-160 subdevices, hence two independent LOs and a real
                     residual CFO.  Short frames never accumulate enough.
  (2) DUTY CYCLE     13% of the time is spent receiving, which may simply
                     overrun the RX chain.

Found while setting this up
---------------------------
`p180_sir_sweep.py` does NOT override the harness's default
`IEEE80211_HDR_COMP_DISABLE=1`, so the entire SIR sweep ran WITHOUT per-symbol
CFO compensation.  Per .claude/rules/env-vars.md that switch is essential in
the cross-device regime (0% -> 65% FCS_OK, P176) and merely "noise dominated"
single-board; but a self-link spans two LOs, so the long-frame case may need
it too.  That makes it a third variable, tested here.

Arms (single variable between adjacent rows)
--------------------------------------------
  A  len1000  100 ms  COMP=1   long frame, low duty, no compensation
  B  len38     10 ms  COMP=1   short frame, same-ish duty, no compensation
  C  len1000  100 ms  COMP=0   long frame, low duty, WITH compensation
  D  len1000   10 ms  COMP=0   long frame, HIGH duty, WITH compensation

Reading
-------
  A fails, B works           -> frame length is the culprit
  C works (A vs C)           -> accumulated CFO, fixed by per-symbol comp
  D works (C vs D)           -> duty is NOT the obstacle  => a working
                                high-duty BSS exists => true Co-SR is possible
  D fails, C works           -> duty itself is the wall, independent of CFO
"""
import argparse
import datetime
import os
import re
import subprocess
from pathlib import Path

REPO = Path('/home/hy/gr-ieee802-11')
HARNESS = REPO / 'test_usrp_rxonly_instrumented.py'
PY = '/home/hy/conda/envs/gnuradio/bin/python'
CAP_ROOT = Path('/home/hy/captures')

RESULT_RE = re.compile(
    r'\[RESULT\].*?est_sent~(\d+)\s+PDU=(\d+)\s+FCS_OK=(\d+)\s+FCS_FAIL=(\d+)')

ARMS = [
    ('A', 1000, 100.0, '1', '长帧 + 低占空比 + 无补偿'),
    ('B', 38, 10.0, '1', '短帧 + 同量级占空比 + 无补偿'),
    ('C', 1000, 100.0, '0', '长帧 + 低占空比 + 有补偿'),
    ('D', 1000, 10.0, '0', '长帧 + 高占空比 + 有补偿'),
]


def parse_env(spec):
    out = {}
    for kv in (spec or '').split(','):
        k, sep, v = kv.partition('=')
        if sep:
            out[k.strip()] = v.strip()
    return out


def base_env(hdr_comp_disable, extra=''):
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = str(REPO / 'wrap_rpc2.so')
    env['PYTHONPATH'] = (f'{REPO}/build/python/bindings:'
                         f'{REPO}/python:{REPO}/examples')
    env['IEEE80211_HDR_COMP_DISABLE'] = hdr_comp_disable
    env.update(parse_env(extra))
    return env


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--addr', default='192.168.20.3', help='box under test')
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--tx-gain', type=float, default=31.5)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--mcs', type=int, default=0)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=5)
    p.add_argument('--tag', default='')
    p.add_argument('--lengths', default='',
                   help='comma list -> run a LENGTH SWEEP instead of the four '
                        'named arms, at --sweep-interval / --sweep-comp.  P192 '
                        'showed len1000 fails 0/750 on a clean self-link at ANY '
                        'duty and with or without CFO compensation, while len38 '
                        'runs 88.9%; this finds where it breaks.')
    p.add_argument('--sweep-interval', type=float, default=100.0)
    p.add_argument('--sweep-comp', default='1')
    p.add_argument('--env', default='',
                   help='extra K=V,K2=V2 injected into the harness env, e.g. '
                        'IEEE80211_SYM52_DUMP=all')
    a = p.parse_args()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = CAP_ROOT / f'p192_selflink_{ts}{"_" + a.tag if a.tag else ""}'
    outdir.mkdir(parents=True, exist_ok=True)
    print('=== P192 自链路配置判别（长帧 vs 占空比 vs CFO 补偿）===', flush=True)
    print(f'[P192] 被测箱 {a.addr}；自链路 {a.rx_subdev}；MCS{a.mcs}；'
          f'logs -> {outdir}', flush=True)

    if a.lengths:
        arms = [(f'L{L}', L, a.sweep_interval, a.sweep_comp,
                 f'帧长扫描 len={L}')
                for L in (int(x) for x in a.lengths.split(','))]
    else:
        arms = ARMS

    for name, length, interval, comp, note in arms:
        duty = (length + 28) * 8 / 6.5e6 / (interval / 1000.0) * 100
        cmd = [PY, str(HARNESS),
               '--mcs', str(a.mcs), '--freq', str(a.freq),
               '--tx-addr', a.addr, '--rx-addr', a.addr, '--rate', '20',
               '--tx-gain', str(a.tx_gain), '--tx-scale', '1.0',
               '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
               '--rx-subdev', a.rx_subdev,
               '--interval', str(interval), '--len', str(length),
               '--warmup', str(a.warmup), '--run', str(a.run),
               '--scales', ','.join([str(a.rx_scale)] * a.windows)]
        tag = f'{name}_len{length}_i{interval:g}_comp{comp}'
        print(f'[P192] --- 臂 {name}: {note}  (len{length} / {interval:g}ms '
              f'/ HDR_COMP_DISABLE={comp} / 占空比~{duty:.1f}%) ---', flush=True)
        with open(outdir / f'{tag}.out', 'w') as ofh, \
                open(outdir / f'{tag}.err', 'w') as efh:
            rc = subprocess.run(cmd, cwd=str(REPO), stdout=ofh, stderr=efh,
                                timeout=a.warmup + a.run * a.windows + 180,
                                env=base_env(comp, a.env)).returncode
        out = (outdir / f'{tag}.out').read_text(errors='replace')
        wins = [int(x[2]) for x in RESULT_RE.findall(out)]
        ok = sum(wins)
        est = sum(int(x[0]) for x in RESULT_RE.findall(out))
        pct = ok * 100.0 / est if est else 0.0
        print(f'[P192] 臂 {name}: FCS_OK={ok}/{est} = {pct:.1f}%  '
              f'rc={rc}  win={wins}', flush=True)

    print(f'[P192] logs: {outdir}', flush=True)


if __name__ == '__main__':
    main()
