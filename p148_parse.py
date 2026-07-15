#!/usr/bin/env python3
"""Phase 148: parse a p148/p147 replay stderr log into per-stage funnel counts.

Stage semantics (each is a count of matching stderr lines):
  sync_long_tag  - 'SYNC_LONG_TAG'            sync_long emitted a wifi_start tag (frame detected)
  frame_detect   - 'FRAME_DETECT'             frame-detect events
  lsig_ok        - '[LSIG_DECODE] OK'         L-SIG viterbi "OK" (noisy / false-positive prone)
  ht_cand        - 'HT_SIG_CAND'              HT-SIG candidate events (dominated by noise)
  fcs_ok         - '[DECODE_SUCCESS]'         a frame fully decoded with FCS OK
  fcs_fail       - '[DECODE_FAIL] LDPC FCS error'  a DISTINCT frame that failed (terminal LDPC fail)
  decoded        - fcs_ok + fcs_fail          distinct frames that reached a terminal decode outcome

NOTE: we deliberately count '[DECODE_FAIL] LDPC FCS error' (terminal) NOT all 'DECODE_FAIL'
lines, because each failed frame prints both a 'Conv FCS error' and an 'LDPC FCS error' line.
Counting distinct terminal frames avoids the inflated-denominator bug (42% -> real 59%).
"""
import sys

STAGES = ["sync_long_tag", "frame_detect", "lsig_ok", "ht_cand",
          "fcs_ok", "fcs_fail", "decoded"]


def parse(path):
    n = {k: 0 for k in STAGES}
    with open(path, errors="replace") as f:
        for line in f:
            if "SYNC_LONG_TAG" in line:
                n["sync_long_tag"] += 1
            if "FRAME_DETECT" in line:
                n["frame_detect"] += 1
            if "[LSIG_DECODE] OK" in line:
                n["lsig_ok"] += 1
            if "HT_SIG_CAND" in line:
                n["ht_cand"] += 1
            if "[DECODE_SUCCESS]" in line:
                n["fcs_ok"] += 1
            if "[DECODE_FAIL] LDPC FCS error" in line:
                n["fcs_fail"] += 1
    n["decoded"] = n["fcs_ok"] + n["fcs_fail"]
    return n


if __name__ == "__main__":
    import json
    print(json.dumps(parse(sys.argv[1])))
