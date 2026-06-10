#!/home/hy/conda/envs/gnuradio/bin/python
"""
Offline analysis of viterbi path-metric audit logs from USRP.

Reads `[LSIG_VITERBI_AUDIT] inv=N deintl48=...` lines from a log file and
reports the distribution of deinterleaved-bit balance across frames.
A balanced (24/48) distribution suggests random noise; a 0/48 or 48/48
distribution suggests BPSK is "stuck" on one polarity (massive phase
rotation hasn't been compensated).
"""
import re
import sys
import numpy as np


def parse_audit_line(line):
    """Extract (inv, deintl48_str) from a [LSIG_VITERBI_AUDIT] line."""
    m = re.search(r"\[LSIG_VITERBI_AUDIT\] inv=(\d+) deintl48=([01]+)", line)
    if not m:
        return None
    inv = int(m.group(1))
    deintl48_str = m.group(2)
    if len(deintl48_str) != 48:
        return None
    return inv, [int(c) for c in deintl48_str]


def analyze_log(log_path):
    """Walk a log file, parse audit lines, and print a summary."""
    raw_text = open(log_path).read()
    # Count raw tag occurrences vs well-formed matches so we can warn when
    # the bulk of audit lines were shredded by concurrent stdout writes.
    raw_tag_count = raw_text.count("[LSIG_VITERBI_AUDIT]")
    # Use regex over the full text to extract ONLY well-formed audit lines
    # (those with exactly 48 bits in deintl48 and not truncated by
    # interleaved log writes from other threads).
    well_formed_ok = re.findall(
        r"\[LSIG_VITERBI_AUDIT\] inv=\d+ deintl48=([01]{48}) decoded24=([01]{24})",
        raw_text,
    )
    well_formed_fail = re.findall(
        r"\[LSIG_VITERBI_AUDIT\] inv=\d+ deintl48=([01]{48})\n",
        raw_text,
    )

    print(f"Total [LSIG_VITERBI_AUDIT] tags in log: {raw_tag_count}")
    print(f"  well-formed OK-path lines (with decoded24): {len(well_formed_ok)}")
    print(f"  well-formed FAIL-path lines (48 bits, no decoded24): {len(well_formed_fail)}")
    # Warn if more than half of audit tags were shredded by concurrent stdout
    # writes (i.e. the audit line is incomplete and matches no strict regex).
    well_formed_total = len(well_formed_ok) + len(well_formed_fail)
    shredded = raw_tag_count - well_formed_total
    if raw_tag_count > 0 and shredded > 0 and shredded * 2 > raw_tag_count:
        print(f"WARNING: {shredded}/{raw_tag_count} audit lines were shredded by concurrent stdout writes.")
        print(f"  Fix: see USRP_LOG macro — must be atomic or buffered.")

    # Build a unified list of (inv, bits) using the strict regex on each tag
    audit_records = []
    for bits48, _dec24 in well_formed_ok:
        # we don't know inv from this regex — pull inv from the surrounding
        # match by searching the original line; for now default 0 and re-parse
        # from raw_text to get inv
        pass
    # Re-parse inv with a stricter pattern that also captures the inv flag.
    ok_matches = re.findall(
        r"\[LSIG_VITERBI_AUDIT\] inv=(\d+) deintl48=([01]{48}) decoded24=([01]{24})",
        raw_text,
    )
    fail_matches = re.findall(
        r"\[LSIG_VITERBI_AUDIT\] inv=(\d+) deintl48=([01]{48})\n",
        raw_text,
    )
    all_records = ok_matches + fail_matches
    if not all_records:
        print("No well-formed audit lines found. The log was likely captured")
        print("with multi-threaded stdout writes that interleaved log lines,")
        print("truncating the 48-bit pattern.")
        return

    inv0_count = sum(1 for inv, _b, *_ in all_records if inv == "0")
    inv1_count = sum(1 for inv, _b, *_ in all_records if inv == "1")
    print(f"  inv=0 attempts (well-formed): {inv0_count}")
    print(f"  inv=1 attempts (well-formed): {inv1_count}")
    print(f"  ratio: {inv1_count / max(1, inv0_count):.2f}")

    balances = []
    for rec in all_records:
        bits_str = rec[1]
        balances.append(sum(int(c) for c in bits_str))
    if balances:
        mean_bal = np.mean(balances)
        std_bal = np.std(balances)
        print(f"  deintl48 bit balance: mean={mean_bal:.1f}/48 std={std_bal:.1f}")
        # Interpret the distribution
        if abs(mean_bal - 24) < 2:
            verdict = "RANDOM (no signal — viterbi inputs are noise)"
        elif mean_bal < 8:
            verdict = "BPSK STUCK AT 0 (huge phase rotation, signal at imaginary axis)"
        elif mean_bal > 40:
            verdict = "BPSK STUCK AT 1 (huge phase rotation, signal at imaginary axis)"
        elif 10 <= mean_bal <= 14 or 34 <= mean_bal <= 38:
            verdict = "MODERATE SIGNAL (some signal, but distorted)"
        else:
            verdict = f"INTERMEDIATE (mean={mean_bal:.1f})"
        print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_path>")
        sys.exit(1)
    analyze_log(sys.argv[1])
