#!/usr/bin/env python3
"""Phase 158 N=8 interleaved ABAB confirmation batch.

Design (user-approved 2026-08-04, supersedes full N=16 A/B):
  - 8 pairs; each pair = control(OFF) + experiment(COPY_REDETECT=1) back-to-back.
  - Within-pair order alternates (odd pairs A->B, even pairs B->A) to cancel
    linear time-of-day drift (P158 verdict lesson #4: baseline is
    time/environment-modulated; back-to-back comparison is the only ruler).
  - Per-run env identical except the single arm variable
    IEEE80211_SYNC_SHORT_COPY_REDETECT (unset/'0' = OFF, '1' = ON).
  - Hang timeout per attempt (P158 verdict lesson #3: batch without timeout
    can wedge the X310 -> next UHD init hangs forever). On timeout: SIGKILL
    the whole process group, uhd_usrp_probe nudge, retry (max 3 attempts).
  - Infra failures (UHD/RFNoC init fail on ALL attempts, or repeated hang)
    are excluded from decoder statistics (P152 convention).

Pre-registered decision rule:
  diff_i = gt_ok(B) - gt_ok(A) over pairs where BOTH arms are valid.
  CONFIRMED iff mean(diff) > 0 AND two-sided paired t-test p < 0.05.
  Wilcoxon signed-rank reported as a non-parametric cross-check.

Usage:
  python3 p158_abab_batch.py [--pairs 8]
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
REDETECT_ENV = 'IEEE80211_SYNC_SHORT_COPY_REDETECT'


def uhd_nudge():
    subprocess.run(['uhd_usrp_probe', '--args', 'addr=192.168.10.2'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)


def run_once(tag, outdir, redetect_on):
    """One validation run. Returns parsed dict + infra_fail/timed_out flags."""
    env = os.environ.copy()
    env.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
    env.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')
    env.pop(REDETECT_ENV, None)
    if redetect_on:
        env[REDETECT_ENV] = '1'

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
    if rt_err.exists():
        shutil.copy(rt_err, outdir / f'{tag}.rt.err')

    parsed = parse_run(out or '')
    parsed['rc'] = proc.returncode
    parsed['attempts'] = attempt
    parsed['timed_out'] = timed_out
    # Infra = never got a real measurement (all attempts init-failed or final
    # attempt hung). Excluded from stats, like P152.
    parsed['infra_fail'] = timed_out or (proc.returncode != 0 and uhd_init_fail)
    return parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', type=int, default=8)
    args = ap.parse_args()

    gov = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    if gov.exists() and gov.read_text().strip() != 'performance':
        print('[ABAB] FATAL: governor != performance — fix first: '
              'sudo systemctl start gr-cpu-performance.service', flush=True)
        return 2

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = REPO / 'batch_results' / 'p158_abab' / ts
    outdir.mkdir(parents=True, exist_ok=True)
    print(f'[ABAB] output dir: {outdir}')
    print(f'[ABAB] pairs={args.pairs}  arm A=control(OFF)  arm B=experiment(ON)')
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
            print(f'[ABAB] === {tag} start ({"ON" if arm == "B" else "OFF"}) ===',
                  flush=True)
            r = run_once(tag, outdir, redetect_on=(arm == 'B'))
            arms[pair][arm] = r
            print(f'[ABAB] {tag}: DECODE_SUCCESS={r["gt_ok"]:3d} '
                  f'arrival={r["arrival"]:>6s} UF={r["uf"]} OF={r["of"]} '
                  f'rc={r["rc"]} attempts={r["attempts"]} '
                  f'{"INFRA_FAIL" if r["infra_fail"] else "OK"}', flush=True)

    # ---- paired statistics ----
    diffs, valid_pairs = [], []
    for pair in range(1, args.pairs + 1):
        a, b = arms[pair].get('A'), arms[pair].get('B')
        if a and b and not a['infra_fail'] and not b['infra_fail']:
            diffs.append(b['gt_ok'] - a['gt_ok'])
            valid_pairs.append(pair)

    a_vals = [arms[p]['A']['gt_ok'] for p in valid_pairs]
    b_vals = [arms[p]['B']['gt_ok'] for p in valid_pairs]
    lines = [
        f'pairs run: {args.pairs}, valid (both arms non-infra): {len(valid_pairs)}',
        f'valid pair order: {valid_pairs}',
        f'A (OFF) values: {a_vals}',
        f'B (ON)  values: {b_vals}',
        f'per-pair diff (B-A): {diffs}',
    ]
    verdict = 'INCONCLUSIVE (insufficient valid pairs)'
    if len(diffs) >= 3:
        mean_d = statistics.mean(diffs)
        std_d = statistics.stdev(diffs)
        n = len(diffs)
        t_stat = mean_d / (std_d / n ** 0.5) if std_d > 0 else float('inf')
        lines.append(f'mean diff = {mean_d:+.2f}  std diff = {std_d:.2f}  '
                     f't({n - 1}) = {t_stat:.2f}')
        p_val = None
        try:
            from scipy import stats as sps
            p_val = float(sps.ttest_rel(b_vals, a_vals).pvalue)
            w = sps.wilcoxon(diffs)
            lines.append(f'scipy paired t p = {p_val:.4f}; '
                         f'wilcoxon p = {float(w.pvalue):.4f}')
        except Exception as e:
            lines.append(f'(scipy unavailable: {e}; compare t vs '
                         f'crit 2.365@df7 / 2.447@df6 / 2.571@df5 / 2.776@df4)')
            # Conservative manual fallback: |t| above two-sided 0.05 crit.
            crit = {7: 2.365, 6: 2.447, 5: 2.571, 4: 2.776, 3: 3.182}
            p_val = 0.01 if abs(t_stat) > crit.get(n - 1, 3.182) else 1.0
        confirmed = mean_d > 0 and p_val is not None and p_val < 0.05
        verdict = ('CONFIRMED: COPY re-detect improves arrival '
                   f'(+{mean_d:.1f}/45s, p={p_val:.4f})' if confirmed else
                   f'NOT CONFIRMED (mean diff {mean_d:+.1f}, p={p_val})')
    lines.append(f'VERDICT: {verdict}')

    summary = '\n'.join(lines)
    (outdir / 'summary.txt').write_text(summary + '\n')
    print('[ABAB] === SUMMARY ===')
    print(summary)
    print(f'[ABAB] logs saved in {outdir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
