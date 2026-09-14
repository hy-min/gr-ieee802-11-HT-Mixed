#!/usr/bin/env python3
"""
p181_channel_stability.py — victim-link stability over a long continuous run,
interferer SILENT.

Motivation: three P179/P180 sweeps each hit one unexplained burst dropout
(10.9% / 5.7% / 5.3% against a ~90% baseline) that self-recovered within
minutes.  Their magnitude (up to 84pp) exceeds the interference effect we are
trying to measure, so recurrence rate, duration and amplitude must be
quantified before any SIR sweep can be sized — otherwise the sweep cannot tell
an interferer effect from an environment episode.

Runs ONE harness invocation with N windows back to back (no per-point teardown,
so the time series has no gaps), then reports:

  - per-window FCS_OK/est_sent time series (PNG + printed table)
  - mean / sd / min / max
  - EPISODES: runs of consecutive windows below --episode-thresh, with their
    start window, duration in seconds, and depth

Usage:
    python3 p181_channel_stability.py --windows 60 --run 15
"""
import argparse
import datetime
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path('/home/hy/gr-ieee802-11')
HARNESS = REPO / 'test_usrp_rxonly_instrumented.py'
PY = '/home/hy/conda/envs/gnuradio/bin/python'
CAP_ROOT = Path('/home/hy/captures')

RESULT_RE = re.compile(
    r'\[RESULT\] scale=\S+ rx_gain=\S+ cap=\S+ window=(\S+)s est_sent~(\d+) '
    r'PDU=(\d+) FCS_OK=(\d+) FCS_FAIL=(\d+)')


def parse_env(spec):
    out = {}
    for kv in (spec or '').split(','):
        k, sep, v = kv.partition('=')
        if sep:
            out[k.strip()] = v.strip()
    return out


def base_env(env_extra=None):
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = str(REPO / 'wrap_rpc2.so')
    env['PYTHONPATH'] = f'{REPO}/build/python/bindings:{REPO}/python:{REPO}/examples'
    if env_extra:
        env.update(env_extra)
    return env


