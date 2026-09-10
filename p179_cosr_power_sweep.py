#!/usr/bin/env python3
"""
p179_cosr_power_sweep.py — Co-SR interferer-power sweep (two boxes, OTA, co-channel).

Question: as box B's TX power rises, what happens to box A's own-link FCS_OK
rate?  Far geometry (1m box distance, each box's antennas near its own RX) —
P178 live discrimination (2026-09-09) found box-to-box interference negligible
at 4 coarse points; this script produces the systematic fine-grained CURVE.

Roles
  Victim A (192.168.10.2): cross-board link A:0 TX -> B:0 RX2, MCS0 len38
    (PSDU 66), FIXED operating point (tx-gain 31.5, tx-scale 1.0).
  Interferer B (192.168.20.3): same cross-board link, MCS0 len61 (PSDU 89 —
    length attribution, P178 method), tx-gain SWEPT.  B runs the standard
    instrumented harness (clean frames — P178's tx_only_interferer.py dirty-
    frame confound excluded); B's own output is logged, not parsed.

Sweep order = palindrome OFF,0,8,16,24,31.5,31.5,24,16,8,0,OFF (OTA drift
within one batch measured 23-55pp on 2026-09-09 — palindrome symmetry makes
drift visible as asc/desc arm hysteresis; first vs last OFF = drift control,
flag if |delta| > 5pp, mcs_sweep.py precedent).

Per point: A runs 3x15s windows (est_sent~150/window).  ON points: B starts
25s before A and transmits throughout.  OFF points: A alone.

PRIMARY ENDPOINT (only): A own-frame FCS_OK / est_sent per power point, from
A's [RESULT] window lines.  len89 DECODE_SUCCESS lines in A's stderr are
counted as a CONTAMINATION FLAG (foreign frames inflating A's FCS_OK counter)
— reported as a column, not an endpoint.

This is a CHARACTERIZATION measurement, not an A/B verdict.  Pure
orchestration — no C++/harness changes, loopback gate N/A (mcs_sweep.py
precedent).

Usage:
    python3 p179_cosr_power_sweep.py [--tag name] [--windows 3] [--run 15]
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

RESULT_RE = re.compile(
    r'\[RESULT\] scale=\S+ rx_gain=\S+ cap=\S+ window=(\S+)s est_sent~(\d+) '
    r'PDU=(\d+) FCS_OK=(\d+) FCS_FAIL=(\d+)')
# Foreign-frame contamination: B's PSDU is 89 (len61+28); A's own is 66.
DECODE_OK_RE = re.compile(r'\[DECODE_SUCCESS\].*?len=(\d+)')


def uhd_nudge(addr):
    try:
        subprocess.run(['uhd_usrp_probe', '--args', f'addr={addr}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
    except Exception:
        pass


def base_env(env_extra=None):
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    env['LD_PRELOAD'] = str(REPO / 'wrap_rpc2.so')
    env['PYTHONPATH'] = f'{REPO}/build/python/bindings:{REPO}/python:{REPO}/examples'
    if env_extra:
        env.update(env_extra)
    return env


def parse_env(spec):
    """'A=1,B=2' -> {'A':'1','B':'2'}; empty -> {}."""
    out = {}
    for kv in (spec or '').split(','):
        k, sep, v = kv.partition('=')
        if sep:
            out[k.strip()] = v.strip()
    return out


def harness_cmd(a, addr, tx_gain, length, run_s, windows):
    return [PY, str(HARNESS),
            '--mcs', '0',
            '--freq', str(a.freq), '--tx-addr', addr, '--rx-addr', addr,
            '--rate', '20',
            '--tx-gain', str(tx_gain), '--tx-scale', str(a.tx_scale),
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.interval), '--len', str(length),
            '--warmup', str(a.warmup), '--run', str(run_s),
            '--scales', ','.join([str(a.rx_scale)] * windows)]


def start_interferer(a, gain, outdir, tag):
    """Launch B on box2; it transmits from flowgraph start (warmup only gates
    B's own measurement, which we don't use).  One long window; we kill it
    after A finishes.  Returns (proc, logfile path)."""
    cmd = harness_cmd(a, a.b_addr, gain, a.b_len, 240, 1)
    logf = open(outdir / f'{tag}_interferer.out', 'w')
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=logf,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True,
                            env=base_env(parse_env(a.b_env)))
    return proc, logf


def run_victim(a, gain, idx, arm, outdir):
    """One A run (3 windows).  Returns (windows list, len89_ok, attempts,
    infra_ok).  gain is 'OFF' or a float — used only for tagging."""
    tag = f'idx{idx:02d}_g{gain}_{arm}'
    cmd = harness_cmd(a, a.a_addr, a.a_tx_gain, a.len, a.run, a.windows)
    timeout = a.warmup + a.run * a.windows + 150  # USRP init margin

    for attempt in range(1, 3):
        proc = subprocess.Popen(cmd, cwd=str(REPO),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True,
                                env=base_env(parse_env(a.a_env)))
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
        len89_ok = sum(1 for m in DECODE_OK_RE.finditer(err or '')
                       if m.group(1) == '89')
        retryable = timed_out or (proc.returncode != 0 and uhd_init_fail) \
            or len(windows) < a.windows
        if retryable and attempt < 2:
            reason = ('HANG timeout' if timed_out else
                      'UHD init failed' if uhd_init_fail else
                      f'only {len(windows)}/{a.windows} windows')
            print(f'[SWEEP] {tag} attempt {attempt}: {reason}; '
                  f'probe + retry...', flush=True)
            uhd_nudge(a.a_addr)
            time.sleep(5)
            continue
        return windows, len89_ok, attempt, not retryable
    return [], 0, 2, False


def rate_of(windows):
    ok = sum(int(w[3]) for w in windows)
    est = sum(int(w[1]) for w in windows)
    return (ok / est * 100) if est else 0.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--a-addr', default='192.168.10.2', help='victim box')
    p.add_argument('--b-addr', default='192.168.20.3', help='interferer box')
    p.add_argument('--a-tx-gain', type=float, default=31.5,
                   help='victim FIXED tx gain')
    p.add_argument('--tx-scale', type=float, default=1.0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0', help='RX daughterboard')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38, help='A payload (PSDU 66)')
    p.add_argument('--b-len', type=int, default=61, help='B payload (PSDU 89)')
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=3)
    p.add_argument('--points', default='OFF,0,8,16,24,31.5,31.5,24,16,8,0,OFF',
                   help='palindrome sweep of B tx-gain (dB); OFF = B silent')
    p.add_argument('--b-start-grace', type=float, default=25,
                   help='seconds B transmits before A starts (ON points)')
    p.add_argument('--a-env', default='',
                   help='victim env overrides, e.g. HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1')
    p.add_argument('--b-env', default='', help='interferer env overrides')
    p.add_argument('--tag', default='')
    args = p.parse_args()

    points = [tok if tok == 'OFF' else float(tok)
              for tok in args.points.split(',')]
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{args.tag}' if args.tag else ''
    outdir = CAP_ROOT / f'p179_cosr_power_sweep_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    est_per_win = int(round(args.run * 1000.0 / args.interval))
    print('=== P179 Co-SR INTERFERER POWER SWEEP (characterization) ===',
          flush=True)
    print(f'[SWEEP] points={points}  windows={args.windows}x{args.run}s '
          f'(est_sent~{est_per_win}/window)', flush=True)
    print(f'[SWEEP] A(victim)={args.a_addr} MCS0 len{args.len} '
          f'tx-gain {args.a_tx_gain} FIXED | B(interferer)={args.b_addr} '
          f'MCS0 len{args.b_len} tx-gain swept', flush=True)
    print(f'[SWEEP] freq={args.freq} rx-gain={args.rx_gain} '
          f'rx-scale={args.rx_scale} interval={args.interval}ms', flush=True)
    print(f'[SWEEP] A env: {args.a_env or "(harness defaults)"} | '
          f'B env: {args.b_env or "(harness defaults)"}', flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[SWEEP] governor={gov.read_text().strip() if gov.exists() else "?"}'
          f'  logs -> {outdir}', flush=True)

    results = []  # (idx, gain, arm, windows, len89_ok, attempts, infra_ok)
    for idx, gain in enumerate(points):
        arm = 'asc' if idx < len(points) / 2 else 'desc'
        print(f'[SWEEP] --- idx {idx}: B tx-gain={gain} ({arm}) ---',
              flush=True)
        b_proc = b_log = None
        if gain != 'OFF':
            b_proc, b_log = start_interferer(args, gain, outdir, f'idx{idx:02d}')
            time.sleep(args.b_start_grace)
            if b_proc.poll() is not None:
                print(f'[SWEEP] WARNING: interferer exited during grace '
                      f'(rc={b_proc.returncode}); nudge + continue, point '
                      f'may be invalid', flush=True)
                uhd_nudge(args.b_addr)
        windows, len89_ok, attempts, infra_ok = run_victim(
            args, gain, idx, arm, outdir)
        if b_proc is not None:
            b_died_early = b_proc.poll() is not None
            if not b_died_early:
                os.killpg(b_proc.pid, signal.SIGKILL)
            b_proc.wait()
            b_log.close()
            if b_died_early:
                print(f'[SWEEP] WARNING: interferer died mid-point; '
                      f'nudging B box', flush=True)
                uhd_nudge(args.b_addr)
            time.sleep(5)
        results.append((idx, gain, arm, windows, len89_ok, attempts,
                        infra_ok))
        ok_sum = sum(int(w[3]) for w in windows)
        est_sum = sum(int(w[1]) for w in windows)
        rate = (ok_sum / est_sum * 100) if est_sum else 0.0
        print(f'[SWEEP] gain={gain} ({arm}): A FCS_OK={ok_sum}/{est_sum} = '
              f'{rate:.1f}%  len89_contam={len89_ok}  (attempts={attempts}, '
              f'infra_ok={infra_ok})', flush=True)
        time.sleep(3)

    # ---- summary ----
    print('\n========== P179 SWEEP SUMMARY ==========', flush=True)
    print('idx  B_gain  arm    A_FCS_OK/est  rate%  len89_contam  attempts',
          flush=True)
    for idx, gain, arm, windows, len89_ok, attempts, infra_ok in results:
        ok_sum = sum(int(w[3]) for w in windows)
        est_sum = sum(int(w[1]) for w in windows)
        rate = (ok_sum / est_sum * 100) if est_sum else 0.0
        g = f'{gain}' if gain == 'OFF' else f'{gain:g}'
        print(f'{idx:3d}  {g:>6s}  {arm:4s}  {ok_sum:6d}/{est_sum:<6d}  '
              f'{rate:6.2f}  {len89_ok:5d}  {attempts}', flush=True)

    # Drift control: first vs last OFF point.
    offs = [(idx, rate_of(w)) for idx, gain, _, w, _, _, _ in results
            if gain == 'OFF' and w]
    if len(offs) >= 2:
        drift = abs(offs[0][1] - offs[-1][1])
        flag = 'DRIFT SUSPECT' if drift > 5.0 else 'drift ok'
        print(f'[SWEEP] OFF repeat control: first={offs[0][1]:.2f}% '
              f'last={offs[-1][1]:.2f}%  |delta|={drift:.2f}pp -> {flag}',
              flush=True)

    # ---- curve plot ----
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        levels = []  # x tick labels in order of first appearance
        for _, gain, _, _, _, _, _ in results:
            lbl = gain if gain == 'OFF' else f'{gain:g}'
            if lbl not in levels:
                levels.append(lbl)
        xpos = {lbl: i for i, lbl in enumerate(levels)}
        fig, ax = plt.subplots(figsize=(8, 5))
        for arm, marker, color in (('asc', 'o', 'tab:blue'),
                                   ('desc', 's', 'tab:orange')):
            xs, ys, yw = [], [], []
            for _, gain, a2, windows, _, _, _ in results:
                if a2 != arm or not windows:
                    continue
                lbl = gain if gain == 'OFF' else f'{gain:g}'
                xs.append(xpos[lbl])
                ys.append(rate_of(windows))
                yw.append([int(w[3]) / int(w[1]) * 100 for w in windows
                           if int(w[1]) > 0])
            for x, ws in zip(xs, yw):
                ax.scatter([x] * len(ws), ws, color=color, alpha=0.3, s=15)
            ax.plot(xs, ys, marker=marker, color=color,
                    label=f'B power {arm} arm', linewidth=1.5)
        if offs:
            ax.axhline(sum(r for _, r in offs) / len(offs), color='gray',
                       linestyle='--', alpha=0.6, label='OFF baseline mean')
        ax.set_xticks(range(len(levels)))
        ax.set_xticklabels(levels)
        ax.set_xlabel('Box B (interferer) TX gain (dB)  [OFF = silent]')
        ax.set_ylabel('Box A victim FCS_OK / est_sent (%)')
        ax.set_ylim(0, 105)
        ax.grid(alpha=0.3)
        ax.legend()
        ax.set_title(f'P179 Co-SR power sweep — A={args.a_addr} victim MCS0 '
                     f'fixed, B={args.b_addr} swept, {args.freq:g} MHz, 1m OTA\n'
                     f'{ts}')
        png = outdir / 'p179_curve.png'
        fig.tight_layout()
        fig.savefig(png, dpi=150)
        print(f'[SWEEP] curve: {png}', flush=True)
    except Exception as e:
        print(f'[SWEEP] plot failed (data above still valid): {e}',
              flush=True)
    print(f'[SWEEP] logs: {outdir}', flush=True)


if __name__ == '__main__':
    main()
