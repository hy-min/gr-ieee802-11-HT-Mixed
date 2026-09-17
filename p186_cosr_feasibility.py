#!/usr/bin/env python3
"""
p186_cosr_feasibility.py — Co-SR feasibility REGION measurement.

What Co-SR actually asks
------------------------
Not "how much does an interferer hurt a victim", but:

    does there EXIST a power pair (P_A, P_B) at which BOTH links succeed
    simultaneously?

That is a 2-D feasibility-region question, and it needs BOTH links to be real,
working BSS links -- not one working link plus one pure jammer.  Every earlier
phase (P179-P184) measured only the victim's link and let box B act as a bare
interferer (its own link never decoded a single frame).  This script fixes that:
both boxes run their normal self-link (MCS0 len38 / 100 ms), both transmit AND
receive at the same time, and both success rates are recorded per grid point.

Grid
----
(A tx-gain) x (B tx-gain) over --a-gains x --b-gains.  Two solo reference
points (one box transmitting with the other silent) bracket the grid so each
link's uninfluenced rate is known.

Both harnesses run CONCURRENTLY (different boxes), so each grid point costs one
arm, not two.

Output: per-point (A rate, B rate), a feasibility flag (both >= --thresh), and
a heat map PNG.

Usage
-----
  python3 p186_cosr_feasibility.py --a-gains 0,16,31.5 --b-gains 0,16,31.5
"""
import argparse
import datetime
import os
import re
import signal
import subprocess
import time
from pathlib import Path

REPO = Path('/home/hy/gr-ieee802-11')
HARNESS = REPO / 'test_usrp_rxonly_instrumented.py'
PY = '/home/hy/conda/envs/gnuradio/bin/python'
CAP_ROOT = Path('/home/hy/captures')

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


def link_cmd(a, addr, tx_gain, tag):
    """One BSS link: transmit AND receive on the same box (normal config)."""
    return [PY, str(HARNESS),
            '--mcs', str(a.mcs),
            '--freq', str(a.freq),
            '--tx-addr', addr, '--rx-addr', addr,
            '--rate', '20',
            '--tx-gain', str(tx_gain), '--tx-scale', str(a.tx_scale),
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.interval), '--len', str(a.len),
            '--warmup', str(a.warmup), '--run', str(a.run),
            '--scales', ','.join([str(a.rx_scale)] * a.windows)]


def launch(a, addr, tx_gain, tag, outdir):
    outpath = outdir / f'{tag}.out'
    errpath = outdir / f'{tag}.err'
    ofh = open(outpath, 'w')
    efh = open(errpath, 'w')
    proc = subprocess.Popen(link_cmd(a, addr, tx_gain, tag), cwd=str(REPO),
                            stdout=ofh, stderr=efh, text=True,
                            start_new_session=True,
                            env=base_env(parse_env(a.env)))
    return proc, ofh, efh, outpath, errpath