def find_episodes(rates, win_s, thresh):
    """-> list of (start_idx, n_windows, min_rate) for runs below thresh."""
    eps, i = [], 0
    while i < len(rates):
        if rates[i] < thresh:
            j = i
            while j < len(rates) and rates[j] < thresh:
                j += 1
            eps.append((i, j - i, min(rates[i:j])))
            i = j
        else:
            i += 1
    return eps


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--addr', default='192.168.10.2', help='victim box (B stays silent)')
    p.add_argument('--tx-gain', type=float, default=31.5)
    p.add_argument('--tx-scale', type=float, default=1.0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--mcs', type=int, default=0)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=60)
    p.add_argument('--episode-thresh', type=float, default=50.0,
                   help='a window below this %% counts as an episode window')
    p.add_argument('--env', default='HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1')
    p.add_argument('--tag', default='')
    args = p.parse_args()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{args.tag}' if args.tag else ''
    outdir = CAP_ROOT / f'p181_channel_stability_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    total_s = args.run * args.windows
    print('=== P181 CHANNEL STABILITY (interferer SILENT) ===', flush=True)
    print(f'[STAB] victim={args.addr} MCS{args.mcs} len{args.len} '
          f'tx-gain={args.tx_gain} | {args.windows} windows x {args.run}s '
          f'= {total_s/60:.1f} min continuous', flush=True)
    print(f'[STAB] freq={args.freq} rx-gain={args.rx_gain} env={args.env}',
          flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[STAB] governor={gov.read_text().strip() if gov.exists() else "?"}  '
          f'logs -> {outdir}', flush=True)

    cmd = [PY, str(HARNESS),
           '--mcs', str(args.mcs),
           '--freq', str(args.freq), '--tx-addr', args.addr,
           '--rx-addr', args.addr, '--rate', '20',
           '--tx-gain', str(args.tx_gain), '--tx-scale', str(args.tx_scale),
           '--rx-gain', str(args.rx_gain), '--rx-scale', str(args.rx_scale),
           '--rx-subdev', args.rx_subdev,
           '--interval', str(args.interval), '--len', str(args.len),
           '--warmup', str(args.warmup), '--run', str(args.run),
           '--scales', ','.join([str(args.rx_scale)] * args.windows)]
    timeout = args.warmup + total_s + 300

    t0 = time.time()
    # stderr MUST NOT be a pipe we only drain at the end: the harness logs
    # heavily via USRP_LOG, and once the 64 KiB pipe buffer fills the harness
    # blocks in write() — RX/TX stall, every window reads 0/150, and the
    # process never exits (observed 2026-09-14, three full 15-min runs lost).
    # Send stderr straight to a file instead; nothing can block.
    errfile = outdir / 'run.err'
    err_fh = open(errfile, 'w')
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=err_fh, text=True,
                            start_new_session=True, env=base_env(parse_env(args.env)))
    # Stream windows live AND persist each one immediately — a harness that
    # runs its windows then hangs in teardown (observed 2026-09-14, cost a
    # full 15-min run) must not take the data with it.
    rates = []
    winfile = outdir / 'windows.tsv'
    stalled = False
    # Per-line watchdog: the harness emits one RESULT per window (--run s
    # apart).  Silence longer than that + margin means it is hung.
    quiet_limit = args.run + 60
    while True:
        ready, _, _ = select.select([proc.stdout], [], [], quiet_limit)
        if not ready:
            stalled = True
            print(f'[STAB] WARNING: no output for {quiet_limit:.0f}s — harness '
                  f'hung; killing and keeping {len(rates)} windows', flush=True)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
            break
        line = proc.stdout.readline()
        if not line:
            break
        m = RESULT_RE.search(line)
        if m:
            est, ok = int(m.group(2)), int(m.group(4))
            rate = ok / est * 100 if est else 0.0
            rates.append(rate)
            with open(winfile, 'a') as wf:
                wf.write(f'{len(rates)}\t{ok}\t{est}\t{rate:.2f}\n')
            print(f'[STAB] win {len(rates):3d}/{args.windows}  '
                  f'{ok:3d}/{est}  {rate:5.1f}%', flush=True)
    proc.wait()
    err_fh.close()
    print(f'[STAB] harness rc={proc.returncode}  elapsed={time.time()-t0:.0f}s'
          f'{"  [STALLED]" if stalled else ""}  '
          f'stderr={errfile.stat().st_size/1024:.0f}K', flush=True)

    if not rates:
        sys.exit('no windows parsed — harness failed (see run.err)')

    import statistics
    mean = statistics.fmean(rates)
    sd = statistics.pstdev(rates) if len(rates) > 1 else 0.0
    print(f'\n========== P181 SUMMARY ==========', flush=True)
    print(f'windows={len(rates)}  mean={mean:.2f}%  sd={sd:.2f}  '
          f'min={min(rates):.2f}%  max={max(rates):.2f}%', flush=True)
    eps = find_episodes(rates, args.run, args.episode_thresh)
    if eps:
        print(f'episodes below {args.episode_thresh:g}%: {len(eps)}', flush=True)
        for i, (start, n, lo) in enumerate(eps, 1):
            print(f'  ep{i}: win {start+1}-{start+n}  ({n*args.run:.0f}s)  '
                  f'min={lo:.1f}%', flush=True)
    else:
        print(f'episodes below {args.episode_thresh:g}%: NONE', flush=True)

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        xs = [(i + 0.5) * args.run / 60.0 for i in range(len(rates))]  # minutes
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(xs, rates, marker='o', ms=3, lw=1.2, color='tab:blue')
        ax.axhline(mean, color='gray', ls='--', alpha=0.7,
                   label=f'mean {mean:.1f}%')
        ax.axhline(args.episode_thresh, color='red', ls=':', alpha=0.5,
                   label=f'episode threshold {args.episode_thresh:g}%')
        for start, n, lo in eps:
            ax.axvspan(start * args.run / 60.0,
                       (start + n) * args.run / 60.0,
                       color='red', alpha=0.12)
        ax.set_xlabel('elapsed (min)')
        ax.set_ylabel(f'victim FCS_OK / est_sent per {args.run:g}s window (%)')
        ax.set_ylim(0, 105)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_title(f'P181 channel stability — interferer silent, '
                     f'{args.freq:g} MHz, tx-gain {args.tx_gain:g}, {ts}')
        png = outdir / 'p181_stability.png'
        fig.tight_layout()
        fig.savefig(png, dpi=150)
        print(f'[STAB] curve: {png}', flush=True)
    except Exception as e:
        print(f'[STAB] plot failed (data above still valid): {e}', flush=True)
    print(f'[STAB] logs: {outdir}', flush=True)


if __name__ == '__main__':
    main()
