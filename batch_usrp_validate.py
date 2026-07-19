#!/usr/bin/env python3
"""Batch-run usrp_realtime_validate.sh and compute statistics.

Usage:
    python3 batch_usrp_validate.py [-n 16]

Environment:
    IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR=1
    IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH=1
"""
import argparse
import datetime
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path('/home/hy/gr-ieee802-11')
# Overridable for synthetic retry-logic tests (BATCH_VALIDATE_SCRIPT=/tmp/fake.sh).
SCRIPT = Path(os.environ.get('BATCH_VALIDATE_SCRIPT', str(REPO / 'usrp_realtime_validate.sh')))


def parse_run(text: str) -> dict:
    gt_ok = 0
    gt_fail = 0
    uf = 0
    ofv = 0
    pdu_sum = None
    arrival = 'N/A'
    for line in text.splitlines():
        m = re.search(r'DECODE_SUCCESS \(ground truth\)\s*=\s*(\d+)', line)
        if m:
            gt_ok = int(m.group(1))
        m = re.search(r'DECODE_FAIL \(LDPC terminal\)\s*=\s*(\d+)', line)
        if m:
            gt_fail = int(m.group(1))
        m = re.search(r'TX underflow\s*=\s*(\d+)\s+RX overflow\s*=\s*(\d+)', line)
        if m:
            uf, ofv = int(m.group(1)), int(m.group(2))
        m = re.search(r'total PDU FCS_OK.*?(\d+)', line)
        if m:
            pdu_sum = int(m.group(1))
        m = re.search(r'arrival \(est\)\s*=\s*\d+\s*/\s*~\d+\s*=\s*([\d.]+%)', line)
        if m:
            arrival = m.group(1)
    return {
        'gt_ok': gt_ok,
        'gt_fail': gt_fail,
        'uf': uf,
        'of': ofv,
        'pdu_sum': pdu_sum,
        'arrival': arrival,
    }


def main():
    parser = argparse.ArgumentParser(description='Batch USRP realtime validation')
    parser.add_argument('-n', '--runs', type=int, default=16, help='number of validation runs')
    parser.add_argument('--threshold', type=int, default=15)
    parser.add_argument('--windows', type=int, default=3)
    parser.add_argument('--run', type=int, default=15)
    parser.add_argument('--out-dir', default='batch_results', help='directory for per-run logs')
    args = parser.parse_args()

    os.environ.setdefault('IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR', '1')
    os.environ.setdefault('IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH', '1')

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = REPO / args.out_dir / ts
    outdir.mkdir(parents=True, exist_ok=True)

    print(f'[BATCH] output dir: {outdir}')
    print(f'[BATCH] runs={args.runs} threshold={args.threshold} windows={args.windows} run={args.run}s')
    print(f'[BATCH] env: BOXCAR={os.environ.get("IEEE80211_SYNC_SHORT_FUSED_USE_BOXCAR")} '
          f'ADAPTIVE={os.environ.get("IEEE80211_SYNC_SHORT_USE_ADAPTIVE_THRESH")}')

    results = []
    for i in range(1, args.runs + 1):
        print(f'[BATCH] === run {i}/{args.runs} start ===', flush=True)
        out_file = outdir / f'run_{i:02d}.log'
        err_file = outdir / f'run_{i:02d}.err'

        max_attempts = 3
        proc = None
        for attempt in range(1, max_attempts + 1):
            proc = subprocess.run(
                [
                    str(SCRIPT),
                    '--threshold', str(args.threshold),
                    '--windows', str(args.windows),
                    '--run', str(args.run),
                ],
                cwd=str(REPO),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            attempt_out = outdir / f'run_{i:02d}_attempt{attempt}.log'
            attempt_err = outdir / f'run_{i:02d}_attempt{attempt}.err'
            attempt_out.write_text(proc.stdout)
            attempt_err.write_text(proc.stderr or '')

            # Retry only on UHD RFNOC graph initialization failures.
            combined_err = (proc.stdout or '') + '\n' + (proc.stderr or '')
            uhd_init_fail = ('Failure to create rfnoc_graph' in combined_err or
                             'RfnocError' in combined_err or
                             'Management operation failed' in combined_err)
            if proc.returncode != 0 and uhd_init_fail and attempt < max_attempts:
                print(f'[BATCH] run {i:02d} attempt {attempt} UHD init failed, probing device + retrying...', flush=True)
                # uhd_usrp_probe nudge: documented recovery for X310 RFNoC bad
                # state (project retrospective). Also gives the control plane
                # settle time before the next init attempt.
                subprocess.run(['uhd_usrp_probe', '--args', 'addr=192.168.10.2'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=60)
                time.sleep(5)
                continue
            break

        # Keep the final attempt as the canonical log.
        out_file.write_text(proc.stdout)
        err_file.write_text(proc.stderr or '')
        parsed = parse_run(proc.stdout)
        parsed['pass'] = proc.returncode == 0
        parsed['rc'] = proc.returncode
        parsed['attempts'] = attempt
        # Infra failure = harness died on UHD/RFNoC init on ALL attempts.
        # Excluded from decoder statistics (not a decoder measurement).
        parsed['infra_fail'] = proc.returncode != 0 and uhd_init_fail
        results.append(parsed)
        status = 'PASS' if parsed['pass'] else 'FAIL'
        print(
            f'[BATCH] run {i:02d} (attempts={attempt}): DECODE_SUCCESS={parsed["gt_ok"]:3d} '
            f'arrival={parsed["arrival"]:>6s} UF={parsed["uf"]:2d} OF={parsed["of"]:2d} '
            f'PDU={parsed["pdu_sum"] if parsed["pdu_sum"] is not None else "?"} '
            f'rc={parsed["rc"]} {status}',
            flush=True,
        )

    infra_fails = sum(1 for r in results if r.get('infra_fail'))
    ok_values = [r['gt_ok'] for r in results if not r.get('infra_fail')]
    passes = sum(r['pass'] for r in results)
    if len(ok_values) > 1:
        mean = statistics.mean(ok_values)
        stdev = statistics.stdev(ok_values)
    elif ok_values:
        mean = ok_values[0]
        stdev = 0.0
    else:
        mean = 0.0
        stdev = 0.0

    summary_lines = [
        f'runs: {args.runs}',
        f'passes: {passes}/{args.runs}',
        f'infra_failures (excluded from stats): {infra_fails}',
        f'DECODE_SUCCESS mean={mean:.2f} std={stdev:.2f} min={min(ok_values) if ok_values else 0} max={max(ok_values) if ok_values else 0}  (n={len(ok_values)})',
        f'values: {ok_values}',
    ]
    summary = '\n'.join(summary_lines)
    (outdir / 'summary.txt').write_text(summary + '\n')
    print('[BATCH] === SUMMARY ===')
    print(summary)
    print(f'[BATCH] logs saved in {outdir}')

    # Infra failures are neutral (excluded); decoder failures fail the batch.
    return 0 if (passes + infra_fails) == args.runs else 1


if __name__ == '__main__':
    sys.exit(main())
