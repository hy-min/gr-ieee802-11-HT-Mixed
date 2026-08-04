#!/usr/bin/env python3
"""Interleaved ABAB single-variable A/B harness for USRP realtime validation.

History:
  - Phase 158 (2026-08-04): N=8 ABAB replaced the full N=16 A/B (user-approved
    time saver); proved the preliminary +25.3 was cross-block confounding.
  - Now the STANDARD harness for all single-variable USRP A/B experiments
    (P158-ABAB verdict: un-paired cross-block comparisons carry ~±30 drift
    confound on this testbed; paired interleaving is mandatory).

Design:
  - N pairs; each pair = control(env absent) + experiment(env set) back-to-back.
  - Within-pair order alternates (odd pairs A->B, even pairs B->A) to cancel
    linear time-of-day drift.
  - Single variable: --exp-env NAME=VALUE (control arm unsets NAME).
  - Hang timeout per attempt (P158 lesson #3): on timeout SIGKILL the whole
    process group, uhd_usrp_probe nudge, retry (max 3 attempts).
  - Infra failures (UHD/RFNoC init fail on ALL attempts, or repeated hang)
    excluded from decoder statistics (P152 convention).
  - Per-run metrics: DECODE_SUCCESS (primary, ground truth) + arrival proxy
    (count of LSIG_DECODE enc=0 len=72 lines in harness stderr — frames that
    reached a correct L-SIG; decode rate = DS/arrived, the LO-wall term).

Pre-registered decision rule:
  diff_i = metric(B) - metric(A) over pairs where BOTH arms are valid.
  CONFIRMED iff mean(diff) > 0 AND two-sided paired t p < 0.05 (primary =
  DECODE_SUCCESS; arrival reported as mechanism evidence).

Usage:
  python3 p158_abab_batch.py [--pairs 8] [--tag p158_abab] \
      [--exp-env IEEE80211_SYNC_SHORT_COPY_REDETECT=1]
"""
import argparse
import datetime
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/home/hy/gr-ieee802-11')
from batch_usrp_validate import parse_run  # noqa: E402  (reuse the P152 parser)

REPO = Path('/home/hy/gr-ieee802-11')
SCRIPT = REPO / 'usrp_realtime_validate.sh'
RUN_TIMEOUT = 240        # s per attempt; normal run is ~80s (warmup 20 + 45 + init)
MAX_ATTEMPTS = 3


