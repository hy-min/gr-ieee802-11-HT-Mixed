#!/usr/bin/env python3
"""
p182_ota_noise_sweep.py — Co-SR: interferer transmits CONTINUOUS NOISE
(zero harness code change; box B replays a noise waveform via --tx-file).

Why this experiment exists
--------------------------
P179/P179b/P180 swept the interferer's power with box B transmitting *frames*
and found no effect at any power.  The reason is now believed to be the DUTY
CYCLE: an MCS0 frame is ~120 us long on a 100 ms strobe (0.12 % duty), so two
independently-strobed links collide for only ~2*120us/100ms = 0.24 % of the
victim's frames.  The interferer essentially never overlaps a victim frame,
so it never gets the chance to act -- and P178's offline study only saw
capture effects because it mixed frames at tau=0 (exact simultaneity) by
construction.

Replacing box B's frames with CONTINUOUS in-band noise keeps the same RF
chain, coupling and frequency, but raises the interference duty cycle from
0.12 % to 100 % (~29 dB more interference energy acting on every victim
frame).  This is the first configuration in this project in which the
"two systems transmitting at the same time" condition of Co-SR is actually
realised on air.

Arms
----
Interleaved A/B/A (and more, via --arms): victim-only arms bracket the
noise arm so device/channel drift is visible (P158: unpaired sequential
comparison is the classic trap -- this script never does one).

Victim (A) runs its normal own-link measurement; box B only transmits.
B's own RX output is irrelevant and is discarded to a file.

Usage
-----
  # quick go/no-go probe (3 arms, ~7 min)
  python3 p182_ota_noise_sweep.py --probe

  # full amplitude sweep (palindrome)
  python3 p182_ota_noise_sweep.py --levels 0.01,0.032,0.1,0.32
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
NOISE_DIR = CAP_ROOT / 'p182_noise'

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


def victim_cmd(a, windows):
    return [PY, str(HARNESS),
            '--mcs', str(a.mcs),
            '--freq', str(a.freq), '--tx-addr', a.a_addr, '--rx-addr', a.a_addr,
            '--rate', '20',
            '--tx-gain', str(a.a_tx_gain), '--tx-scale', str(a.a_tx_scale),
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.interval), '--len', str(a.len),
            '--warmup', str(a.warmup), '--run', str(a.run),
            '--scales', ','.join([str(a.rx_scale)] * windows)]


def noise_cmd(a, wavefile):
    """Box B: replay the noise waveform straight into the USRP sink."""
    return [PY, str(HARNESS),
            '--mcs', '0', '--len', str(a.b_len),
            '--freq', str(a.freq),
            '--tx-addr', a.b_addr, '--rx-addr', a.b_addr,
            '--rate', '20',
            '--tx-gain', str(a.b_tx_gain), '--tx-scale', '1.0',
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.interval), '--warmup', '5', '--run', '600',
            '--scales', str(a.rx_scale),
            '--tx-file', str(wavefile)]


def frame_interferer_cmd(a):
    """Box B as a REAL 802.11 interferer: normal frame TX, but a long payload
    and a fast strobe so its frames occupy the channel a large fraction of the
    time.  This is the configuration Co-SR actually faces -- a competing FRAME
    that can capture the victim's receiver -- as opposed to noise, which can
    only raise the noise floor."""
    return [PY, str(HARNESS),
            '--mcs', '0', '--len', str(a.b_len),
            '--freq', str(a.freq),
            '--tx-addr', a.b_addr, '--rx-addr', a.b_addr,
            '--rate', '20',
            '--tx-gain', str(a.b_tx_gain), '--tx-scale', '1.0',
            '--rx-gain', str(a.rx_gain), '--rx-scale', str(a.rx_scale),
            '--rx-subdev', a.rx_subdev,
            '--interval', str(a.b_interval), '--warmup', '5', '--run', '600',
            '--scales', str(a.rx_scale)]


def run_victim(a, tag, outdir):
    """One victim arm. Returns (windows, len89, attempts, infra_ok).

    stderr goes straight to a FILE, never a pipe.  The harness logs 8-11 MB/s
    via unbuffered fprintf(stderr) — one write() syscall per line.  Through a
    pipe that is a per-line parent wakeup storm, which jitters host scheduling
    and (per the P179b/P180 force-transition finding) tips the splitter into
    the abandon-frame churn state.  p181, which always writes stderr to a file,
    has never once collapsed (mean 92.5%, sd 2.6, incl. this same session).
    """
    cmd = victim_cmd(a, a.windows)
    timeout = a.warmup + a.run * a.windows + 150
    for attempt in range(1, 3):
        outpath = outdir / f'{tag}_attempt{attempt}.out'
        errpath = outdir / f'{tag}_attempt{attempt}.err'
        with open(outpath, 'w') as ofh, open(errpath, 'w') as efh:
            proc = subprocess.Popen(cmd, cwd=str(REPO),
                                    stdout=ofh, stderr=efh,
                                    text=True, start_new_session=True,
                                    env=base_env(parse_env(a.a_env)))
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                timed_out = True
        out = outpath.read_text(errors='replace')
        err = errpath.read_text(errors='replace')
        combined = out + '\n' + err
        uhd_init_fail = ('Failure to create rfnoc_graph' in combined or
                         'RfnocError' in combined)
        windows = RESULT_RE.findall(out or '')
        len89 = sum(1 for m in DECODE_OK_RE.finditer(err or '')
                    if m.group(1) == '89')
        retryable = timed_out or (proc.returncode != 0 and uhd_init_fail) \
            or len(windows) < a.windows
        if retryable and attempt < 2:
            print(f'[P182] {tag} attempt {attempt}: retryable '
                  f'(timeout={timed_out} initfail={uhd_init_fail} '
                  f'win={len(windows)}/{a.windows}); probe + retry', flush=True)
            uhd_nudge(a.a_addr)
            time.sleep(5)
            continue
        return windows, len89, attempt, not retryable
    return [], 0, 2, False


def rate_of(windows):
    ok = sum(int(w[3]) for w in windows)
    est = sum(int(w[1]) for w in windows)
    return (ok / est * 100) if est else 0.0


SUMMARY_RE = re.compile(
    r'^\s*(\d+)\s+(\S+)\s+(\d+)/(\d+)\s+([\d.]+)\s+(.*)$')


def parse_summary(logpath):
    """-> list of (idx, label, sigma_or_None, rate, [per_window_rate...])."""
    rows, in_tbl = [], False
    for line in Path(logpath).read_text(errors='replace').splitlines():
        if 'P182 SUMMARY' in line:
            in_tbl = True
            continue
        if not in_tbl:
            continue
        m = SUMMARY_RE.match(line)
        if not m:
            continue
        idx, label, ok, est, rate, per = m.groups()
        perw = []
        for tok in per.split('/'):
            tok = tok.strip()
            if tok.isdigit():
                perw.append(int(tok) / 150.0 * 100.0)
        if label == 'off':
            xval = None
        else:
            try:
                xval = float(label[:-2] if label.endswith('dB') else label)
            except ValueError:
                continue
        rows.append((int(idx), label, xval, float(rate), perw))
    return rows


def plot_curve(rows, outpath, title_extra='', xlabel=None, xlog=False):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    offs = [r for r in rows if r[2] is None]
    ons = sorted([r for r in rows if r[2] is not None], key=lambda r: r[2])
    if not ons:
        print('[P182] no noise arms to plot', flush=True)
        return

    fig, ax = plt.subplots(figsize=(9, 5.5))
    if offs:
        om = sum(r[3] for r in offs) / len(offs)
        olo, ohi = min(r[3] for r in offs), max(r[3] for r in offs)
        ax.axhspan(olo, ohi, color='tab:green', alpha=0.12,
                   label=f'noise OFF arms ({olo:.0f}-{ohi:.0f}%)')
        ax.axhline(om, color='tab:green', ls='--', alpha=0.8)

    # per-level: individual windows (scatter) + arm mean (line)
    lvl = {}
    for r in ons:
        lvl.setdefault(r[2], []).append(r)
    xs = sorted(lvl)
    means = [sum(r[3] for r in lvl[s]) / len(lvl[s]) for s in xs]
    for s in xs:
        for r in lvl[s]:
            ax.scatter([s] * len(r[4]), r[4], color='tab:red', alpha=0.35, s=18)
    ax.plot(xs, means, marker='o', color='tab:red', lw=1.8,
            label=xlabel and 'victim link under interference' or 'victim link')

    if xlog:
        ax.set_xscale('log')
    ax.set_xlabel(xlabel or
                  'Box B noise amplitude $\\sigma$ (per-sample std, log scale)')
    ax.set_ylabel('Victim (box A) FCS_OK / est_sent (%)')
    ax.set_ylim(0, 105)
    ax.grid(alpha=0.3, which='both')
    ax.legend()
    ax.set_title(title_extra or (
        'Interferer power sweep — victim MCS0 len38, 5890 MHz, <10 cm, '
        '<10cm box spacing'))
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    print(f'[P182] curve: {outpath}', flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--probe', action='store_true',
                   help='A/B/A triple with the loudest noise only (go/no-go)')
    p.add_argument('--levels', default='0.01,0.032,0.1,0.32',
                   help='noise sigmas to sweep (palindrome applied)')
    p.add_argument('--b-gains', default='',
                   help='sweep box B TX GAIN instead of sigma (same axis as '
                        'P179 frame-interferer sweep); uses --noise-sigma for '
                        'the waveform. e.g. 0,6,12,18,24,31.5')
    p.add_argument('--noise-sigma', type=float, default=0.32,
                   help='waveform sigma used when --b-gains is given')
    p.add_argument('--b-frames', action='store_true',
                   help='TRUE Co-SR mode: B transmits real 802.11 FRAMES (not '
                        'noise) at --b-len/--b-interval, raising its duty cycle '
                        'so its frames actually overlap the victim\'s frames. '
                        'Noise can never CAPTURE a receiver; only frames can.')
    p.add_argument('--b-interval', type=float, default=5.0,
                   help='B strobe interval (ms) in --b-frames mode. '
                        'len1000 + 5ms -> ~1.3ms frame -> 26%% duty '
                        '(vs 0.12%% at the default len38/100ms)')
    p.add_argument('--freq', type=float, default=5890)
    p.add_argument('--a-addr', default='192.168.10.2')
    p.add_argument('--b-addr', default='192.168.20.3')
    p.add_argument('--a-tx-gain', type=float, default=31.5)
    p.add_argument('--a-tx-scale', type=float, default=1.0)
    p.add_argument('--b-tx-gain', type=float, default=31.5)
    p.add_argument('--rx-gain', type=float, default=31.5)
    p.add_argument('--rx-subdev', default='B:0')
    p.add_argument('--rx-scale', type=float, default=40)
    p.add_argument('--interval', type=float, default=100)
    p.add_argument('--mcs', type=int, default=0)
    p.add_argument('--len', type=int, default=38)
    p.add_argument('--b-len', type=int, default=61)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--run', type=float, default=15)
    p.add_argument('--windows', type=int, default=4)
    p.add_argument('--b-start-grace', type=float, default=25)
    p.add_argument('--a-env', default='HDR_COMP_DISABLE=0,SYNC_LONG_XCORR_FS=1')
    p.add_argument('--exclude-arms', default='',
                   help='comma list of arm indices to flag and drop from the '
                        'curve (measurement-system transients, identified by '
                        'the force-transition diagnostic / window pattern)')
    p.add_argument('--replot', default='',
                   help='parse an existing console log, write the curve, exit '
                        '(no USRP access)')
    p.add_argument('--tag', default='')
    a = p.parse_args()

    excl = {int(x) for x in a.exclude_arms.split(',') if x.strip()}

    if a.replot:
        rows = parse_summary(a.replot)
        if not rows:
            raise SystemExit(f'no P182 SUMMARY table found in {a.replot}')
        raw_n = len(rows)
        rows = [r for r in rows if r[0] not in excl]
        if excl:
            print(f'[P182] flagged/excluded arms: {sorted(excl)} '
                  f'({raw_n - len(rows)} of {raw_n} dropped)', flush=True)
        out = Path(a.replot).with_suffix('.png')
        gain_mode = any(r[1].endswith('dB') for r in rows)
        xl = ('Box B (interferer) TX gain (dB) — same axis as P179'
              if gain_mode else
              'Box B noise amplitude $\\sigma$ (per-sample std, log scale)')
        ttl = ('Co-SR frame validation — B transmits REAL frames, '
               'duty 13% (len1000/10ms)'
               if gain_mode else
               'Interferer = CONTINUOUS noise (duty 100%)')
        plot_curve(rows, out, xlabel=xl, xlog=not gain_mode, title_extra=ttl)
        for idx, label, sig, rate, perw in rows:
            pw = '/'.join(f'{x:.0f}' for x in perw)
            print(f'  arm{idx:02d} noise={label:6s} {rate:6.2f}%  [{pw}]')
        return

    # plan entries: (label, sigma_or_None, b_tx_gain)
    if a.probe:
        plan = [('off', None, a.b_tx_gain),
                ('0.32', 0.32, a.b_tx_gain),
                ('off', None, a.b_tx_gain)]
    elif a.b_gains:
        gs = [float(x) for x in a.b_gains.split(',')]
        seq = gs + gs[::-1]                       # palindrome
        # In --b-frames mode the sigma field is only an "B is ON" marker (1.0);
        # the noise waveform is not used.  The swept quantity is b_gain.
        on_marker = 1.0 if a.b_frames else a.noise_sigma
        plan = [('off', None, a.b_tx_gain)]
        plan += [(f'{g:g}dB', on_marker, g) for g in seq]
        plan += [('off', None, a.b_tx_gain)]
    else:
        lv = [float(x) for x in a.levels.split(',')]
        seq = lv + lv[::-1]                       # palindrome
        plan = [('off', None, a.b_tx_gain)]
        plan += [(f'{s:g}', s, a.b_tx_gain) for s in seq]
        plan += [('off', None, a.b_tx_gain)]

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = f'_{a.tag}' if a.tag else ''
    outdir = CAP_ROOT / f'p182_ota_noise_{ts}{tag}'
    outdir.mkdir(parents=True, exist_ok=True)
    if a.b_gains:
        xlabel = 'Box B (interferer) TX gain (dB) — same axis as P179'
    else:
        xlabel = ('Box B noise amplitude $\\sigma$ (per-sample std, log scale)')

    if a.b_frames:
        print('=== OTA INTERFERER SWEEP — B TRANSMITS REAL FRAMES '
              '(Co-SR mode) ===', flush=True)
        duty = None
        try:
            psdu = (a.b_len + 28) * 8
            sym = -(-(16 + psdu + 6) // 26)          # MCS0, 26 dbps
            dur_ms = (sym * 4 + 32) / 1000.0
            duty = dur_ms / a.b_interval * 100.0
        except Exception:
            pass
        if duty:
            print(f'[P182] B frame {dur_ms:.2f} ms / interval '
                  f'{a.b_interval:g} ms -> duty {duty:.1f}%  '
                  f'(victim frames hit ~{(0.12 + dur_ms) / a.b_interval * 100:.1f}%)',
                  flush=True)
    else:
        print('=== P182 OTA CONTINUOUS-NOISE SWEEP (B transmits noise) ===',
              flush=True)
    print(f'[P182] plan={[lbl for lbl, _, _ in plan]} ({len(plan)} arms x '
          f'{a.windows} windows x {a.run}s)', flush=True)
    bdesc = (f'FRAMES len{a.b_len}/interval {a.b_interval:g}ms'
             if a.b_frames else 'noise via --tx-file')
    print(f'[P182] victim A={a.a_addr} MCS{a.mcs} len{a.len} tx-gain '
          f'{a.a_tx_gain} | interferer B={a.b_addr} tx-gain {a.b_tx_gain} '
          f'{bdesc}', flush=True)
    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    print(f'[P182] governor={gov.read_text().strip() if gov.exists() else "?"}  '
          f'logs -> {outdir}', flush=True)

    results = []
    for idx, (label, sigma, b_gain) in enumerate(plan):
        print(f'[P182] --- arm {idx}: noise={label} b_tx_gain={b_gain:g} ---',
              flush=True)
        b_proc = b_log = None
        if sigma is not None:
            a.b_tx_gain = b_gain
            if a.b_frames:
                bcmd = frame_interferer_cmd(a)
            else:
                wave = NOISE_DIR / f'noise_{sigma:g}.fc32'
                if not wave.is_file():
                    print(f'[P182] MISSING waveform {wave} — skipping arm',
                          flush=True)
                    continue
                bcmd = noise_cmd(a, wave)
            b_log = open(outdir / f'arm{idx:02d}_b_{label}.out', 'w')
            b_proc = subprocess.Popen(bcmd, cwd=str(REPO),
                                      stdout=b_log, stderr=subprocess.STDOUT,
                                      text=True, start_new_session=True,
                                      env=base_env(parse_env(a.a_env)))
            time.sleep(a.b_start_grace)
            if b_proc.poll() is not None:
                print(f'[P182] WARNING: noise TX exited in grace '
                      f'(rc={b_proc.returncode})', flush=True)
                uhd_nudge(a.b_addr)
        windows, len89, attempts, infra_ok = run_victim(
            a, f'arm{idx:02d}_a_{label}', outdir)
        if b_proc is not None:
            died = b_proc.poll() is not None
            if not died:
                os.killpg(b_proc.pid, signal.SIGKILL)
            b_proc.wait()
            b_log.close()
            # B's log is its own RX flooded by its own TX (~1.3 GB/arm).  Before
            # dropping it (disk), extract the handful of counters that answer
            # "did B actually transmit?" -- the VICTIM's len=1028 count cannot
            # answer that, because a collapsed victim receiver stops decoding
            # B's frames for its own reasons.  B's own RX hears its own frames
            # through the near-field self-link, so B's self-reception is the
            # only measure that is independent of the victim's state.
            bpath = outdir / f'arm{idx:02d}_b_{label}.out'
            try:
                counts = {}
                for tag, pat in (('b_self_len1028', r'DECODE_SUCCESS.*len=1028'),
                                 ('b_self_any_ok', r'DECODE_SUCCESS'),
                                 ('b_tx_lsig', r'\[TX_LSIG\]'),
                                 ('b_tx_htsig', r'\[TX_HT_SIG\]')):
                    r = subprocess.run(['grep', '-c', '-E', pat, str(bpath)],
                                       capture_output=True, text=True)
                    counts[tag] = (r.stdout or '0').strip()
                (outdir / f'arm{idx:02d}_b_{label}.summary.txt').write_text(
                    ' '.join(f'{k}={v}' for k, v in counts.items()) + '\n')
                print(f'[P182]   B TX check: ' +
                      ' '.join(f'{k}={v}' for k, v in counts.items()),
                      flush=True)
                bpath.unlink()
            except OSError:
                pass
            if died:
                print('[P182] WARNING: noise TX died mid-arm', flush=True)
                uhd_nudge(a.b_addr)
            time.sleep(5)
        results.append((idx, label, sigma, b_gain, windows, len89, attempts,
                        infra_ok))
        print(f'[P182] arm {idx} noise={label} bg={b_gain:g}: A FCS_OK='
              f'{sum(int(w[3]) for w in windows)}/'
              f'{sum(int(w[1]) for w in windows)} = {rate_of(windows):.1f}%  '
              f'len89={len89}  (attempts={attempts}, infra_ok={infra_ok})',
              flush=True)
        time.sleep(3)

    print('\n========== P182 SUMMARY ==========', flush=True)
    print('arm  noise    A_FCS_OK/est   rate%   per-window', flush=True)
    print('arm  noise      b_gain  A_FCS_OK/est   rate%   per-window',
          flush=True)
    for idx, label, sigma, bg, windows, len89, attempts, infra in results:
        per = '/'.join(w[3] for w in windows)
        tot_ok = sum(int(w[3]) for w in windows)
        tot_es = sum(int(w[1]) for w in windows)
        print(f'{idx:3d}  {label:6s}  {tot_ok:6d}/{tot_es:<6d}  '
              f'{rate_of(windows):6.2f}   {per}', flush=True)
    offs = [rate_of(w) for _, l, _, _, w, _, _, _ in results if l == 'off' and w]
    ons = [rate_of(w) for _, l, _, _, w, _, _, _ in results if l != 'off' and w]
    if offs:
        print(f'\n[P182] victim-only arms: {[f"{r:.1f}" for r in offs]}  '
              f'mean={sum(offs)/len(offs):.2f}%', flush=True)
    if ons:
        print(f'[P182] noise arms:       {[f"{r:.1f}" for r in ons]}  '
              f'mean={sum(ons)/len(ons):.2f}%', flush=True)
    print(f'[P182] logs: {outdir}', flush=True)

    rows = []
    for idx, label, sigma, bg, windows, len89, attempts, infra in results:
        rate = rate_of(windows)
        perw = [(int(w[3]) / int(w[1]) * 100.0) if int(w[1]) else 0.0
                for w in windows]
        # x axis follows whichever knob was swept
        xval = bg if a.b_gains else sigma
        rows.append((idx, label, xval, rate, perw))
    plot_curve(rows, outdir / 'p182_curve.png', xlabel=xlabel,
               xlog=not a.b_gains)


if __name__ == '__main__':
    main()
