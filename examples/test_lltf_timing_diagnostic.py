#!/home/hy/conda/envs/gnuradio/bin/python
"""Phase 31 31a: 30-second capture of L-LTF timing data via IEEE80211_LLTF_TIMING_DUMP=1.

Outputs /tmp/p31a_diagnostic.csv with columns:
  splitter_seq, splitter_lts0_idx, splitter_lts1_idx,
  eq_lts0_idx, eq_lts1_idx, avg_snr_lsig, lsig_ok

Usage: run via examples/test_usrp_minimal_loopback.py with env-vars set.
The dump output is parsed post-hoc from /tmp/p31a_raw.log.

Note: actual dump format from Task 2/3 implementations:
  [SPLITTER] LTS0 seq=N current_idx=M lts1_expected_rel=K
  [EQUALIZER] H52 compute nread=A lts0_bin=B lts1_bin=C d_sym_idx=D lts0_mag0=E lts0_mag25=F
LTS1 is NOT dumped by the splitter (by design); we use the predicted lts1_expected_rel.
"""
import os
import sys
import re
import csv
import subprocess
import time

# Resolve project root from this script's location so we don't depend on cwd.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
LOOPBACK_SCRIPT = os.path.join(PROJECT_ROOT, "test_usrp_minimal_loopback.py")

DURATION = 30  # seconds
OUTPUT_CSV = "/tmp/p31a_diagnostic.csv"
RAW_LOG = "/tmp/p31a_raw.log"

ENV = os.environ.copy()
ENV["IEEE80211_LLTF_TIMING_DUMP"] = "1"
ENV["IEEE80211_SYNC_SHORT_BYPASS_ENERGY_GATE"] = "1"
ENV["IEEE80211_LSIG_RATE_FORCE"] = "0xD"
# Phase 31b (2026-06-17): dump L-SIG constellation to verify H-X2+H-X6
# hypothesis. avg_snr_lsig=12.91 is inflated |eq|², not low SNR. Per-SC
# phase error may be rotating BPSK symbols across decision boundary.
ENV["IEEE80211_LSIG_EQ_DUMP"] = "1"

def run_capture():
    if not os.path.isfile(LOOPBACK_SCRIPT):
        print(f"[31a] ERROR: loopback script not found: {LOOPBACK_SCRIPT}", file=sys.stderr)
        sys.exit(1)
    cmd = [
        "/home/hy/conda/envs/gnuradio/bin/python",
        LOOPBACK_SCRIPT,
        "--duration", str(DURATION),
        # Phase 31b fix: use the working p28_ltf0_timing.py USRP config
        # (5890 MHz, 20 dB TX gain) — defaults of test_usrp_minimal_loopback.py
        # (5180 MHz, 10 dB) produce 13-20 dB weaker air signal and 0 frames.
        "--freq", "5890",
        "--tx-gain", "20",
    ]
    with open(RAW_LOG, "w") as f:
        proc = subprocess.Popen(cmd, env=ENV, stdout=f, stderr=subprocess.STDOUT, cwd=PROJECT_ROOT)
        proc.wait()
    if proc.returncode != 0:
        print(f"[31a] ERROR: loopback capture failed (returncode={proc.returncode}); see {RAW_LOG}", file=sys.stderr)
        sys.exit(proc.returncode)

def parse_dump_to_csv():
    """Parse [SPLITTER] LTS0 and [EQUALIZER] H52 dump lines into CSV rows."""
    # Note: actual dump format (from Task 2 implementation):
    #   [SPLITTER] LTS0 seq=N current_idx=M lts1_expected_rel=K
    # LTS1 is NOT dumped (by design); we use lts1_expected_rel from the splitter
    # and lts1_bin from the equalizer.
    pattern_splitter = re.compile(
        r"\[SPLITTER\] LTS0 seq=(\d+) current_idx=(\d+) lts1_expected_rel=(\d+)"
    )
    pattern_eq = re.compile(
        r"\[EQUALIZER\] H52 compute nread=(\d+) lts0_bin=(\d+) lts1_bin=(\d+)"
    )
    pattern_snr = re.compile(r"avg_snr_lsig=([\d.\-]+)")
    pattern_lsig_ok = re.compile(r"LSIG_OK=(\d)")

    rows = []
    with open(RAW_LOG) as f:
        for line in f:
            m_s = pattern_splitter.search(line)
            m_e = pattern_eq.search(line)
            m_n = pattern_snr.search(line)
            m_l = pattern_lsig_ok.search(line)
            if m_s or m_e:
                lts0_idx = m_s.group(2) if m_s else ""
                lts1_idx = m_s.group(3) if m_s else ""
                rows.append({
                    "splitter_seq": m_s.group(1) if m_s else "",
                    "splitter_lts0_idx": lts0_idx,
                    "splitter_lts1_idx": lts1_idx,
                    "eq_lts0_idx": m_e.group(2) if m_e else "",
                    "eq_lts1_idx": m_e.group(3) if m_e else "",
                    "avg_snr_lsig": m_n.group(1) if m_n else "",
                    "lsig_ok": m_l.group(1) if m_l else "",
                })
    return rows

if __name__ == "__main__":
    print(f"[31a] Capturing {DURATION}s of USRP frames with IEEE80211_LLTF_TIMING_DUMP=1 ...")
    run_capture()
    rows = parse_dump_to_csv()
    print(f"[31a] Collected {len(rows)} LTS0/LTS1 records")
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "splitter_seq", "splitter_lts0_idx", "splitter_lts1_idx",
            "eq_lts0_idx", "eq_lts1_idx",
            "avg_snr_lsig", "lsig_ok",
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[31a] Wrote {OUTPUT_CSV}")
