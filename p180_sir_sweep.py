#!/usr/bin/env python3
"""
p180_sir_sweep.py — Co-SR SIR-axis sweep at near geometry (<10 cm).

P179 swept the INTERFERER's power with the victim pinned at max and found no
degradation — but SIR, not absolute interferer power, is what governs the
capture break.  This script drives SIR from the victim side: each victim TX
gain level is measured TWICE, back to back —

    arm "silent":  interferer silent
    arm "max":     interferer at --b-gain-max (default 31.5 dB)

The per-level difference (max - silent) is the pure interference effect, free
of the victim's own SNR change (lowering the victim's gain also lowers its own
link SNR — both arms share that, so it cancels in the difference).

Victim gain levels run as a palindrome (asc then desc) and the arm ORDER flips
between the two passes (asc: silent->max, desc: max->silent), so any
order/thermal bias shows up as an asc-vs-desc discrepancy.

Geometry: both boxes self-contained (each box's A:0 TX -> its own B:0 RX2),
co-channel 5890 MHz, co-located (<10 cm).

PRIMARY ENDPOINT: per-level difference of victim own-frame FCS_OK/est_sent
(max arm minus silent arm), from the victim's [RESULT] window lines.

This is a CHARACTERIZATION measurement, not an A/B verdict.  Pure
orchestration — no C++/harness changes.

Usage:
    python3 p180_sir_sweep.py --a-gains 31.5,16,8,0,0,8,16,31.5
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
DECODE_OK_RE = re.compile(r'\[DECODE_SUCCESS\].*?len=(\d+)')


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
    cmd = harness_cmd(a, a.b_addr, gain, a.b_len, 240, 1)
    logf = open(outdir / f'{tag}_interferer.out', 'w')
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=logf,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True,
                            env=base_env(parse_env(a.b_env)))
    return proc, logf


def run_victim(a, a_gain, idx, arm, b_on, outdir):
    """One victim run.  a_gain = victim TX gain; b_on = interferer running.
    Returns (windows, len89_ok, attempts, infra_ok)."""
    tag = f'idx{idx:02d}_a{a_gain:g}_{arm}_b{"on" if b_on else "off"}'
    cmd = harness_cmd(a, a.a_addr, a_gain, a.len, a.run, a.windows)
    timeout = a.warmup + a.run * a.windows + 150

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
            print(f'[SIR] {tag} attempt {attempt}: {reason}; probe + retry...',
                  flush=True)
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
    p.add_argument('--b-gain-max', type=float, default=31.5,
                   help='interferer TX gain for the "max" arm')
    p.add_argument('--tx-scale', type=float, default=1.0)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--len', type=int, default=38, help='A payload (PSDU 66)')
    p.add_argument('--b-len', type=int, default=61, help='B payload (PSDU 89)')
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=5)
    p.add_argument('--a-gains', default='31.5,16,8,0,0,8,16,31.5',
                   help='victim TX gain levels, palindrome')
    p.add_argument('--b-start-grace', type=float, default=25)
    p.add_argument('--a-env', default='')
    p.add_argument('--b-env', default='')
    p.add_argument('--tag', default='')
    args = p.parse_args()

    a_gains = [float(x) for x in args.a_gains.split(',')]
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{args.tag}' if args.tag else ''
    outdir = CAP_ROOT / f'p180_sir_sweep_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)

    est_per_win = int(round(args.run * 1000.0 / args.interval))
    print('=== P180 Co-SR SIR SWEEP (victim-gain axis, <10cm) ===', flush=True)
    print(f'[SIR] A(victim)={args.a_addr} MCS0 len{args.len} gains={a_gains} | '
          f'B(interferer)={args.b_addr} MCS0 len{args.b_len} '
          f'silent vs {args.b_gain_max:g}dB', flush=True)
    print(f'[SIR] windows={args.windows}x{args.run}s (est_sent~{est_per_win}/win)'
          f'  freq={args.freq} rx-gain={args.rx_gain}', flush=True)
    print(f'[SIR] A env: {args.a_env or "(defaults)"} | '
          f'B env: {args.b_env or "(defaults)"}', flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[SIR] governor={gov.read_text().strip() if gov.exists() else "?"}  '
          f'logs -> {outdir}', flush=True)

    results = []  # (idx, a_gain, arm, b_on, windows, len89_ok, attempts, infra_ok)
    half = len(a_gains) / 2
    for idx, a_gain in enumerate(a_gains):
        pass_name = 'asc' if idx < half else 'desc'
        # Arm order flips between passes: asc silent->max, desc max->silent.
        order = [False, True] if pass_name == 'asc' else [True, False]
        for b_on in order:
            arm = 'max' if b_on else 'silent'
            print(f'[SIR] --- idx {idx}: A_gain={a_gain:g} ({pass_name}) '
                  f'arm={arm} ---', flush=True)
            b_proc = b_log = None
            if b_on:
                b_proc, b_log = start_interferer(
                    args, args.b_gain_max, outdir, f'idx{idx:02d}')
                time.sleep(args.b_start_grace)
                if b_proc.poll() is not None:
                    print(f'[SIR] WARNING: interferer exited during grace '
                          f'(rc={b_proc.returncode}); point may be invalid',
                          flush=True)
                    uhd_nudge(args.b_addr)
            windows, len89_ok, attempts, infra_ok = run_victim(
                args, a_gain, idx, arm, b_on, outdir)
            if b_proc is not None:
                died = b_proc.poll() is not None
                if not died:
                    os.killpg(b_proc.pid, signal.SIGKILL)
                b_proc.wait()
                b_log.close()
                if died:
                    print('[SIR] WARNING: interferer died mid-point',
                          flush=True)
                    uhd_nudge(args.b_addr)
                time.sleep(5)
            results.append((idx, a_gain, pass_name, arm, b_on, windows,
                            len89_ok, attempts, infra_ok))
            ok_sum = sum(int(w[3]) for w in windows)
            est_sum = sum(int(w[1]) for w in windows)
            rate = (ok_sum / est_sum * 100) if est_sum else 0.0
            print(f'[SIR] A_gain={a_gain:g} {pass_name} {arm}: '
                  f'FCS_OK={ok_sum}/{est_sum} = {rate:.1f}%  '
                  f'len89_contam={len89_ok}  (attempts={attempts}, '
                  f'infra_ok={infra_ok})', flush=True)
            time.sleep(3)

    # ---- summary: per (level, pass) the silent vs max pair ----
    print('\n========== P180 SIR SWEEP SUMMARY ==========', flush=True)
    print('idx  A_gain  pass  silent%  max%   diff(max-silent)  contam',
          flush=True)
    pairs = {}
    for idx, a_gain, pname, arm, b_on, windows, c89, attempts, infra in results:
        pairs.setdefault((idx, a_gain, pname), {})[arm] = (rate_of(windows), c89)
    for (idx, a_gain, pname), arms in sorted(pairs.items()):
        s = arms.get('silent', (float('nan'), 0))
        m = arms.get('max', (float('nan'), 0))
        d = m[0] - s[0]
        print(f'{idx:3d}  {a_gain:6g}  {pname:4s}  {s[0]:6.2f}   {m[0]:6.2f}  '
              f'{d:+8.2f}pp        {s[1]+m[1]}', flush=True)

    # ---- plot: victim rate vs victim gain, plus interference delta ----
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
        for pname, marker in (('asc', 'o'), ('desc', 's')):
            xs_s, ys_s, xs_m, ys_m = [], [], [], []
            for (idx, a_gain, pn), arms in sorted(pairs.items()):
                if pn != pname:
                    continue
                if 'silent' in arms:
                    xs_s.append(a_gain); ys_s.append(arms['silent'][0])
                if 'max' in arms:
                    xs_m.append(a_gain); ys_m.append(arms['max'][0])
            ax1.plot(xs_s, ys_s, marker=marker, linestyle='-', color='tab:blue',
                     alpha=0.6 if pname == 'desc' else 1.0,
                     label=f'B silent ({pname})')
            ax1.plot(xs_m, ys_m, marker=marker, linestyle='--', color='tab:red',
                     alpha=0.6 if pname == 'desc' else 1.0,
                     label=f'B max {args.b_gain_max:g}dB ({pname})')
        ax1.set_xlabel('Victim (box A) TX gain (dB)')
        ax1.set_ylabel('Victim FCS_OK / est_sent (%)')
        ax1.invert_xaxis()
        ax1.set_ylim(0, 105)
        ax1.grid(alpha=0.3)
        ax1.legend(fontsize=8)
        ax1.set_title('Victim link: interferer silent vs max')

        for pname, marker, color in (('asc', 'o', 'tab:purple'),
                                     ('desc', 's', 'tab:brown')):
            xs, ds = [], []
            for (idx, a_gain, pn), arms in sorted(pairs.items()):
                if pn != pname or 'silent' not in arms or 'max' not in arms:
                    continue
                xs.append(a_gain)
                ds.append(arms['max'][0] - arms['silent'][0])
            ax2.plot(xs, ds, marker=marker, color=color, label=pname)
        ax2.axhline(0, color='gray', linestyle='--', alpha=0.7)
        ax2.set_xlabel('Victim (box A) TX gain (dB)')
        ax2.set_ylabel('Interference effect (max - silent, pp)')
        ax2.invert_xaxis()
        ax2.grid(alpha=0.3)
        ax2.legend(fontsize=8)
        ax2.set_title('Pure interference effect (SIR axis)')
        fig.suptitle(f'P180 Co-SR SIR sweep — near geometry (<10cm), '
                     f'{args.freq:g} MHz, {ts}')
        fig.tight_layout()
        png = outdir / 'p180_sir_curve.png'
        fig.savefig(png, dpi=150)
        print(f'[SIR] curve: {png}', flush=True)
    except Exception as e:
        print(f'[SIR] plot failed (data above still valid): {e}', flush=True)
    print(f'[SIR] logs: {outdir}', flush=True)


if __name__ == '__main__':
    main()
