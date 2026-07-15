#!/usr/bin/env python3
"""Phase 148: run a replay funnel N times, report per-stage mean/std/CV, and run
the DETERMINISM TEST (decode counts must be stable across identical runs).

  RED   (harness=p147_replay_funnel.py, no drain): fcs_ok/decoded swing -> FAIL
  GREEN (harness=p148_funnel.py,       drain):     fcs_ok/decoded stable -> PASS

Exit code 0 = trustworthy (PASS), 1 = non-deterministic (FAIL).
"""
import argparse, os, statistics, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p148_parse import parse, STAGES


def run_once(harness, iq, nsamp, err):
    line = ("unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so "
            "PYTHONPATH=build/python/bindings:python:examples "
            f"/home/hy/conda/envs/gnuradio/bin/python {harness} "
            f"--iq-file {iq} --nsamp {nsamp} > /dev/null 2> {err}")
    subprocess.run(["bash", "-c", line], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--harness', default='p148_funnel.py')
    ap.add_argument('--iq', default='/tmp/p146_rxonly_cap.fc32')
    ap.add_argument('--nsamp', type=int, default=0)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--cv-thresh', type=float, default=0.05,
                    help='PASS if decoded CV below this')
    args = ap.parse_args()
    nsamp = args.nsamp or (os.path.getsize(args.iq) // 8)

    rows = []
    for r in range(args.runs):
        err = f"/tmp/p148_run{r}.err"
        try:
            run_once(args.harness, args.iq, nsamp, err)
        except subprocess.CalledProcessError as e:
            print(f"[run {r}] harness exited {e.returncode} (see {err})", file=sys.stderr)
            sys.exit(2)
        row = parse(err)
        rows.append(row)
        print(f"[run {r}] fcs_ok={row['fcs_ok']} fcs_fail={row['fcs_fail']} decoded={row['decoded']}",
              flush=True)

    print("\nstage            mean    std     cv     min   max")
    res = {}
    for s in STAGES:
        vals = [row[s] for row in rows]
        m = statistics.mean(vals)
        sd = statistics.pstdev(vals)
        cv = (sd / m) if m else 0.0
        res[s] = (m, sd, cv, min(vals), max(vals))
        print(f"{s:14s} {m:7.2f} {sd:6.2f} {cv:6.3f} {min(vals):5d} {max(vals):5d}")

    ok_m, ok_sd, ok_cv, ok_min, ok_max = res['fcs_ok']
    dc_m, dc_sd, dc_cv, dc_min, dc_max = res['decoded']
    full_det = (ok_min == ok_max) and (dc_min == dc_max)
    trustworthy = (dc_m > 0) and (dc_cv < args.cv_thresh)
    print(f"\nDETERMINISM: fcs_ok cv={ok_cv:.3f} [{ok_min}..{ok_max}]  "
          f"decoded cv={dc_cv:.3f} [{dc_min}..{dc_max}]  fully-constant={full_det}")
    print("VERDICT:", "PASS (trustworthy)" if trustworthy
          else "FAIL (non-deterministic — drain / root-cause needed)")
    sys.exit(0 if trustworthy else 1)


if __name__ == '__main__':
    main()
