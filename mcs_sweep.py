#!/usr/bin/env python3
"""
mcs_sweep.py — HT MCS 0-7 FCS_OK rate characterization (single USRP, cable).

For each MCS in --order, runs test_usrp_rxonly_instrumented.py --mcs N at the
Phase 165 cable operating point (freq 5250, tx-gain 0, tx-scale 0.1,
rx-gain 31.5, rx-scale 40, interval 100ms) and collects the per-window
[RESULT] lines: FCS_OK / FCS_FAIL counters are window-deltas, same domain as
est_sent (warmup excluded — P159b denominator rule).

Default order: 0,1,2,3,4,5,6,7,0 — trailing MCS 0 re-measures the baseline as
a drift control (P158: device drift is real; flag if the two MCS-0 arms
differ by > 5 pp).

Retry policy mirrors p158_abab_batch.py (P152): hang -> SIGKILL the process
group + uhd_usrp_probe nudge, up to 2 attempts per MCS.

Usage:
    python3 mcs_sweep.py [--run 15] [--windows 3] [--order 0,1,2,3,4,5,6,7,0]

This is a CHARACTERIZATION measurement, not an A/B verdict — it reports the
measured per-MCS FCS_OK/est_sent curve with raw per-window data.
"""
import argparse
import datetime
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path('/home/hy/gr-ieee802-11')
HARNESS = REPO / 'test_usrp_rxonly_instrumented.py'
PY = '/home/hy/conda/envs/gnuradio/bin/python'
CAP_ROOT = Path('/home/hy/captures')

MCS_NAME = {0: 'BPSK 1/2', 1: 'QPSK 1/2', 2: 'QPSK 3/4', 3: '16QAM 1/2',
            4: '16QAM 3/4', 5: '64QAM 2/3', 6: '64QAM 3/4', 7: '64QAM 5/6'}

RESULT_RE = re.compile(
    r'\[RESULT\] scale=\S+ rx_gain=\S+ cap=\S+ window=(\S+)s est_sent~(\d+) '
    r'PDU=(\d+) FCS_OK=(\d+) FCS_FAIL=(\d+)')


