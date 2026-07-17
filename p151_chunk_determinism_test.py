#!/usr/bin/env python3
"""Phase 151 RED test: sync_long chunk-invariance determinism test.

The bug (Phase 148 root-cause): sync_long's correlation accumulation is
chunk-partition-dependent. The deadlock-skip consumes small chunks (ninput<=63)
WITHOUT computing correlation, so d_offset falls behind true consumption by the
skipped amount -> all subsequent 320-sample correlation windows shift by 63 ->
L-LTF peak leaves its +/-50 position tolerance -> frames detected on some runs,
missed on others. Result: identical replay of a STATIC capture yields DIFFERENT
frame_bytes / DECODE_SUCCESS counts run-to-run.

This test replays the SAME capture N times and asserts the detection/arrival
metrics are PERFECTLY constant. Pre-fix: FAILS (non-deterministic). Post-fix
(env-gated chunk-invariant accumulation): PASSES.

Usage:
  python3 p151_chunk_determinism_test.py --runs 4 [--nsamp 0] [--fix-env NAME=VALUE ...]
Exit 0 = deterministic (GREEN), 1 = non-deterministic (RED).
"""
import argparse, os, subprocess, sys

REPO = os.path.dirname(os.path.abspath(__file__))


def run_once(iq, nsamp, drain, err, extra_env):
    env = os.environ.copy()
    env.pop('LD_LIBRARY_PATH', None)
    for kv in extra_env:
        k, v = kv.split('=', 1)
        env[k] = v
    env['LD_PRELOAD'] = './wrap_rpc2.so'
    env['PYTHONPATH'] = 'build/python/bindings:python:examples'
    cmd = ['/home/hy/conda/envs/gnuradio/bin/python', 'p148_funnel.py',
           '--iq-file', iq, '--nsamp', str(nsamp), '--drain', str(drain)]
    with open('/dev/null', 'w') as dn, open(err, 'w') as ef:
        subprocess.run(cmd, cwd=REPO, env=env, stdout=dn, stderr=ef, check=True)


def count(path, sub):
    n = 0
    with open(path, errors='replace') as f:
        for line in f:
            if sub in line:
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iq', default='/home/hy/captures/p150_ant_g15.fc32')
    ap.add_argument('--nsamp', type=int, default=0, help='samples (0=whole file)')
    ap.add_argument('--drain', type=float, default=3.0)
    ap.add_argument('--runs', type=int, default=4)
    ap.add_argument('--fix-env', action='append', default=[],
                    help='extra env NAME=VALUE (e.g. the chunk-invariance fix flag)')
    args = ap.parse_args()
    nsamp = args.nsamp or (os.path.getsize(args.iq) // 8)

    synclong_det, frame_bytes, fcs_ok = [], [], []
    for r in range(args.runs):
        err = f'/tmp/p151_det_run{r}.err'
        try:
            run_once(args.iq, nsamp, args.drain, err, args.fix_env)
        except subprocess.CalledProcessError as e:
            print(f'[run {r}] harness exited {e.returncode} (see {err})', file=sys.stderr)
            sys.exit(2)
        d = count(err, 'LONG: frame start at 174')   # sync_long detections
        fb = count(err, 'EQ_TAG] frame_bytes')        # frames reaching decode_mac
        fs = count(err, '[DECODE_SUCCESS]')           # FCS_OK
        synclong_det.append(d); frame_bytes.append(fb); fcs_ok.append(fs)
        print(f'[run {r}] sync_long_det={d} frame_bytes={fb} fcs_ok={fs}', flush=True)

    def rng(v): return (min(v), max(v))
    det_ok = len(set(synclong_det)) == 1
    fb_ok = len(set(frame_bytes)) == 1
    fs_ok = len(set(fcs_ok)) == 1
    print(f'\nsync_long_det {rng(synclong_det)}  frame_bytes {rng(frame_bytes)}  fcs_ok {rng(fcs_ok)}')
    print(f'constant: sync_long_det={det_ok} frame_bytes={fb_ok} fcs_ok={fs_ok}')

    deterministic = det_ok and fb_ok and fs_ok
    print('VERDICT:', 'PASS (deterministic / chunk-invariant)' if deterministic
          else 'FAIL (non-deterministic — chunk-partition-dependent)')
    sys.exit(0 if deterministic else 1)


if __name__ == '__main__':
    main()
