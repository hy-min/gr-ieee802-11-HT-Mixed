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
import shutil
import signal
import subprocess
import time
from pathlib import Path

# Each arm writes the harness's stderr to disk (50-300 MB: the splitter storm
# logs ~2.6 M lines per interfered arm).  Running out mid-batch does not just
# stop the batch -- it kills it inside run_victim's write_text(), losing the
# arm in flight (P189c: ENOSPC took out both A=31.5 arms).  Refuse to start and
# abort cleanly instead.
MIN_FREE_GB = 3.0


def free_gb(path):
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return float('inf')

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


def harness_cmd(a, addr, tx_gain, length, run_s, windows, interval=None):
    return [PY, str(HARNESS),
            '--mcs', '0',
            '--freq', str(a.freq), '--tx-addr', addr, '--rx-addr', addr,
            '--rate', '20',
            '--tx-gain', str(tx_gain), '--tx-scale', str(a.tx_scale),
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.interval if interval is None else interval),
            '--len', str(length),
            '--warmup', str(a.warmup), '--run', str(run_s),
            '--scales', ','.join([str(a.rx_scale)] * windows)]


def interferer_lifetime_s(logpath, interval_ms):
    """Interferer's on-air time, inferred from its TX log.

    The harness prints `TX_LSIG_Original` twice per generated frame (verified
    empirically: a ~125 s arm yields ~25 000 lines at a 10 ms strobe), so
    lifetime = lines / 2 * interval.

    This exists because a SHORT-LIVED interferer silently fakes a "no effect"
    reading: the victim simply spends part of its window with no interferer
    present and averages back toward its silent baseline.  That fooled P189
    twice (A=18 read +0.1 pp with B up only 55 s of a 125 s arm).
    """
    try:
        n = sum(1 for _ in open(logpath, errors='replace')
                if 'TX_LSIG_Original' in _)
    except OSError:
        return 0.0
    return (n / 2.0) * interval_ms / 1000.0


def wait_interferer_started(logpath, proc, timeout_s=120):
    """Block until the interferer's own log shows its first transmitted frame.

    Replaces the old fixed `time.sleep(b_start_grace)`, which was an ARBITRARY
    TIMEOUT standing in for a precondition.  If UHD init ran long the victim's
    measurement window opened before the interferer was on air, and the arm
    quietly measured a partly interferer-free window: P191 idx1 gave
    b_life=66 s against a ~120 s window with died=False -- the interferer was
    simply LATE, not dead, and the 240->900 s run fix could not catch that.

    Polling the log makes "the interferer is transmitting" a VERIFIED
    precondition.  The child's stdout is block-buffered but its log grows at
    ~4.5 kB per frame, so a fresh TX line surfaces well under a second.
    Returns (ok, reason).
    """
    deadline = time.time() + timeout_s
    pos = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, 'exited before first frame'
        try:
            with open(logpath, errors='replace') as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
            if 'TX_LSIG_Original' in chunk:
                return True, 'ok'
        except OSError:
            pass
        time.sleep(0.5)
    return False, f'no TX within {timeout_s}s'