def uhd_nudge():
    subprocess.run(['uhd_usrp_probe', '--args', 'addr=192.168.10.2'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)


def count_arrivals(rt_err_copy):
    """Arrival proxy: frames reaching a correct L-SIG (enc=0 len=72)."""
    n = 0
    try:
        with open(rt_err_copy, errors='ignore') as f:
            for line in f:
                if 'LSIG_DECODE' in line and 'enc=0 len=72' in line:
                    n += 1
    except OSError:
        pass
    return n


def run_once(tag, outdir, exp_name, exp_value):
    """One validation run. Returns parsed dict + infra_fail/timed_out flags."""
    env = os.environ.copy()
    env.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
    env.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')
    env.pop(exp_name, None)
    if exp_value is not None:
        env[exp_name] = exp_value

    proc = None
    timed_out = False
    uhd_init_fail = False
    out = err = ''
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proc = subprocess.Popen(
            [str(SCRIPT), '--threshold', '15', '--windows', '3', '--run', '15'],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, env=env)
        try:
            out, err = proc.communicate(timeout=RUN_TIMEOUT)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            timed_out = True
        (outdir / f'{tag}_attempt{attempt}.log').write_text(out or '')
        (outdir / f'{tag}_attempt{attempt}.err').write_text(err or '')

        combined = (out or '') + '\n' + (err or '')
        uhd_init_fail = ('Failure to create rfnoc_graph' in combined or
                         'RfnocError' in combined or
                         'Management operation failed' in combined)
        retryable = timed_out or (proc.returncode != 0 and uhd_init_fail)
        if retryable and attempt < MAX_ATTEMPTS:
            reason = 'HANG timeout' if timed_out else 'UHD init failed'
            print(f'[ABAB] {tag} attempt {attempt}: {reason}; probe + retry...',
                  flush=True)
            try:
                uhd_nudge()
            except Exception as e:  # probe itself may fail; still retry
                print(f'[ABAB] {tag} probe error: {e}', flush=True)
            time.sleep(5)
            continue
        break

    # Archive the harness stderr (overwritten per run by the validate script).
    rt_err = Path('/tmp/rt_validate.err')
    rt_copy = outdir / f'{tag}.rt.err'
    if rt_err.exists():
        shutil.copy(rt_err, rt_copy)

    parsed = parse_run(out or '')
    parsed['arrived'] = count_arrivals(rt_copy)
    parsed['rc'] = proc.returncode
    parsed['attempts'] = attempt
    parsed['timed_out'] = timed_out
    # Infra = never got a real measurement (all attempts init-failed or final
    # attempt hung). Excluded from stats, like P152.
    parsed['infra_fail'] = timed_out or (proc.returncode != 0 and uhd_init_fail)
    return parsed


def paired_report(lines, label, a_vals, b_vals):
    """Paired t + Wilcoxon on (B - A); appends lines, returns (mean, p)."""
    diffs = [b - a for a, b in zip(a_vals, b_vals)]
    lines.append(f'{label}: A={a_vals}')
    lines.append(f'{label}: B={b_vals}')
    lines.append(f'{label}: per-pair diff (B-A)={diffs}')
    if len(diffs) < 3:
        lines.append(f'{label}: <3 valid pairs, no stats')
        return None, None
    mean_d = statistics.mean(diffs)
    std_d = statistics.stdev(diffs)
    n = len(diffs)
    t_stat = mean_d / (std_d / n ** 0.5) if std_d > 0 else float('inf')
    p_val = None
    try:
        from scipy import stats as sps
        p_val = float(sps.ttest_rel(b_vals, a_vals).pvalue)
        w = sps.wilcoxon(diffs)
        lines.append(f'{label}: mean diff = {mean_d:+.2f}  std = {std_d:.2f}  '
                     f't({n - 1}) = {t_stat:.2f}  paired t p = {p_val:.4f}  '
                     f'wilcoxon p = {float(w.pvalue):.4f}')
    except Exception as e:
        lines.append(f'{label}: mean diff = {mean_d:+.2f}  std = {std_d:.2f}  '
                     f't({n - 1}) = {t_stat:.2f} (scipy unavailable: {e})')
    return mean_d, p_val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', type=int, default=8)
    ap.add_argument('--tag', default='p158_abab',
                    help='subdir name under batch_results/')
    ap.add_argument('--exp-env',
                    default='IEEE80211_SYNC_SHORT_COPY_REDETECT=1',
                    help='NAME=VALUE for the experiment arm (control unsets NAME)')
    args = ap.parse_args()
    exp_name, _, exp_value = args.exp_env.partition('=')
    if not exp_name:
        print('[ABAB] FATAL: --exp-env must be NAME=VALUE', flush=True)
        return 2

    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    if gov.exists() and gov.read_text().strip() != 'performance':
        print('[ABAB] FATAL: governor != performance — fix first: '
              'sudo systemctl start gr-cpu-performance.service', flush=True)
        return 2

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = REPO / 'batch_results' / args.tag / ts
    outdir.mkdir(parents=True, exist_ok=True)
    print(f'[ABAB] output dir: {outdir}')
    print(f'[ABAB] pairs={args.pairs}  A=control({exp_name} unset)  '
          f'B=experiment({exp_name}={exp_value})')
    print('[ABAB] order: odd pairs A->B, even pairs B->A (drift cancellation)')

    # Pre-flight probe: confirms X310 reachable before burning run 1.
    print('[ABAB] pre-flight uhd_usrp_probe...', flush=True)
    try:
        uhd_nudge()
        print('[ABAB] probe OK', flush=True)
    except Exception as e:
        print(f'[ABAB] WARN: probe failed: {e} (continuing anyway)', flush=True)

    arms = {}   # pair_idx -> {'A': parsed, 'B': parsed}
    for pair in range(1, args.pairs + 1):
        order = ('A', 'B') if pair % 2 == 1 else ('B', 'A')
        arms[pair] = {}
        for arm in order:
            tag = f'pair{pair:02d}_{arm}'
            is_exp = (arm == 'B')
            print(f'[ABAB] === {tag} start ({"EXP" if is_exp else "CTL"}) ===',
                  flush=True)
            r = run_once(tag, outdir, exp_name, exp_value if is_exp else None)
            arms[pair][arm] = r
            print(f'[ABAB] {tag}: DECODE_SUCCESS={r["gt_ok"]:3d} '
                  f'arrived={r["arrived"]:3d} arrival={r["arrival"]:>6s} '
                  f'UF={r["uf"]} OF={r["of"]} rc={r["rc"]} '
                  f'attempts={r["attempts"]} '
                  f'{"INFRA_FAIL" if r["infra_fail"] else "OK"}', flush=True)

    # ---- paired statistics ----
    valid = [p for p in range(1, args.pairs + 1)
             if arms[p].get('A') and arms[p].get('B')
             and not arms[p]['A']['infra_fail'] and not arms[p]['B']['infra_fail']]
    a_ds = [arms[p]['A']['gt_ok'] for p in valid]
    b_ds = [arms[p]['B']['gt_ok'] for p in valid]
    a_ar = [arms[p]['A']['arrived'] for p in valid]
    b_ar = [arms[p]['B']['arrived'] for p in valid]

    lines = [
        f'experiment: {exp_name}={exp_value} vs unset',
        f'pairs run: {args.pairs}, valid (both arms non-infra): {len(valid)}',
        f'valid pair order: {valid}',
    ]
    mean_ds, p_ds = paired_report(lines, 'DECODE_SUCCESS', a_ds, b_ds)
    mean_ar, p_ar = paired_report(lines, 'ARRIVAL(enc=0 len=72)', a_ar, b_ar)

    if mean_ds is None:
        verdict = 'INCONCLUSIVE (insufficient valid pairs)'
    else:
        confirmed = mean_ds > 0 and p_ds is not None and p_ds < 0.05
        verdict = ('CONFIRMED: experiment improves DECODE_SUCCESS '
                   f'(+{mean_ds:.1f}/45s, p={p_ds:.4f}; '
                   f'arrival {mean_ar:+.1f})' if confirmed else
                   f'NOT CONFIRMED (DS diff {mean_ds:+.1f}, p={p_ds}; '
                   f'arrival {mean_ar:+.1f}, p={p_ar})')
    lines.append(f'VERDICT: {verdict}')

    summary = '\n'.join(lines)
    (outdir / 'summary.txt').write_text(summary + '\n')
    print('[ABAB] === SUMMARY ===')
    print(summary)
    print(f'[ABAB] logs saved in {outdir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