def collect(a, proc, ofh, efh, outpath, errpath):
    """Wait for one link, retry once on UHD init failure. -> (rate, windows)"""
    timeout = a.warmup + a.run * a.windows + 150
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    ofh.close()
    efh.close()
    out = outpath.read_text(errors='replace')
    err = errpath.read_text(errors='replace')
    windows = RESULT_RE.findall(out)
    init_fail = ('Failure to create rfnoc_graph' in out + err or
                 'RfnocError' in out + err)
    ok = sum(int(w[3]) for w in windows)
    est = sum(int(w[1]) for w in windows)
    return ((ok / est * 100) if est else 0.0), len(windows), init_fail


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--a-gains', default='0,16,31.5', help='grid along box A')
    p.add_argument('--b-gains', default='0,16,31.5', help='grid along box B')
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--a-addr', default='192.168.10.2')
    p.add_argument('--b-addr', default='192.168.20.3')
    p.add_argument('--tx-scale', type=float, default=1.0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--mcs', type=int, default=0)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=4)
    p.add_argument('--thresh', type=float, default=50.0,
                   help='a grid point is FEASIBLE when both links >= this %%')
    p.add_argument('--solo', action='store_true', default=True,
                   help='also measure each link alone (other box silent)')
    p.add_argument('--env', default='HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1')
    p.add_argument('--tag', default='')
    a = p.parse_args()

    a_gains = [float(x) for x in a.a_gains.split(',')]
    b_gains = [float(x) for x in a.b_gains.split(',')]
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{a.tag}' if a.tag else ''
    outdir = CAP_ROOT / f'p186_cosr_region_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    est_per_win = int(round(a.run * 1000.0 / a.interval))
    print('=== P186 Co-SR FEASIBILITY REGION ===', flush=True)
    print(f'[P186] grid: A {a_gains} x B {b_gains}  '
          f'({len(a_gains)*len(b_gains)} points x {a.windows} windows '
          f'x {a.run}s, est_sent~{est_per_win}/win)', flush=True)
    print(f'[P186] BOTH boxes run a real self-link (MCS{a.mcs} len{a.len} '
          f'{a.interval:g}ms) AND transmit simultaneously; feasible = '
          f'both >= {a.thresh:g}%', flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[P186] governor={gov.read_text().strip() if gov.exists() else "?"}  '
          f'logs -> {outdir}', flush=True)

    results = {}

    def run_point(gA, gB, label):
        a_proc = a_h = a_e = a_out = a_err = None
        b_proc = b_h = b_e = b_out = b_err = None
        if gA is not None:
            a_proc, a_h, a_e, a_out, a_err = launch(
                a, a.a_addr, gA, f'{label}_A', outdir)
        if gB is not None:
            b_proc, b_h, b_e, b_out, b_err = launch(
                a, a.b_addr, gB, f'{label}_B', outdir)
        ra = rb = None
        if a_proc is not None:
            ra, wa, ifa = collect(a, a_proc, a_h, a_e, a_out, a_err)
            if ifa or wa < a.windows:
                uhd_nudge(a.a_addr)
                print(f'[P186] {label}: A init/underrun (win={wa})', flush=True)
        if b_proc is not None:
            rb, wb, ifb = collect(a, b_proc, b_h, b_e, b_out, b_err)
            if ifb or wb < a.windows:
                uhd_nudge(a.b_addr)
                print(f'[P186] {label}: B init/underrun (win={wb})', flush=True)
        results[(gA, gB)] = (ra, rb)
        sa = '--' if ra is None else f'{ra:5.1f}'
        sb = '--' if rb is None else f'{rb:5.1f}'
        if ra is None or rb is None:
            verdict = 'solo baseline'
        elif ra >= a.thresh and rb >= a.thresh:
            verdict = 'FEASIBLE'
        else:
            verdict = 'not feasible'
        print(f'[P186] A={gA if gA is not None else "silent":>6} '
              f'B={gB if gB is not None else "silent":>6}  '
              f'A={sa}%  B={sb}%  {verdict}', flush=True)
        time.sleep(5)

    if a.solo:
        print('[P186] --- solo baselines (other box silent) ---', flush=True)
        run_point(31.5, None, 'soloA')
        run_point(None, 31.5, 'soloB')

    print('[P186] --- grid ---', flush=True)
    for gA in a_gains:
        for gB in b_gains:
            run_point(gA, gB, f'g{ga_name(gA)}_{ga_name(gB)}')

    # ---- summary ----
    print('\n========== P186 FEASIBILITY REGION ==========', flush=True)
    hdr = 'A\\B   ' + ''.join(f'{g:>9g}' for g in b_gains)
    print(hdr, flush=True)
    for gA in a_gains:
        row = f'{gA:>5g} '
        for gB in b_gains:
            ra, rb = results.get((gA, gB), (None, None))
            if ra is None or rb is None:
                row += f'{"--":>9}'
            else:
                ok = ra >= a.thresh and rb >= a.thresh
                row += f'{("OK " if ok else "no ") + f"{min(ra, rb):.0f}":>9}'
        print(row, flush=True)
    print('(cell = min(A,B) rate; OK = both >= thresh)', flush=True)
    feas_pts = [(gA, gB) for (gA, gB), (ra, rb) in results.items()
                if ra is not None and rb is not None
                and ra >= a.thresh and rb >= a.thresh]
    print(f'\nfeasible grid points: {len(feas_pts)} / {len(a_gains)*len(b_gains)}',
          flush=True)

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        grid = np.full((len(a_gains), len(b_gains)), np.nan)
        for i, gA in enumerate(a_gains):
            for j, gB in enumerate(b_gains):
                ra, rb = results.get((gA, gB), (None, None))
                if ra is not None and rb is not None:
                    grid[i, j] = min(ra, rb)
        fig, ax = plt.subplots(figsize=(6.5, 5.5))
        im = ax.imshow(grid, origin='lower', cmap='RdYlGn', vmin=0, vmax=100)
        ax.set_xticks(range(len(b_gains)))
        ax.set_xticklabels([f'{g:g}' for g in b_gains])
        ax.set_yticks(range(len(a_gains)))
        ax.set_yticklabels([f'{g:g}' for g in a_gains])
        ax.set_xlabel('Box B (AP-B) TX gain (dB)')
        ax.set_ylabel('Box A (AP-A) TX gain (dB)')
        for i in range(len(a_gains)):
            for j in range(len(b_gains)):
                if not np.isnan(grid[i, j]):
                    ax.text(j, i, f'{grid[i, j]:.0f}', ha='center',
                            va='center', fontsize=11)
        ax.set_title('P186 Co-SR feasibility region — cell = min(A,B) '
                     'FCS_OK %\n(both links transmit simultaneously; '
                     'each runs its own self-link)')
        fig.colorbar(im, ax=ax, label='min(A, B) FCS_OK %')
        fig.tight_layout()
        png = outdir / 'p186_region.png'
        fig.savefig(png, dpi=150)
        print(f'[P186] region map: {png}', flush=True)
    except Exception as e:
        print(f'[P186] plot failed (table above still valid): {e}', flush=True)
    print(f'[P186] logs: {outdir}', flush=True)


def ga_name(g):
    return f'{g:g}'.replace('.', 'p')


if __name__ == '__main__':
    main()