def uhd_nudge(addr):
    try:
        subprocess.run(['uhd_usrp_probe', '--args', f'addr={addr}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
    except Exception:
        pass


def run_mcs(mcs, idx, a, outdir, length=None, env_extra=None):
    """One MCS run. Returns (windows list, attempts, infra_ok).
    env_extra: dict of env overrides applied to the harness process (for
    paired ABAB, e.g. {'IEEE80211_TX_PAD_ALIGN': '8'})."""
    length = a.len if length is None else length
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = str(REPO / 'wrap_rpc2.so')
    env['PYTHONPATH'] = f'{REPO}/build/python/bindings:{REPO}/python:{REPO}/examples'
    if env_extra:
        env.update(env_extra)
    cmd = [PY, str(HARNESS),
           '--mcs', str(mcs),
           '--freq', str(a.freq), '--tx-addr', a.tx_addr, '--rx-addr', a.rx_addr,
           '--rate', '20',
           '--tx-gain', str(a.tx_gain), '--tx-scale', str(a.tx_scale),
           '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
           '--interval', str(a.interval), '--len', str(length),
           '--warmup', str(a.warmup), '--run', str(a.run),
           '--scales', ','.join([str(a.rx_scale)] * a.windows)]
    timeout = a.warmup + a.run * a.windows + 150  # USRP init margin
    tag = f'mcs{mcs}_idx{idx}'

    for attempt in range(1, 3):
        proc = subprocess.Popen(cmd, cwd=str(REPO),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, env=env)
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            timed_out = True
        (outdir / f'{tag}_attempt{attempt}.out').write_text(out or '')
        (outdir / f'{tag}_attempt{attempt}.err').write_text(err or '')

        combined = (out or '') + '\n' + (err or '')
        uhd_init_fail = ('Failure to create rfnoc_graph' in combined or
                         'RfnocError' in combined or
                         'Management operation failed' in combined)
        windows = RESULT_RE.findall(out or '')
        retryable = timed_out or (proc.returncode != 0 and uhd_init_fail) \
            or len(windows) < a.windows
        if retryable and attempt < 2:
            reason = ('HANG timeout' if timed_out else
                      'UHD init failed' if uhd_init_fail else
                      f'only {len(windows)}/{a.windows} windows')
            print(f'[SWEEP] {tag} attempt {attempt}: {reason}; probe + retry...',
                  flush=True)
            uhd_nudge(a.rx_addr)
            time.sleep(5)
            continue
        return windows, attempt, not retryable
    return [], 2, False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5250)
    p.add_argument('--tx-addr', default='192.168.10.2')
    p.add_argument('--rx-addr', default='192.168.10.2')
    p.add_argument('--tx-gain', type=float, default=0)
    p.add_argument('--tx-scale', type=float, default=0.1)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38, help='TX payload bytes (PSDU = len + 28)')
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=3)
    p.add_argument('--order', default='0,1,2,3,4,5,6,7,0',
                   help='MCS sequence; trailing 0 = drift control. '
                        'Per-item payload override: "mcs[:len]" e.g. "1:38,2:100"')
    p.add_argument('--tag', default='')
    args = p.parse_args()

    order = []  # list of (mcs, len, env_extra) tuples; len defaults to --len
    for tok in args.order.split(','):
        parts = tok.split(':')
        mcs = int(parts[0])
        length = int(parts[1]) if len(parts) > 1 and parts[1] else args.len
        env_extra = {}
        if len(parts) > 2 and parts[2]:
            k, _, v = parts[2].partition('=')
            env_extra = {k: v}
        order.append((mcs, length, env_extra))
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{args.tag}' if args.tag else ''
    outdir = CAP_ROOT / f'mcs_sweep_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    est_per_win = int(round(args.run * 1000.0 / args.interval))
    print('=== MCS SWEEP (characterization, not A/B) ===', flush=True)
    print(f'[SWEEP] order={order}  windows={args.windows}x{args.run}s '
          f'(est_sent~{est_per_win}/window)', flush=True)
    print(f'[SWEEP] config: freq={args.freq} tx-gain={args.tx_gain} '
          f'tx-scale={args.tx_scale} rx-gain={args.rx_gain} '
          f'rx-scale={args.rx_scale} interval={args.interval}ms', flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[SWEEP] governor={gov.read_text().strip() if gov.exists() else "?"}  '
          f'logs -> {outdir}', flush=True)

    results = []  # (idx, mcs, length, env_extra, [(win,est,pdu,ok,fail)...], attempts)
    for idx, (mcs, length, env_extra) in enumerate(order):
        env_desc = (' ' + ' '.join(f'{k}={v}' for k, v in env_extra.items())
                    if env_extra else '')
        print(f'[SWEEP] --- idx {idx}: MCS {mcs} ({MCS_NAME[mcs]}) '
              f'len={length}{env_desc} ---', flush=True)
        windows, attempts, infra_ok = run_mcs(mcs, idx, args, outdir, length, env_extra)
        results.append((idx, mcs, length, env_extra, windows, attempts, infra_ok))
        ok_sum = sum(int(w[3]) for w in windows)
        est_sum = sum(int(w[1]) for w in windows)
        fail_sum = sum(int(w[4]) for w in windows)
        rate = (ok_sum / est_sum * 100) if est_sum else 0.0
        print(f'[SWEEP] MCS {mcs} len={length}{env_desc}: FCS_OK={ok_sum}/{est_sum} = '
              f'{rate:.1f}%  FCS_FAIL={fail_sum}  (attempts={attempts}, '
              f'infra_ok={infra_ok})', flush=True)
        time.sleep(3)

    print('\n========== MCS SWEEP SUMMARY ==========', flush=True)
    print('idx  MCS  len   env     modulation   FCS_OK/est_sent  rate%  FCS_FAIL  attempts',
          flush=True)
    for idx, mcs, length, env_extra, windows, attempts, infra_ok in results:
        ok_sum = sum(int(w[3]) for w in windows)
        est_sum = sum(int(w[1]) for w in windows)
        fail_sum = sum(int(w[4]) for w in windows)
        rate = (ok_sum / est_sum * 100) if est_sum else 0.0
        env_s = ''.join(f'{k}={v}' for k, v in env_extra.items()) or '-'
        print(f'{idx:3d}  {mcs:3d}  {length:4d}  {env_s:18s}  {MCS_NAME[mcs]:11s}  '
              f'{ok_sum:6d}/{est_sum:<6d}     {rate:6.2f}  {fail_sum:8d}  '
              f'{attempts}', flush=True)

    # Drift control: repeated identical (mcs, len, env) arms, first vs last.
    from collections import defaultdict
    by_arm = defaultdict(list)
    for idx, mcs, length, env_extra, windows, _, _ in results:
        by_arm[(mcs, length, tuple(sorted(env_extra.items())))].append((idx, windows))
    for (mcs, length, envk), reps in by_arm.items():
        if len(reps) < 2:
            continue
        def rate_of(windows):
            ok = sum(int(w[3]) for w in windows)
            est = sum(int(w[1]) for w in windows)
            return (ok / est * 100) if est else 0.0
        r_first, r_last = rate_of(reps[0][1]), rate_of(reps[-1][1])
        drift = abs(r_first - r_last)
        flag = 'DRIFT SUSPECT' if drift > 5.0 else 'drift ok'
        print(f'[SWEEP] MCS{mcs}/len{length} repeat control: first={r_first:.2f}% '
              f'last={r_last:.2f}%  |delta|={drift:.2f}pp -> {flag}', flush=True)
    print(f'[SWEEP] logs: {outdir}', flush=True)


if __name__ == '__main__':
    main()