def start_interferer(a, gain, outdir, tag):
    # run_s is deliberately far longer than any victim arm so the interferer
    # can only ever leave by being killed by this script -- never by reaching
    # its own natural end INSIDE the victim's measurement window (P189: a
    # 240 s run meant a retried victim arm outlived its interferer).
    cmd = harness_cmd(a, a.b_addr, gain, a.b_len, 900, 1,
                      interval=a.b_interval)
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
        # [decode_mac][CAPTURE] is logged once per symbol while a frame is being
        # assembled, so its count measures how much frame material actually
        # reached the decoder.  Calibrated over every SIR-sweep arm to date:
        #   silent arms       12,980 -  20,636   (victim's own frames only)
        #   normal  max arms 890,342 - 1,033,315
        #   known-bad arms        49,165 (interferer up only 55 s of 125 s)
        #                       318,205 (P188 idx00 -- the arm that read -60 pp)
        # 5x separation with no overlap; unlike b_life this needs no interferer
        # log, so it works off the victim side alone.
        cap_n = (out or '').count('decode_mac][CAPTURE]')
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
        return windows, len89_ok, attempt, not retryable, cap_n
    return [], 0, 2, False, 0


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
    p.add_argument('--b-interval', type=float, default=None,
                   help='B strobe interval (ms) -- shortens B\'s frame period '
                        'to raise its DUTY CYCLE so its frames actually overlap '
                        'the victim\'s. len1000 + 10ms -> 1.30ms frame -> 13%%, '
                        'which combined with the A-gain sweep puts SIR on the '
                        'P178-predicted +3 dB capture threshold')
    p.add_argument('--min-capture', type=int, default=700000,
                   help='max arms whose [decode_mac][CAPTURE] count falls below '
                        'this are INVALID.  CALIBRATED FOR THIS B CONFIG ONLY '
                        '(len1000 / 10 ms): normal 840k-1.03M, A-degraded '
                        '115k/318k/547k, B-absent 20k/49k.  The count scales '
                        'with B frame length x rate -- the 2026-09-14 neargeo '
                        'batch used a different config and ran 38k-47k on arms '
                        'that were perfectly GOOD, so RE-CALIBRATE whenever '
                        '--b-len/--b-interval change or every arm reads low.')
    p.add_argument('--max-silent-capture', type=int, default=60000,
                   help='silent arms above this are INVALID: something was '
                        'transmitting during the "silent" arm (normal 13k-21k)')
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

    if free_gb(CAP_ROOT) < MIN_FREE_GB:
        print(f'[SIR] ABORT: only {free_gb(CAP_ROOT):.1f} GB free on {CAP_ROOT} '
              f'(need >= {MIN_FREE_GB} GB; an interfered arm costs up to ~0.3 GB)',
              flush=True)
        return

    # (idx, a_gain, pass, arm, b_on, windows, len89_ok, attempts,
    #  infra_ok, valid, b_life)
    results = []
    aborted = False
    half = len(a_gains) / 2
    for idx, a_gain in enumerate(a_gains):
        pass_name = 'asc' if idx < half else 'desc'
        # Arm order flips between passes: asc silent->max, desc max->silent.
        order = [False, True] if pass_name == 'asc' else [True, False]
        for b_on in order:
            arm = 'max' if b_on else 'silent'
            if free_gb(CAP_ROOT) < MIN_FREE_GB:
                print(f'[SIR] ABORT before idx {idx} {arm}: only '
                      f'{free_gb(CAP_ROOT):.1f} GB free --- stopping NOW '
                      f'rather than dying inside a write and losing the arm',
                      flush=True)
                aborted = True
                break
            print(f'[SIR] --- idx {idx}: A_gain={a_gain:g} ({pass_name}) '
                  f'arm={arm} ---', flush=True)
            b_proc = b_log = None
            if b_on:
                b_proc, b_log = start_interferer(
                    args, args.b_gain_max, outdir, f'idx{idx:02d}')
                started, why = wait_interferer_started(
                    outdir / f'idx{idx:02d}_interferer.out', b_proc)
                if not started:
                    print(f'[SIR] WARNING: interferer did not start '
                          f'({why}); point may be invalid', flush=True)
                    uhd_nudge(args.b_addr)
                else:
                    # short settle after the first frame, in place of the old
                    # blanket grace sleep
                    time.sleep(5)
            windows, len89_ok, attempts, infra_ok, cap_n = run_victim(
                args, a_gain, idx, arm, b_on, outdir)
            wv = [int(w[3]) for w in windows]
            # Within-arm trend: late windows vs early ones.  Calibrated over
            # every arm to date: healthy 0.84-1.10, SELF-COLLAPSING <0.35
            # (P191: 0.03/0.15/0.32 at A=31.5, with B absent in the silent
            # ones -- the victim's own link dies mid-measurement).  Reported,
            # not gated on its own: it is two-sided (A=6 max reads 2.30, a
            # genuine recovery).
            trend = (sum(wv[-2:]) / sum(wv[:2])
                     if len(wv) == 5 and sum(wv[:2]) else float('nan'))
            # Within-arm COLLAPSE: the link starts healthy and dies mid-arm.
            # The `wv[0] >= 80` guard is load-bearing -- "uniformly dead" also
            # trends to zero, and A=0's max arms ([1,0,0,0,0]) are the most
            # reproducible measurement in the project; a bare trend gate would
            # throw them away.  Calibrated over 67 arms: fires on 5, every one
            # an A=31.5 collapse; the lowest healthy first-window drop is 0.63,
            # so the 0.4 factor leaves margin both ways.
            collapse = (len(wv) == 5 and wv[0] >= 80 and
                        wv[-1] < 0.4 * wv[0])
            b_life = None
            b_died = False
            if b_proc is not None:
                b_died = b_proc.poll() is not None
                if not b_died:
                    os.killpg(b_proc.pid, signal.SIGKILL)
                b_proc.wait()
                b_log.close()
                b_life = interferer_lifetime_s(
                    outdir / f'idx{idx:02d}_interferer.out', args.b_interval)
                if b_died:
                    print('[SIR] WARNING: interferer died mid-point',
                          flush=True)
                    uhd_nudge(args.b_addr)
                time.sleep(5)
            # An arm is only usable if the victim itself ran AND the interferer
            # was on air across the whole victim measurement window.  Without
            # the second condition a short-lived interferer masquerades as
            # "no interference effect" (the victim averages back to its silent
            # baseline).  Invalidate rather than report a fabricated number.
            # The interferer is now started-and-VERIFIED before the victim runs,
            # so what it must cover is the victim's whole measurement window
            # (warmup + all windows) -- the old formula folded in the grace
            # sleep, which no longer exists.
            need_s = 0.9 * (args.warmup + args.run * args.windows)
            cap_ok = (cap_n >= args.min_capture if b_on
                      else cap_n <= args.max_silent_capture)
            valid = (infra_ok and len(windows) >= args.windows and cap_ok and
                     not collapse and
                     (not b_on or (not b_died and b_life >= need_s)))
            results.append((idx, a_gain, pass_name, arm, b_on, windows,
                            len89_ok, attempts, infra_ok, valid, b_life,
                            cap_n))
            ok_sum = sum(int(w[3]) for w in windows)
            est_sum = sum(int(w[1]) for w in windows)
            rate = (ok_sum / est_sum * 100) if est_sum else 0.0
            win_list = ' '.join(str(int(w[3])) for w in windows)
            bstr = 'n/a' if b_life is None else f'{b_life:.0f}s(died={b_died})'
            if not valid:
                print(f'[SIR] A_gain={a_gain:g} {pass_name} {arm}: '
                      f'INVALID --- attempts={attempts} infra_ok={infra_ok} '
                      f'b_life={bstr} need>={need_s:.0f}s capture={cap_n} '
                      f'collapse={collapse}  '
                      f'(raw FCS_OK={ok_sum}/{est_sum}, DISCARDED)  '
                      f'win=[{win_list}]', flush=True)
            else:
                print(f'[SIR] A_gain={a_gain:g} {pass_name} {arm}: '
                      f'FCS_OK={ok_sum}/{est_sum} = {rate:.1f}%  '
                      f'len89_contam={len89_ok}  (attempts={attempts}, '
                      f'infra_ok={infra_ok}, b_life={bstr}, '
                      f'capture={cap_n}, trend={trend:.2f})  '
                      f'win=[{win_list}]', flush=True)
            time.sleep(3)
        if aborted:
            break

    # ---- summary: per (level, pass) the silent vs max pair ----
    print('\n========== P180 SIR SWEEP SUMMARY ==========', flush=True)
    print('idx  A_gain  pass  silent%  max%   diff(max-silent)  contam',
          flush=True)
    pairs = {}
    for (i, a_gain, pname, arm, b_on, windows, c89,
         attempts, infra, valid, b_life, cap_n) in results:
        pairs.setdefault((i, a_gain, pname), {})[arm] = (
            rate_of(windows), c89, valid)
    n_invalid = sum(1 for r in results if not r[9])
    for (idx, a_gain, pname), arms in sorted(pairs.items()):
        s = arms.get('silent')
        m = arms.get('max')
        s_txt = 'INVAL' if (s is None or not s[2]) else f'{s[0]:6.2f}'
        m_txt = 'INVAL' if (m is None or not m[2]) else f'{m[0]:6.2f}'
        if s and m and s[2] and m[2]:
            d_txt, contam = f'{m[0] - s[0]:+8.2f}pp', s[1] + m[1]
        else:
            d_txt, contam = '--', 0
        print(f'{idx:3d}  {a_gain:6g}  {pname:4s}  {s_txt:>6s}   {m_txt:>6s}  '
              f'{d_txt:>11s}        {contam}', flush=True)
    print(f'invalid arms: {n_invalid} / {len(results)} '
          f'(dropped, never reported as a number)', flush=True)

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
                sv, mv = arms.get('silent'), arms.get('max')
                if sv and sv[2]:
                    xs_s.append(a_gain); ys_s.append(sv[0])
                if mv and mv[2]:
                    xs_m.append(a_gain); ys_m.append(mv[0])
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
                if pn != pname:
                    continue
                sv, mv = arms.get('silent'), arms.get('max')
                if not (sv and mv and sv[2] and mv[2]):
                    continue
                xs.append(a_gain)
                ds.append(mv[0] - sv[0])
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
