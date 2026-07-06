#!/usr/bin/env python3
"""Phase 104: Diff per-frame diagnostic CSVs between clean and USRP captures.

Reads the clean baseline CSV plus N USRP capture CSVs and produces a
side-by-side summary showing frame counts, FCS_OK rates, and length distributions.
"""
import argparse
import csv
import os
import sys
from collections import Counter


def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def summarize(rows):
    if not rows:
        return {"count": 0, "fcs_ok": 0, "fcs_fail": 0, "length_dist": {}}
    n = len(rows)
    fcs_ok = sum(1 for r in rows if r.get('mac_crc') == '1')
    fcs_fail = n - fcs_ok
    lengths = Counter(r.get('length', '?') for r in rows)
    return {
        "count": n,
        "fcs_ok": fcs_ok,
        "fcs_fail": fcs_fail,
        "fcs_ok_pct": 100.0 * fcs_ok / max(1, n),
        "length_dist": dict(lengths),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--clean', required=True, help='Clean baseline CSV')
    p.add_argument('--usrp', nargs='+', required=True, help='One or more USRP capture CSVs')
    p.add_argument('--out', default='/tmp/p104_diff_summary.md', help='Output markdown report')
    args = p.parse_args()

    clean_rows = load_csv(args.clean)
    clean_summary = summarize(clean_rows)

    print(f"=== Phase 104 Diff Summary ===\n")
    print(f"Clean baseline ({args.clean}):")
    print(f"  Frames detected: {clean_summary['count']}")
    print(f"  FCS_OK: {clean_summary['fcs_ok']} ({clean_summary.get('fcs_ok_pct', 0):.1f}%)")
    print(f"  FCS_FAIL: {clean_summary['fcs_fail']}")
    print(f"  Length distribution: {clean_summary['length_dist']}\n")

    with open(args.out, 'w') as out:
        out.write("# Phase 104 Diff Summary\n\n")
        out.write("## Clean Baseline\n\n")
        out.write(f"- Frames: {clean_summary['count']}\n")
        out.write(f"- FCS_OK: {clean_summary['fcs_ok']} ({clean_summary.get('fcs_ok_pct', 0):.1f}%)\n")
        out.write(f"- Length distribution: {clean_summary['length_dist']}\n\n")
        out.write("## USRP Captures\n\n")

        usrp_summaries = []
        for path in args.usrp:
            rows = load_csv(path)
            s = summarize(rows)
            usrp_summaries.append((path, s))
            print(f"USRP capture ({path}):")
            print(f"  Frames detected: {s['count']}")
            print(f"  FCS_OK: {s['fcs_ok']} ({s.get('fcs_ok_pct', 0):.1f}%)")
            print(f"  FCS_FAIL: {s['fcs_fail']}")
            print(f"  Length distribution: {s['length_dist']}\n")
            out.write(f"### {path}\n\n")
            out.write(f"- Frames: {s['count']}\n")
            out.write(f"- FCS_OK: {s['fcs_ok']} ({s.get('fcs_ok_pct', 0):.1f}%)\n")
            out.write(f"- Length distribution: {s['length_dist']}\n\n")

        out.write("## Interpretation\n\n")
        out.write("If clean FCS_OK > 0 and USRP FCS_OK == 0:\n")
        out.write("- USRP streaming injects frame-level damage that survives capture\n")
        out.write("- Phase 105 should investigate UHD buffer / sample delivery\n\n")
        out.write("If both clean and USRP FCS_OK > 0:\n")
        out.write("- USRP capture is fine; problem is purely real-time delivery\n")
        out.write("- Phase 105 should investigate UHD realtime scheduling\n\n")
        out.write("If both clean and USRP FCS_OK == 0:\n")
        out.write("- Even with capture, the algorithm chain fails on this IQ\n")
        out.write("- Phase 105 should re-examine equalizer / sync_short algorithms\n")

    print(f"Report written to {args.out}")


if __name__ == '__main__':
    sys.exit(main() or 0)