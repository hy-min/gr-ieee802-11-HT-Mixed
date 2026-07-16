#!/usr/bin/env python3
"""Phase 149 regression test: sync_long detection must be chunk-INVARIANT.

Runs the offline funnel twice on a STATIC capture and asserts:
  (a) the sync_long wifi_start offset sequences are byte-identical, and
  (b) the decoded-frame count is constant across the two runs.

Exit 0 = deterministic (PASS), 1 = non-deterministic (FAIL).
This is the RED/GREEN gate for the sync_long chunk-invariance fix.
"""
import os
import re
import subprocess
import sys

IQ = "/tmp/p146_rxonly_cap.fc32"


def run_once(err):
    line = ("unset LD_LIBRARY_PATH && LD_PRELOAD=./wrap_rpc2.so "
            "PYTHONPATH=build/python/bindings:python:examples "
            "/home/hy/conda/envs/gnuradio/bin/python p148_funnel.py "
            f"--iq-file {IQ} --drain 6 > /dev/null 2> {err}")
    subprocess.run(["bash", "-c", line], check=True)


def offsets(err):
    offs = []
    ok = 0
    decoded = 0
    for line in open(err, errors="replace"):
        m = re.search(r"SYNC_LONG_TAG.*offset=(\d+)", line)
        if m:
            offs.append(int(m.group(1)))
        if "[DECODE_SUCCESS]" in line:
            ok += 1
            decoded += 1
        elif "[DECODE_FAIL] LDPC FCS error" in line:
            decoded += 1
    return offs, ok, decoded


def main():
    e0, e1 = "/tmp/p149_det0.err", "/tmp/p149_det1.err"
    run_once(e0)
    run_once(e1)
    o0, ok0, d0 = offsets(e0)
    o1, ok1, d1 = offsets(e1)
    same_off = (o0 == o1)
    same_dec = (d0 == d1)
    print(f"run0: {len(o0)} offsets, fcs_ok={ok0}, decoded={d0}")
    print(f"run1: {len(o1)} offsets, fcs_ok={ok1}, decoded={d1}")
    print(f"offsets identical: {same_off}; decoded constant: {same_dec}")
    if same_off and same_dec:
        print("PASS: sync_long detection is chunk-invariant (deterministic)")
        sys.exit(0)
    nmis = sum(1 for a, b in zip(o0, o1) if a != b) + abs(len(o0) - len(o1))
    print(f"FAIL: detection is chunk-dependent "
          f"({nmis} offset mismatches; decoded {d0} vs {d1})")
    sys.exit(1)


if __name__ == "__main__":
    main()
